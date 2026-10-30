import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, search_videos
from app.models import SearchRequest, VideoCandidate
from app.pipeline import Counts
from app.rutube import fetch_counts as fetch_rutube_counts
from app.tracker import TrackedStore, refresh_due

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def video(platform="youtube", video_id="a", views=1000, likes=50, comments=5, published=NOW):
    return VideoCandidate(
        platform=platform, video_id=video_id, url=f"https://example.com/{video_id}", title=video_id,
        channel_title="author", published_at=published - timedelta(hours=10), duration_seconds=30,
        views=views, likes=likes, comments=comments, age_hours=10,
        views_per_hour=views / 10 if views is not None else None,
        like_rate=None, comment_rate=None, popularity_score=None,
    )


@pytest.fixture
def youtube_counts(monkeypatch):
    calls: list[list[str]] = []
    current: dict[str, Counts] = {}

    async def fetch(self, video_ids):
        calls.append(list(video_ids))
        return {video_id: current[video_id] for video_id in video_ids if video_id in current}

    monkeypatch.setattr(settings, "youtube_api_key", "key")
    monkeypatch.setattr("app.tracker.YouTubeProvider.fetch_counts", fetch)
    return calls, current


def test_search_results_with_views_are_tracked(monkeypatch):
    monkeypatch.setattr(settings, "tracker_hours", 48)

    class YouTube:
        async def search(self, request):
            return 2, [video("youtube", "a"), video("youtube", "hidden", views=None)]

    monkeypatch.setattr("app.main.YouTubeProvider", YouTube)
    before = datetime.now(timezone.utc)
    asyncio.run(search_videos(SearchRequest(query="cats")))

    [tracked] = TrackedStore().active()
    assert tracked.video.video_id == "a" and not tracked.pinned
    assert before + timedelta(hours=48) <= tracked.tracked_until <= datetime.now(timezone.utc) + timedelta(hours=48)
    assert tracked.last_checked_at is not None


def test_refresh_follows_interval_and_measures_growth(youtube_counts, monkeypatch):
    monkeypatch.setattr(settings, "tracker_interval_minutes", 30)
    monkeypatch.setattr(settings, "growth_min_interval_minutes", 30)
    calls, current = youtube_counts
    store = TrackedStore()
    store.track([video("youtube", "a")], now=NOW)

    current["a"] = Counts(1000, 50, 5)
    assert asyncio.run(refresh_due(NOW + timedelta(minutes=10))) == 0
    assert asyncio.run(refresh_due(NOW + timedelta(minutes=31))) == 1
    current["a"] = Counts(1600, 80, 6)
    asyncio.run(refresh_due(NOW + timedelta(minutes=62)))

    assert calls == [["a"], ["a"]]
    [tracked] = store.active(NOW + timedelta(minutes=62))
    assert tracked.video.views == 1600 and tracked.video.likes == 80
    assert tracked.video.growth_views_per_hour == round(600 / (31 / 60), 2)
    assert [point.views for point in tracked.history] == [1000, 1600]
    assert tracked.last_error is None


def test_expired_videos_stop_unless_pinned(youtube_counts, monkeypatch):
    monkeypatch.setattr(settings, "tracker_hours", 1)
    calls, current = youtube_counts
    store = TrackedStore()
    store.track([video("youtube", "a"), video("youtube", "b")], now=NOW)
    assert store.set_pinned("youtube", "b", True, now=NOW)
    current.update(a=Counts(2000), b=Counts(2000))

    later = NOW + timedelta(hours=2)
    asyncio.run(refresh_due(later))
    assert calls == [["b"]]
    assert [tracked.video.video_id for tracked in store.active(later)] == ["b"]


def test_refresh_failure_and_missing_video_are_recorded(monkeypatch):
    async def fail(self, video_ids):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(settings, "youtube_api_key", "key")
    monkeypatch.setattr("app.tracker.YouTubeProvider.fetch_counts", fail)
    monkeypatch.setattr("app.tracker.fetch_rutube_counts", lambda video_ids: asyncio.sleep(0, result={}))
    store = TrackedStore()
    store.track([video("youtube", "a"), video("rutube", "gone", likes=None, comments=None)], now=NOW)

    later = NOW + timedelta(minutes=31)
    asyncio.run(refresh_due(later))
    errors = {tracked.video.video_id: tracked.last_error for tracked in store.active(later)}
    assert "ConnectError" in errors["a"]
    assert "недоступен" in errors["gone"]
    # A failed check still waits for the next interval instead of retrying every minute.
    assert asyncio.run(refresh_due(later + timedelta(minutes=1))) == 0


