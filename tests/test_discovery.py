import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.discovery import DiscoveryStore, load_queries, run_discovery
from app.main import app
from app.models import AiVerdict, PlatformSearchStatus, SearchResponse, VideoCandidate
from app.pipeline import RawVideo
from app.tracker import TrackedStore
from app.youtube import YouTubeProvider

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def queries(tmp_path, monkeypatch):
    path = tmp_path / "queries.txt"
    monkeypatch.setattr(settings, "discovery_queries_file", path)
    monkeypatch.setattr(settings, "youtube_api_key", "")

    def write(*lines):
        path.write_text("\n".join(lines), encoding="utf-8")

    write()
    return write


class FakeSearch:
    def __init__(self, statuses=None):
        self.requests = []
        self.statuses = statuses or {}

    async def __call__(self, request):
        self.requests.append(request)
        return SearchResponse(query=request.query, found=0, returned=0, videos=[], platform_statuses=[
            self.statuses.get(platform, PlatformSearchStatus(platform=platform, status="ok"))
            for platform in request.platforms
        ])


def video(video_id, channel_id, *, speed, platform="youtube"):
    return VideoCandidate(
        platform=platform, video_id=video_id, url=f"https://example.com/{video_id}", title=video_id,
        channel_id=channel_id, channel_title=f"Channel {channel_id}", published_at=NOW - timedelta(hours=5),
        duration_seconds=30, views=50_000, likes=None, comments=None, age_hours=5,
        views_per_hour=speed, like_rate=None, comment_rate=None, popularity_score=None,
    )


def raw(video_id, *, age_hours, views):
    return RawVideo(platform="youtube", video_id=video_id, url=f"https://www.youtube.com/shorts/{video_id}",
                    title=video_id, published_at=NOW - timedelta(hours=age_hours), duration_seconds=25,
                    views=views, likes=10, comments=1, channel_id="UCfarm", channel_title="Farm")


def test_queries_file_skips_comments_blanks_and_repeats(queries):
    queries("# маркеры нейрослопа", "", "  sora ai  ", "veo 3", "sora ai")
    assert load_queries() == ["sora ai", "veo 3"]


def test_run_searches_every_marker_within_the_daily_youtube_budget(queries, monkeypatch):
    queries("a", "b", "c")
    monkeypatch.setattr(settings, "discovery_platforms", "youtube,rutube")
    monkeypatch.setattr(settings, "discovery_interval_hours", 24)
    monkeypatch.setattr(settings, "discovery_youtube_units_per_day", 250)
    search = FakeSearch()

    run = asyncio.run(run_discovery(search, now=NOW))
    assert [(request.query, request.platforms) for request in search.requests] == [
        ("a", ["youtube", "rutube"]), ("b", ["youtube", "rutube"]), ("c", ["rutube"]),
    ]
    first = search.requests[0]
    assert first.max_age_hours == settings.discovery_max_age_hours
    assert first.min_views == settings.hype_min_views and first.limit == 50
    assert run.queries == 3 and run.youtube_units == 204

    # 46 units are left today, so the next run skips YouTube; the budget starts over the next day.
    later = FakeSearch()
    asyncio.run(run_discovery(later, now=NOW + timedelta(hours=6)))
    assert all("youtube" not in request.platforms for request in later.requests)
    tomorrow = FakeSearch()
    asyncio.run(run_discovery(tomorrow, now=NOW + timedelta(days=1)))
    assert any("youtube" in request.platforms for request in tomorrow.requests)


def test_youtube_markers_rotate_so_the_budget_lasts_all_day(queries, monkeypatch):
    queries("a", "b", "c", "d", "e")
    monkeypatch.setattr(settings, "discovery_platforms", "youtube,rutube")
    monkeypatch.setattr(settings, "discovery_interval_hours", 6)
    # 4 runs a day share 816 units: two YouTube markers per run.
    monkeypatch.setattr(settings, "discovery_youtube_units_per_day", 816)

    def on_youtube(run_at):
        search = FakeSearch()
        asyncio.run(run_discovery(search, now=run_at))
        assert len(search.requests) == 5
        return [request.query for request in search.requests if "youtube" in request.platforms]

    assert on_youtube(NOW) == ["a", "b"]
    assert on_youtube(NOW + timedelta(hours=6)) == ["c", "d"]
    assert on_youtube(NOW + timedelta(hours=12)) == ["a", "e"]


def test_unavailable_youtube_spends_no_budget_and_errors_are_reported_once(queries, monkeypatch):
    queries("a", "b")
    monkeypatch.setattr(settings, "discovery_platforms", "youtube,rutube")
    search = FakeSearch({
        "youtube": PlatformSearchStatus(platform="youtube", status="unavailable", detail="YOUTUBE_API_KEY is not configured."),
        "rutube": PlatformSearchStatus(platform="rutube", status="error", detail="Площадка ответила HTTP 503."),
    })
    run = asyncio.run(run_discovery(search, now=NOW))
    assert run.youtube_units == 0
    assert run.errors == ["youtube: YOUTUBE_API_KEY is not configured.", "rutube: Площадка ответила HTTP 503."]
    assert DiscoveryStore().last_run().errors == run.errors


