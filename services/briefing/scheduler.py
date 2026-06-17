"""
每日简报调度器：每分钟检查是否到达发送时间，到点且当天未发则发送一次。
"""

from __future__ import annotations

import threading

from services.briefing.service import DailyBriefingService
from utils.time_parser import now_shanghai


class DailyBriefingScheduler:
    def __init__(self, service: DailyBriefingService, send_time: str = "07:30"):
        self.service = service
        self.send_time = self._normalize(send_time)
        self._stop = threading.Event()
        self._thread = None
        self._last_sent_date = None

    @staticmethod
    def _normalize(value: str) -> str:
        value = (value or "07:30").strip()
        try:
            hh, mm = value.split(":")
            return f"{int(hh):02d}:{int(mm):02d}"
        except Exception:
            return "07:30"

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="daily-briefing", daemon=True)
        self._thread.start()
        print(f"[简报] 调度已启动，每天 {self.send_time} 发送。")

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _loop(self):
        while not self._stop.is_set():
            try:
                now = now_shanghai()
                today = now.strftime("%Y-%m-%d")
                if now.strftime("%H:%M") == self.send_time and self._last_sent_date != today:
                    self._last_sent_date = today
                    self.service.send_today()
            except Exception as e:
                print(f"[简报] 调度异常：{e}")
            self._stop.wait(30)
