"""Search public short Rutube videos by a text query."""

from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import httpx

from app.config import settings
from app.models import SearchRequest, VideoCandidate
from app.scoring import ranking_key
from app.snapshots import SnapshotStore


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
        max_age = request.max_age_hours if request.max_age_hours is not None else settings.default_max_age_hours
        min_views = request.min_views if request.min_views is not None else settings.default_min_views
        min_views_per_hour = request.min_views_per_hour if request.min_views_per_hour is not None else settings.default_min_views_per_hour
        min_like_rate = request.min_like_rate if request.min_like_rate is not None else settings.default_min_like_rate
        max_duration = request.max_duration_seconds if request.max_duration_seconds is not None else settings.default_max_duration_seconds
        now = datetime.now(timezone.utc)
        found = 0
        candidates: list[VideoCandidate] = []
        snapshots: SnapshotStore | None = None
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
                    if not video_id or not duration or duration > max_duration or published_at is None:
                        continue
                    age_hours = max((now - published_at).total_seconds() / 3600, 1.0)
                    views_per_hour = views / age_hours if views is not None else None
                    if views is None:
                        continue
                    if snapshots is None:
                        snapshots = SnapshotStore(settings.data_dir / "snapshots.sqlite3")
                    growth = snapshots.observe_views(f"rutube:{video_id}", views=views)
                    if age_hours > max_age or views < min_views:
                        continue
                    if views_per_hour is None or views_per_hour < min_views_per_hour:
                        continue
                    # Search does not expose likes. A positive like-rate filter
                    # cannot be proven for this result.
                    if min_like_rate > 0:
                        continue

                    author = item.get("author") or {}
                    if not isinstance(author, dict):
                        author = {}
                    candidates.append(VideoCandidate(
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
                        likes=None,
                        comments=None,
                        age_hours=round(age_hours, 2),
                        views_per_hour=round(views_per_hour, 2),
                        like_rate=None,
                        comment_rate=None,
                        previous_views=growth.previous_views,
                        growth_views_per_hour=growth.growth_views_per_hour,
                        growth_likes_per_hour=None,
                        snapshot_age_minutes=growth.snapshot_age_minutes,
                        popularity_score=None,
                    ))

                if len(candidates) >= request.limit or not payload.get("has_next"):
                    break
                next_url = _next_page(payload.get("next"))

        candidates.sort(key=ranking_key, reverse=True)
        return found, candidates[:request.limit]
