from datetime import datetime, timezone

from app.models import VideoCandidate


def build_comfy_manifest(video: VideoCandidate, local_video_path: str | None = None) -> dict:
    """Stable handoff contract for a future ComfyUI workflow adapter."""
    return {
        "schema_version": "0.2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "platform": "youtube",
            "video_id": video.video_id,
            "url": video.url,
            "local_video_path": local_video_path,
            "title": video.title,
            "description": video.description,
            "channel_id": video.channel_id,
            "channel_title": video.channel_title,
            "thumbnail_url": video.thumbnail_url,
            "published_at": video.published_at.isoformat(),
            "duration_seconds": video.duration_seconds,
        },
        "virality": {
            "views": video.views,
            "likes": video.likes,
            "comments": video.comments,
            "subscribers": video.subscribers,
            "age_hours": video.age_hours,
            "views_per_hour": video.views_per_hour,
            "like_rate": video.like_rate,
            "comment_rate": video.comment_rate,
            "breakout_ratio": video.breakout_ratio,
            "previous_views": video.previous_views,
            "growth_views_per_hour": video.growth_views_per_hour,
            "growth_likes_per_hour": video.growth_likes_per_hour,
            "snapshot_age_minutes": video.snapshot_age_minutes,
            "popularity_score": video.popularity_score,
        },
        "routing": {
            "metadata_only": local_video_path is None,
            "download_required_for_video_analysis": local_video_path is None,
            "recommended_next_stage": (
                "comfyui_metadata_experiment"
                if local_video_path is None
                else "comfyui_video_analysis_or_generation"
            ),
        },
        "comfyui_inputs": {
            "source_url": video.url,
            "source_video_path": local_video_path,
            "source_title": video.title,
            "source_description": video.description,
            "virality_score": video.popularity_score,
            "views_per_hour": video.views_per_hour,
            "growth_views_per_hour": video.growth_views_per_hour,
            "like_rate_pct": round(video.like_rate * 100, 3),
            "comment_rate_pct": round(video.comment_rate * 100, 3),
            "breakout_ratio": video.breakout_ratio,
        },
    }
