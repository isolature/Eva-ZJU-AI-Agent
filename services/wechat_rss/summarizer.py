"""
用 DeepSeek 把公众号文章总结成结构化通知摘要，并判断重要性。
"""

from __future__ import annotations

import json

MODEL = "deepseek-v4-flash"

SYSTEM_PROMPT = (
    "你是校园通知筛选助手。给你一篇微信公众号文章，请判断它是否是面向学生的“通知”，"
    "并给出结构化摘要。只输出 JSON，不要解释。字段："
    "is_notice(bool 是否通知类), "
    "importance(high/medium/low: 有截止/考试/选课/报名/缴费/学籍/奖学金/需要本人操作=high; "
    "一般活动/讲座/竞赛=medium; 纯宣传/风采=low), "
    "summary(一句话摘要), action_required(需要做什么,没有则空字符串), deadline(截止时间,没有则空字符串)。"
)


def summarize_article(client, article: dict) -> dict:
    content = (article.get("content_text") or "")[:6000]
    user_text = (
        f"公众号：{article.get('account','')}\n"
        f"标题：{article.get('title','')}\n"
        f"发布时间：{article.get('published_at','')}\n"
        f"正文：\n{content}"
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    return {
        "is_notice": bool(data.get("is_notice", False)),
        "importance": str(data.get("importance", "low")).lower(),
        "summary": str(data.get("summary", "")).strip(),
        "action_required": str(data.get("action_required", "")).strip(),
        "deadline": str(data.get("deadline", "")).strip(),
    }
