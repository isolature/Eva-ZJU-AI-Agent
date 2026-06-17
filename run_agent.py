"""
Agent 核心循环：基于 function calling 的手写 ReAct 主循环，不依赖外部 agent 框架。

每轮把 messages 与工具表 TOOLS 提交给模型；模型返回纯文本即视为最终答案，
返回工具调用则按 dispatch 执行并将结果写回 messages 后继续，直至产出答案或达到步数上限。

扩展工具：在 tools/ 实现函数、在 TOOLS 补充 schema、在 build_dispatch 注册即可。
约定所有工具返回字符串（结构化结果用 json.dumps 序列化）。
"""

from __future__ import annotations

import json

from tools import course as course_tools
from tools import memory as memory_tools
from tools import reminder as reminder_tools
from tools import wechat as wechat_tools
from tools import zju_notices
from tools.weather import get_weather
from tools.web_search import web_search

MODEL = "deepseek-v4-flash"


# ======================================================================
# 1) 工具 schema 定义（function calling 协议）
# ======================================================================
TOOLS = [
    {"type": "function", "function": {
        "name": "get_weather",
        "description": "查询某城市实时天气（状况、气温、湿度、风）。问天气时调用。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": "城市名，如 杭州/北京"}}, "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "联网搜索实时信息（新闻、最新事实、本地没有的内容）。天气/教务通知请用专门工具。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索内容"},
            "max_results": {"type": "integer", "description": "返回条数，默认 5"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "search_zju_notices",
        "description": "搜索浙大本科教务网通知列表，可按关键词过滤(选课/考试/学籍)，留空返回最新。"
                       "返回标题/时间/news_id/链接/摘要。需要正文再用 news_id 调 get_zju_notice_detail。",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "关键词，留空返回最新"},
            "limit": {"type": "integer", "description": "最多几条，默认 10"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "get_zju_notice_detail",
        "description": "按 news_id 抓取某条教务通知完整正文。news_id 来自 search_zju_notices。",
        "parameters": {"type": "object", "properties": {
            "news_id": {"type": "string", "description": "通知编号"}}, "required": ["news_id"]}}},
    {"type": "function", "function": {
        "name": "create_reminder",
        "description": "创建单次日程提醒。event_time 必须是北京时间绝对时间(YYYY-MM-DD HH:MM:SS)。"
                       "用户时间表达不明确时先追问再调用。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "提醒标题"},
            "event_time": {"type": "string", "description": "事件时间，北京时间绝对时间"},
            "location": {"type": "string", "description": "地点，可空"},
            "details": {"type": "string", "description": "备注，可空"},
            "remind_before_minutes": {"type": "integer", "description": "提前几分钟，默认30"}},
            "required": ["title", "event_time"]}}},
    {"type": "function", "function": {
        "name": "list_reminders",
        "description": "查看当前用户未完成的提醒列表。",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "cancel_reminder",
        "description": "取消一个尚未触发的提醒。",
        "parameters": {"type": "object", "properties": {
            "reminder_id": {"type": "integer", "description": "提醒编号"}}, "required": ["reminder_id"]}}},
    {"type": "function", "function": {
        "name": "update_reminder",
        "description": "修改已存在提醒的时间/地点/标题/备注/提前量。改时间需传北京时间绝对时间。",
        "parameters": {"type": "object", "properties": {
            "reminder_id": {"type": "integer", "description": "提醒编号"},
            "title": {"type": "string"}, "event_time": {"type": "string"},
            "location": {"type": "string"}, "details": {"type": "string"},
            "remind_before_minutes": {"type": "integer"}}, "required": ["reminder_id"]}}},
    {"type": "function", "function": {
        "name": "query_courses",
        "description": "查询我的课表。weekday 可选(1=周一…7=周日)，留空返回整周。问'今天/周几有什么课'时调用。",
        "parameters": {"type": "object", "properties": {
            "weekday": {"type": "integer", "description": "1-7，留空返回整周"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "remember",
        "description": "记住关于用户的长期信息(专业/爱好/性格/年龄/习惯等)。key 是类别，value 是内容。",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "类别，如 专业/爱好/年龄"},
            "value": {"type": "string", "description": "具体内容"}}, "required": ["key", "value"]}}},
    {"type": "function", "function": {
        "name": "forget",
        "description": "忘记某条已记住的个人信息。",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "description": "要忘记的类别"}}, "required": ["key"]}}},
    {"type": "function", "function": {
        "name": "get_profile",
        "description": "查看 agent 当前记住的关于用户的全部长期信息。",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "list_wechat_notices",
        "description": "查看最近已抓取并总结的学校公众号通知。importance 可选 high/medium/low/all。",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "条数，默认10"},
            "importance": {"type": "string", "description": "high/medium/low/all"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "search_wechat_notices",
        "description": "在已抓取的公众号通知里按关键词搜索。",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "关键词"},
            "limit": {"type": "integer", "description": "条数，默认10"}}, "required": ["keyword"]}}},
]


