from dataclasses import dataclass
from datetime import datetime, timezone
from math import log1p

from app.config import settings
from app.models import VideoCandidate


@dataclass(slots=True)
class PublicStats:
    """Public counters as a platform reports them; None means the platform did not say."""
    views: int | None
    likes: int | None
    comments: int | None
    published_at: datetime | None
    duration_seconds: int | None


@dataclass(slots=True)
class DerivedStats:
    age_hours: float | None
    views_per_hour: float | None
    like_rate: float | None
    comment_rate: float | None
    popularity_score: float | None


def derive_stats(stats: PublicStats, now: datetime | None = None) -> DerivedStats:
    now = now or datetime.now(timezone.utc)
    age_hours = None
    if stats.published_at is not None:
        published = stats.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_hours = max((now - published).total_seconds() / 3600, 1.0)

    views = max(stats.views, 0) if stats.views is not None else None
    safe_views = max(views or 0, 1)
    views_per_hour = views / age_hours if views is not None and age_hours is not None else None
    like_rate = max(stats.likes, 0) / safe_views if views is not None and stats.likes is not None else None
    comment_rate = max(stats.comments, 0) / safe_views if views is not None and stats.comments is not None else None

    # Fixed, bounded normalizers make the score comparable across API calls.
    # They are intentionally config-driven and easy to tune on real data.
    # View speed is the one metric every scored platform shares, so the score
    # needs it; the weight of a missing like or comment rate goes to the rest.
    score = None
    if views_per_hour is not None:
        parts = [(settings.weight_views_per_hour, min(log1p(views_per_hour) / log1p(1_000_000), 1.0))]
        if like_rate is not None:
            parts.append((settings.weight_like_rate, min(like_rate / 0.10, 1.0)))
        if comment_rate is not None:
            parts.append((settings.weight_comment_rate, min(comment_rate / 0.02, 1.0)))
        total_weight = sum(weight for weight, _ in parts) or 1.0
        score = sum(weight * value for weight, value in parts) / total_weight

    def rounded(value: float | None, digits: int) -> float | None:
        return round(value, digits) if value is not None else None

    return DerivedStats(
        age_hours=rounded(age_hours, 2),
        views_per_hour=rounded(views_per_hour, 2),
        like_rate=rounded(like_rate, 6),
        comment_rate=rounded(comment_rate, 6),
        popularity_score=rounded(score, 6),
    )


def ranking_key(video: VideoCandidate) -> tuple[float, float]:
    """Rank by the best estimate of current speed: measured growth, else lifetime views/h."""
    speed = video.growth_views_per_hour if video.growth_views_per_hour is not None else video.views_per_hour
    return speed or 0, video.popularity_score or 0


def passes_filter(
    stats: PublicStats,
    derived: DerivedStats,
    *,
    max_age_hours: int,
    min_views: int,
    min_views_per_hour: float,
    min_like_rate: float,
    max_duration_seconds: int,
) -> bool:
    """An unreported metric cannot prove a positive threshold.

    Unknown duration passes: platform searches already target short videos,
    and providers drop results where duration is required to tell.
    """
    def at_least(value: float | None, threshold: float) -> bool:
        return threshold <= 0 or (value is not None and value >= threshold)

    return (
        derived.age_hours is not None
        and derived.age_hours <= max_age_hours
        and at_least(stats.views, min_views)
        and at_least(derived.views_per_hour, min_views_per_hour)
        and at_least(derived.like_rate, min_like_rate)
        and (stats.duration_seconds is None or stats.duration_seconds <= max_duration_seconds)
    )
