from datetime import datetime, timedelta, timezone
from math import log1p

from app.config import settings
from app.scoring import PublicStats, derive_stats, passes_filter

FILTERS = dict(max_age_hours=168, min_views=1, min_views_per_hour=0, min_like_rate=0, max_duration_seconds=180)


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


def test_score_redistributes_weight_of_missing_engagement(monkeypatch):
    monkeypatch.setattr(settings, "weight_views_per_hour", 0.55)
    monkeypatch.setattr(settings, "weight_like_rate", 0.30)
    monkeypatch.setattr(settings, "weight_comment_rate", 0.15)
    now = datetime.now(timezone.utc)
    published = now - timedelta(hours=10)
    speed = log1p(500) / log1p(1_000_000)

    views_only = derive_stats(PublicStats(5_000, None, None, published, 30), now=now)
    assert views_only.like_rate is None and views_only.comment_rate is None
    assert views_only.popularity_score == round(speed, 6)

    no_comments = derive_stats(PublicStats(5_000, 250, None, published, 30), now=now)
    assert no_comments.popularity_score == round((0.55 * speed + 0.30 * 0.5) / 0.85, 6)


def test_score_needs_view_speed():
    now = datetime.now(timezone.utc)
    no_views = derive_stats(PublicStats(None, 10, 1, now - timedelta(hours=10), 30), now=now)
    no_date = derive_stats(PublicStats(5_000, 10, 1, None, 30), now=now)
    assert no_views.views_per_hour is None and no_views.popularity_score is None
    assert no_date.age_hours is None and no_date.popularity_score is None


def test_unknown_metric_cannot_pass_positive_threshold():
    now = datetime.now(timezone.utc)
    stats = PublicStats(None, None, None, now - timedelta(hours=10), None)
    derived = derive_stats(stats, now=now)
    assert not passes_filter(stats, derived, **FILTERS)
    assert passes_filter(stats, derived, **{**FILTERS, "min_views": 0})
    assert not passes_filter(stats, derived, **{**FILTERS, "min_views": 0, "min_like_rate": 0.01})

    undated = PublicStats(5_000, None, None, None, 30)
    assert not passes_filter(undated, derive_stats(undated, now=now), **FILTERS)
