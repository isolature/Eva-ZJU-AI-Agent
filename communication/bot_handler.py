"""
钉钉 Stream 接入层：接收群消息，组装上下文后交给 agent 主循环，再将结果回复到群。

维护按 conversation_id 隔离的多轮短期记忆（内存态，重启清空）；每轮在 system
提示的副本中注入当前北京时间与用户长期画像，避免污染历史；回复以 Markdown 一次性发出。
具体推理与工具调用见 run_agent.py。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import dingtalk_stream
from dingtalk_stream import AckMessage

from run_agent import run_agent

SYSTEM_PROMPT = (
    "你是一个在钉钉群里工作的私人智能助手，回答简洁、友好、说中文。"
    "你拥有这些工具：查天气、联网搜索、搜索浙大本科教务网通知、读通知正文、"
    "创建和管理日程提醒、查询我的课表、记住/查询我的长期信息（专业/爱好/习惯等）、查看学校公众号通知。"
    "需要实时信息（天气、教务通知、网络搜索、公众号通知）时必须先调用工具，不要凭记忆编造。"
    "查教务通知先用 search_zju_notices 找列表，需要正文再用 news_id 调 get_zju_notice_detail。"
    "处理提醒时若时间表达不明确要先追问；时间明确后换算成北京时间绝对时间再调用；用户没说提前量时默认提前30分钟。"
    "当用户透露了个人长期偏好/信息（如专业、爱好、作息习惯）时，主动用 remember 记住。"
    "回复用 Markdown（标题/加粗/列表/链接），但钉钉不支持表格，绝不要用 | 表格 |，改用分行或 - 列表。"
)

MAX_HISTORY = 10


def _now_beijing() -> str:
    tz = timezone(timedelta(hours=8))
    week = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    dt = datetime.now(tz)
    return f"{dt.year}年{dt.month}月{dt.day}日 {week[dt.weekday()]} {dt.strftime('%H:%M')}"


class AgentBotHandler(dingtalk_stream.ChatbotHandler):
    def __init__(self, client, reminder_service=None, memory_store=None, wechat_store=None):
        super().__init__()
        self.client = client
        self.reminder_service = reminder_service
        self.memory_store = memory_store
        self.wechat_store = wechat_store
        self.conversations: dict[str, list] = {}

    def _get_history(self, conv_id: str) -> list:
        if conv_id not in self.conversations:
            self.conversations[conv_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
        return self.conversations[conv_id]

    @staticmethod
    def _trim(history: list):
        if len(history) > MAX_HISTORY + 1:
            history[:] = [history[0]] + history[-MAX_HISTORY:]

    def _build_system(self, user_id: str | None) -> str:
        text = SYSTEM_PROMPT + f"\n（当前时间：{_now_beijing()}，北京时间。涉及今天/明天/还有几天等推算以此为准。）"
        if self.memory_store and user_id:
            try:
                profile = self.memory_store.profile_as_text(user_id)
                if profile:
                    text += f"\n（关于这位用户你已知道：{profile}。）"
            except Exception:
                pass
        return text

    async def process(self, callback: dingtalk_stream.CallbackMessage):
        incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
        user_text = incoming.text.content.strip()
        conv_id = incoming.conversation_id or "default"
        user_id = incoming.sender_staff_id or incoming.sender_id
        print(f"\n[收到][{conv_id[:8]}…] {user_text}")
        print(f"[发送人 userid] {user_id}  （把这个填进 .env 的 DINGTALK_OWNER_USERID）")

        if user_text in ("清空记忆", "重置", "/reset"):
            self.conversations.pop(conv_id, None)
            self.reply_text("已清空本会话的短期记忆。（长期记忆不受影响）", incoming)
            return AckMessage.STATUS_OK, "OK"

        history = self._get_history(conv_id)
        history.append({"role": "user", "content": user_text})

        working = list(history)
        working[0] = {"role": "system", "content": self._build_system(user_id)}

        tool_context = {
            "reminder_service": self.reminder_service,
            "memory_store": self.memory_store,
            "wechat_store": self.wechat_store,
            "conversation_id": conv_id,
            "sender_staff_id": incoming.sender_staff_id or incoming.sender_id,
            "sender_id": incoming.sender_id,
            "sender_nick": incoming.sender_nick,
            "raw_user_text": user_text,
        }

        reply = self._run_plain(working, tool_context, incoming)

        history.append({"role": "assistant", "content": reply})
        self._trim(history)
        print(f"[回复] {reply[:120]}")
        return AckMessage.STATUS_OK, "OK"

    def _run_plain(self, working, tool_context, incoming) -> str:
        try:
            reply = run_agent(self.client, working, tool_context=tool_context)
        except Exception as e:
            print(f"[出错] {e}")
            reply = "抱歉，我这边出了点问题，请稍后再试。"
        self.reply_markdown("小助手", reply, incoming)
        return reply
