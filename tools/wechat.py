"""
公众号通知查询工具：只读本地已总结的摘要，不在查询时实时爬取。
"""

from __future__ import annotations

import json

from services.wechat_rss.store import WeChatRssStore


def _fmt(rows: list[dict]) -> list[dict]:
    return [
        {
            "account": r.get("account", ""),
            "title": r.get("title", ""),
            "summary": r.get("summary", ""),
            "importance": r.get("importance", ""),
            "action_required": r.get("action_required", ""),
            "deadline": r.get("deadline", ""),
            "url": r.get("url", ""),
        }
        for r in rows
    ]


def list_wechat_notices(store: WeChatRssStore | None, limit: int = 10, importance: str = "all") -> str:
    if store is None:
        return _err("公众号通知服务未启用（未配置 WECHAT_RSS）。")
    rows = store.list_recent(limit=int(limit), importance=importance)
    return _ok({"ok": True, "count": len(rows), "items": _fmt(rows)})


def search_wechat_notices(store: WeChatRssStore | None, keyword: str, limit: int = 10) -> str:
    if store is None:
        return _err("公众号通知服务未启用（未配置 WECHAT_RSS）。")
    rows = store.search((keyword or "").strip(), limit=int(limit))
    return _ok({"ok": True, "keyword": keyword, "count": len(rows), "items": _fmt(rows)})


def _ok(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)
