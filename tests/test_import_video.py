import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.config import settings
from app.import_video import _validated_video_url, import_video
from app.models import ImportRequest


@pytest.mark.parametrize("url,platform", [
    ("https://vk.com/clip-123_456", "vk"),
    ("https://www.tiktok.com/@author/video/123456789", "tiktok"),
    ("https://www.instagram.com/reel/AbC_123-", "instagram"),
    ("https://rutube.ru/shorts/abc123/", "rutube"),
])
def test_supported_single_video_urls(url, platform):
    assert _validated_video_url(ImportRequest(url=url)) == (platform, url)


@pytest.mark.parametrize("url", [
    "https://vk.com/clips", "https://www.tiktok.com/@author",
    "https://www.instagram.com/reels/", "https://rutube.ru/channel/123",
    "https://127.0.0.1/reel/abc", "https://instagram.com.evil.test/reel/abc",
    "http://vk.com/clip-123_456", "https://vk.com:444/clip-123_456",
])
def test_rejects_non_video_or_untrusted_urls(url):
    with pytest.raises(HTTPException) as exc:
        _validated_video_url(ImportRequest(url=url))
    assert exc.value.status_code == 422


def test_import_uses_shared_scoring_and_platform_qualified_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    captured = []
    timestamp = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp())

    def extract(url):
        captured.append(url)
        return {"id": "123456789", "title": "Клип", "uploader": "Автор",
                "timestamp": timestamp, "duration": 25, "view_count": 10000,
                "like_count": 200, "comment_count": 20}

    monkeypatch.setattr("app.import_video._extract", extract)
    result = asyncio.run(import_video(ImportRequest(url="https://www.tiktok.com/@author/video/123456789?tracking=1")))
    video = result.video
    assert captured == ["https://www.tiktok.com/@author/video/123456789"]
    assert video.platform == "tiktok"
    assert video.views == 10000
    assert video.views_per_hour is not None
    assert video.popularity_score is not None
    with sqlite3.connect(tmp_path / "snapshots.sqlite3") as conn:
        assert conn.execute("SELECT video_id FROM video_snapshots").fetchone()[0] == "tiktok:123456789"


def test_import_preserves_missing_metrics_as_null(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("app.import_video._extract", lambda url: {
        "id": "-12_34", "title": "Клип", "upload_date": "20261001", "view_count": 500
    })
    video = asyncio.run(import_video(ImportRequest(url="https://vk.com/clip-12_34"))).video
    assert video.views == 500
    assert video.likes is None and video.comments is None
    assert video.published_at is None and video.age_hours is None
    assert video.views_per_hour is None and video.popularity_score is None
    assert not (tmp_path / "snapshots.sqlite3").exists()


def test_import_rejects_playlist_result(monkeypatch):
    monkeypatch.setattr("app.import_video._extract", lambda url: {"_type": "playlist", "entries": []})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(import_video(ImportRequest(url="https://vk.com/clip-12_34")))
    assert exc.value.status_code == 502
