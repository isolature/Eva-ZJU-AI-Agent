"""
每日简报服务（自动化定时任务的核心）。

原则：事实由程序收集，表达由 LLM 生成，任一数据源失败仍能降级发送。
汇总：当天课表 + 当天日程提醒 + 学校天气(下雨提醒带伞) + 公众号通知 + 教务通知。
"""

from __future__ import annotations

import json

from tools.weather import get_weather
from utils.time_parser import now_shanghai

MODEL = "deepseek-v4-flash"
WEEKDAY_CN = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
RAIN_KEYWORDS = ("雨", "阵雨", "雷阵雨", "暴雨", "小雨", "中雨", "大雨")

BRIEFING_SYSTEM_PROMPT = (
    "你是用户的私人日程助理。基于给定的 JSON 事实生成今天的晨报，要求："
    "1.不要编造未提供的信息；2.按时间顺序整理今日日程（课表+提醒）；"
    "3.如有下雨风险，明确提醒带伞；4.通知只总结与用户可能需要行动有关的部分；"
    "5.输出 Markdown 但不要用表格；6.语气简洁、具体、可执行；"
    "7.如果某数据源缺失，轻描淡写带过，不要吓人。"
)


class DailyBriefingService:
    def __init__(
        self,
        client,
        memory_store=None,
        reminder_store=None,
        wechat_store=None,
        notifier=None,
        owner_userid: str | None = None,
        city: str = "杭州",
    ):
        self.client = client
        self.memory_store = memory_store
        self.reminder_store = reminder_store
        self.wechat_store = wechat_store
        self.notifier = notifier
        self.owner_userid = (owner_userid or "").strip()
        self.city = city

    # ---------- 收集事实（每源独立 try，互不拖累）----------
    def build_facts(self) -> dict:
        now = now_shanghai()
        weekday = now.isoweekday()  # 1=周一 .. 7=周日
        today = now.strftime("%Y-%m-%d")
        facts: dict = {
            "date": today,
            "weekday": WEEKDAY_CN.get(weekday, ""),
            "courses": [],
            "reminders": [],
            "weather": {},
            "notices": [],
            "warnings": [],
        }

        # 课表
        try:
            if self.memory_store and self.owner_userid:
                for c in self.memory_store.list_courses(self.owner_userid, weekday=weekday):
                    facts["courses"].append(
                        {"time": f"{c['start_time']}-{c['end_time']}", "name": c["name"], "location": c.get("location", "")}
                    )
        except Exception as e:
            facts["warnings"].append(f"课表读取失败：{e}")

        # 当天提醒
        try:
            if self.reminder_store and self.owner_userid:
                for r in self.reminder_store.list_reminders_for_user(self.owner_userid):
                    if str(r.get("event_time", "")).startswith(today):
                        facts["reminders"].append(
                            {"time": r["event_time"][11:16], "title": r["title"], "location": r.get("location", "")}
                        )
        except Exception as e:
            facts["warnings"].append(f"提醒读取失败：{e}")

        # 天气
        try:
            text = get_weather(self.city)
            facts["weather"] = {"city": self.city, "text": text, "rain_alert": any(k in text for k in RAIN_KEYWORDS)}
        except Exception as e:
            facts["warnings"].append(f"天气查询失败：{e}")

        # 公众号通知（最近，已总结）
        try:
            if self.wechat_store:
                for n in self.wechat_store.list_recent(limit=5):
                    facts["notices"].append(
                        {"source": n.get("account", "公众号"), "title": n["title"], "summary": n.get("summary", ""), "deadline": n.get("deadline", ""), "url": n.get("url", "")}
                    )
        except Exception as e:
            facts["warnings"].append(f"公众号通知读取失败：{e}")

        # 教务通知（尽力，校外可能需 WebVPN）
        try:
            from tools import zju_notices
            res = zju_notices.search_notices("", limit=3)
            for it in res.get("items", []):
                facts["notices"].append(
                    {"source": "教务网", "title": it["title"], "summary": it.get("summary", ""), "deadline": "", "url": it.get("url", "")}
                )
        except Exception as e:
            facts["warnings"].append(f"教务通知读取失败：{e}")

        return facts

    def generate(self, facts: dict) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": BRIEFING_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
                ],
            )
            return resp.choices[0].message.content or self._fallback(facts)
        except Exception as e:
            print(f"[简报] LLM 生成失败，降级为模板：{e}")
            return self._fallback(facts)

    def send_today(self) -> str:
        facts = self.build_facts()
        briefing = self.generate(facts)
        if self.notifier and self.notifier.enabled and self.owner_userid:
            try:
                self.notifier.send_markdown(self.owner_userid, "每日简报", briefing)
                print("[简报] 已发送今日简报。")
            except Exception as e:
                print(f"[简报] 发送失败：{e}")
        else:
            print("[简报] 未配置主动推送，已生成但未发送。")
        return briefing

    @staticmethod
    def _fallback(facts: dict) -> str:
        lines = [f"# 早上好，今天是 {facts['date']} {facts['weekday']}", "", "## 今日日程"]
        for c in facts.get("courses", []):
            lines.append(f"- {c['time']} {c['name']} {c.get('location','')}")
        for r in facts.get("reminders", []):
            lines.append(f"- {r['time']} {r['title']} {r.get('location','')}")
        if not facts.get("courses") and not facts.get("reminders"):
            lines.append("- 今天没有课程和已安排提醒。")
        w = facts.get("weather", {})
        if w:
            lines += ["", "## 天气", w.get("text", "")]
            if w.get("rain_alert"):
                lines.append("今天可能有雨，记得带伞。")
        notices = facts.get("notices", [])
        if notices:
            lines += ["", "## 通知"]
            for n in notices[:5]:
                lines.append(f"- [{n['source']}] {n['title']}：{n.get('summary','')}")
        return "\n".join(lines)
