"""Hashtag discovery of public Instagram Reels via the official Graph API.

Requires a Professional IG account linked to a Facebook Page and access to
hashtagged public content. There is no arbitrary free-text Reels search here.
"""

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.models import SearchRequest, VideoCandidate
from app.pipeline import CandidateBuilder, RawVideo, resolve_filters
from app.search_errors import ProviderUnavailableError


_API = "https://graph.facebook.com/v26.0"
_HASHTAG = re.compile(r"\w+", re.UNICODE)


class InstagramHashtagProvider:
    def __init__(self, *, access_token: str | None = None, user_id: str | None = None):
        self.access_token = access_token if access_token is not None else settings.instagram_access_token
        self.user_id = user_id if user_id is not None else settings.instagram_user_id
        if not self.access_token or not self.user_id:
            raise ProviderUnavailableError(
                "Instagram Graph API не настроен: нужны INSTAGRAM_ACCESS_TOKEN и "
                "INSTAGRAM_USER_ID профессионального аккаунта с доступом к публичным хештегам."
            )
        if not self.user_id.isdigit():
            raise ProviderUnavailableError("INSTAGRAM_USER_ID должен быть числовым ID профессионального аккаунта.")

    async def search(self, request: SearchRequest) -> tuple[int, list[VideoCandidate]]:
        hashtag = request.query.strip().removeprefix("#")
        if not _HASHTAG.fullmatch(hashtag):
            raise ProviderUnavailableError("Instagram Graph API ищет один хештег. Введите одно слово, например #рецепты.")
        if (request.min_views or 0) > 0 or (request.min_views_per_hour or 0) > 0 or (request.min_like_rate or 0) > 0:
            raise ProviderUnavailableError("Instagram не возвращает метрики для чужих Reels в поиске по хештегу; уберите фильтры по просмотрам и лайкам.")
        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                hashtag_response = await client.get(
                    f"{_API}/ig_hashtag_search",
                    params={"user_id": self.user_id, "q": hashtag}, headers=headers,
                )
                hashtag_response.raise_for_status()
                matching = hashtag_response.json().get("data") or []
                if not matching:
                    return 0, []
                hashtag_id = str(matching[0]["id"])
                if not hashtag_id.isdigit():
                    raise ValueError("Invalid hashtag ID")
                raw_media = []
                after = None
                for _ in range(3):
                    params = {"user_id": self.user_id,
                              "fields": "id,caption,media_type,permalink,timestamp,thumbnail_url",
                              "limit": min(settings.max_results, 50)}
                    if after:
                        params["after"] = after
                    media_response = await client.get(
                        f"{_API}/{hashtag_id}/recent_media", params=params, headers=headers,
                    )
                    media_response.raise_for_status()
                    page = media_response.json()
                    items = page.get("data") or []
                    raw_media.extend(items)
                    after = ((page.get("paging") or {}).get("cursors") or {}).get("after")
                    if not items or not isinstance(after, str) or not after or len(after) > 200:
                        break
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                raise RuntimeError("Instagram Graph API отклонил поиск. Проверьте токен, профессиональный аккаунт и доступ к публичным хештегам.") from exc
        filters = resolve_filters(request)
        # Explicit metric filters were rejected above; defaults cannot apply to
        # metrics that hashtag search never reports.
        filters.min_views = filters.min_views_per_hour = filters.min_like_rate = 0
        reels: list[RawVideo] = []
        for raw in raw_media:
            # VIDEO includes feed videos: a /reel/ permalink is required.
            permalink = str(raw.get("permalink") or "")
            parts = urlsplit(permalink)
            if (raw.get("media_type") != "VIDEO" or parts.hostname not in {"instagram.com", "www.instagram.com"}
                    or not re.fullmatch(r"/reel/[A-Za-z0-9_-]+/?", parts.path)):
                continue
            try:
                published = datetime.fromisoformat(str(raw["timestamp"]).replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                video_id = str(raw["id"])
            except (KeyError, ValueError, TypeError):
                continue
            caption = str(raw.get("caption") or "")
            reels.append(RawVideo(
                platform="instagram", video_id=video_id,
                url=f"https://www.instagram.com{parts.path}",
                title=caption[:120] or f"Instagram Reel {video_id}", description=caption,
                channel_title="Instagram", thumbnail_url=raw.get("thumbnail_url"),
                published_at=published, duration_seconds=None, views=None,
            ))
        candidates = CandidateBuilder().select(reels, filters)
        # Without metrics, the newest reels come first.
        candidates.sort(key=lambda video: video.published_at, reverse=True)
        return len(raw_media), candidates[:request.limit]
