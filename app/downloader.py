import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from yt_dlp import YoutubeDL

from app.config import settings


def _registry() -> sqlite3.Connection:
    # Persisted so a server restart does not forget which URL each local MP4 belongs to.
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.data_dir / "downloads.sqlite3")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS downloads ("
        "url TEXT PRIMARY KEY, path TEXT NOT NULL, downloaded_at TEXT NOT NULL)"
    )
    return conn


def remember_download(url: str, path: str) -> None:
    with closing(_registry()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO downloads(url, path, downloaded_at) VALUES (?, ?, ?)",
            (url, str(Path(path).resolve()), datetime.now(timezone.utc).isoformat()),
        )


def was_downloaded_for(url: str, path: Path) -> bool:
    with closing(_registry()) as conn:
        row = conn.execute("SELECT path FROM downloads WHERE url = ?", (url,)).fetchone()
    return row is not None and Path(row[0]) == path


def downloaded_path(url: str) -> Path | None:
    """The local file the app downloaded for this URL, if it is still there."""
    with closing(_registry()) as conn:
        row = conn.execute("SELECT path FROM downloads WHERE url = ?", (url,)).fetchone()
    path = Path(row[0]) if row else None
    return path if path and path.is_file() else None


def downloaded_mp4(path_text: str, source_url: str) -> Path:
    """The local MP4 the app downloaded for this URL, or 422 for any other file."""
    try:
        root = settings.download_dir.resolve()
        path = Path(path_text).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Сначала скачайте MP4 через приложение.") from exc
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() != ".mp4":
        raise HTTPException(status_code=422, detail="Нужен MP4 из папки загрузок приложения.")
    if not was_downloaded_for(source_url, path):
        raise HTTPException(status_code=422, detail="Сначала скачайте именно этот ролик через приложение.")
    return path


class VideoDownloader:
    def __init__(self, output_dir: Path | None = None):
        self.output_dir = output_dir or settings.download_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def download(self, url: str) -> tuple[str, str]:
        options = {
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "outtmpl": str(self.output_dir / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info.get("id") or "unknown"
            requested = info.get("requested_downloads") or []
            if requested and requested[0].get("filepath"):
                path = requested[0]["filepath"]
            else:
                path = ydl.prepare_filename(info)
            if options["merge_output_format"] == "mp4":
                mp4 = str(Path(path).with_suffix(".mp4"))
                if Path(mp4).exists():
                    path = mp4
            return video_id, str(path)
