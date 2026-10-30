"""Import public short-video metadata from one explicitly allowed video URL."""

import asyncio
import re
from datetime import datetime, timezone

import yt_dlp
from fastapi import HTTPException

from app.models import ImportRequest, ImportResponse
from app.pipeline import CandidateBuilder, RawVideo


_VIDEO_PATHS = {
    "vk": re.compile(r"/clip-?\d+_\d+/?"),
    "tiktok": re.compile(r"/@[A-Za-z0-9._-]+/video/\d+/?"),
    "instagram": re.compile(r"/reel/[A-Za-z0-9_-]+/?"),
    "rutube": re.compile(r"/(?:video|shorts)/[A-Za-z0-9_-]+/?"),
}
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_HOSTS = {
    "vk.com": "vk", "www.vk.com": "vk", "m.vk.com": "vk",
    "tiktok.com": "tiktok", "www.tiktok.com": "tiktok",
    "instagram.com": "instagram", "www.instagram.com": "instagram",
    "rutube.ru": "rutube", "www.rutube.ru": "rutube",
}


def _validated_video_url(request: ImportRequest) -> tuple[str, str]:
    parsed = request.url
    platform = _HOSTS.get(parsed.host or "")
    if not platform or parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail="Поддерживаются публичные клипы VK, TikTok, Instagram Reels и Rutube по прямой HTTPS-ссылке.")
    path = parsed.path or ""
    if not _VIDEO_PATHS[platform].fullmatch(path):
        raise HTTPException(status_code=422, detail="Нужна прямая ссылка на один ролик, а не профиль, подборку или ленту.")
    return platform, f"https://{parsed.host}{path}"


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def _extract(url: str) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "playlistend": 1,
        "cachedir": False,
        "socket_timeout": 15,
        "retries": 1,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"} or "entries" in info:
        raise ValueError("Expected one video")
    return info


async def import_video(request: ImportRequest) -> ImportResponse:
    platform, url = _validated_video_url(request)
    try:
        info = await asyncio.wait_for(asyncio.to_thread(_extract, url), timeout=60)
    except (yt_dlp.utils.DownloadError, ValueError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail="Не удалось получить метаданные ролика. Проверьте публичность ссылки; площадка может требовать вход или ограничивать доступ.") from exc

    video_id = str(info.get("id") or info.get("display_id") or "").strip()
    if not video_id:
        raise HTTPException(status_code=502, detail="Площадка не вернула идентификатор ролика.")

    timestamp = info.get("timestamp") or info.get("release_timestamp")
    try:
        published_at = datetime.fromtimestamp(timestamp, timezone.utc) if isinstance(timestamp, (int, float)) else None
    except (OverflowError, OSError, ValueError):
        published_at = None

    video = CandidateBuilder().candidate(RawVideo(
        platform=platform,
        video_id=video_id,
        url=url,
        title=str(info.get("title") or ""),
        description=str(info.get("description") or ""),
        channel_id=str(info.get("channel_id") or info.get("uploader_id") or ""),
        channel_title=str(info.get("channel") or info.get("uploader") or ""),
        thumbnail_url=info.get("thumbnail"),
        published_at=published_at,
        duration_seconds=_count(info.get("duration")),
        views=_count(info.get("view_count")),
        likes=_count(info.get("like_count")),
        comments=_count(info.get("comment_count")),
        subscribers=_count(info.get("channel_follower_count")),
    ))
    return ImportResponse(video=video)
