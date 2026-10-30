from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


SearchPlatform = Literal["youtube", "vk", "tiktok", "instagram", "rutube"]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    platforms: list[SearchPlatform] = Field(default_factory=lambda: ["youtube"], min_length=1)
    max_age_hours: int | None = Field(default=None, ge=1, le=24 * 365)
    min_views: int | None = Field(default=None, ge=0)
    min_views_per_hour: float | None = Field(default=None, ge=0)
    min_like_rate: float | None = Field(default=None, ge=0, le=1)
    max_duration_seconds: int | None = Field(default=None, ge=1, le=600)
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("platforms")
    @classmethod
    def unique_platforms(cls, platforms: list[SearchPlatform]) -> list[SearchPlatform]:
        return list(dict.fromkeys(platforms))


class VideoCandidate(BaseModel):
    video_id: str
    platform: str = "youtube"
    url: str
    title: str
    description: str = ""
    channel_id: str = ""
    channel_title: str
    thumbnail_url: str | None = None
    published_at: datetime | None
    duration_seconds: int | None
    views: int | None
    likes: int | None
    comments: int | None
    subscribers: int | None = None
    age_hours: float | None
    views_per_hour: float | None
    like_rate: float | None
    comment_rate: float | None
    breakout_ratio: float | None = None
    previous_views: int | None = None
    growth_views_per_hour: float | None = None
    growth_likes_per_hour: float | None = None
    snapshot_age_minutes: float | None = None
    popularity_score: float | None


class SearchResponse(BaseModel):
    query: str
    found: int
    returned: int
    videos: list[VideoCandidate]
    platform_statuses: list["PlatformSearchStatus"] = Field(default_factory=list)


class PlatformSearchStatus(BaseModel):
    platform: SearchPlatform
    status: Literal["ok", "unavailable", "error"]
    found: int = 0
    returned: int = 0
    detail: str | None = None


class ImportRequest(BaseModel):
    url: HttpUrl


class ImportResponse(BaseModel):
    video: VideoCandidate


class DownloadRequest(BaseModel):
    url: HttpUrl


class DownloadResponse(BaseModel):
    status: Annotated[str, Field(pattern="^(completed|failed)$")]
    video_id: str | None = None
    path: str | None = None
    error: str | None = None


class StoryRequest(BaseModel):
    url: HttpUrl
    local_video_path: str | None = None


class StoryResponse(BaseModel):
    url: str
    model: str
    story: str


class ComfyManifestRequest(BaseModel):
    video: VideoCandidate
    local_video_path: str | None = None


class ComfyManifestResponse(BaseModel):
    manifest: dict[str, Any]


class SnapshotPoint(BaseModel):
    captured_at: datetime
    views: int


class TrackedVideo(BaseModel):
    video: VideoCandidate
    pinned: bool
    tracked_until: datetime
    last_checked_at: datetime | None
    last_error: str | None
    history: list[SnapshotPoint]


class TrackedResponse(BaseModel):
    interval_minutes: float
    tracked_hours: float
    videos: list[TrackedVideo]


class HypePick(BaseModel):
    video: VideoCandidate
    hype_score: float
    acceleration: float | None
    reasons: list[str]


class HypeResponse(BaseModel):
    considered: int
    picks: list[HypePick]


class PinRequest(BaseModel):
    platform: SearchPlatform
    video_id: str = Field(min_length=1, max_length=200)
    pinned: bool


class TranscriptSegment(BaseModel):
    start_seconds: float = Field(ge=0, le=24 * 3600)
    end_seconds: float = Field(gt=0, le=24 * 3600)
    speaker: str | None = Field(default=None, max_length=100)
    text: str = Field(max_length=2000)

    @field_validator("text")
    @classmethod
    def non_empty_text(cls, text: str) -> str:
        text = text.strip()
        if not text:
            raise ValueError("Реплика не может быть пустой.")
        return text

    @model_validator(mode="after")
    def ends_after_start(self) -> "TranscriptSegment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Конец реплики должен быть позже начала.")
        return self


class TranscriptRequest(BaseModel):
    url: HttpUrl
    local_video_path: str = Field(min_length=1)


class Transcript(BaseModel):
    """Speech with timestamps; a draft to review before quoting or publishing."""
    source_url: str
    language: str | None
    model: str
    segments: list[TranscriptSegment]


class SrtRequest(TranscriptRequest):
    segments: list[TranscriptSegment] = Field(min_length=1, max_length=5000)


class SrtResponse(BaseModel):
    path: str
    srt: str
