from datetime import datetime
from typing import Annotated

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
    channel_title: str
    published_at: datetime
    duration_seconds: int
    views: int
    likes: int
    comments: int
    age_hours: float
    views_per_hour: float
    like_rate: float
    comment_rate: float
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
