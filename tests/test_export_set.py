import asyncio
import json
from datetime import datetime, timedelta, timezone

from app.export_set import export_set, video_type
from app.models import AiVerdict, VideoCandidate
from app.tracker import TrackedStore

NOW = datetime.now(timezone.utc)


def video(video_id, title, speed=5_000):
    return VideoCandidate(
        video_id=video_id, url=f"https://www.youtube.com/shorts/{video_id}", title=title, channel_title="Канал",
        published_at=NOW - timedelta(hours=5), duration_seconds=30, views=60_000, likes=None, comments=None,
        age_hours=5, views_per_hour=speed, like_rate=None, comment_rate=None, popularity_score=None,
    )


def test_video_type_prefers_the_specific_type():
    assert video_type(video("a", "Italian Brainrot cat")) == "brainrot"
    assert video_type(video("b", "AI cat cartoon")) == "animals"
    assert video_type(video("c", "Random clip")) == "other"


def test_export_takes_the_best_ai_video_of_each_type(monkeypatch, tmp_path):
    TrackedStore().track([
        video("human", "cat clip", speed=9_000), video("slop", "cat story", speed=5_000),
        video("asmr1", "ai asmr", speed=4_000),
    ], now=NOW)

    async def check(videos):
        return {(v.platform, v.video_id): AiVerdict(ai_generated=v.video_id != "human", reason="", checked_at=NOW)
                for v in videos}

    async def brief(platform, video_id, with_story):
        return {"source": {"video_id": video_id}, "story": {"text": "сюжет"}, "story_error": None}

    monkeypatch.setattr("app.export_set.check_videos", check)
    monkeypatch.setattr("app.export_set.generation_brief", brief)
    index = asyncio.run(export_set(tmp_path / "set"))

    assert {(row["type"], row["file"]) for row in index} == {
        ("animals", "animals-slop.generation.json"), ("asmr", "asmr-asmr1.generation.json"),
    }
    saved = json.loads((tmp_path / "set" / "animals-slop.generation.json").read_text(encoding="utf-8"))
    assert saved["source"]["video_id"] == "slop"
    assert len(json.loads((tmp_path / "set" / "index.json").read_text(encoding="utf-8"))) == 2
