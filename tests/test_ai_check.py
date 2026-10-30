import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai_check import check_videos
from app.config import settings
from app.downloader import remember_download
from app.main import app
from app.models import VideoCandidate
from app.tracker import TrackedStore

NOW = datetime.now(timezone.utc)


def candidate(video_id, *, platform="youtube", speed=1_000, title=None):
    url = (f"https://www.youtube.com/shorts/{video_id}" if platform == "youtube"
           else f"https://rutube.ru/video/{video_id}/")
    return VideoCandidate(
        platform=platform, video_id=video_id, url=url, title=title or video_id, channel_title="Канал",
        description="#aivideo", thumbnail_url=f"https://img.example/{video_id}.jpg",
        published_at=NOW - timedelta(hours=5), duration_seconds=30, views=50_000, likes=None, comments=None,
        age_hours=5, views_per_hour=speed, like_rate=None, comment_rate=None, popularity_score=None,
    )


@pytest.fixture
def gemini(monkeypatch):
    """Fake Gemini: `answer(body)` returns the model's text or an httpx.Response."""
    monkeypatch.setattr(settings, "gemini_api_key", "secret")
    calls = []
    state = {"answer": lambda body: '{"ai_generated": true, "confidence": 0.8, "reason": "Сгенерировано"}'}
    original = httpx.AsyncClient

    def handler(request):
        if request.url.host != "generativelanguage.googleapis.com":
            return httpx.Response(200, content=b"jpeg-bytes", headers={"content-type": "image/jpeg"})
        assert request.headers["x-goog-api-key"] == "secret"
        body = json.loads(request.content)
        calls.append(body)
        answer = state["answer"](body)
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json={"steps": [
            {"type": "model_output", "content": [{"type": "text", "text": answer}]}]})

    monkeypatch.setattr("app.ai_check.httpx.AsyncClient",
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return calls, state


def test_youtube_video_is_judged_from_its_link_and_cached(gemini):
    calls, state = gemini
    state["answer"] = lambda body: 'Вот ответ:\n```json\n{"ai_generated": true, "confidence": 0.9, "reason": "Морфинг лиц"}\n```'
    video = candidate("a")

    verdict = asyncio.run(check_videos([video]))[("youtube", "a")]
    assert verdict.ai_generated is True and verdict.confidence == 0.9 and verdict.reason == "Морфинг лиц"
    assert calls[0]["model"] == settings.ai_check_model
    assert calls[0]["input"][1] == {"type": "video", "uri": "https://www.youtube.com/shorts/a"}
    assert "Название: a" in calls[0]["input"][0]["text"] and "#aivideo" in calls[0]["input"][0]["text"]

    asyncio.run(check_videos([video]))
    assert len(calls) == 1


def test_other_platforms_are_judged_from_the_thumbnail(gemini):
    calls, state = gemini
    state["answer"] = lambda body: '{"ai_generated": false, "confidence": 0.7, "reason": "Обычная съёмка"}'
    verdict = asyncio.run(check_videos([candidate("r", platform="rutube")]))[("rutube", "r")]
    assert verdict.ai_generated is False
    assert calls[0]["input"][1] == {
        "type": "image", "data": base64.b64encode(b"jpeg-bytes").decode("ascii"), "mime_type": "image/jpeg",
    }


def test_blocked_region_and_unclear_answers_are_not_cached(gemini):
    calls, state = gemini
    answers = iter([
        httpx.Response(400, json={"error": {"message": "User location is not supported for the API use."}}),
        "Не могу сказать.",
    ])
    state["answer"] = lambda body: next(answers)
    video = candidate("a")

    blocked = asyncio.run(check_videos([video]))[("youtube", "a")]
    assert blocked.ai_generated is None and "VPN" in blocked.reason
    unclear = asyncio.run(check_videos([video]))[("youtube", "a")]
    assert unclear.ai_generated is None
    assert len(calls) == 2


def test_hype_ai_only_drops_human_made_videos(gemini):
    calls, state = gemini
    state["answer"] = lambda body: (
        '{"ai_generated": false, "confidence": 0.9, "reason": "Новостной ролик"}'
        if body["input"][1]["uri"].endswith("/news")
        else '{"ai_generated": true, "confidence": 0.9, "reason": "Сгенерированные персонажи"}'
    )
    TrackedStore().track([candidate("news", speed=5_000), candidate("slop", speed=900)], now=NOW)
    client = TestClient(app)

    data = client.get("/api/hype", params={"platform": "youtube", "ai_only": True}).json()
    assert [pick["video"]["video_id"] for pick in data["picks"]] == ["slop"]
    assert data["picks"][0]["ai_check"]["ai_generated"] is True
    plain = client.get("/api/hype", params={"platform": "youtube"}).json()
    assert [pick["video"]["video_id"] for pick in plain["picks"]] == ["news", "slop"]


def test_hype_ai_only_needs_a_gemini_key(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    response = TestClient(app).get("/api/hype", params={"ai_only": True})
    assert response.status_code == 503 and "GEMINI_API_KEY" in response.json()["detail"]


def test_generation_brief_collects_everything_known(gemini, monkeypatch, tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "download_dir", downloads)
    video = candidate("a")
    TrackedStore().track([video], now=NOW)
    mp4 = downloads / "a.mp4"
    mp4.write_bytes(b"video")
    (downloads / "a.srt").write_text("1\n00:00:00,000 --> 00:00:01,500\nПривет\n", encoding="utf-8")
    remember_download(video.url, str(mp4))
    asyncio.run(check_videos([video]))
    client = TestClient(app)

    brief = client.get("/api/tracked/youtube/a/brief").json()
    assert brief["source"]["url"] == video.url
    assert brief["source"]["local_video_path"] == str(mp4.resolve())
    assert brief["transcript_srt"].startswith("1\n00:00:00,000")
    assert brief["ai_check"]["ai_generated"] is True
    assert brief["tracking"]["pinned"] is False and brief["tracking"]["history"] == []
    assert brief["virality"]["views"] == 50_000
    assert client.get("/api/tracked/youtube/zzz/brief").status_code == 404


def test_brief_without_download_or_check_keeps_those_fields_empty():
    TrackedStore().track([candidate("b")], now=NOW)
    brief = TestClient(app).get("/api/tracked/youtube/b/brief").json()
    assert brief["source"]["local_video_path"] is None
    assert brief["transcript_srt"] is None and brief["ai_check"] is None


def story_gemini(monkeypatch, response):
    """Fake Gemini for the story module; returns the list of request bodies it received."""
    monkeypatch.setattr(settings, "gemini_api_key", "secret")
    calls = []
    original = httpx.AsyncClient

    def handler(request):
        calls.append(json.loads(request.content))
        return response

    monkeypatch.setattr("app.story.httpx.AsyncClient",
                        lambda *args, **kwargs: original(*args, transport=httpx.MockTransport(handler), **kwargs))
    return calls


def story_reply(text):
    return httpx.Response(200, json={"steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}]})


def test_story_button_result_is_kept_for_the_generation_json(monkeypatch):
    calls = story_gemini(monkeypatch, story_reply("0:00 — кот превращается в арбуз."))
    video = candidate("a")
    TrackedStore().track([video], now=NOW)
    client = TestClient(app)

    assert client.post("/api/story", json={"url": video.url}).status_code == 200
    brief = client.get("/api/tracked/youtube/a/brief").json()
    assert brief["story"]["text"] == "0:00 — кот превращается в арбуз."
    assert brief["story"]["model"] == calls[0]["model"]
    assert "story_error" not in brief
    assert len(calls) == 1


def test_generation_json_can_ask_gemini_for_the_story(monkeypatch):
    calls = story_gemini(monkeypatch, story_reply("Что видно: фрукты танцуют."))
    TrackedStore().track([candidate("a")], now=NOW)
    client = TestClient(app)

    brief = client.get("/api/tracked/youtube/a/brief", params={"with_story": True}).json()
    assert brief["story"]["text"] == "Что видно: фрукты танцуют."
    assert calls[0]["input"][1] == {"type": "video", "uri": "https://www.youtube.com/shorts/a"}
    # The story is stored: the next JSON reuses it without asking Gemini again.
    again = client.get("/api/tracked/youtube/a/brief", params={"with_story": True}).json()
    assert again["story"]["text"] == "Что видно: фрукты танцуют." and len(calls) == 1


def test_generation_json_reports_why_the_story_is_missing(monkeypatch):
    story_gemini(monkeypatch, httpx.Response(400, json={"error": {"message": "User location is not supported for the API use."}}))
    TrackedStore().track([candidate("a"), candidate("r", platform="rutube")], now=NOW)
    client = TestClient(app)

    blocked = client.get("/api/tracked/youtube/a/brief", params={"with_story": True}).json()
    assert blocked["story"] is None and "VPN" in blocked["story_error"]
    not_downloaded = client.get("/api/tracked/rutube/r/brief", params={"with_story": True}).json()
    assert not_downloaded["story"] is None and "MP4" in not_downloaded["story_error"]
