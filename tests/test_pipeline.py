import sqlite3
from datetime import datetime, timedelta, timezone

from app.pipeline import CandidateBuilder, Filters, RawVideo

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
FILTERS = Filters(max_age_hours=168, min_views=1000, min_views_per_hour=0,
                  min_like_rate=0, max_duration_seconds=180)


def raw(platform, video_id, *, views, duration=30, likes=None, comments=None):
    return RawVideo(platform=platform, video_id=video_id, url=f"https://example.com/{video_id}",
                    title=video_id, published_at=NOW - timedelta(hours=10), duration_seconds=duration,
                    views=views, likes=likes, comments=comments)


def test_baseline_is_recorded_before_popularity_filters_but_not_outside_duration(tmp_path):
    selected = CandidateBuilder(now=NOW).select(
        [raw("rutube", "small", views=50), raw("rutube", "long", views=9999, duration=600)], FILTERS,
    )
    assert selected == []
    with sqlite3.connect(tmp_path / "snapshots.sqlite3") as conn:
        assert conn.execute("SELECT video_id, views FROM view_snapshots").fetchall() == [("rutube:small", 50)]


def test_youtube_snapshot_keys_stay_unprefixed(tmp_path):
    CandidateBuilder(now=NOW).select([
        raw("youtube", "abc", views=5000, likes=100, comments=10),
        raw("vk", "-1_2", views=5000, likes=100, comments=10),
    ], FILTERS)
    with sqlite3.connect(tmp_path / "snapshots.sqlite3") as conn:
        assert conn.execute("SELECT video_id FROM video_snapshots ORDER BY id").fetchall() == [("abc",), ("vk:-1_2",)]


def test_candidate_without_filters_keeps_unknown_metrics_null():
    video = CandidateBuilder(now=NOW).candidate(RawVideo(
        platform="vk", video_id="1", url="https://vk.com/clip1_1", title="t",
        published_at=None, duration_seconds=None, views=500,
    ))
    assert video.views == 500 and video.age_hours is None
    assert video.views_per_hour is None and video.like_rate is None
    assert video.popularity_score is None


def test_breakout_ratio_needs_known_subscribers():
    builder = CandidateBuilder(now=NOW)
    known = raw("youtube", "a", views=5000)
    known.subscribers = 1000
    assert builder.candidate(known).breakout_ratio == 5
    assert builder.candidate(raw("youtube", "b", views=5000)).breakout_ratio is None
