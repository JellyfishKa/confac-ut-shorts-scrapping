"""Shared path from one platform's raw video data to ranked candidates.

Providers only fetch and parse; snapshots, derived metrics, filters and
ranking happen here, so every platform follows the same rules.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import settings
from app.models import SearchRequest, VideoCandidate
from app.scoring import DerivedStats, PublicStats, derive_stats, passes_filter, ranking_key
from app.snapshots import GrowthStats, SnapshotStore


@dataclass(slots=True)
class RawVideo:
    """One video as a platform reports it; None means the platform did not say."""
    platform: str
    video_id: str
    url: str
    title: str
    published_at: datetime | None
    duration_seconds: int | None
    views: int | None
    likes: int | None = None
    comments: int | None = None
    description: str = ""
    channel_id: str = ""
    channel_title: str = ""
    thumbnail_url: str | None = None
    subscribers: int | None = None


@dataclass(slots=True)
class Filters:
    max_age_hours: int
    min_views: int
    min_views_per_hour: float
    min_like_rate: float
    max_duration_seconds: int


def resolve_filters(request: SearchRequest) -> Filters:
    def pick(value, default):
        return default if value is None else value

    return Filters(
        max_age_hours=pick(request.max_age_hours, settings.default_max_age_hours),
        min_views=pick(request.min_views, settings.default_min_views),
        min_views_per_hour=pick(request.min_views_per_hour, settings.default_min_views_per_hour),
        min_like_rate=pick(request.min_like_rate, settings.default_min_like_rate),
        max_duration_seconds=pick(request.max_duration_seconds, settings.default_max_duration_seconds),
    )


def rank(candidates: Iterable[VideoCandidate], limit: int) -> list[VideoCandidate]:
    return sorted(candidates, key=ranking_key, reverse=True)[:limit]


class CandidateBuilder:
    """Turns raw videos into candidates for one search, sharing one snapshot store."""

    def __init__(self, now: datetime | None = None):
        self.now = now or datetime.now(timezone.utc)
        self._snapshots: SnapshotStore | None = None

    def select(self, raw_videos: Iterable[RawVideo], filters: Filters) -> list[VideoCandidate]:
        """Candidates that pass the filters, unranked.

        The snapshot is recorded before popularity filters, so a video that
        qualifies on a later search already has growth history. Videos longer
        than the duration limit are outside the search and are not tracked.
        """
        candidates = []
        for raw in raw_videos:
            if raw.duration_seconds is not None and raw.duration_seconds > filters.max_duration_seconds:
                continue
            growth = self._observe(raw)
            stats = _public_stats(raw)
            derived = derive_stats(stats, now=self.now)
            if passes_filter(
                stats, derived,
                max_age_hours=filters.max_age_hours,
                min_views=filters.min_views,
                min_views_per_hour=filters.min_views_per_hour,
                min_like_rate=filters.min_like_rate,
                max_duration_seconds=filters.max_duration_seconds,
            ):
                candidates.append(_candidate(raw, derived, growth))
        return candidates

    def candidate(self, raw: RawVideo) -> VideoCandidate:
        """One candidate without filters, for a video the user picked directly."""
        growth = self._observe(raw)
        return _candidate(raw, derive_stats(_public_stats(raw), now=self.now), growth)

    def _observe(self, raw: RawVideo) -> GrowthStats:
        if raw.views is None:
            return GrowthStats()
        if self._snapshots is None:
            self._snapshots = SnapshotStore(settings.data_dir / "snapshots.sqlite3")
        # YouTube snapshots predate platform prefixes; keep their keys stable.
        key = raw.video_id if raw.platform == "youtube" else f"{raw.platform}:{raw.video_id}"
        if raw.likes is not None and raw.comments is not None:
            return self._snapshots.observe(key, views=raw.views, likes=raw.likes, comments=raw.comments)
        return self._snapshots.observe_views(key, views=raw.views)


def _public_stats(raw: RawVideo) -> PublicStats:
    return PublicStats(raw.views, raw.likes, raw.comments, raw.published_at, raw.duration_seconds)


def _candidate(raw: RawVideo, derived: DerivedStats, growth: GrowthStats) -> VideoCandidate:
    breakout_ratio = None
    if raw.views is not None and raw.subscribers:
        breakout_ratio = round(raw.views / raw.subscribers, 4)
    return VideoCandidate(
        platform=raw.platform,
        video_id=raw.video_id,
        url=raw.url,
        title=raw.title,
        description=raw.description,
        channel_id=raw.channel_id,
        channel_title=raw.channel_title,
        thumbnail_url=raw.thumbnail_url,
        published_at=raw.published_at,
        duration_seconds=raw.duration_seconds,
        views=raw.views,
        likes=raw.likes,
        comments=raw.comments,
        subscribers=raw.subscribers,
        age_hours=derived.age_hours,
        views_per_hour=derived.views_per_hour,
        like_rate=derived.like_rate,
        comment_rate=derived.comment_rate,
        breakout_ratio=breakout_ratio,
        previous_views=growth.previous_views,
        growth_views_per_hour=growth.growth_views_per_hour,
        growth_likes_per_hour=growth.growth_likes_per_hour,
        snapshot_age_minutes=growth.snapshot_age_minutes,
        popularity_score=derived.popularity_score,
    )
