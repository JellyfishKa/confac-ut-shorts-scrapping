from datetime import datetime, timedelta, timezone
import sqlite3

from app.config import settings
from app.snapshots import SnapshotStore


def test_growth_between_snapshots(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    start = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    first = store.observe("abc", views=100, likes=10, comments=1, captured_at=start)
    assert first.growth_views_per_hour is None

    second = store.observe(
        "abc",
        views=220,
        likes=22,
        comments=2,
        captured_at=start + timedelta(hours=2),
    )
    assert second.previous_views == 100
    assert second.growth_views_per_hour == 60
    assert second.growth_likes_per_hour == 6
    assert second.snapshot_age_minutes == 120


def test_view_only_growth_does_not_create_fake_likes_or_comments(tmp_path):
    db_path = tmp_path / "snapshots.sqlite3"
    store = SnapshotStore(db_path)
    start = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    first = store.observe_views("rutube:abc", views=100, captured_at=start)
    second = store.observe_views("rutube:abc", views=220, captured_at=start + timedelta(hours=2))

    assert first.previous_views is None
    assert second.previous_views == 100
    assert second.growth_views_per_hour == 60
    assert second.growth_likes_per_hour is None
    assert second.snapshot_age_minutes == 120
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM video_snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT views FROM view_snapshots ORDER BY id").fetchall() == [(100,), (220,)]


def test_growth_ignores_snapshots_younger_than_min_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "growth_min_interval_minutes", 30)
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    start = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    store.observe("abc", views=100, likes=10, comments=1, captured_at=start)

    early = store.observe("abc", views=150, likes=15, comments=1, captured_at=start + timedelta(minutes=5))
    assert early.previous_views is None
    assert early.growth_views_per_hour is None

    # 12:32 is compared with 12:00, not with the 12:05 snapshot taken 27 minutes earlier.
    later = store.observe("abc", views=420, likes=42, comments=2, captured_at=start + timedelta(minutes=32))
    assert later.previous_views == 100
    assert later.growth_views_per_hour == 600
    assert later.growth_likes_per_hour == 60
    assert later.snapshot_age_minutes == 32


def test_view_only_growth_ignores_snapshots_younger_than_min_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "growth_min_interval_minutes", 30)
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    start = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    store.observe_views("rutube:abc", views=100, captured_at=start)

    early = store.observe_views("rutube:abc", views=150, captured_at=start + timedelta(minutes=5))
    assert early.previous_views is None and early.growth_views_per_hour is None

    later = store.observe_views("rutube:abc", views=420, captured_at=start + timedelta(minutes=32))
    assert later.previous_views == 100
    assert later.growth_views_per_hour == 600


def test_growth_window_compares_snapshots_across_time_zones(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "growth_min_interval_minutes", 30)
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    moscow = timezone(timedelta(hours=3))
    store.observe("abc", views=100, likes=10, comments=1, captured_at=datetime(2026, 9, 21, 15, 0, tzinfo=moscow))

    later = store.observe("abc", views=220, likes=22, comments=2,
                          captured_at=datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc))
    assert later.previous_views == 100
    assert later.growth_views_per_hour == 60
