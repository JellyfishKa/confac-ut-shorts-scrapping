from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, HttpUrl


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    max_age_hours: int | None = Field(default=None, ge=1, le=24 * 365)
    min_views: int | None = Field(default=None, ge=0)
    min_views_per_hour: float | None = Field(default=None, ge=0)
    min_like_rate: float | None = Field(default=None, ge=0, le=1)
    max_duration_seconds: int | None = Field(default=None, ge=1, le=600)
    limit: int = Field(default=10, ge=1, le=50)


class VideoCandidate(BaseModel):
    video_id: str
    url: str
    title: str
    description: str = ""
    channel_id: str = ""
    channel_title: str
    thumbnail_url: str | None = None
    published_at: datetime
    duration_seconds: int
    views: int
    likes: int
    comments: int
    subscribers: int | None = None
    age_hours: float
    views_per_hour: float
    like_rate: float
    comment_rate: float
    breakout_ratio: float | None = None
    previous_views: int | None = None
    growth_views_per_hour: float | None = None
    growth_likes_per_hour: float | None = None
    snapshot_age_minutes: float | None = None
    popularity_score: float


class SearchResponse(BaseModel):
    query: str
    found: int
    returned: int
    videos: list[VideoCandidate]


class DownloadRequest(BaseModel):
    url: HttpUrl


class DownloadResponse(BaseModel):
    status: Annotated[str, Field(pattern="^(completed|failed)$")]
    video_id: str | None = None
    path: str | None = None
    error: str | None = None


class ComfyManifestRequest(BaseModel):
    video: VideoCandidate
    local_video_path: str | None = None


class ComfyManifestResponse(BaseModel):
    manifest: dict[str, Any]
