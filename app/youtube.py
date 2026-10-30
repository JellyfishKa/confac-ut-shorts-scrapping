import re
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.models import SearchRequest, VideoCandidate
from app.pipeline import CandidateBuilder, Counts, RawVideo, rank, resolve_filters
from app.search_errors import ProviderUnavailableError

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
_DURATION_RE = re.compile(r"PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?")


def _count(value: object) -> int | None:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


def _utc_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_duration(value: str) -> int:
    match = _DURATION_RE.fullmatch(value or "")
    if not match:
        return 0
    return int(match.group("h") or 0) * 3600 + int(match.group("m") or 0) * 60 + int(match.group("s") or 0)


class YouTubeProvider:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.youtube_api_key
        if not self.api_key:
            raise ProviderUnavailableError(
                "YOUTUBE_API_KEY is not configured. Copy .env.example to .env "
                "and paste an API key with YouTube Data API v3 enabled."
            )

    async def fetch_counts(self, video_ids: list[str]) -> dict[str, Counts]:
        """Current counters by video ID: one quota unit per 50 videos, unlike 100 per search.

        Deleted or private videos are absent from the result.
        """
        counts: dict[str, Counts] = {}
        async with httpx.AsyncClient(timeout=20) as client:
            for start in range(0, len(video_ids), 50):
                response = await client.get(
                    f"{YOUTUBE_API}/videos",
                    params={"part": "statistics", "id": ",".join(video_ids[start:start + 50]), "key": self.api_key},
                )
                response.raise_for_status()
                for item in response.json().get("items", []):
                    statistics = item.get("statistics", {})
                    counts[item["id"]] = Counts(
                        _count(statistics.get("viewCount")),
                        _count(statistics.get("likeCount")),
                        _count(statistics.get("commentCount")),
                    )
        return counts

    async def recent_uploads(self, channel_ids: list[str], published_after: datetime) -> list[str]:
        """IDs of videos the channels uploaded since `published_after`, one quota unit per channel.

        A channel's uploads playlist is its ID with "UC" replaced by "UU"; other
        IDs and channels without that playlist are skipped.
        """
        video_ids: list[str] = []
        async with httpx.AsyncClient(timeout=20) as client:
            for channel_id in channel_ids:
                if not channel_id.startswith("UC"):
                    continue
                response = await client.get(
                    f"{YOUTUBE_API}/playlistItems",
                    params={"part": "contentDetails", "playlistId": "UU" + channel_id[2:],
                            "maxResults": 10, "key": self.api_key},
                )
                if response.status_code == 404:
                    continue
                response.raise_for_status()
                for item in response.json().get("items", []):
                    details = item.get("contentDetails", {})
                    published = details.get("videoPublishedAt")
                    if details.get("videoId") and published and _utc_time(published) >= published_after:
                        video_ids.append(details["videoId"])
        return video_ids

    async def fetch_videos(self, video_ids: list[str]) -> list[RawVideo]:
        """Full metadata for known video IDs: two quota units per 50 videos."""
        parsed: list[RawVideo] = []
        async with httpx.AsyncClient(timeout=20) as client:
            for start in range(0, len(video_ids), 50):
                parsed.extend(await self._details(client, video_ids[start:start + 50]))
        return parsed

    async def search(self, request: SearchRequest) -> tuple[int, list[VideoCandidate]]:
        filters = resolve_filters(request)
        published_after = datetime.now(timezone.utc) - timedelta(hours=filters.max_age_hours)

        async with httpx.AsyncClient(timeout=20) as client:
            search_response = await client.get(
                f"{YOUTUBE_API}/search",
                params={
                    "part": "snippet",
                    "type": "video",
                    "q": request.query,
                    "order": "viewCount",
                    "videoDuration": "short",
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
            parsed = await self._details(client, video_ids)

        candidates = CandidateBuilder().select(parsed, filters)
        return len(parsed), rank(candidates, request.limit)

    async def _details(self, client: httpx.AsyncClient, video_ids: list[str]) -> list[RawVideo]:
        """Videos and their channels' subscriber counts for up to 50 IDs."""
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

        channel_ids = sorted({
            item.get("snippet", {}).get("channelId", "")
            for item in raw_videos
            if item.get("snippet", {}).get("channelId")
        })
        channel_stats: dict[str, int | None] = {}
        if channel_ids:
            channels_response = await client.get(
                f"{YOUTUBE_API}/channels",
                params={
                    "part": "statistics",
                    "id": ",".join(channel_ids[:50]),
                    "key": self.api_key,
                },
            )
            channels_response.raise_for_status()
            for channel in channels_response.json().get("items", []):
                stats = channel.get("statistics", {})
                hidden = stats.get("hiddenSubscriberCount", False)
                value = None if hidden else int(stats.get("subscriberCount", 0) or 0)
                channel_stats[channel["id"]] = value

        parsed: list[RawVideo] = []
        for item in raw_videos:
            snippet = item.get("snippet", {})
            statistics = item.get("statistics", {})
            content = item.get("contentDetails", {})
            video_id = item["id"]
            channel_id = snippet.get("channelId", "")
            thumbs = snippet.get("thumbnails", {})
            parsed.append(RawVideo(
                platform="youtube",
                video_id=video_id,
                url=f"https://www.youtube.com/shorts/{video_id}",
                title=snippet.get("title", ""),
                description=snippet.get("description", ""),
                channel_id=channel_id,
                channel_title=snippet.get("channelTitle", ""),
                thumbnail_url=(
                    thumbs.get("high", {}).get("url")
                    or thumbs.get("medium", {}).get("url")
                    or thumbs.get("default", {}).get("url")
                ),
                published_at=_utc_time(snippet["publishedAt"]),
                duration_seconds=parse_duration(content.get("duration", "")),
                # Hidden likes and disabled comments are absent from statistics: unknown, not zero.
                views=_count(statistics.get("viewCount")),
                likes=_count(statistics.get("likeCount")),
                comments=_count(statistics.get("commentCount")),
                subscribers=channel_stats.get(channel_id),
            ))
        return parsed
