"""
从 we-mp-rss 的 JSON Feed（/feed/all.json）拉取公众号文章。

将上游字段（id/title/link/updated/content/channel_name 等）规整为统一的内部 dict：
    {article_id, account, title, url, published_at, content_text}
去重与总结分别由 store / summarizer 负责。url/limit 缺省时取 WECHAT_RSS_URL /
WECHAT_RSS_LIMIT，未配置 URL 时各调用安全跳过。
"""

from __future__ import annotations

import hashlib
import html
import os
import re

import requests


def _clean_html(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


class WechatRssFetcher:
    def __init__(self, url: str | None = None, limit: int | None = None, timeout: int = 20):
        self.url = (url if url is not None else os.getenv("WECHAT_RSS_URL", "")).strip()
        self.limit = int(limit if limit is not None else os.getenv("WECHAT_RSS_LIMIT", "20"))
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def fetch_latest(self, limit: int | None = None) -> dict:
        """拉取最新文章，返回 {ok,count,items,error}。未配置 URL 时安全跳过。"""
        if not self.enabled:
            return {"ok": False, "error": "WECHAT_RSS_URL 未配置，公众号源已跳过。", "count": 0, "items": []}
        n = int(limit) if limit else self.limit
        try:
            resp = requests.get(self.url, params={"limit": n}, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            return {"ok": False, "error": f"拉取 we-mp-rss 失败：{e}", "count": 0, "items": []}
        except ValueError:
            return {"ok": False, "error": "we-mp-rss 返回的不是 JSON。", "count": 0, "items": []}
        items = self._extract_items(data, n)
        return {"ok": True, "count": len(items), "items": items}

    def fetch_articles(self, limit: int | None = None) -> list[dict]:
        """供后台采集器使用：只返回文章列表（不带 ok 包装）。"""
        return self.fetch_latest(limit).get("items", [])

    def _extract_items(self, data, limit: int) -> list[dict]:
        items = data.get("items") if isinstance(data, dict) else None
        if not items:
            return []
        out: list[dict] = []
        for it in items[:limit]:
            # 原文链接：we-mp-rss 用 link；兼容标准 JSON Feed 的 url
            link = str(it.get("link") or it.get("url") or it.get("external_url") or "").strip()
            # 去重 id：优先 we-mp-rss 的稳定 id，否则按链接/标题 hash
            real_id = it.get("id")
            if real_id:
                article_id = str(real_id)
            else:
                seed = link or str(it.get("title", ""))
                article_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
            # 公众号名：we-mp-rss 用 channel_name / feed.name；兼容其它 RSS 源
            account = str(
                it.get("channel_name")
                or (it.get("feed") or {}).get("name")
                or it.get("mp_name")
                or it.get("author")
                or it.get("source")
                or "公众号"
            )
            # 正文：we-mp-rss 用 content(HTML)；兼容 content_html / content_text / description
            content = (
                it.get("content")
                or it.get("content_html")
                or it.get("content_text")
                or it.get("description")
                or ""
            )
            # 发布时间：we-mp-rss 用 updated；兼容 date_published
            published_at = str(it.get("updated") or it.get("date_published") or "").strip()
            out.append(
                {
                    "article_id": article_id,
                    "account": account,
                    "title": str(it.get("title") or "").strip(),
                    "url": link,
                    "published_at": published_at,
                    "content_text": _clean_html(content),
                }
            )
        return out


# 向后兼容的薄封装：旧调用 fetch_feed(url, limit) 仍可用（后台采集器在用）。
def fetch_feed(url: str, limit: int = 20, timeout: int = 20) -> list[dict]:
    return WechatRssFetcher(url, limit, timeout).fetch_articles()
