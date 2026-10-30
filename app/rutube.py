"""Search public short Rutube videos by a text query."""

from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import httpx

from app.models import SearchRequest, VideoCandidate
from app.pipeline import CandidateBuilder, RawVideo, rank, resolve_filters


RUTUBE_SEARCH = "https://rutube.ru/api/search/video/"
_RUTUBE_TIMEZONE = timezone(timedelta(hours=3))


def _count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return count if count >= 0 else None


def _published_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Rutube's public search response can omit the offset. Its displayed
    # publication times use Moscow local time; retain that assumption here.
    return parsed.replace(tzinfo=_RUTUBE_TIMEZONE) if parsed.tzinfo is None else parsed


def _video_url(raw_url: object, video_id: str) -> str:
    candidate = str(raw_url or "")
    parsed = urlsplit(candidate)
    if parsed.scheme == "https" and parsed.hostname in {"rutube.ru", "www.rutube.ru"} and parsed.path.startswith(("/video/", "/shorts/")):
        return f"https://rutube.ru{parsed.path}"
    return f"https://rutube.ru/video/{video_id}/"


def _next_page(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    url = urljoin(RUTUBE_SEARCH, value)
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname == "rutube.ru" and parsed.path == "/api/search/video/":
        return url
    return None


class RutubeProvider:
    async def search(self, request: SearchRequest) -> tuple[int, list[VideoCandidate]]:
        filters = resolve_filters(request)
        builder = CandidateBuilder()
        found = 0
        candidates: list[VideoCandidate] = []
        seen_ids: set[str] = set()
        next_url: str | None = RUTUBE_SEARCH
        async with httpx.AsyncClient(timeout=20) as client:
            # Search is not short-video-only, so inspect several pages to find
            # short results without crawling an unbounded number of pages.
            for page in range(5):
                if next_url is None:
                    break
                response = await client.get(next_url, params={"query": request.query} if page == 0 else None)
                response.raise_for_status()
                payload = response.json()
                items = payload.get("results", [])
                if not isinstance(items, list):
                    raise ValueError("Rutube returned an invalid search response")
                found += len(items)

                raw_videos: list[RawVideo] = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    video_id = str(item.get("id") or "").strip()
                    if video_id in seen_ids:
                        continue
                    seen_ids.add(video_id)
                    duration = _count(item.get("duration"))
                    views = _count(item.get("hits"))
                    published_at = _published_at(item.get("publication_ts") or item.get("created_ts"))
                    # Search mixes long videos in, so a short one must report its duration.
                    if not video_id or not duration or published_at is None or views is None:
                        continue
                    author = item.get("author") or {}
                    if not isinstance(author, dict):
                        author = {}
                    # Search does not expose likes or comments; they stay unknown.
                    raw_videos.append(RawVideo(
                        platform="rutube",
                        video_id=video_id,
                        url=_video_url(item.get("video_url"), video_id),
                        title=str(item.get("title") or ""),
                        description=str(item.get("description") or ""),
                        channel_id=str(author.get("id") or ""),
                        channel_title=str(author.get("name") or ""),
                        thumbnail_url=str(item["thumbnail_url"]) if item.get("thumbnail_url") else None,
                        published_at=published_at,
                        duration_seconds=duration,
                        views=views,
                    ))
                candidates.extend(builder.select(raw_videos, filters))

                if len(candidates) >= request.limit or not payload.get("has_next"):
                    break
                next_url = _next_page(payload.get("next"))

        return found, rank(candidates, request.limit)
