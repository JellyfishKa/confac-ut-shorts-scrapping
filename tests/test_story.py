import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
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
