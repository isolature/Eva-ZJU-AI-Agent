from __future__ import annotations

import json

from services.reminder.service import ReminderService


def create_reminder(
    service: ReminderService | None,
    context: dict,
    title: str,
    event_time: str,
    location: str = "",
    details: str = "",
    remind_before_minutes: int | str = 30,
) -> str:
    if service is None:
        return _error("提醒服务未初始化。")

    return _run_safely(
        lambda: service.create_reminder(
            user_id=context.get("sender_staff_id") or context.get("sender_id"),
            conversation_id=context.get("conversation_id"),
            raw_user_text=context.get("raw_user_text", ""),
            title=title,
            event_time=event_time,
            location=location,
            details=details,
            remind_before_minutes=remind_before_minutes,
        )
    )


def list_reminders(service: ReminderService | None, context: dict) -> str:
    if service is None:
        return _error("提醒服务未初始化。")
    return _run_safely(
        lambda: service.list_reminders(
            user_id=context.get("sender_staff_id") or context.get("sender_id"),
        )
    )


def cancel_reminder(
    service: ReminderService | None,
    context: dict,
    reminder_id: int | str,
) -> str:
    if service is None:
        return _error("提醒服务未初始化。")
    return _run_safely(
        lambda: service.cancel_reminder(
            user_id=context.get("sender_staff_id") or context.get("sender_id"),
            reminder_id=reminder_id,
        )
    )


def update_reminder(
    service: ReminderService | None,
    context: dict,
    reminder_id: int | str,
    title: str | None = None,
    event_time: str | None = None,
    location: str | None = None,
    details: str | None = None,
    remind_before_minutes: int | str | None = None,
) -> str:
    if service is None:
        return _error("提醒服务未初始化。")
    return _run_safely(
        lambda: service.update_reminder(
            user_id=context.get("sender_staff_id") or context.get("sender_id"),
            reminder_id=reminder_id,
            title=title,
            event_time=event_time,
            location=location,
            details=details,
            remind_before_minutes=remind_before_minutes,
        )
    )


def _error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _run_safely(fn) -> str:
    try:
        return json.dumps(fn(), ensure_ascii=False)
    except Exception as e:
        return _error(str(e))
