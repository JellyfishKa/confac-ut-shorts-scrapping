import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

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
