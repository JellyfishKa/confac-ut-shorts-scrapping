import base64
import importlib
import json
import subprocess

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.downloader import remember_download, was_downloaded_for
from app.main import app


def test_story_requires_server_side_key(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    response = TestClient(app).post(
        "/api/story", json={"url": "https://www.youtube.com/shorts/abc123"}
    )
    assert response.status_code == 503
    assert "GEMINI_API_KEY" in response.json()["detail"]


def test_story_sends_youtube_video_and_returns_model_output(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-secret")

    def respond(request):
        assert request.headers["x-goog-api-key"] == "test-secret"
        body = __import__("json").loads(request.content)
        assert body["model"] == "gemini-3.8-flash"
        assert body["input"][1] == {
            "type": "video", "uri": "https://www.youtube.com/shorts/abc123"
        }
        assert "не выдумывай" in body["input"][0]["text"].lower()
        return httpx.Response(200, json={"status": "completed", "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": "0:00 — герой входит."}]}
        ]})

    transport = httpx.MockTransport(respond)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        return original_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr("app.story.httpx.AsyncClient", mock_client)
    response = TestClient(app).post(
        "/api/story", json={"url": "https://www.youtube.com/shorts/abc123"}
    )
    assert response.status_code == 200
    assert response.json()["story"] == "0:00 — герой входит."
    assert "test-secret" not in str(response.json())


def test_story_retries_overloaded_model_with_video_capable_fallback(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-secret")
    models = []

    def respond(request):
        body = __import__("json").loads(request.content)
        models.append(body["model"])
        assert body["input"][1]["type"] == "video"
        if body["model"] == "gemini-3.8-flash":
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return httpx.Response(200, json={"status": "completed", "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": "Что видно: ...\nЧто слышно: ..."}]}
        ]})

    transport = httpx.MockTransport(respond)
    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.story.httpx.AsyncClient", lambda *a, **kw: original_client(*a, transport=transport, **kw))

    response = TestClient(app).post("/api/story", json={"url": "https://youtu.be/abc123"})
    assert response.status_code == 200
    assert models == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
    assert response.json()["model"] == "gemini-3.5-flash-lite"
    assert "Что слышно" in response.json()["story"]


@pytest.mark.parametrize(
    ("upstream_status", "expected_status", "message"),
    [(401, 502, "ключ"), (429, 503, "Лимит"),
     (502, 503, "перегружен"), (503, 503, "перегружен"), (504, 503, "перегружен")],
)
def test_story_explains_upstream_failure_without_leaking_key(monkeypatch, upstream_status, expected_status, message):
    monkeypatch.setattr(settings, "gemini_api_key", "test-secret")
    calls = []

    def respond(request):
        calls.append(__import__("json").loads(request.content)["model"])
        return httpx.Response(upstream_status, json={"error": {"message": "test-secret"}})

    transport = httpx.MockTransport(respond)
    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.story.httpx.AsyncClient", lambda *a, **kw: original_client(*a, transport=transport, **kw))

    response = TestClient(app).post("/api/story", json={"url": "https://youtu.be/abc123"})
    assert response.status_code == expected_status
    assert message in response.json()["detail"]
    assert "test-secret" not in str(response.json())
    assert len(calls) == (2 if upstream_status in {502, 503, 504} else 1)


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/@creator/video/123",
    "https://vk.com/clip-10_123",
    "https://www.instagram.com/reel/ABC123/",
    "https://rutube.ru/video/abc123/",
])
def test_social_story_uses_only_small_downloaded_mp4(monkeypatch, tmp_path, url):
    monkeypatch.setattr(settings, "gemini_api_key", "test-secret")
    monkeypatch.setattr(settings, "download_dir", tmp_path)
    video = tmp_path / "123.mp4"
    video.write_bytes(b"fake mp4 bytes")
    remember_download(url, str(video))
    monkeypatch.setattr("app.story.subprocess.run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, "30.5\n", ""))

    def respond(request):
        body = json.loads(request.content)
        assert body["input"][1] == {
            "type": "video", "data": base64.b64encode(video.read_bytes()).decode("ascii"),
            "mime_type": "video/mp4",
        }
        assert str(video) not in str(body)
        return httpx.Response(200, json={"steps": [
            {"type": "model_output", "content": [{"type": "text", "text": "Сюжет и реплики."}]}
        ]})

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr("app.story.httpx.AsyncClient", lambda *a, **kw: original_client(*a, transport=transport, **kw))
    response = TestClient(app).post("/api/story", json={
        "url": url, "local_video_path": str(video),
    })
    assert response.status_code == 200
    assert response.json()["story"] == "Сюжет и реплики."


def test_social_story_requires_download_and_rejects_other_hosts(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-secret")
    client = TestClient(app)
    missing = client.post("/api/story", json={"url": "https://www.instagram.com/reel/ABC/"})
    assert missing.status_code == 422
    assert "MP4" in missing.json()["detail"]
    other = client.post("/api/story", json={
        "url": "https://example.com/video/123", "local_video_path": "downloads/123.mp4",
    })
    assert other.status_code == 422


def test_social_story_rejects_file_outside_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "gemini_api_key", "test-secret")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "download_dir", downloads)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"video")
    response = TestClient(app).post("/api/story", json={
        "url": "https://rutube.ru/video/abc/", "local_video_path": str(outside),
    })
    assert response.status_code == 422
    assert "папки загрузок" in response.json()["detail"]


