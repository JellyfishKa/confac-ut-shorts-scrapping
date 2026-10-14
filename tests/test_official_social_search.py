import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.models import SearchRequest
from app.search_errors import ProviderUnavailableError
from app.instagram_search import InstagramHashtagProvider
from app.tiktok_search import TikTokResearchProvider, _TOKEN_CACHE


def _mock_client(monkeypatch, module_name, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(f"{module_name}.httpx.AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler)))


def test_tiktok_requires_approved_credentials():
    with pytest.raises(ProviderUnavailableError, match="research.data.basic"):
        TikTokResearchProvider(client_key="", client_secret="", token="")


def test_tiktok_client_token_and_keyword_search(monkeypatch):
    _TOKEN_CACHE.clear()
    calls = []
    now = int(datetime.now(timezone.utc).timestamp()) - 3600

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/oauth/token/"):
            assert b"client_secret=private" in request.content
            return httpx.Response(200, json={"access_token": "temporary", "expires_in": 7200})
        assert request.headers["authorization"] == "Bearer temporary"
        assert request.url.params["fields"].startswith("id,video_description")
        assert b'"field_name":"keyword"' in request.content
        return httpx.Response(200, json={"error": {"code": "ok"}, "data": {"videos": [{
            "id": 123, "username": "chef", "video_description": "Soup",
            "create_time": now, "video_duration": 20,
            "view_count": 10000, "like_count": 1000, "comment_count": 100,
        }]}})

    _mock_client(monkeypatch, "app.tiktok_search", handler)
    provider = TikTokResearchProvider(client_key="public", client_secret="private", token="")
    request = SearchRequest(query="soup", platforms=["tiktok"], min_views=0)
    found, videos = asyncio.run(provider.search(request))
    assert found == 1 and videos[0].platform == "tiktok"
    assert videos[0].url == "https://www.tiktok.com/@chef/video/123"
    assert videos[0].views_per_hour is not None
    asyncio.run(provider.search(request))
    assert len([call for call in calls if call.url.path.endswith("/oauth/token/")]) == 1


def test_tiktok_api_error_is_not_empty_success(monkeypatch):
    _mock_client(monkeypatch, "app.tiktok_search", lambda request: httpx.Response(
        200, json={"error": {"code": "scope_not_authorized", "message": "private"}}))
    provider = TikTokResearchProvider(client_key="", client_secret="", token="secret")
    with pytest.raises(RuntimeError, match="research.data.basic") as exc:
        asyncio.run(provider.search(SearchRequest(query="test", platforms=["tiktok"])))
    assert "secret" not in str(exc.value)


def test_instagram_requires_credentials_and_one_hashtag():
    with pytest.raises(ProviderUnavailableError, match="INSTAGRAM_ACCESS_TOKEN"):
        InstagramHashtagProvider(access_token="", user_id="")
    provider = InstagramHashtagProvider(access_token="secret", user_id="123")
    with pytest.raises(ProviderUnavailableError, match="один хештег"):
        asyncio.run(provider.search(SearchRequest(query="two words", platforms=["instagram"])))


def test_instagram_returns_only_reels_and_null_metrics(monkeypatch):
    seen = []
    timestamp = datetime.now(timezone.utc).isoformat()

    def handler(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer secret"
        if request.url.path.endswith("/ig_hashtag_search"):
            assert request.url.params["q"] == "рецепты"
            return httpx.Response(200, json={"data": [{"id": "999"}]})
        assert request.url.path.endswith("/999/recent_media")
        return httpx.Response(200, json={"data": [
            {"id": "1", "media_type": "VIDEO", "permalink": "https://www.instagram.com/reel/ABC/", "timestamp": timestamp, "caption": "Food"},
            {"id": "2", "media_type": "VIDEO", "permalink": "https://www.instagram.com/p/DEF/", "timestamp": timestamp},
            {"id": "3", "media_type": "IMAGE", "permalink": "https://www.instagram.com/reel/GHI/", "timestamp": timestamp},
        ]})

    _mock_client(monkeypatch, "app.instagram_search", handler)
    provider = InstagramHashtagProvider(access_token="secret", user_id="123")
    found, videos = asyncio.run(provider.search(SearchRequest(query="#рецепты", platforms=["instagram"], min_views=0)))
    assert found == 3 and len(videos) == 1
    assert videos[0].url == "https://www.instagram.com/reel/ABC/"
    assert videos[0].views is None and videos[0].popularity_score is None
    assert len(seen) == 2


def test_instagram_explicit_metric_filter_fails_clearly():
    provider = InstagramHashtagProvider(access_token="secret", user_id="123")
    with pytest.raises(ProviderUnavailableError, match="уберите фильтры"):
        asyncio.run(provider.search(SearchRequest(query="food", platforms=["instagram"], min_views=1000)))


def test_tiktok_skips_result_without_username(monkeypatch):
    now = int(datetime.now(timezone.utc).timestamp()) - 3600
    _mock_client(monkeypatch, "app.tiktok_search", lambda request: httpx.Response(200, json={
        "error": {"code": "ok"}, "data": {"videos": [{
            "id": 123, "video_description": "No handle", "create_time": now,
            "video_duration": 20, "view_count": 10000, "like_count": 1000,
            "comment_count": 100,
        }]},
    }))
    provider = TikTokResearchProvider(client_key="", client_secret="", token="secret")
    found, videos = asyncio.run(provider.search(SearchRequest(query="test", platforms=["tiktok"])))
    assert found == 1 and videos == []


def test_tiktok_window_is_unavailable():
    provider = TikTokResearchProvider(client_key="", client_secret="", token="secret")
    with pytest.raises(ProviderUnavailableError, match="30 дней"):
        asyncio.run(provider.search(SearchRequest(query="test", platforms=["tiktok"], max_age_hours=721)))


def test_tiktok_ranks_qualifying_second_page(monkeypatch):
    now = int(datetime.now(timezone.utc).timestamp()) - 3600
    pages = []

    def handler(request):
        body = request.content.decode()
        pages.append(body)
        if '"cursor":5' in body:
            assert '"search_id":"batch-1"' in body
            return httpx.Response(200, json={"error": {"code": "ok"}, "data": {
                "videos": [{"id": 2, "username": "creator", "video_description": "Popular",
                            "create_time": now, "video_duration": 20,
                            "view_count": 100000, "like_count": 10000, "comment_count": 1000}],
                "has_more": False,
            }})
        return httpx.Response(200, json={"error": {"code": "ok"}, "data": {
            "videos": [{"id": 1, "username": "creator", "video_description": "Small",
                        "create_time": now, "video_duration": 20,
                        "view_count": 10, "like_count": 1, "comment_count": 0}],
            "has_more": True, "cursor": 5, "search_id": "batch-1",
        }})

    _mock_client(monkeypatch, "app.tiktok_search", handler)
    provider = TikTokResearchProvider(client_key="", client_secret="", token="secret")
    found, videos = asyncio.run(provider.search(SearchRequest(
        query="food", platforms=["tiktok"], min_views=1000, limit=1,
    )))
    assert len(pages) == 2
    assert found == 2 and [video.video_id for video in videos] == ["2"]


def test_instagram_paginates_past_non_reels(monkeypatch):
    timestamp = datetime.now(timezone.utc).isoformat()
    pages = []

    def handler(request):
        if request.url.path.endswith("/ig_hashtag_search"):
            return httpx.Response(200, json={"data": [{"id": "999"}]})
        pages.append(request.url.params.get("after"))
        if not request.url.params.get("after"):
            return httpx.Response(200, json={"data": [{
                "id": "1", "media_type": "IMAGE", "permalink": "https://www.instagram.com/p/ABC/",
                "timestamp": timestamp,
            }], "paging": {"cursors": {"after": "next"}}})
        return httpx.Response(200, json={"data": [{
            "id": "2", "media_type": "VIDEO", "permalink": "https://www.instagram.com/reel/DEF/",
            "timestamp": timestamp,
        }]})

    _mock_client(monkeypatch, "app.instagram_search", handler)
    provider = InstagramHashtagProvider(access_token="secret", user_id="123")
    found, videos = asyncio.run(provider.search(SearchRequest(query="food", platforms=["instagram"])))
    assert pages == [None, "next"]
    assert found == 2 and [video.video_id for video in videos] == ["2"]
