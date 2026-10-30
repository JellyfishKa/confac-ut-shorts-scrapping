"""Re-poll videos found by search, so growth is measured without repeating the search.

The server runs `run_forever` in the background. `python -m app.tracker` runs one
pass, for a system scheduler when the server is not running.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.import_video import _count, _extract
from app.models import SnapshotPoint, TrackedVideo, VideoCandidate
from app.pipeline import CandidateBuilder, Counts, RawVideo, snapshot_key
from app.rutube import fetch_counts as fetch_rutube_counts
from app.scoring import ranking_key
from app.search_errors import ProviderUnavailableError
from app.snapshots import SnapshotStore
from app.youtube import YouTubeProvider

logger = logging.getLogger(__name__)

POLL_SECONDS = 60
ID_API_PLATFORMS = {"youtube", "rutube"}
# Reading a public page through yt-dlp takes seconds, so each pass reads a bounded batch.
PAGE_READS_PER_PASS = 10
PAGE_READ_CONCURRENCY = 4
MISSING_VIDEO = "Площадка не вернула просмотры: ролик удалён, скрыт или недоступен."


class TrackedStore:
    """Tracked videos with their latest candidate data, next to the snapshots."""

    def __init__(self):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = settings.data_dir / "snapshots.sqlite3"
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tracked_videos (
                    platform TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    candidate TEXT NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    tracked_until TEXT NOT NULL,
                    last_checked_at TEXT,
                    last_error TEXT,
                    PRIMARY KEY (platform, video_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def track(self, videos: Iterable[VideoCandidate], now: datetime | None = None) -> None:
        """Track videos for TRACKER_HOURS; seeing a video again extends the window.

        The caller has just recorded a snapshot, so the first re-poll waits a full interval.
        Without views there is nothing to re-poll.
        """
        now = _utc(now)
        until = (now + timedelta(hours=settings.tracker_hours)).isoformat()
        rows = [
            (video.platform, video.video_id, video.model_dump_json(), until, now.isoformat())
            for video in videos if video.views is not None
        ]
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                """
                INSERT INTO tracked_videos(platform, video_id, candidate, tracked_until, last_checked_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform, video_id) DO UPDATE SET
                    candidate = excluded.candidate,
                    tracked_until = MAX(tracked_until, excluded.tracked_until),
                    last_checked_at = excluded.last_checked_at,
                    last_error = NULL
                """,
                rows,
            )

    def due(self, now: datetime | None = None) -> list[VideoCandidate]:
        now = _utc(now)
        checked_before = (now - timedelta(minutes=settings.tracker_interval_minutes)).isoformat()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT candidate FROM tracked_videos
                WHERE (pinned = 1 OR tracked_until > ?)
                  AND (last_checked_at IS NULL OR last_checked_at <= ?)
                """,
                (now.isoformat(), checked_before),
            ).fetchall()
        return [VideoCandidate.model_validate_json(row[0]) for row in rows]

    def record(self, video: VideoCandidate, checked_at: datetime, error: str | None = None) -> None:
        """Store a re-poll result; on error keep the last known data."""
        with closing(self._connect()) as conn, conn:
            if error is None:
                conn.execute(
                    "UPDATE tracked_videos SET candidate = ?, last_checked_at = ?, last_error = NULL "
                    "WHERE platform = ? AND video_id = ?",
                    (video.model_dump_json(), checked_at.isoformat(), video.platform, video.video_id),
                )
            else:
                conn.execute(
                    "UPDATE tracked_videos SET last_checked_at = ?, last_error = ? "
                    "WHERE platform = ? AND video_id = ?",
                    (checked_at.isoformat(), error, video.platform, video.video_id),
                )

    def active(self, now: datetime | None = None) -> list[TrackedVideo]:
        """Pinned videos and those within their window, fastest first, with view history."""
        now = _utc(now)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT candidate, pinned, tracked_until, last_checked_at, last_error
                FROM tracked_videos WHERE pinned = 1 OR tracked_until > ?
                """,
                (now.isoformat(),),
            ).fetchall()
        snapshots = SnapshotStore(self.db_path)
        tracked = [self._tracked(row, snapshots) for row in rows]
        tracked.sort(key=lambda item: ranking_key(item.video), reverse=True)
        return tracked

    def get(self, platform: str, video_id: str) -> TrackedVideo | None:
        """One tracked video with its view history, expired or not."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT candidate, pinned, tracked_until, last_checked_at, last_error "
                "FROM tracked_videos WHERE platform = ? AND video_id = ?",
                (platform, video_id),
            ).fetchone()
        return self._tracked(row, SnapshotStore(self.db_path)) if row else None

    @staticmethod
    def _tracked(row: tuple, snapshots: SnapshotStore) -> TrackedVideo:
        candidate, pinned, until, checked_at, error = row
        video = VideoCandidate.model_validate_json(candidate)
        history = snapshots.history(snapshot_key(video.platform, video.video_id))
        return TrackedVideo(
            video=video,
            pinned=bool(pinned),
            tracked_until=datetime.fromisoformat(until),
            last_checked_at=datetime.fromisoformat(checked_at) if checked_at else None,
            last_error=error,
            history=[SnapshotPoint(captured_at=at, views=views) for at, views in history],
        )

    def set_pinned(self, platform: str, video_id: str, pinned: bool, now: datetime | None = None) -> bool:
        """Pin to track without a deadline; unpinning restarts the normal window."""
        until = (_utc(now) + timedelta(hours=settings.tracker_hours)).isoformat()
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "UPDATE tracked_videos SET pinned = ?, tracked_until = MAX(tracked_until, ?) "
                "WHERE platform = ? AND video_id = ?",
                (int(pinned), until, platform, video_id),
            )
        return cursor.rowcount > 0

    def remove(self, platform: str, video_id: str) -> bool:
        """Stop tracking; recorded snapshots stay for later analysis."""
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "DELETE FROM tracked_videos WHERE platform = ? AND video_id = ?", (platform, video_id),
            )
        return cursor.rowcount > 0


