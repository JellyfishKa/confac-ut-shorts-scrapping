from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings


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

    @staticmethod
    def _baseline_cutoff(now: datetime) -> str:
        # Stored timestamps are UTC ISO strings, so string comparison follows time order.
        return (now - timedelta(minutes=settings.growth_min_interval_minutes)).isoformat()

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS view_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    views INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_view_snapshots_video_time "
                "ON view_snapshots(video_id, captured_at)"
            )

    def history(self, video_id: str, limit: int = 96) -> list[tuple[datetime, int]]:
        """Recent views over time from both snapshot tables, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT captured_at, views FROM (
                    SELECT captured_at, views FROM video_snapshots WHERE video_id = ?
                    UNION ALL
                    SELECT captured_at, views FROM view_snapshots WHERE video_id = ?
                )
                ORDER BY captured_at DESC
                LIMIT ?
                """,
                (video_id, video_id, limit),
            ).fetchall()
        return [(datetime.fromisoformat(captured_at), int(views)) for captured_at, views in reversed(rows)]

    def observe_views(
        self,
        video_id: str,
        *,
        views: int,
        captured_at: datetime | None = None,
    ) -> GrowthStats:
        """Track view growth when a platform does not publish likes/comments."""
        now = captured_at or datetime.now(timezone.utc)
        now = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)

        with self._connect() as conn:
            previous = conn.execute(
                """
                SELECT captured_at, views
                FROM view_snapshots
                WHERE video_id = ? AND captured_at <= ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (video_id, self._baseline_cutoff(now)),
            ).fetchone()
            conn.execute(
                "INSERT INTO view_snapshots(video_id, captured_at, views) VALUES (?, ?, ?)",
                (video_id, now.isoformat(), views),
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
            snapshot_age_minutes=round(elapsed_hours * 60, 1),
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
        now = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)

        with self._connect() as conn:
            previous = conn.execute(
                """
                SELECT captured_at, views, likes
                FROM video_snapshots
                WHERE video_id = ? AND captured_at <= ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (video_id, self._baseline_cutoff(now)),
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
