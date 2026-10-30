"""Topic-free discovery of AI-generated shorts.

Instead of a user's topic, a background run searches the markers listed in
DISCOVERY_QUERIES_FILE and polls the uploads of YouTube channels whose videos
made the hype list: such channel farms post every day. Everything found goes
to the tracker, so "Хайп" picks from it without anyone typing a topic.
"""

import asyncio
import logging
import math
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from typing import get_args

import httpx

from app.config import settings
from app.ai_check import check_videos
from app.hype import AI_CHECK_POOL, pick_hype
from app.models import (
    DiscoveryRun,
    DiscoveryStatus,
    SearchPlatform,
    SearchRequest,
    SearchResponse,
    WatchedChannel,
)
from app.pipeline import CandidateBuilder, Filters
from app.search_errors import ProviderUnavailableError
from app.tracker import TrackedStore
from app.youtube import YouTubeProvider

logger = logging.getLogger(__name__)

Search = Callable[[SearchRequest], Awaitable[SearchResponse]]

POLL_SECONDS = 300
SEARCH_LIMIT = 50
# search.list costs 100 quota units; the videos.list and channels.list after it cost 1 each.
YOUTUBE_SEARCH_UNITS = 102
DETAIL_UNITS_PER_BATCH = 2
HYPE_CHANNELS_PER_RUN = 10

_running = False


def load_queries() -> list[str]:
    """Markers from the queries file, one per line; a line starting with "# " is a comment.

    A bare hashtag such as "#aiart" is a marker, not a comment.
    """
    try:
        lines = settings.discovery_queries_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    queries: list[str] = []
    for line in lines:
        query = line.strip()
        if not query or query == "#" or query.startswith("# ") or len(query) > 200:
            continue
        if query not in queries:
            queries.append(query)
    return queries


def discovery_platforms() -> list[str]:
    names = (name.strip() for name in settings.discovery_platforms.split(","))
    return [name for name in dict.fromkeys(names) if name in get_args(SearchPlatform)]


class DiscoveryStore:
    """Discovery runs and watched channel farms, next to the snapshots."""

    def __init__(self):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = settings.data_dir / "snapshots.sqlite3"
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS discovery_runs ("
                "started_at TEXT NOT NULL, youtube_units INTEGER NOT NULL, run TEXT NOT NULL)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS watched_channels (
                    channel_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    last_hype_at TEXT NOT NULL,
                    hype_rank INTEGER NOT NULL,
                    last_checked_at TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def record(self, run: DiscoveryRun) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO discovery_runs(started_at, youtube_units, run) VALUES (?, ?, ?)",
                (run.started_at.isoformat(), run.youtube_units, run.model_dump_json()),
            )

    def run_count(self) -> int:
        with closing(self._connect()) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0])

    def last_run(self) -> DiscoveryRun | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT run FROM discovery_runs ORDER BY started_at DESC LIMIT 1").fetchone()
        return DiscoveryRun.model_validate_json(row[0]) if row else None

    def youtube_units_on(self, day: date) -> int:
        """YouTube quota units discovery spent on a UTC day."""
        start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(youtube_units), 0) FROM discovery_runs WHERE started_at >= ? AND started_at < ?",
                (start.isoformat(), (start + timedelta(days=1)).isoformat()),
            ).fetchone()
        return int(row[0])

    def watch_channels(self, channels: list[tuple[str, str]], now: datetime) -> None:
        """Watch channels in hype order, keeping the DISCOVERY_MAX_CHANNELS most recently hyped."""
        titles = dict(channels)
        rows = [
            (channel_id, titles[channel_id], now.isoformat(), now.isoformat(), rank)
            for rank, channel_id in enumerate(dict.fromkeys(channel_id for channel_id, _ in channels))
        ]
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                """
                INSERT INTO watched_channels(channel_id, title, added_at, last_hype_at, hype_rank)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    title = excluded.title,
                    last_hype_at = excluded.last_hype_at,
                    hype_rank = excluded.hype_rank
                """,
                rows,
            )
            conn.execute(
                """
                DELETE FROM watched_channels WHERE channel_id NOT IN (
                    SELECT channel_id FROM watched_channels ORDER BY last_hype_at DESC, hype_rank LIMIT ?
                )
                """,
                (settings.discovery_max_channels,),
            )

    def channels(self) -> list[WatchedChannel]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT channel_id, title, added_at, last_checked_at FROM watched_channels "
                "ORDER BY last_hype_at DESC, hype_rank"
            ).fetchall()
        return [
            WatchedChannel(
                channel_id=channel_id, title=title, added_at=datetime.fromisoformat(added_at),
                last_checked_at=datetime.fromisoformat(checked_at) if checked_at else None,
            )
            for channel_id, title, added_at, checked_at in rows
        ]

    def mark_checked(self, channel_ids: list[str], now: datetime) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                "UPDATE watched_channels SET last_checked_at = ? WHERE channel_id = ?",
                [(now.isoformat(), channel_id) for channel_id in channel_ids],
            )