async def refresh_due(now: datetime | None = None) -> int:
    """Re-poll videos whose interval has passed; returns how many were checked.

    Platforms are polled concurrently, so slow page reads cannot delay
    YouTube and Rutube. Page reads beyond PAGE_READS_PER_PASS stay due
    and are picked up by the next pass.
    """
    now = _utc(now)
    store = TrackedStore()
    by_platform: dict[str, list[VideoCandidate]] = {}
    page_reads = 0
    for video in store.due(now):
        if video.platform not in ID_API_PLATFORMS:
            if page_reads >= PAGE_READS_PER_PASS:
                continue
            page_reads += 1
        by_platform.setdefault(video.platform, []).append(video)

    results = await asyncio.gather(
        *(_fetch_counts(platform, videos) for platform, videos in by_platform.items()),
        return_exceptions=True,
    )
    builder = CandidateBuilder(now=now)
    checked = 0
    for videos, counts in zip(by_platform.values(), results):
        checked += len(videos)
        if isinstance(counts, BaseException):
            for video in videos:
                store.record(video, now, error=_describe(counts))
            continue
        for video in videos:
            current = counts.get(video.video_id)
            if current is None or current.views is None:
                store.record(video, now, error=MISSING_VIDEO)
            else:
                store.record(builder.candidate(_with_counts(video, current)), now)
    return checked


async def run_forever() -> None:
    """Refresh due videos while the server runs."""
    while True:
        try:
            await refresh_due()
        except Exception:
            logger.exception("Tracker refresh failed")
        await asyncio.sleep(POLL_SECONDS)


async def _fetch_counts(platform: str, videos: list[VideoCandidate]) -> dict[str, Counts]:
    video_ids = [video.video_id for video in videos]
    if platform == "youtube":
        return await YouTubeProvider().fetch_counts(video_ids)
    if platform == "rutube":
        return await fetch_rutube_counts(video_ids)
    # VK, TikTok and Instagram have no free by-ID counter API here; read the public page.
    slots = asyncio.Semaphore(PAGE_READ_CONCURRENCY)

    async def read(video: VideoCandidate) -> tuple[str, Counts] | None:
        async with slots:
            try:
                info = await asyncio.wait_for(asyncio.to_thread(_extract, video.url), timeout=60)
            except Exception:
                return None
        return video.video_id, Counts(
            _count(info.get("view_count")), _count(info.get("like_count")), _count(info.get("comment_count")),
        )

    return dict(result for result in await asyncio.gather(*(read(video) for video in videos)) if result)


def _with_counts(video: VideoCandidate, counts: Counts) -> RawVideo:
    return RawVideo(
        platform=video.platform,
        video_id=video.video_id,
        url=video.url,
        title=video.title,
        description=video.description,
        channel_id=video.channel_id,
        channel_title=video.channel_title,
        thumbnail_url=video.thumbnail_url,
        published_at=video.published_at,
        duration_seconds=video.duration_seconds,
        views=counts.views,
        likes=counts.likes,
        comments=counts.comments,
        subscribers=video.subscribers,
    )


def _describe(exc: Exception) -> str:
    if isinstance(exc, ProviderUnavailableError):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Площадка ответила HTTP {exc.response.status_code}."
    return f"Не удалось получить данные: {type(exc).__name__}."


def _utc(value: datetime | None) -> datetime:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc)


if __name__ == "__main__":
    checked = asyncio.run(refresh_due())
    print(f"Проверено роликов: {checked}")
