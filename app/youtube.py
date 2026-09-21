import re
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.models import SearchRequest, VideoCandidate
from app.scoring import PublicStats, derive_stats, passes_filter

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
_DURATION_RE = re.compile(r"PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?")


def parse_duration(value: str) -> int:
    match = _DURATION_RE.fullmatch(value or "")
    if not match:
        return 0
    return int(match.group("h") or 0) * 3600 + int(match.group("m") or 0) * 60 + int(match.group("s") or 0)


class YouTubeProvider:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.youtube_api_key
        if not self.api_key:
            raise RuntimeError("YOUTUBE_API_KEY is not configured")

    async def search(self, request: SearchRequest) -> tuple[int, list[VideoCandidate]]:
        max_age = request.max_age_hours or settings.default_max_age_hours
        published_after = datetime.now(timezone.utc) - timedelta(hours=max_age)

        async with httpx.AsyncClient(timeout=20) as client:
            search_response = await client.get(
                f"{YOUTUBE_API}/search",
                params={
                    "part": "snippet",
                    "type": "video",
                    "q": request.query,
                    "order": "viewCount",
                    "publishedAfter": published_after.isoformat().replace("+00:00", "Z"),
                    "maxResults": min(settings.max_results, 50),
                    "key": self.api_key,
                },
            )
            search_response.raise_for_status()
            items = search_response.json().get("items", [])
            video_ids = [item.get("id", {}).get("videoId") for item in items]
            video_ids = [video_id for video_id in video_ids if video_id]
            if not video_ids:
                return 0, []

            videos_response = await client.get(
                f"{YOUTUBE_API}/videos",
                params={
                    "part": "snippet,statistics,contentDetails",
                    "id": ",".join(video_ids),
                    "key": self.api_key,
                },
            )
            videos_response.raise_for_status()
            raw_videos = videos_response.json().get("items", [])

        candidates: list[VideoCandidate] = []
        for item in raw_videos:
            snippet = item.get("snippet", {})
            statistics = item.get("statistics", {})
            content = item.get("contentDetails", {})
            published_at = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
            public = PublicStats(
                views=int(statistics.get("viewCount", 0)),
                likes=int(statistics.get("likeCount", 0)),
                comments=int(statistics.get("commentCount", 0)),
                published_at=published_at,
                duration_seconds=parse_duration(content.get("duration", "")),
            )
            derived = derive_stats(public)

            if not passes_filter(
                public,
                derived,
                max_age_hours=max_age,
                min_views=request.min_views if request.min_views is not None else settings.default_min_views,
                min_views_per_hour=request.min_views_per_hour if request.min_views_per_hour is not None else settings.default_min_views_per_hour,
                min_like_rate=request.min_like_rate if request.min_like_rate is not None else settings.default_min_like_rate,
                max_duration_seconds=request.max_duration_seconds if request.max_duration_seconds is not None else settings.default_max_duration_seconds,
            ):
                continue

            video_id = item["id"]
            candidates.append(VideoCandidate(
                video_id=video_id,
                url=f"https://www.youtube.com/shorts/{video_id}",
                title=snippet.get("title", ""),
                channel_title=snippet.get("channelTitle", ""),
                published_at=published_at,
                duration_seconds=public.duration_seconds,
                views=public.views,
                likes=public.likes,
                comments=public.comments,
                age_hours=derived.age_hours,
                views_per_hour=derived.views_per_hour,
                like_rate=derived.like_rate,
                comment_rate=derived.comment_rate,
                popularity_score=derived.popularity_score,
            ))

        candidates.sort(key=lambda video: video.popularity_score, reverse=True)
        return len(raw_videos), candidates[: request.limit]
