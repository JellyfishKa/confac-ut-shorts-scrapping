from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import settings
from app.hype import acceleration, pick_hype
from app.main import app
from app.models import SnapshotPoint, TrackedVideo, VideoCandidate
from app.pipeline import Counts
from app.tracker import TrackedStore, refresh_due

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def points(*pairs):
    return [SnapshotPoint(captured_at=NOW + timedelta(minutes=minutes), views=views) for minutes, views in pairs]


def tracked(video_id, *, speed, views=50_000, growth=None, like_rate=None, breakout=None, age=10.0, history=()):
    video = VideoCandidate(
        platform="youtube", video_id=video_id, url=f"https://www.youtube.com/shorts/{video_id}",
        title=video_id, channel_title="author", published_at=NOW - timedelta(hours=age), duration_seconds=30,
        views=views, likes=None, comments=None, age_hours=age, views_per_hour=speed,
        like_rate=like_rate, comment_rate=None, breakout_ratio=breakout,
        growth_views_per_hour=growth, popularity_score=None,
    )
    return TrackedVideo(video=video, pinned=False, tracked_until=NOW + timedelta(hours=48),
                        last_checked_at=NOW, last_error=None, history=list(history))


def test_acceleration_compares_latest_interval_with_previous(monkeypatch):
    monkeypatch.setattr(settings, "growth_min_interval_minutes", 30)
    # 600 views/h, then 1200 views/h.
    assert acceleration(points((0, 0), (30, 300), (60, 900))) == 2
    # A snapshot closer than the growth window is skipped, not used as a baseline.
    assert acceleration(points((0, 0), (30, 300), (50, 500), (60, 900))) == 2


def test_acceleration_needs_two_full_intervals_and_previous_growth(monkeypatch):
    monkeypatch.setattr(settings, "growth_min_interval_minutes", 30)
    assert acceleration(points((0, 0), (30, 300))) is None
    assert acceleration(points((0, 0), (20, 300), (40, 900))) is None
    assert acceleration(points((0, 500), (30, 500), (60, 900))) is None
    monkeypatch.setattr(settings, "growth_min_interval_minutes", 0)
    assert acceleration(points((0, 0), (0, 300), (0, 900))) is None


def test_hype_drops_junk_and_explains_picks(monkeypatch):
    monkeypatch.setattr(settings, "growth_min_interval_minutes", 30)
    monkeypatch.setattr(settings, "hype_min_views", 1000)
    rocket = tracked("rocket", speed=2_000, growth=9_000, like_rate=0.08, breakout=20, age=6,
                     history=points((0, 40_000), (30, 41_000), (60, 45_500)))
    steady = tracked("steady", speed=8_000, growth=8_000, like_rate=0.02, age=40,
                     history=points((0, 40_000), (30, 44_000), (60, 48_000)))
    tiny = tracked("tiny", speed=50, views=300)
    falling = tracked("falling", speed=5_000, growth=-20)

    result = pick_hype([steady, tiny, falling, rocket], limit=5)
    assert result.considered == 2
    assert [pick.video.video_id for pick in result.picks] == ["rocket", "steady"]
    top = result.picks[0]
    assert top.acceleration == 4.5
    assert 0 < top.hype_score <= 1
    reasons = " ".join(top.reasons)
    assert "ускоряется ×4.5" in reasons and "подписчиков" in reasons


def test_reasons_read_naturally(monkeypatch):
    monkeypatch.setattr(settings, "growth_min_interval_minutes", 30)
    noisy = tracked("noisy", speed=500, breakout=2.2, history=points((0, 0), (30, 10), (60, 1000)))
    reasons = " ".join(pick_hype([noisy], limit=1).picks[0].reasons)
    assert "в 2 раза больше" in reasons
    assert "ускоряется ×10+" in reasons


def test_hype_without_tracker_history_falls_back_to_average_speed():
    result = pick_hype([tracked("slow", speed=100), tracked("fast", speed=3_000)], limit=1)
    assert [pick.video.video_id for pick in result.picks] == ["fast"]
    assert result.picks[0].acceleration is None


def test_hype_api_uses_tracked_videos(monkeypatch):
    monkeypatch.setattr(settings, "youtube_api_key", "key")
    monkeypatch.setattr(settings, "hype_min_views", 1000)
    current = {}

    async def fetch(self, video_ids):
        return {video_id: current[video_id] for video_id in video_ids}

    monkeypatch.setattr("app.tracker.YouTubeProvider.fetch_counts", fetch)
    now = datetime.now(timezone.utc)
    store = TrackedStore()
    store.track([tracked("a", speed=500).video, tracked("junk", speed=1, views=10).video],
                now=now - timedelta(minutes=70))
    current.update(a=Counts(60_000), junk=Counts(11))
    import asyncio
    asyncio.run(refresh_due(now - timedelta(minutes=35)))

    response = TestClient(app).get("/api/hype", params={"limit": 3})
    assert response.status_code == 200
    data = response.json()
    assert data["considered"] == 1
    assert [pick["video"]["video_id"] for pick in data["picks"]] == ["a"]
    assert TestClient(app).get("/api/hype", params={"limit": 0}).status_code == 422
