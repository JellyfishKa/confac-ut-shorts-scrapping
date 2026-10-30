import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import transcribe
from app.config import settings
from app.downloader import remember_download
from app.main import app
from app.models import TranscriptSegment
from app.transcribe import to_srt
from scripts import autoclip_handoff

URL = "https://rutube.ru/video/abc/"


class FakeModel:
    def __init__(self, segments, duration=12.0, language="ru"):
        self.segments = segments
        self.info = SimpleNamespace(language=language, duration=duration)
        self.calls = []
        self.consumed = False

    def transcribe(self, audio, **options):
        self.calls.append((audio, options))

        def produce():
            self.consumed = True
            yield from self.segments

        return produce(), self.info


@pytest.fixture
def downloaded(monkeypatch, tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "download_dir", downloads)
    video = downloads / "abc.mp4"
    video.write_bytes(b"fake mp4")
    remember_download(URL, str(video))
    return video


@pytest.fixture
def model(monkeypatch):
    fake = FakeModel([
        SimpleNamespace(start=0.0, end=2.345, text="  Привет, это тест. "),
        SimpleNamespace(start=2.5, end=2.5, text="пустой интервал"),
        SimpleNamespace(start=3.0, end=4.0, text="   "),
        SimpleNamespace(start=4.2, end=6.0, text=" Вторая реплика."),
    ])
    monkeypatch.setattr(transcribe, "_load_model", lambda name, device, compute_type: fake)
    return fake


def test_transcript_returns_timed_segments(downloaded, model, monkeypatch):
    monkeypatch.setattr(settings, "whisper_model", "small")
    monkeypatch.setattr(settings, "whisper_language", "")
    response = TestClient(app).post("/api/transcript", json={"url": URL, "local_video_path": str(downloaded)})

    assert response.status_code == 200
    body = response.json()
    assert body["source_url"] == URL and body["model"] == "small" and body["language"] == "ru"
    # Empty text and zero-length segments are dropped; text is trimmed.
    assert body["segments"] == [
        {"start_seconds": 0.0, "end_seconds": 2.35, "speaker": None, "text": "Привет, это тест."},
        {"start_seconds": 4.2, "end_seconds": 6.0, "speaker": None, "text": "Вторая реплика."},
    ]
    audio, options = model.calls[0]
    assert audio == str(downloaded.resolve())
    assert options["language"] is None and options["vad_filter"] is True


def test_transcript_rejects_long_video_before_decoding_speech(downloaded, model):
    model.info.duration = 181
    response = TestClient(app).post("/api/transcript", json={"url": URL, "local_video_path": str(downloaded)})
    assert response.status_code == 422
    assert "180 секунд" in response.json()["detail"]
    assert not model.consumed


def test_transcript_explains_missing_dependency(downloaded, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    transcribe._load_model.cache_clear()
    response = TestClient(app).post("/api/transcript", json={"url": URL, "local_video_path": str(downloaded)})
    assert response.status_code == 503
    assert "requirements-transcribe.txt" in response.json()["detail"]


def test_transcript_rejects_file_outside_downloads(downloaded, model, tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"video")
    remember_download(URL, str(outside))
    response = TestClient(app).post("/api/transcript", json={"url": URL, "local_video_path": str(outside)})
    assert response.status_code == 422
    assert "папки загрузок" in response.json()["detail"]
    assert model.calls == []


def test_transcript_rejects_mp4_of_another_video(downloaded, model):
    response = TestClient(app).post("/api/transcript", json={
        "url": "https://rutube.ru/video/other/", "local_video_path": str(downloaded),
    })
    assert response.status_code == 422
    assert "именно этот ролик" in response.json()["detail"]
    assert model.calls == []


def test_srt_numbers_sorted_blocks_with_comma_milliseconds():
    srt = to_srt([
        TranscriptSegment(start_seconds=3725.5, end_seconds=3727.0444, text="Позже"),
        TranscriptSegment(start_seconds=0, end_seconds=1.2, text="Первая строка\n\n  вторая строка  "),
    ])
    assert srt == (
        "1\n00:00:00,000 --> 00:00:01,200\nПервая строка\nвторая строка\n\n"
        "2\n01:02:05,500 --> 01:02:07,044\nПозже\n"
    )


@pytest.mark.parametrize("segment", [
    {"start_seconds": 2, "end_seconds": 2, "text": "ноль"},
    {"start_seconds": -1, "end_seconds": 2, "text": "минус"},
    {"start_seconds": 0, "end_seconds": 2, "text": "   "},
])
def test_srt_rejects_invalid_segments(downloaded, segment):
    response = TestClient(app).post("/api/transcript/srt", json={
        "url": URL, "local_video_path": str(downloaded), "segments": [segment],
    })
    assert response.status_code == 422


def test_srt_is_saved_next_to_mp4_and_accepted_by_autoclip_handoff(downloaded):
    response = TestClient(app).post("/api/transcript/srt", json={
        "url": URL, "local_video_path": str(downloaded),
        "segments": [{"start_seconds": 0.5, "end_seconds": 2, "speaker": None, "text": "Проверенная реплика"}],
    })
    assert response.status_code == 200
    body = response.json()
    saved = downloaded.with_suffix(".srt").resolve()
    assert body["path"] == str(saved)
    assert saved.read_text(encoding="utf-8") == body["srt"] == "1\n00:00:00,500 --> 00:00:02,000\nПроверенная реплика\n"
    assert autoclip_handoff._input_file(body["path"], suffix=".srt") == saved


def test_srt_requires_registered_mp4(downloaded):
    response = TestClient(app).post("/api/transcript/srt", json={
        "url": "https://rutube.ru/video/other/", "local_video_path": str(downloaded),
        "segments": [{"start_seconds": 0, "end_seconds": 1, "text": "x"}],
    })
    assert response.status_code == 422
    assert not downloaded.with_suffix(".srt").exists()


def test_transcript_reports_audio_error_while_decoding(downloaded, model):
    def broken():
        yield SimpleNamespace(start=0.0, end=1.0, text="начало")
        raise RuntimeError("corrupted audio frame")

    model.segments = broken()
    response = TestClient(app).post("/api/transcript", json={"url": URL, "local_video_path": str(downloaded)})
    assert response.status_code == 422
    assert "прочитать звук" in response.json()["detail"]


def test_second_transcript_is_refused_while_one_runs(downloaded, model):
    assert transcribe._busy.acquire(blocking=False)
    try:
        response = TestClient(app).post("/api/transcript", json={"url": URL, "local_video_path": str(downloaded)})
    finally:
        transcribe._busy.release()
    assert response.status_code == 409
    assert model.calls == []
    # The lock is free again for the next request.
    assert TestClient(app).post("/api/transcript", json={"url": URL, "local_video_path": str(downloaded)}).status_code == 200
