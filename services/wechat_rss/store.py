"""
微信公众号文章 / 通知摘要的本地存储（消费 we-mp-rss 的 RSS/JSON）。

职责：去重、保存正文、保存 LLM 摘要与重要性、记录推送状态。
和其它功能共用同一个 SQLite 文件（data/agent.db）。
"""

from __future__ import annotations

import os
import sqlite3

from utils.time_parser import now_shanghai, to_storage_string


class WeChatRssStore:
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
                CREATE TABLE IF NOT EXISTS wechat_articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_id TEXT NOT NULL UNIQUE,
                    account TEXT,
                    title TEXT NOT NULL,
                    url TEXT,
                    published_at TEXT,
                    content_text TEXT,
                    summary TEXT,
                    importance TEXT,
                    action_required TEXT,
                    deadline TEXT,
                    is_notice INTEGER DEFAULT 0,
                    summary_status TEXT NOT NULL DEFAULT 'pending',
                    notify_status TEXT NOT NULL DEFAULT 'pending',
                    notified_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wechat_created ON wechat_articles(created_at);
                CREATE INDEX IF NOT EXISTS idx_wechat_summary_status ON wechat_articles(summary_status);
                """
            )

    def exists(self, article_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM wechat_articles WHERE article_id = ?", (article_id,)
            ).fetchone()
        return row is not None

    def save_new_article(self, article: dict) -> bool:
        """保存一篇新文章（仅在不存在时）。返回是否新增。"""
        if self.exists(article["article_id"]):
            return False
        now_iso = to_storage_string(now_shanghai())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO wechat_articles
                    (article_id, account, title, url, published_at, content_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article["article_id"],
                    article.get("account", ""),
                    article.get("title", ""),
                    article.get("url", ""),
                    article.get("published_at", ""),
                    article.get("content_text", ""),
                    now_iso,
                ),
            )
        return True

    def refresh_if_better(self, article: dict) -> bool:
        """已存在但正文为空、而新抓到有正文时，回填正文并重新排队总结。

        用于 we-mp-rss 后开启全文采集后，自动升级早先只存了标题的旧文章。
        返回是否发生了回填。
        """
        new_content = (article.get("content_text") or "").strip()
        if not new_content:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, content_text FROM wechat_articles WHERE article_id = ?",
                (article["article_id"],),
            ).fetchone()
            if row is None or (row["content_text"] or "").strip():
                return False  # 不存在，或已有正文，不动
            conn.execute(
                "UPDATE wechat_articles SET content_text = ?, summary_status = 'pending' WHERE id = ?",
                (new_content, row["id"]),
            )
        return True

    def list_pending_summary(self, limit: int = 5) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM wechat_articles WHERE summary_status = 'pending' ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_summary(self, article_id_pk: int, summary: dict):
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE wechat_articles
                SET summary = ?, importance = ?, action_required = ?, deadline = ?,
                    is_notice = ?, summary_status = 'done'
                WHERE id = ?
                """,
                (
                    summary.get("summary", ""),
                    summary.get("importance", "low"),
                    summary.get("action_required", ""),
                    summary.get("deadline", ""),
                    1 if summary.get("is_notice") else 0,
                    article_id_pk,
                ),
            )

    def mark_summary_failed(self, article_id_pk: int):
        with self._connect() as conn:
            conn.execute(
                "UPDATE wechat_articles SET summary_status = 'failed' WHERE id = ?",
                (article_id_pk,),
            )

    def list_pending_notify(self, importance=("high",)) -> list[dict]:
        placeholders = ",".join("?" for _ in importance)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM wechat_articles
                WHERE summary_status = 'done'
                  AND notify_status = 'pending'
                  AND is_notice = 1
                  AND importance IN ({placeholders})
                ORDER BY id ASC
                """,
                tuple(importance),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_notified(self, article_id_pk: int):
        now_iso = to_storage_string(now_shanghai())
        with self._connect() as conn:
            conn.execute(
                "UPDATE wechat_articles SET notify_status = 'sent', notified_at = ? WHERE id = ?",
                (now_iso, article_id_pk),
            )

    def list_recent(self, limit: int = 10, importance: str = "all") -> list[dict]:
        sql = "SELECT * FROM wechat_articles WHERE summary_status = 'done'"
        params: list = []
        if importance and importance != "all":
            sql += " AND importance = ?"
            params.append(importance)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def search(self, keyword: str, limit: int = 10) -> list[dict]:
        like = f"%{keyword}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM wechat_articles
                WHERE summary_status = 'done'
                  AND (title LIKE ? OR summary LIKE ? OR content_text LIKE ?)
                ORDER BY id DESC LIMIT ?
                """,
                (like, like, like, limit),
            ).fetchall()
        return [dict(r) for r in rows]
