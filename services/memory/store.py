"""
长期记忆与课表的 SQLite 存储层。

memory_facts 表以 key/value 保存用户画像，courses 表保存课表。与其它模块共用同一个
SQLite 文件（默认 data/agent.db），各自建表互不干扰；时间统一使用北京时间字符串。
"""

from __future__ import annotations

import json
import os
import sqlite3

from utils.time_parser import now_shanghai, to_storage_string


class MemoryStore:
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, key)
                );
                CREATE TABLE IF NOT EXISTS courses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    weekday INTEGER NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    location TEXT,
                    teacher TEXT,
                    weeks TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_courses_user_day
                    ON courses(user_id, weekday);
                """
            )

    # ---------------- 长期记忆 (key/value) ----------------
    def remember(self, user_id: str, key: str, value: str) -> dict:
        key = (key or "").strip()
        value = (value or "").strip()
        if not key or not value:
            return {"ok": False, "error": "记忆的 key 和 value 都不能为空。"}
        now_iso = to_storage_string(now_shanghai())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_facts (user_id, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (user_id, key, value, now_iso),
            )
        return {"ok": True, "message": f"已记住：{key} = {value}", "key": key, "value": value}

    def forget(self, user_id: str, key: str) -> dict:
        key = (key or "").strip()
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM memory_facts WHERE user_id = ? AND key = ?",
                (user_id, key),
            )
        if cur.rowcount == 0:
            return {"ok": False, "error": f"没有找到关于「{key}」的记忆。"}
        return {"ok": True, "message": f"已忘记：{key}"}

    def get_profile(self, user_id: str) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM memory_facts WHERE user_id = ? ORDER BY key",
                (user_id,),
            ).fetchall()
        return {row["key"]: row["value"] for row in rows}

    def profile_as_text(self, user_id: str) -> str:
        """将用户画像拼成单行文本，用于注入 system prompt。"""
        profile = self.get_profile(user_id)
        if not profile:
            return ""
        return "；".join(f"{k}：{v}" for k, v in profile.items())

    # ---------------- 课表 ----------------
    def replace_courses(self, user_id: str, courses: list[dict]) -> dict:
        """整体替换某用户的课表（用于从 JSON 导入或重新上传）。"""
        now_iso = to_storage_string(now_shanghai())
        cleaned = []
        for c in courses:
            try:
                cleaned.append(
                    (
                        user_id,
                        str(c["name"]).strip(),
                        int(c["weekday"]),
                        str(c["start_time"]).strip(),
                        str(c["end_time"]).strip(),
                        str(c.get("location", "") or "").strip(),
                        str(c.get("teacher", "") or "").strip(),
                        json.dumps(c.get("weeks", []), ensure_ascii=False),
                        now_iso,
                    )
                )
            except (KeyError, ValueError, TypeError) as e:
                return {"ok": False, "error": f"课表条目格式错误：{e}（需要 name/weekday/start_time/end_time）"}
        with self._connect() as conn:
            conn.execute("DELETE FROM courses WHERE user_id = ?", (user_id,))
            conn.executemany(
                """
                INSERT INTO courses
                    (user_id, name, weekday, start_time, end_time, location, teacher, weeks, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                cleaned,
            )
        return {"ok": True, "message": f"已保存 {len(cleaned)} 门课程。", "count": len(cleaned)}

    def list_courses(self, user_id: str, weekday: int | None = None) -> list[dict]:
        sql = "SELECT * FROM courses WHERE user_id = ?"
        params: list = [user_id]
        if weekday is not None:
            sql += " AND weekday = ?"
            params.append(int(weekday))
        sql += " ORDER BY weekday ASC, start_time ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["weeks"] = json.loads(item.get("weeks") or "[]")
            except json.JSONDecodeError:
                item["weeks"] = []
            result.append(item)
        return result
