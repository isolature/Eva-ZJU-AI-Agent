from __future__ import annotations

from datetime import timedelta

from services.reminder.store import ReminderStore
from utils.time_parser import now_shanghai, parse_user_datetime, to_display_string, to_storage_string


class ReminderService:
    """提醒创建与管理的业务逻辑层。"""

    def __init__(self, store: ReminderStore, notifications_enabled: bool = True):
        self.store = store
        self.notifications_enabled = notifications_enabled

    def create_reminder(
        self,
        *,
        user_id: str | None,
        conversation_id: str | None,
        raw_user_text: str,
        title: str,
        event_time: str,
        location: str = "",
        details: str = "",
        remind_before_minutes: int | str = 30,
    ) -> dict:
        if not user_id:
            return {"ok": False, "error": "当前消息缺少发送者 ID，暂时无法创建主动提醒。"}

        remind_before = self._coerce_minutes(remind_before_minutes)
        if remind_before < 0:
            return {"ok": False, "error": "提前提醒分钟数不能为负数。"}

        event_dt = parse_user_datetime(event_time)
        remind_dt = event_dt - timedelta(minutes=remind_before)
        now_dt = now_shanghai()
        if remind_dt <= now_dt:
            return {
                "ok": False,
                "error": "提醒时间已经早于或接近当前时间，请换一个更晚的事件时间或减少提前量。",
            }

        title_text = self._normalize_title(title, details, raw_user_text)
        now_iso = to_storage_string(now_dt)
        item = self.store.create_reminder(
            {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "title": title_text,
                "location": (location or "").strip(),
                "details": (details or "").strip(),
                "event_time": to_storage_string(event_dt),
                "remind_before_minutes": remind_before,
                "remind_time": to_storage_string(remind_dt),
                "created_at": now_iso,
                "updated_at": now_iso,
                "raw_user_text": raw_user_text,
            }
        )
        return {
            "ok": True,
            "message": "提醒创建成功。",
            "notifications_enabled": self.notifications_enabled,
            "warning": None if self.notifications_enabled else "当前未配置主动通知发送，提醒会保存但不会真正发出。",
            "item": self._serialize(item),
        }

    def list_reminders(self, *, user_id: str | None) -> dict:
        if not user_id:
            return {"ok": False, "error": "当前消息缺少发送者 ID，无法查询你的提醒。"}

        items = [self._serialize(item) for item in self.store.list_reminders_for_user(user_id)]
        return {"ok": True, "count": len(items), "items": items}

    def cancel_reminder(self, *, user_id: str | None, reminder_id: int | str) -> dict:
        if not user_id:
            return {"ok": False, "error": "当前消息缺少发送者 ID，无法取消提醒。"}

        reminder_id = self._coerce_id(reminder_id)
        now_iso = to_storage_string(now_shanghai())
        before = self.store.get_reminder(reminder_id)
        if before is None or before["user_id"] != user_id:
            return {"ok": False, "error": f"未找到编号为 {reminder_id} 的提醒。"}
        if before["status"] not in ("pending", "sending"):
            return {"ok": False, "error": "这个提醒已经结束，不能再取消。"}

        item = self.store.cancel_reminder(reminder_id, user_id, now_iso)
        return {"ok": True, "message": "提醒已取消。", "item": self._serialize(item)}

    def update_reminder(
        self,
        *,
        user_id: str | None,
        reminder_id: int | str,
        title: str | None = None,
        event_time: str | None = None,
        location: str | None = None,
        details: str | None = None,
        remind_before_minutes: int | str | None = None,
    ) -> dict:
        if not user_id:
            return {"ok": False, "error": "当前消息缺少发送者 ID，无法修改提醒。"}

        reminder_id = self._coerce_id(reminder_id)
        existing = self.store.get_reminder(reminder_id)
        if existing is None or existing["user_id"] != user_id:
            return {"ok": False, "error": f"未找到编号为 {reminder_id} 的提醒。"}
        if existing["status"] not in ("pending", "sending"):
            return {"ok": False, "error": "这个提醒已经结束，不能再修改。"}

        updates = {}
        merged_title = title if title is not None else existing["title"]
        merged_location = location if location is not None else existing["location"]
        merged_details = details if details is not None else existing["details"]
        merged_event_time = event_time if event_time is not None else existing["event_time"]
        merged_remind_before = (
            remind_before_minutes
            if remind_before_minutes is not None
            else existing["remind_before_minutes"]
        )
        remind_before = self._coerce_minutes(merged_remind_before)
        if remind_before < 0:
            return {"ok": False, "error": "提前提醒分钟数不能为负数。"}

        event_dt = parse_user_datetime(str(merged_event_time))
        remind_dt = event_dt - timedelta(minutes=remind_before)
        if remind_dt <= now_shanghai():
            return {
                "ok": False,
                "error": "修改后的提醒时间已经早于或接近当前时间，请换一个更晚的时间。",
            }

        updates["title"] = self._normalize_title(str(merged_title), str(merged_details), existing["raw_user_text"] or "")
        updates["location"] = (merged_location or "").strip()
        updates["details"] = (merged_details or "").strip()
        updates["event_time"] = to_storage_string(event_dt)
        updates["remind_before_minutes"] = remind_before
        updates["remind_time"] = to_storage_string(remind_dt)
        updates["status"] = "pending"
        updates["last_error"] = None

        item = self.store.update_reminder(
            reminder_id,
            user_id,
            updates,
            now_iso=to_storage_string(now_shanghai()),
        )
        return {"ok": True, "message": "提醒已更新。", "item": self._serialize(item)}

    def _serialize(self, item: dict | None) -> dict | None:
        if item is None:
            return None

        return {
            "id": item["id"],
            "title": item["title"],
            "event_time": to_display_string(item["event_time"]),
            "remind_time": to_display_string(item["remind_time"]),
            "location": item["location"] or "",
            "details": item["details"] or "",
            "remind_before_minutes": item["remind_before_minutes"],
            "status": item["status"],
            "retry_count": item["retry_count"],
            "last_error": item["last_error"],
        }

    @staticmethod
    def _coerce_minutes(value: int | str) -> int:
        if value is None or value == "":
            return 30
        minutes = int(value)
        if minutes > 60 * 24 * 30:
            raise ValueError("提前提醒分钟数过大，请控制在 30 天以内。")
        return minutes

    @staticmethod
    def _coerce_id(value: int | str) -> int:
        reminder_id = int(value)
        if reminder_id <= 0:
            raise ValueError("reminder_id 必须是正整数。")
        return reminder_id

    @staticmethod
    def _normalize_title(title: str, details: str, raw_user_text: str) -> str:
        for candidate in (title, details, raw_user_text):
            text = (candidate or "").strip()
            if text:
                return text[:80]
        return "未命名提醒"
