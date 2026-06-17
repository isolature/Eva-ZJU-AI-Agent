"""
长期记忆工具：记忆/查询用户的个人信息。封装 MemoryStore，返回 JSON 字符串，
user_id 取自 context 以隔离不同用户。
"""

from __future__ import annotations

import json

from services.memory.store import MemoryStore


def _uid(context: dict) -> str | None:
    return context.get("sender_staff_id") or context.get("sender_id")


def remember(store: MemoryStore | None, context: dict, key: str, value: str) -> str:
    if store is None:
        return _err("记忆服务未初始化。")
    uid = _uid(context)
    if not uid:
        return _err("缺少用户 ID，无法记忆。")
    return _ok(store.remember(uid, key, value))


def forget(store: MemoryStore | None, context: dict, key: str) -> str:
    if store is None:
        return _err("记忆服务未初始化。")
    uid = _uid(context)
    if not uid:
        return _err("缺少用户 ID，无法操作。")
    return _ok(store.forget(uid, key))


def get_profile(store: MemoryStore | None, context: dict) -> str:
    if store is None:
        return _err("记忆服务未初始化。")
    uid = _uid(context)
    if not uid:
        return _err("缺少用户 ID，无法查询。")
    profile = store.get_profile(uid)
    return _ok({"ok": True, "profile": profile, "count": len(profile)})


def _ok(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)