def test_hype_channels_are_watched_up_to_the_limit(queries, monkeypatch):
    monkeypatch.setattr(settings, "discovery_max_channels", 2)
    monkeypatch.setattr(settings, "hype_min_views", 1000)
    TrackedStore().track([
        video("v1", "UCa", speed=900), video("v2", "UCb", speed=500), video("v3", "UCc", speed=100),
        video("r1", "77", speed=5000, platform="rutube"),
    ], now=NOW)

    asyncio.run(run_discovery(FakeSearch(), now=NOW))
    channels = DiscoveryStore().channels()
    # Rutube has no cheap uploads feed; the two best YouTube channels stay.
    assert {channel.channel_id for channel in channels} == {"UCa", "UCb"}
    assert {channel.title for channel in channels} == {"Channel UCa", "Channel UCb"}


def test_watched_channels_are_polled_for_new_uploads(queries, monkeypatch):
    monkeypatch.setattr(settings, "youtube_api_key", "key")
    DiscoveryStore().watch_channels([("UCfarm", "Farm")], now=NOW - timedelta(days=1))
    calls = {}

    async def recent(self, channel_ids, published_after):
        calls["channels"], calls["after"] = channel_ids, published_after
        return ["new1", "old"]

    async def fetch(self, video_ids):
        calls["ids"] = video_ids
        return [raw("new1", age_hours=5, views=5000), raw("old", age_hours=200, views=9000)]

    monkeypatch.setattr(YouTubeProvider, "recent_uploads", recent)
    monkeypatch.setattr(YouTubeProvider, "fetch_videos", fetch)
    run = asyncio.run(run_discovery(FakeSearch(), now=NOW))

    assert calls == {"channels": ["UCfarm"], "after": NOW - timedelta(hours=settings.discovery_max_age_hours),
                     "ids": ["new1", "old"]}
    assert run.channels_checked == 1 and run.channel_videos == 1
    assert run.youtube_units == 1 + 2
    assert [tracked.video.video_id for tracked in TrackedStore().active(NOW)] == ["new1"]
    assert DiscoveryStore().channels()[0].last_checked_at == NOW


def test_channels_are_not_polled_without_budget(queries, monkeypatch):
    monkeypatch.setattr(settings, "youtube_api_key", "key")
    monkeypatch.setattr(settings, "discovery_youtube_units_per_day", 2)
    DiscoveryStore().watch_channels([("UCfarm", "Farm")], now=NOW)

    async def recent(self, channel_ids, published_after):
        raise AssertionError("must not poll")

    monkeypatch.setattr(YouTubeProvider, "recent_uploads", recent)
    assert asyncio.run(run_discovery(FakeSearch(), now=NOW)).channels_checked == 0


def test_recent_uploads_reads_each_channel_uploads_playlist(monkeypatch):
    requested = []
    after = NOW - timedelta(hours=72)

    def handler(request):
        playlist = request.url.params["playlistId"]
        requested.append(playlist)
        assert request.url.path.endswith("/playlistItems") and request.url.params["part"] == "contentDetails"
        if playlist == "UUgone":
            return httpx.Response(404, json={"error": {"errors": [{"reason": "playlistNotFound"}]}})
        return httpx.Response(200, json={"items": [
            {"contentDetails": {"videoId": "n1", "videoPublishedAt": "2026-10-30T10:00:00Z"}},
            {"contentDetails": {"videoId": "o1", "videoPublishedAt": "2026-10-01T10:00:00Z"}},
        ]})

    original = httpx.AsyncClient
    monkeypatch.setattr("app.youtube.httpx.AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    ids = asyncio.run(YouTubeProvider(api_key="key").recent_uploads(["UCabc", "handle", "UCgone"], after))
    assert ids == ["n1"]
    assert requested == ["UUabc", "UUgone"]


def test_discovery_api_reports_status_and_starts_a_run(queries, monkeypatch):
    queries("sora ai")
    started = []

    async def fake_run(search):
        started.append(search)

    monkeypatch.setattr("app.main.run_discovery_once", fake_run)
    client = TestClient(app)
    status = client.get("/api/discovery").json()
    assert status["queries"] == ["sora ai"]
    assert status["last_run"] is None and status["running"] is False and status["channels"] == []
    assert status["youtube_units_per_day"] == settings.discovery_youtube_units_per_day

    assert client.post("/api/discovery/run").status_code == 202
    assert len(started) == 1
    monkeypatch.setattr("app.main.discovery_running", lambda: True)
    assert client.post("/api/discovery/run").status_code == 409


def test_channel_farms_come_from_the_youtube_top_even_when_rutube_leads(queries, monkeypatch):
    monkeypatch.setattr(settings, "hype_min_views", 1000)
    rutube = [video(f"r{i}", str(i), speed=90_000, platform="rutube") for i in range(12)]
    TrackedStore().track(rutube + [video("v1", "UCa", speed=900)], now=NOW)

    asyncio.run(run_discovery(FakeSearch(), now=NOW))
    assert [channel.channel_id for channel in DiscoveryStore().channels()] == ["UCa"]


def test_run_prechecks_youtube_leaders_for_ai_when_gemini_is_configured(queries, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "secret")
    TrackedStore().track([video("v1", "UCa", speed=900), video("r1", "7", speed=9_000, platform="rutube")], now=NOW)
    checked = []

    async def check(videos):
        checked.extend(video.video_id for video in videos)
        return {("youtube", "v1"): AiVerdict(ai_generated=None, reason="Gemini недоступен из вашего региона.",
                                             checked_at=NOW)}

    monkeypatch.setattr("app.discovery.check_videos", check)
    run = asyncio.run(run_discovery(FakeSearch(), now=NOW))
    assert checked == ["v1"]
    assert "AI-проверка: Gemini недоступен из вашего региона." in run.errors
