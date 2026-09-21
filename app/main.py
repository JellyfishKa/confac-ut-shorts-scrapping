from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.downloader import VideoDownloader
from app.models import DownloadRequest, DownloadResponse, SearchRequest, SearchResponse
from app.youtube import YouTubeProvider

app = FastAPI(title="YouTube Shorts Scrapping MVP", version="0.1.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/search", response_model=SearchResponse)
async def search_videos(request: SearchRequest) -> SearchResponse:
    try:
        found, videos = await YouTubeProvider().search(request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return SearchResponse(
        query=request.query,
        found=found,
        returned=len(videos),
        videos=videos,
    )


@app.post("/api/download", response_model=DownloadResponse)
def download_video(request: DownloadRequest) -> DownloadResponse:
    try:
        video_id, path = VideoDownloader().download(str(request.url))
        return DownloadResponse(status="completed", video_id=video_id, path=path)
    except Exception as exc:
        return DownloadResponse(status="failed", error=str(exc))