async def run_discovery(search: Search, now: datetime | None = None) -> DiscoveryRun:
    """One pass: search every marker, then poll the watched channels for new uploads.

    YouTube calls stop before the run would exceed DISCOVERY_YOUTUBE_UNITS_PER_DAY;
    the other platforms keep searching.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    store = DiscoveryStore()
    budget = settings.discovery_youtube_units_per_day - store.youtube_units_on(now.date())
    units = 0
    searched = candidates = 0
    errors: list[str] = []

    queries = load_queries()
    on_youtube = _youtube_markers(queries, store.run_count())
    for query in queries:
        platforms = discovery_platforms()
        if "youtube" in platforms and (query not in on_youtube or units + YOUTUBE_SEARCH_UNITS > budget):
            platforms.remove("youtube")
        if not platforms:
            continue
        response = await search(SearchRequest(
            query=query, platforms=platforms, max_age_hours=settings.discovery_max_age_hours,
            min_views=settings.hype_min_views, limit=SEARCH_LIMIT,
        ))
        searched += 1
        candidates += response.returned
        for status in response.platform_statuses:
            if status.platform == "youtube" and status.status != "unavailable":
                units += YOUTUBE_SEARCH_UNITS
            if status.status != "ok" and status.detail:
                _note(errors, f"{status.platform}: {status.detail}")

    # Only YouTube has a cheap uploads feed, so farms come from the YouTube top.
    picks = pick_hype(TrackedStore().active(now), HYPE_CHANNELS_PER_RUN, platform="youtube").picks
    farms = [(pick.video.channel_id, pick.video.channel_title) for pick in picks if pick.video.channel_id]
    if farms:
        store.watch_channels(farms, now)

    checked, channel_videos, spent = await _poll_channels(store, budget - units, now, errors)
    units += spent

    if settings.gemini_api_key:
        # Judge the YouTube leaders now, so "Хайп" with the AI filter opens without waiting.
        leaders = pick_hype(TrackedStore().active(now), AI_CHECK_POOL, platform="youtube").picks
        verdicts = await check_videos([pick.video for pick in leaders])
        for verdict in verdicts.values():
            if verdict.ai_generated is None:
                _note(errors, f"AI-проверка: {verdict.reason}")
    run = DiscoveryRun(
        started_at=now, finished_at=datetime.now(timezone.utc), queries=searched, candidates=candidates,
        channels_checked=checked, channel_videos=channel_videos, youtube_units=units, errors=errors,
    )
    store.record(run)
    return run


async def _poll_channels(store: DiscoveryStore, budget: int, now: datetime,
                         errors: list[str]) -> tuple[int, int, int]:
    """Track new uploads of the least recently checked channels the budget covers.

    Returns channels checked, videos tracked and quota units spent.
    """
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    channels = sorted(store.channels(), key=lambda channel: channel.last_checked_at or oldest)
    channels = channels[:max(budget - DETAIL_UNITS_PER_BATCH, 0)]
    if not channels:
        return 0, 0, 0
    spent = 0
    try:
        provider = YouTubeProvider()
        spent += len(channels)
        published_after = now - timedelta(hours=settings.discovery_max_age_hours)
        video_ids = list(dict.fromkeys(
            await provider.recent_uploads([channel.channel_id for channel in channels], published_after)
        ))
        video_ids = video_ids[:max((budget - spent) // DETAIL_UNITS_PER_BATCH, 0) * 50]
        tracked = []
        if video_ids:
            spent += math.ceil(len(video_ids) / 50) * DETAIL_UNITS_PER_BATCH
            raw_videos = await provider.fetch_videos(video_ids)
            tracked = CandidateBuilder(now=now).select(raw_videos, _filters())
            TrackedStore().track(tracked, now)
        store.mark_checked([channel.channel_id for channel in channels], now)
        return len(channels), len(tracked), spent
    except ProviderUnavailableError as exc:
        _note(errors, f"youtube: {exc}")
    except httpx.HTTPStatusError as exc:
        _note(errors, f"youtube: каналы-фермы — площадка ответила HTTP {exc.response.status_code}.")
    except httpx.HTTPError as exc:
        _note(errors, f"youtube: каналы-фермы — {type(exc).__name__}.")
    return 0, 0, spent


def youtube_markers_per_run() -> int:
    """How many markers one run searches on YouTube, so the daily budget lasts for all the day's runs."""
    runs_per_day = max(24 / settings.discovery_interval_hours, 1)
    return max(int(settings.discovery_youtube_units_per_day / runs_per_day) // YOUTUBE_SEARCH_UNITS, 1)


def _youtube_markers(queries: list[str], run_number: int) -> set[str]:
    """A slice of the markers that moves on with every run, so all of them reach YouTube in turn."""
    per_run = youtube_markers_per_run()
    if per_run >= len(queries):
        return set(queries)
    start = run_number * per_run % len(queries)
    return {queries[(start + offset) % len(queries)] for offset in range(per_run)}


def _filters() -> Filters:
    return Filters(
        max_age_hours=settings.discovery_max_age_hours,
        min_views=settings.hype_min_views,
        min_views_per_hour=0,
        min_like_rate=0,
        max_duration_seconds=settings.default_max_duration_seconds,
    )


def _note(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def is_running() -> bool:
    return _running


async def run_once(search: Search) -> DiscoveryRun | None:
    """Run discovery unless a run is already in progress."""
    global _running
    if _running:
        return None
    _running = True
    try:
        return await run_discovery(search)
    finally:
        _running = False


def next_run_at(last: DiscoveryRun | None) -> datetime | None:
    return last.started_at + timedelta(hours=settings.discovery_interval_hours) if last else None


async def run_forever(search: Search) -> None:
    """Run discovery every DISCOVERY_INTERVAL_HOURS while the server runs, the first time right away."""
    while True:
        try:
            due = next_run_at(DiscoveryStore().last_run())
            if due is None or due <= datetime.now(timezone.utc):
                await run_once(search)
        except Exception:
            # A locked database or a failed run must not stop the schedule.
            logger.exception("Discovery run failed")
        await asyncio.sleep(POLL_SECONDS)


def status() -> DiscoveryStatus:
    store = DiscoveryStore()
    last = store.last_run()
    return DiscoveryStatus(
        enabled=settings.discovery_enabled,
        running=_running,
        interval_hours=settings.discovery_interval_hours,
        platforms=discovery_platforms(),
        queries=load_queries(),
        youtube_queries_per_run=youtube_markers_per_run(),
        youtube_units_today=store.youtube_units_on(datetime.now(timezone.utc).date()),
        youtube_units_per_day=settings.discovery_youtube_units_per_day,
        next_run_at=next_run_at(last) if settings.discovery_enabled else None,
        last_run=last,
        channels=store.channels(),
    )
