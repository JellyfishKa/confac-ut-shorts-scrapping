import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.main import search_videos
from app.models import SearchRequest, VideoCandidate
from app.rutube import RutubeProvider
from app.search_errors import ProviderUnavailableError


def candidate(platform: str, video_id: str, speed: float = 10) -> VideoCandidate:
    return VideoCandidate(
        platform=platform,
        video_id=video_id,
        url=f"https://example.com/{video_id}",
        title=video_id,
        channel_title="author",
        published_at=datetime.now(timezone.utc),
        duration_seconds=30,
        views=100,
        likes=None,
        comments=None,
        age_hours=10,
        views_per_hour=speed,
        like_rate=None,
        comment_rate=None,
        popularity_score=None,
    )


def test_search_defaults_to_youtube_and_deduplicates_platform_selection(monkeypatch):
    calls = []

    class YouTube:
        async def search(self, request):
            calls.append(request.query)
            return 2, [candidate("youtube", "a"), candidate("youtube", "a")]

    monkeypatch.setattr("app.main.YouTubeProvider", YouTube)
    request = SearchRequest(query="cats", platforms=["youtube", "youtube"])
    result = asyncio.run(search_videos(request))
    assert calls == ["cats"]
    assert result.found == 2
    assert result.returned == 1
    assert result.platform_statuses[0].status == "ok"
    assert result.platform_statuses[0].returned == 1


def test_default_youtube_search_without_key_reports_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "youtube_api_key", "")
    result = asyncio.run(search_videos(SearchRequest(query="cats")))
    assert result.platform_statuses[0].status == "unavailable"
    assert result.returned == 0


def test_search_collects_selected_sources_with_partial_failure(monkeypatch):
    class YouTube:
        def __init__(self):
            raise ProviderUnavailableError("YOUTUBE_API_KEY is not configured")

    class Rutube:
        async def search(self, request):
            return 3, [candidate("rutube", "r1", 50), candidate("rutube", "r2", 20)]

    class Instagram:
        def __init__(self):
            raise ProviderUnavailableError("INSTAGRAM_ACCESS_TOKEN is not configured")

    monkeypatch.setattr("app.main.YouTubeProvider", YouTube)
    monkeypatch.setattr("app.main.RutubeProvider", Rutube)
    monkeypatch.setattr("app.main.InstagramHashtagProvider", Instagram)
    result = asyncio.run(search_videos(SearchRequest(query="cats", platforms=["youtube", "rutube", "instagram"])))
    assert [video.video_id for video in result.videos] == ["r1", "r2"]
    assert result.found == 3
    assert [(status.platform, status.status, status.returned) for status in result.platform_statuses] == [
        ("youtube", "unavailable", 0),
        ("rutube", "ok", 2),
        ("instagram", "unavailable", 0),
    ]


def test_search_network_failure_does_not_hide_other_source(monkeypatch):
    class YouTube:
        async def search(self, request):
            raise RuntimeError("YouTube API rejected the request")

    class Rutube:
        async def search(self, request):
            return 1, [candidate("rutube", "r1")]

    monkeypatch.setattr("app.main.YouTubeProvider", YouTube)
    monkeypatch.setattr("app.main.RutubeProvider", Rutube)
    result = asyncio.run(search_videos(SearchRequest(query="cats", platforms=["youtube", "rutube"])))
    assert result.returned == 1
    assert result.platform_statuses[0].status == "error"
    assert result.platform_statuses[1].status == "ok"


def test_vk_and_tiktok_missing_credentials_are_individually_reported(monkeypatch):
    class MissingCredentials:
        def __init__(self):
            raise ProviderUnavailableError("Token is missing")

    monkeypatch.setattr("app.main.VkProvider", MissingCredentials)
    monkeypatch.setattr("app.main.TikTokResearchProvider", MissingCredentials)
    result = asyncio.run(search_videos(SearchRequest(query="cats", platforms=["vk", "tiktok"])))
    assert result.videos == []
    assert [(status.platform, status.status) for status in result.platform_statuses] == [
        ("vk", "unavailable"), ("tiktok", "unavailable")
    ]


def test_rutube_search_filters_short_videos_and_keeps_missing_metrics_null(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "growth_min_interval_minutes", 0)
    published = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    short = {
        "id": "a" * 32, "title": "Short clip", "duration": 30,
        "hits": 3000, "publication_ts": published,
        "video_url": "https://rutube.ru/shorts/" + "a" * 32 + "/",
        "author": {"id": 42, "name": "Creator"},
    }
    long = {**short, "id": "b" * 32, "duration": 600}
    no_views = {**short, "id": "c" * 32, "hits": None}
    below_threshold = {**short, "id": "d" * 32, "hits": 100}
    responses = [
        {"results": [short, long, no_views, below_threshold], "has_next": True, "next": "/api/search/video/?query=cat&page=2"},
        {"results": [short], "has_next": False, "next": None},
    ]

    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    class Client:
        def __init__(self, **kwargs):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None):
            body = responses[self.calls]
            self.calls += 1
            return Response(body)

    monkeypatch.setattr("app.rutube.httpx.AsyncClient", Client)
    request = SearchRequest(query="cat", platforms=["rutube"], max_age_hours=24, min_views=1000,
                            max_duration_seconds=60, limit=10)
    found, videos = asyncio.run(RutubeProvider().search(request))
    assert found == 5
    assert len(videos) == 1
    video = videos[0]
    assert video.platform == "rutube" and video.url.startswith("https://rutube.ru/shorts/")
    assert video.views == 3000 and video.views_per_hour is not None
    assert video.likes is None and video.comments is None
    assert video.like_rate is None and video.comment_rate is None
    assert video.popularity_score is not None
    assert video.previous_views is None and video.growth_views_per_hour is None
    with sqlite3.connect(tmp_path / "snapshots.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM video_snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT video_id, views FROM view_snapshots ORDER BY id").fetchall() == [
            (f"rutube:{'a' * 32}", 3000), (f"rutube:{'d' * 32}", 100),
        ]

    request.min_views = 0
    _, repeated = asyncio.run(RutubeProvider().search(request))
    assert repeated[0].previous_views == 3000
    assert repeated[0].growth_views_per_hour == 0
    assert repeated[0].growth_likes_per_hour is None
    assert repeated[1].previous_views == 100

    request.min_like_rate = 0.01
    _, videos = asyncio.run(RutubeProvider().search(request))
    assert videos == []


def test_merged_ranking_uses_measured_growth_instead_of_lifetime_speed(monkeypatch):
    stalled = candidate("youtube", "stalled", speed=100_000)
    stalled.growth_views_per_hour = -5
    accelerating = candidate("rutube", "accelerating", speed=10)
    accelerating.growth_views_per_hour = 5_000
    fresh = candidate("rutube", "fresh", speed=1_000)

    class YouTube:
        async def search(self, request):
            return 1, [stalled]

    class Rutube:
        async def search(self, request):
            return 2, [accelerating, fresh]

    monkeypatch.setattr("app.main.YouTubeProvider", YouTube)
    monkeypatch.setattr("app.main.RutubeProvider", Rutube)
    result = asyncio.run(search_videos(SearchRequest(query="cats", platforms=["youtube", "rutube"])))
    # A second snapshot alone must not outrank faster videos: the stalled clip goes last.
    assert [video.video_id for video in result.videos] == ["accelerating", "fresh", "stalled"]


@pytest.mark.parametrize("platforms", [[], ["unsupported"]])
def test_search_rejects_invalid_platform_selection(platforms):
    with pytest.raises(ValueError):
        SearchRequest(query="cat", platforms=platforms)
