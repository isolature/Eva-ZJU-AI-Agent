"""
联网搜索工具，基于 Tavily Search API，直接走 HTTP 不引入 SDK。
鉴权使用 TAVILY_API_KEY。
"""

from __future__ import annotations

import os

import requests

TAVILY_ENDPOINT = "https://api.tavily.com/search"


def web_search(query: str, max_results: int | None = None) -> str:
    """联网搜索，返回若干条结果（标题 + 摘要 + 链接）及 Tavily 的综合回答。"""
    query = (query or "").strip()
    if not query:
        return "搜索内容不能为空。"

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "未配置 TAVILY_API_KEY，联网搜索不可用，请在 .env 中填写。"

    if max_results is None:
        max_results = int(os.getenv("TAVILY_MAX_RESULTS", "5"))
    max_results = max(1, min(int(max_results), 10))

    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": True,        # 附带综合回答，减少二次调用
        "include_raw_content": False,
    }
    try:
        resp = requests.post(
            TAVILY_ENDPOINT,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return f"联网搜索失败：{e}"
    except ValueError:
        return "联网搜索返回了无法解析的内容。"

    lines: list[str] = []
    answer = (data.get("answer") or "").strip()
    if answer:
        lines.append(f"综合回答：{answer}")

    results = data.get("results") or []
    if not results and not answer:
        return f"没有搜到关于「{query}」的结果。"

    for i, item in enumerate(results, 1):
        title = (item.get("title") or "").strip()
        content = (item.get("content") or "").strip()
        url = (item.get("url") or "").strip()
        if len(content) > 300:
            content = content[:300].rstrip() + "…"
        lines.append(f"{i}. {title}\n   {content}\n   来源：{url}")

    return "\n".join(lines)
