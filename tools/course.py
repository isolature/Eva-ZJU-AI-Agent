"""
课表查询工具。课表导入在 main.py 启动时完成，本模块只提供查询，复用 MemoryStore。
"""

from __future__ import annotations

import json

from services.memory.store import MemoryStore

WEEKDAY_CN = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def _uid(context: dict) -> str | None:
    return context.get("sender_staff_id") or context.get("sender_id")


def query_courses(store: MemoryStore | None, context: dict, weekday: int | str | None = None) -> str:
    """查询课表。weekday 可选（1=周一 … 7=周日）；留空返回整周课表。"""
    if store is None:
        return _err("课表服务未初始化。")
    uid = _uid(context)
    if not uid:
        return _err("缺少用户 ID，无法查询课表。")

    wd = None
    if weekday not in (None, ""):
        try:
            wd = int(weekday)
            if wd < 1 or wd > 7:
                return _err("weekday 必须是 1-7（1=周一，7=周日）。")
        except (ValueError, TypeError):
            return _err("weekday 必须是 1-7 的整数。")

    courses = store.list_courses(uid, weekday=wd)
    items = [
        {
            "name": c["name"],
            "weekday": c["weekday"],
            "weekday_cn": WEEKDAY_CN.get(c["weekday"], ""),
            "time": f"{c['start_time']}-{c['end_time']}",
            "location": c.get("location", ""),
            "teacher": c.get("teacher", ""),
            "weeks": c.get("weeks", []),
        }
        for c in courses
    ]
    if not items:
        scope = WEEKDAY_CN.get(wd, "本周") if wd else "课表里"
        return _ok({"ok": True, "count": 0, "items": [], "note": f"{scope}没有课程记录，或你还没上传课表。"})
    return _ok({"ok": True, "count": len(items), "items": items})


def _ok(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)
