"""Local speech transcription of a downloaded short with faster-whisper.

faster-whisper is optional (requirements-transcribe.txt): without it the
transcript endpoint answers 503 with an install hint. Audio never leaves
the machine; the model is downloaded once on first use.
"""

import asyncio
import threading
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from app.config import settings
from app.downloader import downloaded_mp4
from app.models import SrtRequest, SrtResponse, Transcript, TranscriptRequest, TranscriptSegment

MAX_TRANSCRIPT_DURATION_SECONDS = 180
INSTALL_HINT = (
    "Локальная расшифровка не установлена. Выполните "
    ".\\.venv\\Scripts\\python.exe -m pip install -r requirements-transcribe.txt и перезапустите сервер."
)

# One transcription at a time: a second request would only compete for the same CPU.
_busy = threading.Lock()


@lru_cache(maxsize=1)
def _load_model(name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    return WhisperModel(name, device=device, compute_type=compute_type)


def _model():
    try:
        return _load_model(settings.whisper_model, settings.whisper_device, settings.whisper_compute_type)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=INSTALL_HINT) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Не удалось запустить Whisper ({settings.whisper_model}, {settings.whisper_device}): "
                f"{type(exc).__name__}. Проверьте WHISPER_MODEL и WHISPER_DEVICE; при первом запуске "
                "модель скачивается из интернета."
            ),
        ) from exc


def _transcribe(path: Path) -> tuple[str | None, list[TranscriptSegment]]:
    # Refuse instead of queueing: a waiting request would hold a worker thread for minutes.
    if not _busy.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Уже идёт расшифровка другого ролика. Повторите, когда она закончится.")
    try:
        model = _model()
        unreadable = HTTPException(status_code=422, detail="Не удалось прочитать звук из MP4. Скачайте ролик ещё раз.")
        try:
            segments, info = model.transcribe(
                str(path), language=settings.whisper_language or None, vad_filter=True,
            )
        except Exception as exc:
            raise unreadable from exc
        # The duration is known before any speech is decoded, so long videos stop here.
        if info.duration > MAX_TRANSCRIPT_DURATION_SECONDS:
            raise HTTPException(status_code=422, detail="Для расшифровки нужен ролик длительностью до 180 секунд.")
        result = []
        try:
            # Segments are decoded lazily, so audio errors can surface only while iterating.
            for segment in segments:
                text = segment.text.strip()
                start, end = round(segment.start, 2), round(segment.end, 2)
                if text and end > start:
                    result.append(TranscriptSegment(start_seconds=start, end_seconds=end, text=text))
        except Exception as exc:
            raise unreadable from exc
    finally:
        _busy.release()
    return info.language, result


async def create_transcript(request: TranscriptRequest) -> Transcript:
    url = str(request.url)
    path = downloaded_mp4(request.local_video_path, url)
    language, segments = await asyncio.to_thread(_transcribe, path)
    return Transcript(source_url=url, language=language, model=settings.whisper_model, segments=segments)


def save_srt(request: SrtRequest) -> SrtResponse:
    """Write reviewed segments as an SRT next to the MP4, for scripts/autoclip_handoff.py --srt."""
    video = downloaded_mp4(request.local_video_path, str(request.url))
    srt = to_srt(request.segments)
    target = video.with_suffix(".srt")
    target.write_text(srt, encoding="utf-8", newline="\n")
    return SrtResponse(path=str(target), srt=srt)


def to_srt(segments: list[TranscriptSegment]) -> str:
    blocks = []
    ordered = sorted(segments, key=lambda segment: (segment.start_seconds, segment.end_seconds))
    for number, segment in enumerate(ordered, 1):
        # A blank line ends an SRT block, so text keeps only non-empty lines.
        text = "\n".join(line.strip() for line in segment.text.splitlines() if line.strip())
        blocks.append(
            f"{number}\n{_timestamp(segment.start_seconds)} --> {_timestamp(segment.end_seconds)}\n{text}\n"
        )
    return "\n".join(blocks)


def _timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"
