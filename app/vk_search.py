"""Search public VK Clips with the VK video.search API."""

from datetime import datetime, timezone

import httpx

from app.config import settings
from app.models import SearchRequest, VideoCandidate
from app.search_errors import ProviderUnavailableError
from app.snapshots import SnapshotStore


VK_SEARCH_API = "https://api.vk.com/method/video.search"
VK_API_VERSION = "5.199"


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result >= 0 else None


def _thumbnail(images: object) -> str | None:
    if not isinstance(images, list):
        return None
    available = [image for image in images if isinstance(image, dict) and isinstance(image.get("url"), str)]
    if not available:
        return None
    largest = max(available, key=lambda image: (_nonnegative_int(image.get("width")) or 0) * (_nonnegative_int(image.get("height")) or 0))
    return largest["url"]


def _authors(payload: dict) -> dict[int, str]:
    names: dict[int, str] = {}
    for profile in payload.get("profiles", []):
        if not isinstance(profile, dict) or not isinstance(profile.get("id"), int):
            continue
        names[profile["id"]] = " ".join(part for part in (profile.get("first_name"), profile.get("last_name")) if isinstance(part, str))
    for group in payload.get("groups", []):
        if isinstance(group, dict) and isinstance(group.get("id"), int):
            names[-group["id"]] = str(group.get("name") or "")
    return names


class VkProvider:
    def __init__(self, access_token: str | None = None):
        self.access_token = access_token or settings.vk_access_token
        if not self.access_token:
            raise ProviderUnavailableError("VK_ACCESS_TOKEN не задан. Добавьте бесплатный токен VK API в .env.")

    async def search(self, request: SearchRequest) -> tuple[int, list[VideoCandidate]]:
        max_age = request.max_age_hours if request.max_age_hours is not None else settings.default_max_age_hours
        min_views = request.min_views if request.min_views is not None else settings.default_min_views
        min_views_per_hour = request.min_views_per_hour if request.min_views_per_hour is not None else settings.default_min_views_per_hour
        min_like_rate = request.min_like_rate if request.min_like_rate is not None else settings.default_min_like_rate
        max_duration = request.max_duration_seconds if request.max_duration_seconds is not None else settings.default_max_duration_seconds
        page_size = min(max(settings.max_results, request.limit), 100)
        snapshots = SnapshotStore(settings.data_dir / "snapshots.sqlite3")
        now = datetime.now(timezone.utc)
        candidates: list[VideoCandidate] = []
        seen: set[str] = set()
        found = 0

        async with httpx.AsyncClient(timeout=20) as client:
            for page in range(3):
                try:
                    response = await client.post(VK_SEARCH_API, data={
                        "access_token": self.access_token,
                        "v": VK_API_VERSION,
                        "q": request.query,
                        "count": page_size,
                        "offset": page * page_size,
                        "sort": 0,  # newest first; rank by public view velocity below
                        "shorter": max_duration,
                        "filters": "short",
                        "extended": 1,
                    })
                    response.raise_for_status()
                    payload = response.json()
                except httpx.HTTPStatusError as exc:
                    raise RuntimeError(f"VK API ответил HTTP {exc.response.status_code}.") from None
                except httpx.RequestError:
                    raise RuntimeError("VK API сейчас недоступен по сети.") from None
                except ValueError:
                    raise RuntimeError("VK API вернул некорректный ответ.") from None

                if not isinstance(payload, dict):
                    raise RuntimeError("VK API вернул некорректный ответ.")
                error = payload.get("error")
                if isinstance(error, dict):
                    code = _nonnegative_int(error.get("error_code"))
                    # VK can echo request parameters, including the token, in error details.
                    raise RuntimeError(f"VK API отклонил поиск (код {code if code is not None else 'неизвестен'}).")
                result = payload.get("response")
                if not isinstance(result, dict) or not isinstance(result.get("items"), list):
                    raise RuntimeError("VK API вернул некорректный ответ.")

                authors = _authors(result)
                items = result["items"]
                for item in items:
                    if not isinstance(item, dict) or item.get("type") != "short_video":
                        continue
                    found += 1
                    owner_id = item.get("owner_id")
                    item_id = item.get("id")
                    if not isinstance(owner_id, int) or not isinstance(item_id, int):
                        continue
                    video_id = f"{owner_id}_{item_id}"
                    if video_id in seen:
                        continue
                    seen.add(video_id)

                    duration = _nonnegative_int(item.get("duration"))
                    views = _nonnegative_int(item.get("views"))
                    timestamp = _nonnegative_int(item.get("date"))
                    if not duration or duration > max_duration or views is None or timestamp is None:
                        continue
                    try:
                        published_at = datetime.fromtimestamp(timestamp, timezone.utc)
                    except (OverflowError, OSError, ValueError):
                        continue
                    age_hours = max((now - published_at).total_seconds() / 3600, 1.0)
                    views_per_hour = views / age_hours
                    if age_hours > max_age or views < min_views or views_per_hour < min_views_per_hour:
                        continue

                    raw_likes = item.get("likes")
                    likes = _nonnegative_int(raw_likes.get("count")) if isinstance(raw_likes, dict) else None
                    if min_like_rate > 0 and (likes is None or likes / max(views, 1) < min_like_rate):
                        continue
                    comments = _nonnegative_int(item.get("comments"))
                    growth = (
                        snapshots.observe(f"vk:{video_id}", views=views, likes=likes, comments=comments)
                        if likes is not None and comments is not None else None
                    )
                    candidates.append(VideoCandidate(
                        platform="vk",
                        video_id=video_id,
                        url=f"https://vk.com/clip{video_id}",
                        title=str(item.get("title") or ""),
                        description=str(item.get("description") or ""),
                        channel_id=str(owner_id),
                        channel_title=authors.get(owner_id, str(owner_id)),
                        thumbnail_url=_thumbnail(item.get("image")),
                        published_at=published_at,
                        duration_seconds=duration,
                        views=views,
                        likes=likes,
                        comments=comments,
                        age_hours=round(age_hours, 2),
                        views_per_hour=round(views_per_hour, 2),
                        like_rate=round(likes / max(views, 1), 6) if likes is not None else None,
                        comment_rate=round(comments / max(views, 1), 6) if comments is not None else None,
                        previous_views=growth.previous_views if growth else None,
                        growth_views_per_hour=growth.growth_views_per_hour if growth else None,
                        growth_likes_per_hour=growth.growth_likes_per_hour if growth else None,
                        snapshot_age_minutes=growth.snapshot_age_minutes if growth else None,
                        popularity_score=None,
                    ))

                if len(candidates) >= request.limit or len(items) < page_size:
                    break

        candidates.sort(key=lambda video: (video.growth_views_per_hour is not None, video.growth_views_per_hour or 0, video.views_per_hour or 0), reverse=True)
        return found, candidates[:request.limit]
