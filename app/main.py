import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated

import httpx

from fastapi import FastAPI, HTTPException
from fastapi import Path as UrlPath
from fastapi import Query
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
    PinRequest,
    PlatformSearchStatus,
    SearchPlatform,
    SearchRequest,
    SearchResponse,
    SrtRequest,
    SrtResponse,
    StoryRequest,
    StoryResponse,
    HypeResponse,
    TrackedResponse,
    Transcript,
    TranscriptRequest,
    VideoCandidate,
)
from app.story import create_story
from app.hype import pick_hype
from app.transcribe import create_transcript, save_srt
from app.instagram_search import InstagramHashtagProvider
from app.pipeline import rank
from app.rutube import RutubeProvider
from app.search_errors import ProviderUnavailableError
from app.tiktok_search import TikTokResearchProvider
from app.tracker import TrackedStore, run_forever
from app.vk_search import VkProvider
from app.youtube import YouTubeProvider


@asynccontextmanager
async def lifespan(_: FastAPI):
    tracker = asyncio.create_task(run_forever()) if settings.tracker_enabled else None
    yield
    if tracker is not None:
        tracker.cancel()
        with suppress(asyncio.CancelledError):
            await tracker


app = FastAPI(title="YouTube Shorts Metadata Lab", version="0.2.0", lifespan=lifespan)

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
    TrackedStore().track(unique.values())
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


@app.post("/api/transcript", response_model=Transcript)
async def transcript(request: TranscriptRequest) -> Transcript:
    return await create_transcript(request)


@app.post("/api/transcript/srt", response_model=SrtResponse)
def transcript_srt(request: SrtRequest) -> SrtResponse:
    return save_srt(request)


@app.post("/api/import", response_model=ImportResponse)
async def import_video_url(request: ImportRequest) -> ImportResponse:
    imported = await import_video(request)
    TrackedStore().track([imported.video])
    return imported


@app.get("/api/hype", response_model=HypeResponse)
def hype(limit: Annotated[int, Query(ge=1, le=20)] = 5) -> HypeResponse:
    return pick_hype(TrackedStore().active(), limit)


@app.get("/api/tracked", response_model=TrackedResponse)
def tracked_videos() -> TrackedResponse:
    return TrackedResponse(
        interval_minutes=settings.tracker_interval_minutes,
        tracked_hours=settings.tracker_hours,
        videos=TrackedStore().active(),
    )


@app.post("/api/tracked/pin")
def pin_tracked_video(request: PinRequest) -> dict:
    if not TrackedStore().set_pinned(request.platform, request.video_id, request.pinned):
        raise HTTPException(status_code=404, detail="Ролик не отслеживается.")
    return {"status": "ok", "pinned": request.pinned}


@app.delete("/api/tracked/{platform}/{video_id}")
def untrack_video(platform: SearchPlatform, video_id: Annotated[str, UrlPath(max_length=200)]) -> dict:
    if not TrackedStore().remove(platform, video_id):
        raise HTTPException(status_code=404, detail="Ролик не отслеживается.")
    return {"status": "removed"}


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
