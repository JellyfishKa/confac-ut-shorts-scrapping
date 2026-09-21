from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class GrowthStats:
    previous_views: int | None = None
    growth_views_per_hour: float | None = None
    growth_likes_per_hour: float | None = None
    snapshot_age_minutes: float | None = None


class SnapshotStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS video_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    views INTEGER NOT NULL,
                    likes INTEGER NOT NULL,
                    comments INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_video_snapshots_video_time "
                "ON video_snapshots(video_id, captured_at)"
            )

    def observe(
        self,
        video_id: str,
        *,
        views: int,
        likes: int,
        comments: int,
        captured_at: datetime | None = None,
    ) -> GrowthStats:
        now = captured_at or datetime.now(timezone.utc)
        now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)

        with self._connect() as conn:
            previous = conn.execute(
                """
                SELECT captured_at, views, likes
                FROM video_snapshots
                WHERE video_id = ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (video_id,),
            ).fetchone()

            conn.execute(
                """
                INSERT INTO video_snapshots(video_id, captured_at, views, likes, comments)
                VALUES (?, ?, ?, ?, ?)
                """,
                (video_id, now.isoformat(), views, likes, comments),
            )

        if not previous:
            return GrowthStats()

        previous_at = datetime.fromisoformat(previous[0])
        previous_at = previous_at if previous_at.tzinfo else previous_at.replace(tzinfo=timezone.utc)
        elapsed_hours = (now - previous_at).total_seconds() / 3600
        if elapsed_hours <= 0:
            return GrowthStats(previous_views=int(previous[1]))

        return GrowthStats(
            previous_views=int(previous[1]),
            growth_views_per_hour=round((views - int(previous[1])) / elapsed_hours, 2),
            growth_likes_per_hour=round((likes - int(previous[2])) / elapsed_hours, 2),
            snapshot_age_minutes=round(elapsed_hours * 60, 1),
        )
