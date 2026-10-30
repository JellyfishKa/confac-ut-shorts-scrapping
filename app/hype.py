"""Pick the few fastest-rising videos out of everything the tracker has seen.

Each signal is turned into a rank within the current pool, so platforms with
very different view counts can be compared and one outlier cannot dominate.
A signal a video does not have gives its weight to the remaining ones.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Callable
from datetime import timedelta

from app.config import settings
from app.models import HypePick, HypeResponse, SnapshotPoint, TrackedVideo

# How many leaders the AI check looks at to fill the requested picks after dropping human-made videos.
AI_CHECK_POOL = 15

WEIGHTS: dict[str, float] = {
    "speed": 0.40,
    "acceleration": 0.25,
    "engagement": 0.15,
    "breakout": 0.10,
    "freshness": 0.10,
}


def acceleration(history: list[SnapshotPoint]) -> float | None:
    """Growth over the latest interval divided by growth over the interval before it.

    Intervals are at least GROWTH_MIN_INTERVAL_MINUTES long, as for Δ views/h.
    """
    window = timedelta(minutes=settings.growth_min_interval_minutes)

    def baseline(index: int) -> int | None:
        for earlier in range(index - 1, -1, -1):
            if history[index].captured_at - history[earlier].captured_at >= window:
                return earlier
        return None

    def rate(later: int, earlier: int) -> float:
        hours = (history[later].captured_at - history[earlier].captured_at).total_seconds() / 3600
        # A zero growth window can pair snapshots taken at the same moment.
        return (history[later].views - history[earlier].views) / hours if hours > 0 else 0.0

    latest = len(history) - 1
    middle = baseline(latest) if latest > 0 else None
    start = baseline(middle) if middle is not None else None
    if start is None:
        return None
    previous = rate(middle, start)
    if previous <= 0:
        return None
    return round(rate(latest, middle) / previous, 2)


def pick_hype(tracked: list[TrackedVideo], limit: int, platform: str | None = None) -> HypeResponse:
    """With a platform, videos are ranked against that platform's videos only."""
    pool = [
        item for item in tracked
        if _is_candidate(item) and (platform is None or item.video.platform == platform)
    ]
    accelerations = {id(item): acceleration(item.history) for item in pool}
    signals: dict[str, Callable[[TrackedVideo], float | None]] = {
        "speed": _speed,
        "acceleration": lambda item: accelerations[id(item)],
        "engagement": lambda item: item.video.like_rate,
        "breakout": lambda item: item.video.breakout_ratio,
        "freshness": lambda item: -item.video.age_hours if item.video.age_hours is not None else None,
    }
    ranks = {name: _ranks(pool, signal) for name, signal in signals.items()}

    picks = []
    for item in pool:
        present = [name for name in WEIGHTS if id(item) in ranks[name]]
        total = sum(WEIGHTS[name] for name in present)
        score = sum(WEIGHTS[name] * ranks[name][id(item)] for name in present) / total
        picks.append(HypePick(
            video=item.video,
            hype_score=round(score, 4),
            acceleration=accelerations[id(item)],
            reasons=_reasons(item, accelerations[id(item)]),
        ))
    picks.sort(key=lambda pick: pick.hype_score, reverse=True)
    # Slop is re-uploaded under the same title; show each video once, its best copy.
    unique = {}
    for pick in picks:
        unique.setdefault(" ".join(pick.video.title.lower().split()) or pick.video.video_id, pick)
    return HypeResponse(considered=len(pool), picks=list(unique.values())[:limit])


def _speed(item: TrackedVideo) -> float | None:
    video = item.video
    return video.growth_views_per_hour if video.growth_views_per_hour is not None else video.views_per_hour


def _is_candidate(item: TrackedVideo) -> bool:
    """Junk filter: too few views, or not gaining views right now."""
    speed = _speed(item)
    views = item.video.views
    return views is not None and views >= settings.hype_min_views and speed is not None and speed > 0


def _ranks(pool: list[TrackedVideo], signal: Callable[[TrackedVideo], float | None]) -> dict[int, float]:
    """Mid-rank of each known value within the pool, from 0 to 1."""
    values = {id(item): value for item in pool if (value := signal(item)) is not None}
    ordered = sorted(values.values())
    count = len(ordered)
    return {
        key: (bisect_left(ordered, value) + bisect_right(ordered, value)) / (2 * count)
        for key, value in values.items()
    }


def _reasons(item: TrackedVideo, accel: float | None) -> list[str]:
    video = item.video
    reasons = []
    if video.growth_views_per_hour is not None:
        reasons.append(f"сейчас +{_compact(video.growth_views_per_hour)} просмотров/ч")
    elif video.views_per_hour is not None:
        reasons.append(f"в среднем {_compact(video.views_per_hour)} просмотров/ч")
    if accel is not None and accel >= 1.2:
        # Batched counter updates make huge ratios noise rather than signal.
        reasons.append(f"ускоряется ×{accel:.1f}" if accel < 10 else "ускоряется ×10+")
    if video.breakout_ratio is not None and video.breakout_ratio >= 2:
        times = round(video.breakout_ratio)
        word = "раза" if times % 10 in (2, 3, 4) and times % 100 not in (12, 13, 14) else "раз"
        reasons.append(f"просмотров в {times} {word} больше, чем подписчиков")
    if video.like_rate is not None and video.like_rate >= 0.04:
        reasons.append(f"лайков {video.like_rate:.1%}")
    if video.age_hours is not None and video.age_hours <= 24:
        reasons.append(f"опубликован {video.age_hours:.0f} ч назад")
    return reasons


def _compact(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f} млн"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f} тыс."
    return f"{value:.0f}" if abs(value) >= 10 else f"{value:.1f}"