def test_platforms_without_id_api_refresh_through_yt_dlp(monkeypatch):
    seen = []

    def extract(url):
        seen.append(url)
        return {"view_count": 900, "like_count": 30, "comment_count": 3}

    monkeypatch.setattr("app.tracker._extract", extract)
    store = TrackedStore()
    clip = video("vk", "-1_2", views=500)
    clip.url = "https://vk.com/clip-1_2"
    store.track([clip], now=NOW)

    asyncio.run(refresh_due(NOW + timedelta(minutes=31)))
    [tracked] = store.active(NOW + timedelta(minutes=31))
    assert seen == ["https://vk.com/clip-1_2"]
    assert tracked.video.views == 900 and tracked.video.comments == 3


def test_tracked_api_lists_history_and_manages_videos(youtube_counts):
    _, current = youtube_counts
    now = datetime.now(timezone.utc)
    TrackedStore().track([video("youtube", "a", published=now)], now=now - timedelta(minutes=40))
    current["a"] = Counts(1500, 60, 6)
    asyncio.run(refresh_due(now - timedelta(minutes=5)))
    client = TestClient(app)

    data = client.get("/api/tracked").json()
    assert data["interval_minutes"] == settings.tracker_interval_minutes
    [tracked] = data["videos"]
    assert tracked["video"]["views"] == 1500
    assert [point["views"] for point in tracked["history"]] == [1500]

    assert client.post("/api/tracked/pin", json={"platform": "youtube", "video_id": "a", "pinned": True}).status_code == 200
    assert client.get("/api/tracked").json()["videos"][0]["pinned"] is True
    assert client.post("/api/tracked/pin", json={"platform": "youtube", "video_id": "zzz", "pinned": True}).status_code == 404

    assert client.delete("/api/tracked/youtube/a").status_code == 200
    assert client.get("/api/tracked").json()["videos"] == []
    assert client.delete("/api/tracked/youtube/a").status_code == 404


def test_imported_video_is_tracked(monkeypatch):
    timestamp = int((datetime.now(timezone.utc) - timedelta(hours=5)).timestamp())
    monkeypatch.setattr("app.import_video._extract", lambda url: {
        "id": "-12_34", "title": "Клип", "timestamp": timestamp, "view_count": 500,
    })
    response = TestClient(app).post("/api/import", json={"url": "https://vk.com/clip-12_34"})
    assert response.status_code == 200
    assert [tracked.video.video_id for tracked in TrackedStore().active()] == ["-12_34"]


def test_rutube_counts_skip_deleted_missing_and_invalid_ids(monkeypatch):
    requested = []

    def handler(request):
        requested.append(request.url.path)
        if request.url.path == "/api/video/aaa/":
            return httpx.Response(200, json={"hits": 700})
        if request.url.path == "/api/video/bbb/":
            return httpx.Response(200, json={"hits": 5, "is_deleted": True})
        return httpx.Response(404)

    original = httpx.AsyncClient
    monkeypatch.setattr("app.rutube.httpx.AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    counts = asyncio.run(fetch_rutube_counts(["aaa", "bbb", "ccc", "../x"]))
    assert counts == {"aaa": Counts(700)}
    assert requested == ["/api/video/aaa/", "/api/video/bbb/", "/api/video/ccc/"]


def test_page_reads_are_bounded_per_pass_and_do_not_block_id_apis(youtube_counts, monkeypatch):
    calls, current = youtube_counts
    monkeypatch.setattr("app.tracker.PAGE_READS_PER_PASS", 3)
    monkeypatch.setattr("app.tracker._extract", lambda url: {"view_count": 900})
    store = TrackedStore()
    store.track([video("vk", f"-1_{i}", views=500) for i in range(5)] + [video("youtube", "a")], now=NOW)
    current["a"] = Counts(1200, 60, 6)

    first = NOW + timedelta(minutes=31)
    assert asyncio.run(refresh_due(first)) == 4
    assert calls == [["a"]]
    # The two clips left over stay due and are read on the next pass.
    assert asyncio.run(refresh_due(first + timedelta(minutes=1))) == 2


def test_untrack_rejects_unknown_platform():
    assert TestClient(app).delete("/api/tracked/myspace/abc").status_code == 422
