import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models import SearchRequest
from app.search_errors import ProviderUnavailableError
from app.vk_search import VK_SEARCH_API, VkProvider


@pytest.fixture(autouse=True)
def isolated_snapshots(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, payload, calls):
        self.payload = payload
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def post(self, url, *, data):
        self.calls.append((url, data))
        return FakeResponse(self.payload)


class FakeSnapshots:
    def __init__(self, _):
        pass

    def observe(self, *_args, **_kwargs):
        return SimpleNamespace(previous_views=None, growth_views_per_hour=None,
                               growth_likes_per_hour=None, snapshot_age_minutes=None)


def test_search_only_returns_recent_vk_clips_with_available_stats(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    now = datetime.now(timezone.utc)
    recent = int((now - timedelta(hours=4)).timestamp())
    old = int((now - timedelta(days=20)).timestamp())
    payload = {"response": {
        "count": 4,
        "profiles": [{"id": 7, "first_name": "Анна", "last_name": "Иванова"}],
        "groups": [{"id": 10, "name": "Канал"}],
        "items": [
            {"id": 1, "owner_id": -10, "type": "short_video", "title": "Клип", "duration": 35,
             "date": recent, "views": 12000, "likes": {"count": 600}, "comments": 30,
             "image": [{"url": "https://example.com/s.jpg", "width": 100, "height": 100},
                       {"url": "https://example.com/l.jpg", "width": 800, "height": 800}]},
            {"id": 2, "owner_id": 7, "type": "video", "duration": 20, "date": recent, "views": 900000},
            {"id": 3, "owner_id": 7, "type": "short_video", "duration": 30, "date": old, "views": 10000},
            {"id": 4, "owner_id": 7, "type": "short_video", "duration": 200, "date": recent, "views": 10000},
        ],
    }}
    calls = []
    monkeypatch.setattr("app.vk_search.httpx.AsyncClient", lambda **_: FakeClient(payload, calls))

    request = SearchRequest(query="котики", min_views=1000, max_age_hours=168, max_duration_seconds=90)
    found, videos = asyncio.run(VkProvider(access_token="secret-token").search(request))

    assert found == 3
    assert len(videos) == 1
    video = videos[0]
    assert video.platform == "vk"
    assert video.video_id == "-10_1"
    assert video.url == "https://vk.com/clip-10_1"
    assert video.channel_title == "Канал"
    assert video.thumbnail_url == "https://example.com/l.jpg"
    assert video.views == 12000 and video.likes == 600 and video.comments == 30
    assert video.views_per_hour is not None and video.views_per_hour > 0
    assert video.like_rate == 0.05
    assert calls[0][0] == VK_SEARCH_API
    assert calls[0][1]["q"] == "котики"
    assert calls[0][1]["shorter"] == 90
    assert calls[0][1]["filters"] == "short"
    assert calls[0][1]["access_token"] == "secret-token"
    assert "secret-token" not in calls[0][0]
    payload["response"]["items"][0].update(views=12200, likes={"count": 640}, comments=31)
    _, repeated = asyncio.run(VkProvider(access_token="secret-token").search(request))
    assert repeated[0].previous_views == 12000
    assert repeated[0].growth_views_per_hour is not None and repeated[0].growth_views_per_hour > 0
    assert repeated[0].growth_likes_per_hour is not None and repeated[0].growth_likes_per_hour > 0


def test_missing_likes_cannot_satisfy_positive_like_filter(monkeypatch):
    monkeypatch.setattr("app.vk_search.SnapshotStore", FakeSnapshots)
    timestamp = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    payload = {"response": {"count": 1, "items": [
        {"id": 1, "owner_id": 7, "type": "short_video", "duration": 30, "date": timestamp, "views": 10000}
    ]}}
    monkeypatch.setattr("app.vk_search.httpx.AsyncClient", lambda **_: FakeClient(payload, []))

    found, videos = asyncio.run(VkProvider(access_token="test").search(SearchRequest(
        query="котики", min_views=0, min_like_rate=0.01,
    )))
    assert found == 1
    assert videos == []


def test_missing_comments_use_view_only_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    timestamp = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    payload = {"response": {"count": 1, "items": [
        {"id": 1, "owner_id": 7, "type": "short_video", "duration": 30,
         "date": timestamp, "views": 10000, "likes": {"count": 100}}
    ]}}
    monkeypatch.setattr("app.vk_search.httpx.AsyncClient", lambda **_: FakeClient(payload, []))

    found, videos = asyncio.run(VkProvider(access_token="test").search(SearchRequest(query="котики")))
    assert found == 1
    assert len(videos) == 1
    assert videos[0].likes == 100 and videos[0].comments is None
    assert videos[0].previous_views is None
    assert videos[0].growth_views_per_hour is None
    with sqlite3.connect(tmp_path / "snapshots.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM video_snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT views FROM view_snapshots").fetchall() == [(10000,)]
    payload["response"]["items"][0]["views"] = 10100
    _, repeated = asyncio.run(VkProvider(access_token="test").search(SearchRequest(query="котики")))
    assert repeated[0].previous_views == 10000
    assert repeated[0].growth_views_per_hour is not None and repeated[0].growth_views_per_hour > 0
    assert repeated[0].growth_likes_per_hour is None


def test_vk_error_never_exposes_access_token(monkeypatch):
    monkeypatch.setattr("app.vk_search.SnapshotStore", FakeSnapshots)
    payload = {"error": {"error_code": 15, "error_msg": "secret-token", "request_params": [
        {"key": "access_token", "value": "secret-token"}
    ]}}
    monkeypatch.setattr("app.vk_search.httpx.AsyncClient", lambda **_: FakeClient(payload, []))

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(VkProvider(access_token="secret-token").search(SearchRequest(query="котики")))
    assert not isinstance(exc.value, ProviderUnavailableError)
    assert "15" in str(exc.value)
    assert "secret-token" not in str(exc.value)


def test_missing_token_is_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "vk_access_token", "")
    with pytest.raises(ProviderUnavailableError, match="VK_ACCESS_TOKEN"):
        VkProvider()
