from __future__ import annotations

import threading
from datetime import timedelta

from communication.notifier import DingTalkNotifier
from services.reminder.store import ReminderStore
from utils.time_parser import now_shanghai, to_storage_string


class ReminderScheduler:
    """到期提醒的轻量轮询调度器。"""

    def __init__(
        self,
        store: ReminderStore,
        notifier: DingTalkNotifier,
        poll_seconds: int = 30,
        max_retries: int = 3,
        stale_minutes: int = 5,
    ):
        self.store = store
        self.notifier = notifier
        self.poll_seconds = max(5, int(poll_seconds))
        self.max_retries = max(1, int(max_retries))
        self.stale_minutes = max(1, int(stale_minutes))
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="reminder-scheduler",
            daemon=True,
        )
        self._thread.start()
        print(f"[提醒调度] 已启动，轮询间隔 {self.poll_seconds} 秒。")

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[提醒调度] 轮询失败：{e}")
            self._stop_event.wait(self.poll_seconds)

    def _tick(self):
        now_dt = now_shanghai()
        now_iso = to_storage_string(now_dt)
        stale_before = to_storage_string(now_dt - timedelta(minutes=self.stale_minutes))
        self.store.requeue_stale_sending(now_iso, stale_before)
        due_items = self.store.claim_due_reminders(now_iso, limit=20)
        for item in due_items:
            try:
                self.notifier.send_reminder(item)
                self.store.mark_sent(item["id"], now_iso)
                print(f"[提醒发送] 已发送提醒 #{item['id']} -> {item['user_id']}")
            except Exception as e:
                self.store.release_after_failure(
                    item["id"],
                    now_iso=now_iso,
                    error_message=str(e),
                    max_retries=self.max_retries,
                )
                print(f"[提醒发送] 提醒 #{item['id']} 发送失败：{e}")
