"""Public TikTok keyword discovery through the approved Research API.

Official API: https://developers.tiktok.com/docs/en/research-api-specs-query-videos
Client credentials: https://developers.tiktok.com/docs/en/client-access-token-management
"""

import hashlib
import time
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.models import SearchRequest, VideoCandidate
from app.scoring import PublicStats, derive_stats, passes_filter
from app.search_errors import ProviderUnavailableError


_API = "https://open.tiktokapis.com/v2"
_FIELDS = "id,video_description,create_time,username,view_count,like_count,comment_count,video_duration"
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


class TikTokResearchProvider:
    def __init__(self, *, client_key: str | None = None, client_secret: str | None = None,
                 token: str | None = None):
        self.client_key = client_key if client_key is not None else settings.tiktok_client_key
        self.client_secret = client_secret if client_secret is not None else settings.tiktok_client_secret
        self.static_token = token if token is not None else settings.tiktok_research_token
        if not ((self.client_key and self.client_secret) or self.static_token):
            raise ProviderUnavailableError(
                "TikTok Research API не настроен: нужны TIKTOK_CLIENT_KEY и "
                "TIKTOK_CLIENT_SECRET от одобренного Research-проекта с research.data.basic."
            )

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        if self.client_key and self.client_secret:
            cache_key = hashlib.sha256(f"{self.client_key}:{self.client_secret}".encode()).hexdigest()
            cached = _TOKEN_CACHE.get(cache_key)
            if cached and cached[1] > time.monotonic():
                return cached[0]
            try:
                response = await client.post(
                    f"{_API}/oauth/token/",
                    data={"client_key": self.client_key, "client_secret": self.client_secret,
                          "grant_type": "client_credentials"},
                )
                response.raise_for_status()
                body = response.json()
                token = body.get("access_token")
                if not isinstance(token, str) or not token:
                    raise ValueError("No client token")
                expires = max(int(body.get("expires_in", 7200)) - 60, 1)
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise RuntimeError("TikTok не выдал клиентский токен. Проверьте одобрение Research API и ключи.") from exc
            _TOKEN_CACHE[cache_key] = (token, time.monotonic() + expires)
            return token
        return self.static_token

    async def search(self, request: SearchRequest) -> tuple[int, list[VideoCandidate]]:
        max_age = request.max_age_hours if request.max_age_hours is not None else settings.default_max_age_hours
        if max_age > 30 * 24:
            raise ProviderUnavailableError("TikTok Research API допускает окно запроса до 30 дней; уменьшите период до 720 часов.")
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=max_age)
        body = {
            "query": {"and": [{"operation": "EQ", "field_name": "keyword", "field_values": [request.query]}]},
            "start_date": start.strftime("%Y%m%d"),
            "end_date": now.strftime("%Y%m%d"),
            "max_count": min(settings.max_results, 100),
            "is_random": False,
        }
        raw_videos: list[dict] = []
        seen_ids: set[str] = set()
        async with httpx.AsyncClient(timeout=20) as client:
            token = await self._get_token(client)
            for _ in range(3):
                try:
                    response = await client.post(
                        f"{_API}/research/video/query/",
                        params={"fields": _FIELDS},
                        json=body,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    error = payload.get("error") or {}
                    if error.get("code") not in (None, "ok"):
                        raise RuntimeError("TikTok Research API отклонил поиск. Проверьте доступ research.data.basic или лимиты API.")
                    data = payload.get("data") or {}
                    for raw in data.get("videos") or []:
                        video_id = str(raw.get("id") or "")
                        if video_id and video_id not in seen_ids:
                            raw_videos.append(raw)
                            seen_ids.add(video_id)
                    cursor, search_id = data.get("cursor"), data.get("search_id")
                    if not data.get("has_more") or not isinstance(cursor, int) or not search_id:
                        break
                    body["cursor"] = cursor
                    body["search_id"] = search_id
                except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
                    raise RuntimeError("TikTok Research API отклонил поиск. Проверьте доступ research.data.basic или повторите позже.") from exc
        candidates: list[VideoCandidate] = []
        for raw in raw_videos:
            try:
                video_id = str(raw["id"])
                published = datetime.fromtimestamp(int(raw["create_time"]), timezone.utc)
                public = PublicStats(
                    views=max(int(raw["view_count"]), 0),
                    likes=max(int(raw["like_count"]), 0),
                    comments=max(int(raw["comment_count"]), 0),
                    published_at=published,
                    duration_seconds=max(int(raw["video_duration"]), 0),
                )
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                continue
            derived = derive_stats(public, now=now)
            if not passes_filter(
                public, derived, max_age_hours=max_age,
                min_views=request.min_views if request.min_views is not None else settings.default_min_views,
                min_views_per_hour=request.min_views_per_hour if request.min_views_per_hour is not None else settings.default_min_views_per_hour,
                min_like_rate=request.min_like_rate if request.min_like_rate is not None else settings.default_min_like_rate,
                max_duration_seconds=request.max_duration_seconds if request.max_duration_seconds is not None else settings.default_max_duration_seconds,
            ):
                continue
            username = str(raw.get("username") or "").lstrip("@")
            if not username:
                continue
            description = str(raw.get("video_description") or "")
            candidates.append(VideoCandidate(
                platform="tiktok", video_id=video_id,
                url=f"https://www.tiktok.com/@{username}/video/{video_id}",
                title=description[:120] or f"TikTok {video_id}", description=description,
                channel_id=username, channel_title=username or "TikTok",
                published_at=published, duration_seconds=public.duration_seconds,
                views=public.views, likes=public.likes, comments=public.comments,
                age_hours=derived.age_hours, views_per_hour=derived.views_per_hour,
                like_rate=derived.like_rate, comment_rate=derived.comment_rate,
                popularity_score=derived.popularity_score,
            ))
        candidates.sort(key=lambda video: video.popularity_score or 0, reverse=True)
        return len(raw_videos), candidates[:request.limit]
