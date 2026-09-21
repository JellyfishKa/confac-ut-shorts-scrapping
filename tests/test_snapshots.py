from datetime import datetime, timedelta, timezone

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
