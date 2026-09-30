import httpx
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
