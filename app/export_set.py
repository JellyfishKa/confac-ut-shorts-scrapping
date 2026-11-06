"""Export a test set of generation JSONs: the best verified AI video of each type.

Run from the project root with `python -m app.export_set`; files go to exports/test-set.
"""

import asyncio
import json
from pathlib import Path

from app.ai_check import check_videos
from app.hype import pick_hype
from app.main import generation_brief
from app.models import VideoCandidate
from app.tracker import TrackedStore

OUTPUT_DIR = Path("exports/test-set")
TRIES_PER_TYPE = 5
# The first matching type wins, so more specific types come first.
VIDEO_TYPES = {
    "brainrot": ("brainrot", "брейнрот", "tung tung", "tralalero", "bombardiro", "ballerina cappuccina"),
    "asmr": ("asmr", "асмр", "satisfying", "satisfy"),
    "baby": ("baby", "младен", "малыш"),
    "animals": ("cat", "кот", "dog", "собак", "animal", "животн"),
    "food": ("fruit", "food", "фрукт", "овощ", "еда"),
    "story": ("love", "story", "drama", "драма", "истори", "emotional"),
    "animation": ("animation", "cartoon", "анимац", "мульт"),
}


def video_type(video: VideoCandidate) -> str:
    text = f"{video.title} {video.description[:300]}".lower()
    for name, words in VIDEO_TYPES.items():
        if any(word in text for word in words):
            return name
    return "other"


async def export_set(output_dir: Path = OUTPUT_DIR) -> list[dict]:
    """Write one JSON per type, skipping videos Gemini judged human-made; returns the index."""
    by_type: dict[str, list[VideoCandidate]] = {}
    for pick in pick_hype(TrackedStore().active(), 10_000, platform="youtube").picks:
        by_type.setdefault(video_type(pick.video), []).append(pick.video)

    output_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for name, videos in by_type.items():
        candidates = videos[:TRIES_PER_TYPE]
        verdicts = await check_videos(candidates)
        for video in candidates:
            verdict = verdicts[(video.platform, video.video_id)]
            if not verdict.ai_generated:
                continue
            brief = await generation_brief(video.platform, video.video_id, with_story=True)
            path = output_dir / f"{name}-{video.video_id}.generation.json"
            path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
            index.append({"type": name, "file": path.name, "url": video.url, "title": video.title,
                          "views": video.views, "story": bool(brief["story"]), "story_error": brief["story_error"]})
            break
    (output_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


if __name__ == "__main__":
    for row in asyncio.run(export_set()):
        print(f"{row['type']:10} {row['views']:>10} story={row['story']} {row['file']}")