def test_social_story_rejects_mp4_of_another_video(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "gemini_api_key", "test-secret")
    monkeypatch.setattr(settings, "download_dir", tmp_path)
    video = tmp_path / "123.mp4"
    video.write_bytes(b"video")
    remember_download("https://rutube.ru/video/first/", str(video))
    response = TestClient(app).post("/api/story", json={
        "url": "https://rutube.ru/video/second/", "local_video_path": str(video),
    })
    assert response.status_code == 422
    assert "именно этот ролик" in response.json()["detail"]


def test_download_endpoint_binds_video_url_to_saved_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "download_dir", tmp_path)
    video = tmp_path / "123.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr("app.main.VideoDownloader.download", lambda self, url: ("123", str(video)))
    url = "https://rutube.ru/video/abc/"
    response = TestClient(app).post("/api/download", json={"url": url})
    assert response.status_code == 200
    assert response.json()["path"] == str(video)
    assert was_downloaded_for(url, video)


def test_download_registry_survives_server_restart(tmp_path):
    video = tmp_path / "abc.mp4"
    video.write_bytes(b"video")
    remember_download("https://rutube.ru/video/abc/", str(video))

    import app.downloader
    restarted = importlib.reload(app.downloader)
    assert restarted.was_downloaded_for("https://rutube.ru/video/abc/", video.resolve())
    assert not restarted.was_downloaded_for("https://rutube.ru/video/other/", video.resolve())


def test_download_rejects_unsupported_host(monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.VideoDownloader.download", lambda self, url: calls.append(url))
    response = TestClient(app).post("/api/download", json={"url": "https://example.com/video.mp4"})
    assert response.status_code == 422
    assert calls == []


def test_download_failure_returns_http_error(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "download_dir", tmp_path)

    def fail(self, url):
        raise RuntimeError("Video unavailable")

    monkeypatch.setattr("app.main.VideoDownloader.download", fail)
    response = TestClient(app).post("/api/download", json={"url": "https://www.youtube.com/shorts/abc123"})
    assert response.status_code == 502
    assert response.json()["status"] == "failed"
    assert "Video unavailable" in response.json()["error"]


@pytest.mark.parametrize("size,duration,expected", [(5, "181\n", "180 секунд"), (5, "n/a", "проверить"), (0, "30\n", "14 МБ")])
def test_social_story_checks_size_and_duration(monkeypatch, tmp_path, size, duration, expected):
    monkeypatch.setattr(settings, "gemini_api_key", "test-secret")
    monkeypatch.setattr(settings, "download_dir", tmp_path)
    video = tmp_path / "abc.mp4"
    video.write_bytes(b"x" * size)
    remember_download("https://rutube.ru/video/abc/", str(video))
    monkeypatch.setattr("app.story.subprocess.run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, duration, ""))
    response = TestClient(app).post("/api/story", json={
        "url": "https://rutube.ru/video/abc/", "local_video_path": str(video),
    })
    assert response.status_code == 422
    assert expected in response.json()["detail"]


def test_social_story_limits_max_inline_size(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "gemini_api_key", "test-secret")
    monkeypatch.setattr(settings, "download_dir", tmp_path)
    monkeypatch.setattr("app.story.MAX_INLINE_VIDEO_BYTES", 4)
    video = tmp_path / "abc.mp4"
    video.write_bytes(b"12345")
    remember_download("https://rutube.ru/video/abc/", str(video))
    response = TestClient(app).post("/api/story", json={
        "url": "https://rutube.ru/video/abc/", "local_video_path": str(video),
    })
    assert response.status_code == 422
    assert "14 МБ" in response.json()["detail"]


def test_social_story_requires_ffprobe(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "gemini_api_key", "test-secret")
    monkeypatch.setattr(settings, "download_dir", tmp_path)
    video = tmp_path / "abc.mp4"
    video.write_bytes(b"video")
    remember_download("https://rutube.ru/video/abc/", str(video))
    monkeypatch.setattr("app.story.subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("ffprobe")))
    response = TestClient(app).post("/api/story", json={
        "url": "https://rutube.ru/video/abc/", "local_video_path": str(video),
    })
    assert response.status_code == 503
    assert "ffprobe" in response.json()["detail"]
