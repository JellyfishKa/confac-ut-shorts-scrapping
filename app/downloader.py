from pathlib import Path

from yt_dlp import YoutubeDL

from app.config import settings


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
