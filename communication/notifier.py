from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from utils.time_parser import to_display_string


class DingTalkNotifier:
    """通过钉钉 asyncsend_v2 主动给用户发消息（工作通知）。

    被三处复用：日程提醒到点推送、每日简报、公众号通知。
    所有功能共享一份 access_token 缓存。
    """

    def __init__(self, app_key: str, app_secret: str, agent_id: str | int | None):
        self.app_key = (app_key or "").strip()
        self.app_secret = (app_secret or "").strip()
        self.agent_id = int(agent_id) if str(agent_id or "").strip() else None
        self._token = None
        self._expires_at_utc = datetime.min.replace(tzinfo=timezone.utc)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.app_key and self.app_secret and self.agent_id)

    def send_markdown(self, user_id: str, title: str, text: str):
        """通用主动推送：把一段 Markdown 工作通知发给指定用户。

        被每日简报、公众号通知等服务复用。
        """
        if not self.enabled:
            raise RuntimeError("未配置 DINGTALK_AGENT_ID，主动消息发送功能尚未启用。")
        user_id = (user_id or "").strip()
        if not user_id:
            raise RuntimeError("缺少 user_id，无法主动发送消息。")
        self._send_markdown_raw(user_id=user_id, title=title, text=text)

    def send_reminder(self, reminder: dict):
        if not self.enabled:
            raise RuntimeError("未配置 DINGTALK_AGENT_ID，主动提醒发送功能尚未启用。")

        user_id = (reminder.get("user_id") or "").strip()
        if not user_id:
            raise RuntimeError("提醒缺少 user_id，无法主动发送。")

        title = f"日程提醒：{reminder['title']}"
        body = self._build_markdown(reminder)
        self._send_markdown_raw(user_id=user_id, title=title, text=body)

    def _send_markdown_raw(self, user_id: str, title: str, text: str):
        token = self._get_company_token()
        payload = {
            "agent_id": self.agent_id,
            "userid_list": user_id,
            "msg": {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": text,
                },
            },
        }
        url = (
            "https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2?"
            + urllib.parse.urlencode({"access_token": token})
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "ding-agent/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            parsed = json.loads(response.read().decode("utf-8"))

        if parsed.get("errcode") != 0:
            raise RuntimeError(
                f"DingTalk asyncsend_v2 失败：errcode={parsed.get('errcode')} errmsg={parsed.get('errmsg')}"
            )

    def _get_company_token(self) -> str:
        now_utc = datetime.now(timezone.utc)
        with self._lock:
            if self._token and now_utc < self._expires_at_utc:
                return self._token

            url = (
                "https://oapi.dingtalk.com/gettoken?"
                + urllib.parse.urlencode(
                    {
                        "appkey": self.app_key,
                        "appsecret": self.app_secret,
                    }
                )
            )
            request = urllib.request.Request(url, headers={"User-Agent": "ding-agent/1.0"})
            with urllib.request.urlopen(request, timeout=15) as response:
                parsed = json.loads(response.read().decode("utf-8"))

            if parsed.get("errcode") != 0 or not parsed.get("access_token"):
                raise RuntimeError(
                    f"DingTalk gettoken 失败：errcode={parsed.get('errcode')} errmsg={parsed.get('errmsg')}"
                )

            expires_in = int(parsed.get("expires_in") or 7200)
            self._token = parsed["access_token"]
            self._expires_at_utc = now_utc + timedelta(seconds=max(60, expires_in - 300))
            return self._token

    @staticmethod
    def _build_markdown(reminder: dict) -> str:
        lines = [
            "# 日程提醒",
            f"**事件**：{reminder['title']}",
            f"- 时间：{to_display_string(reminder['event_time'])}",
            f"- 提前：{reminder['remind_before_minutes']} 分钟",
        ]
        location = (reminder.get("location") or "").strip()
        if location:
            lines.append(f"- 地点：{location}")
        details = (reminder.get("details") or "").strip()
        if details:
            lines.append(f"- 说明：{details}")
        return "\n".join(lines)
