from __future__ import annotations

import os
import sqlite3
from typing import Iterable


class ReminderStore:
    """提醒数据的 SQLite 持久化层。"""

    def __init__(self, db_path: str):
        self._in_memory = db_path == ":memory:"
        self._use_uri = db_path.startswith("file:")
        if self._in_memory or self._use_uri:
            self.db_path = db_path
        else:
            self.db_path = os.path.abspath(db_path)
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._memory_conn = self._open_connection() if self._in_memory else None
        self.initialize()

    def initialize(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT,
                    title TEXT NOT NULL,
                    location TEXT,
                    details TEXT,
                    event_time TEXT NOT NULL,
                    remind_before_minutes INTEGER NOT NULL,
                    remind_time TEXT NOT NULL,
                    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sent_at TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    raw_user_text TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(status, remind_time);
                CREATE INDEX IF NOT EXISTS idx_reminders_user
                    ON reminders(user_id, status, event_time);
                """
            )

    def create_reminder(self, payload: dict) -> dict:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reminders (
                    user_id, conversation_id, title, location, details,
                    event_time, remind_before_minutes, remind_time, timezone,
                    status, created_at, updated_at, raw_user_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["user_id"],
                    payload.get("conversation_id"),
                    payload["title"],
                    payload.get("location"),
                    payload.get("details"),
                    payload["event_time"],
                    payload["remind_before_minutes"],
                    payload["remind_time"],
                    payload.get("timezone", "Asia/Shanghai"),
                    payload.get("status", "pending"),
                    payload["created_at"],
                    payload["updated_at"],
                    payload.get("raw_user_text"),
                ),
            )
            reminder_id = cursor.lastrowid
            return self.get_reminder(reminder_id, conn=conn)

    def get_reminder(self, reminder_id: int, conn: sqlite3.Connection | None = None) -> dict | None:
        own_conn = conn is None
        conn = conn or self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            if own_conn and not self._in_memory:
                conn.close()

    def list_reminders_for_user(
        self,
        user_id: str,
        statuses: Iterable[str] = ("pending", "sending"),
    ) -> list[dict]:
        placeholders = ",".join("?" for _ in statuses)
        params = [user_id, *statuses]
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM reminders
                WHERE user_id = ?
                  AND status IN ({placeholders})
                ORDER BY event_time ASC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def cancel_reminder(self, reminder_id: int, user_id: str, now_iso: str) -> dict | None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE reminders
                SET status = 'canceled', updated_at = ?
                WHERE id = ?
                  AND user_id = ?
                  AND status IN ('pending', 'sending')
                """,
                (now_iso, reminder_id, user_id),
            )
            return self.get_reminder(reminder_id, conn=conn)

    def update_reminder(self, reminder_id: int, user_id: str, fields: dict, now_iso: str) -> dict | None:
        if not fields:
            return self.get_reminder(reminder_id)

        assignments = [f"{key} = ?" for key in fields]
        values = list(fields.values())
        assignments.append("updated_at = ?")
        values.append(now_iso)
        values.extend([reminder_id, user_id])

        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE reminders
                SET {", ".join(assignments)}
                WHERE id = ?
                  AND user_id = ?
                  AND status IN ('pending', 'sending')
                """,
                values,
            )
            return self.get_reminder(reminder_id, conn=conn)

    def requeue_stale_sending(self, now_iso: str, stale_before_iso: str):
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE reminders
                SET status = 'pending', updated_at = ?
                WHERE status = 'sending'
                  AND updated_at < ?
                """,
                (now_iso, stale_before_iso),
            )

    def claim_due_reminders(self, now_iso: str, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM reminders
                WHERE status = 'pending'
                  AND remind_time <= ?
                ORDER BY remind_time ASC
                LIMIT ?
                """,
                (now_iso, limit),
            ).fetchall()
            reminder_ids = [row["id"] for row in rows]
            if not reminder_ids:
                return []

            placeholders = ",".join("?" for _ in reminder_ids)
            conn.execute(
                f"""
                UPDATE reminders
                SET status = 'sending', updated_at = ?
                WHERE id IN ({placeholders})
                  AND status = 'pending'
                """,
                [now_iso, *reminder_ids],
            )
            claimed = conn.execute(
                f"""
                SELECT * FROM reminders
                WHERE id IN ({placeholders})
                  AND status = 'sending'
                ORDER BY remind_time ASC
                """,
                reminder_ids,
            ).fetchall()
            return [dict(row) for row in claimed]

    def mark_sent(self, reminder_id: int, sent_at_iso: str):
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE reminders
                SET status = 'sent', sent_at = ?, updated_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (sent_at_iso, sent_at_iso, reminder_id),
            )

    def release_after_failure(
        self,
        reminder_id: int,
        now_iso: str,
        error_message: str,
        max_retries: int,
    ):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT retry_count FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
            if row is None:
                return

            retry_count = int(row["retry_count"]) + 1
            next_status = "failed" if retry_count >= max_retries else "pending"
            conn.execute(
                """
                UPDATE reminders
                SET status = ?, retry_count = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_status, retry_count, error_message[:500], now_iso, reminder_id),
            )

    def _connect(self) -> sqlite3.Connection:
        if self._in_memory:
            return self._memory_conn
        return self._open_connection()

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=30,
            check_same_thread=False,
            uri=self._use_uri,
        )
        conn.row_factory = sqlite3.Row
        return conn
