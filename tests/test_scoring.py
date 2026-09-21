from datetime import datetime, timedelta, timezone

from app.scoring import PublicStats, derive_stats, passes_filter


def test_derive_stats_and_filter():
    now = datetime.now(timezone.utc)
    stats = PublicStats(
        views=120_000,
        likes=6_000,
        comments=600,
        published_at=now - timedelta(hours=24),
        duration_seconds=45,
    )
    derived = derive_stats(stats, now=now)

    assert derived.views_per_hour == 5000
    assert derived.like_rate == 0.05
    assert derived.comment_rate == 0.005
    assert 0 <= derived.popularity_score <= 1
    assert passes_filter(
        stats,
        derived,
        max_age_hours=168,
        min_views=10_000,
        min_views_per_hour=500,
        min_like_rate=0.02,
        max_duration_seconds=180,
    )


def test_old_video_is_rejected():
    now = datetime.now(timezone.utc)
    stats = PublicStats(
        views=2_000_000,
        likes=50_000,
        comments=2_000,
        published_at=now - timedelta(days=30),
        duration_seconds=30,
    )
    derived = derive_stats(stats, now=now)

    assert not passes_filter(
        stats,
        derived,
        max_age_hours=168,
        min_views=10_000,
        min_views_per_hour=0,
        min_like_rate=0,
        max_duration_seconds=180,
    )
