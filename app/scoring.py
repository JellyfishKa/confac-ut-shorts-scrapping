from dataclasses import dataclass
from datetime import datetime, timezone
from math import log1p

from app.config import settings


@dataclass(slots=True)
class PublicStats:
    views: int
    likes: int
    comments: int
    published_at: datetime
    duration_seconds: int


@dataclass(slots=True)
class DerivedStats:
    age_hours: float
    views_per_hour: float
    like_rate: float
    comment_rate: float
    popularity_score: float


def derive_stats(stats: PublicStats, now: datetime | None = None) -> DerivedStats:
    now = now or datetime.now(timezone.utc)
    published = stats.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)

    age_hours = max((now - published).total_seconds() / 3600, 1.0)
    views = max(stats.views, 0)
    safe_views = max(views, 1)

    views_per_hour = views / age_hours
    like_rate = max(stats.likes, 0) / safe_views
    comment_rate = max(stats.comments, 0) / safe_views

    # Fixed, bounded normalizers make the score comparable across API calls.
    # They are intentionally config-driven and easy to tune on real data.
    velocity_norm = min(log1p(views_per_hour) / log1p(1_000_000), 1.0)
    like_norm = min(like_rate / 0.10, 1.0)
    comment_norm = min(comment_rate / 0.02, 1.0)

    total_weight = (
        settings.weight_views_per_hour
        + settings.weight_like_rate
        + settings.weight_comment_rate
    ) or 1.0

    score = (
        settings.weight_views_per_hour * velocity_norm
        + settings.weight_like_rate * like_norm
        + settings.weight_comment_rate * comment_norm
    ) / total_weight

    return DerivedStats(
        age_hours=round(age_hours, 2),
        views_per_hour=round(views_per_hour, 2),
        like_rate=round(like_rate, 6),
        comment_rate=round(comment_rate, 6),
        popularity_score=round(score, 6),
    )


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
    return (
        derived.age_hours <= max_age_hours
        and stats.views >= min_views
        and derived.views_per_hour >= min_views_per_hour
        and derived.like_rate >= min_like_rate
        and stats.duration_seconds <= max_duration_seconds
    )