# ======================================================================
# 2) dispatch 表：工具名 → 可调用对象，依赖经 tool_context 注入
# ======================================================================
def build_dispatch(tool_context: dict | None = None) -> dict:
    ctx = tool_context or {}
    reminder_service = ctx.get("reminder_service")
    memory_store = ctx.get("memory_store")
    wechat_store = ctx.get("wechat_store")
    return {
        "get_weather": lambda city: get_weather(city),
        "web_search": lambda query, max_results=None: web_search(query, max_results),
        "search_zju_notices": lambda keyword="", limit=10:
            json.dumps(zju_notices.search_notices(keyword, limit), ensure_ascii=False),
        "get_zju_notice_detail": lambda news_id:
            json.dumps(zju_notices.get_notice_detail(news_id), ensure_ascii=False),
        "create_reminder": lambda title, event_time, location="", details="", remind_before_minutes=30:
            reminder_tools.create_reminder(reminder_service, ctx, title=title, event_time=event_time,
                                           location=location, details=details,
                                           remind_before_minutes=remind_before_minutes),
        "list_reminders": lambda: reminder_tools.list_reminders(reminder_service, ctx),
        "cancel_reminder": lambda reminder_id:
            reminder_tools.cancel_reminder(reminder_service, ctx, reminder_id=reminder_id),
        "update_reminder": lambda reminder_id, title=None, event_time=None, location=None,
                                  details=None, remind_before_minutes=None:
            reminder_tools.update_reminder(reminder_service, ctx, reminder_id=reminder_id, title=title,
                                           event_time=event_time, location=location, details=details,
                                           remind_before_minutes=remind_before_minutes),
        "query_courses": lambda weekday=None: course_tools.query_courses(memory_store, ctx, weekday=weekday),
        "remember": lambda key, value: memory_tools.remember(memory_store, ctx, key=key, value=value),
        "forget": lambda key: memory_tools.forget(memory_store, ctx, key=key),
        "get_profile": lambda: memory_tools.get_profile(memory_store, ctx),
        "list_wechat_notices": lambda limit=10, importance="all":
            wechat_tools.list_wechat_notices(wechat_store, limit=limit, importance=importance),
        "search_wechat_notices": lambda keyword, limit=10:
            wechat_tools.search_wechat_notices(wechat_store, keyword=keyword, limit=limit),
    }


# ======================================================================
# 3) 主循环
# ======================================================================
def run_agent(client, messages, max_steps: int = 6, tool_context=None) -> str:
    dispatch = build_dispatch(tool_context)
    for _step in range(max_steps):
        response = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS)
        msg = response.choices[0].message
        if not msg.tool_calls:
            return msg.content
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                           for tc in msg.tool_calls],
        })
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            print(f"[工具调用] {name}({args})")
            func = dispatch.get(name)
            if func is None:
                result = f"未知工具：{name}"
            else:
                try:
                    result = func(**args)
                except Exception as e:
                    result = f"工具执行出错：{e}"
            print(f"[工具结果] {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    return "（已达最大步数仍未得出结论，请换个问法再试。）"
