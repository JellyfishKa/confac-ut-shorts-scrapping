import asyncio
from pathlib import Path

import httpx

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.comfy import build_comfy_manifest
from app.config import settings
from app.downloader import VideoDownloader, remember_download
from app.import_video import YOUTUBE_HOSTS, _validated_video_url, import_video
from app.models import (
    ComfyManifestRequest,
    ComfyManifestResponse,
    DownloadRequest,
    DownloadResponse,
    ImportRequest,
    ImportResponse,
    PlatformSearchStatus,
    SearchRequest,
    SearchResponse,
    StoryRequest,
    StoryResponse,
    VideoCandidate,
)
from app.story import create_story
from app.instagram_search import InstagramHashtagProvider
from app.pipeline import rank
from app.rutube import RutubeProvider
from app.search_errors import ProviderUnavailableError
from app.tiktok_search import TikTokResearchProvider
from app.vk_search import VkProvider
from app.youtube import YouTubeProvider

app = FastAPI(title="YouTube Shorts Metadata Lab", version="0.2.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "youtube_api_key_configured": bool(settings.youtube_api_key),
        "snapshot_db": str(settings.data_dir / "snapshots.sqlite3"),
        "growth_min_interval_minutes": settings.growth_min_interval_minutes,
        "mode": "live" if settings.youtube_api_key else "needs_api_key",
    }


@app.post("/api/search", response_model=SearchResponse)
async def search_videos(request: SearchRequest) -> SearchResponse:
    providers = {
        "youtube": YouTubeProvider,
        "vk": VkProvider,
        "tiktok": TikTokResearchProvider,
        "instagram": InstagramHashtagProvider,
        "rutube": RutubeProvider,
    }

    async def collect(platform: str) -> tuple[int, list[VideoCandidate], PlatformSearchStatus]:
        provider = providers.get(platform)
        if provider is None:
            return 0, [], PlatformSearchStatus(
                platform=platform,
                status="unavailable",
                detail=f"Поиск по {platform} пока не подключён; можно импортировать прямую ссылку на ролик.",
            )
        try:
            found, videos = await provider().search(request)
            return found, videos, PlatformSearchStatus(platform=platform, status="ok", found=found)
        except ProviderUnavailableError as exc:
            return 0, [], PlatformSearchStatus(platform=platform, status="unavailable", detail=str(exc))
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                detail = f"Площадка ответила HTTP {exc.response.status_code}."
            elif isinstance(exc, RuntimeError):
                detail = str(exc)
            else:
                detail = f"Не удалось получить данные: {type(exc).__name__}."
            return 0, [], PlatformSearchStatus(platform=platform, status="error", detail=detail)

    results = await asyncio.gather(*(collect(platform) for platform in request.platforms))
    found = sum(result[0] for result in results)
    unique = {}
    for _, candidates, _ in results:
        for video in candidates:
            unique[(video.platform, video.video_id)] = video
    videos = rank(unique.values(), request.limit)
    statuses = [result[2] for result in results]
    for status in statuses:
        status.returned = sum(video.platform == status.platform for video in videos)

    return SearchResponse(
        query=request.query,
        found=found,
        returned=len(videos),
        videos=videos,
        platform_statuses=statuses,
    )


@app.post("/api/comfy/manifest", response_model=ComfyManifestResponse)
def comfy_manifest(request: ComfyManifestRequest) -> ComfyManifestResponse:
    return ComfyManifestResponse(
        manifest=build_comfy_manifest(request.video, request.local_video_path)
    )


@app.post("/api/story", response_model=StoryResponse)
async def story(request: StoryRequest) -> StoryResponse:
    return await create_story(request)


@app.post("/api/import", response_model=ImportResponse)
async def import_video_url(request: ImportRequest) -> ImportResponse:
    return await import_video(request)


@app.post("/api/download", response_model=DownloadResponse)
def download_video(request: DownloadRequest) -> DownloadResponse | JSONResponse:
    if request.url.host not in YOUTUBE_HOSTS:
        _validated_video_url(ImportRequest(url=request.url))
    try:
        video_id, path = VideoDownloader().download(str(request.url))
        remember_download(str(request.url), path)
        return DownloadResponse(status="completed", video_id=video_id, path=path)
    except Exception as exc:
        failed = DownloadResponse(status="failed", error=str(exc))
        return JSONResponse(status_code=502, content=failed.model_dump())
