#!/usr/bin/env python3
"""
浙江大学本科教务网通知抓取，仅依赖标准库。对外暴露两个入口：

    - search_notices(keyword, limit)  搜索通知列表
    - get_notice_detail(news_id)      抓取指定通知正文

访问 zdbk 的 session 可由 set_session 注入（校外经 WebVPN），未注入时走 urllib 直连。
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ---- 常量：浙大教务网的两个接口地址和请求头 ----
LIST_ENDPOINT = "https://zdbk.zju.edu.cn/jwglxt/xtgl/xwck_cxMoreLoginNews.html?doType=query"
DETAIL_ENDPOINT = "https://zdbk.zju.edu.cn/jwglxt/xtgl/xwck_ckLoginNews.html"
BASE_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    "Origin": "https://zdbk.zju.edu.cn",
    "Referer": "https://zdbk.zju.edu.cn/jwglxt/xtgl/xwck_cxMoreLoginNews.html",
    "User-Agent": "Mozilla/5.0 (compatible; ZjuNoticeAgentDemo/1.0; +https://zdbk.zju.edu.cn/)",
    "X-Requested-With": "XMLHttpRequest",
}
HTML_HEADERS = {"User-Agent": BASE_HEADERS["User-Agent"]}
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_PAGE_SIZE = 15
MAX_AUTO_PAGES = 10

# 由 communication.campus_http 在启动时注入；为 None 时回退 urllib 直连。
_SESSION = None


def set_session(session) -> None:
    """注入访问 zdbk 的 session（requests.Session 或 ZJUWebVPNSession）。"""
    global _SESSION
    _SESSION = session


@dataclass
class NoticeItem:
    """一条通知的结构化表示。"""
    news_id: str
    title: str
    published_at: str
    publisher: str
    url: str
    is_pinned: bool
    category: str
    summary: str
    content_preview: str


# ---------------------------------------------------------------------------
# 底层：HTTP 抓取
# ---------------------------------------------------------------------------
def fetch_json(url: str, data: dict[str, object]) -> dict[str, object]:
    # 优先走注入的 session（可能是 WebVPN），否则退回 urllib 直连。
    if _SESSION is not None:
        try:
            resp = _SESSION.post(url, data=data, headers=BASE_HEADERS, timeout=DEFAULT_TIMEOUT_SECONDS)
            resp.raise_for_status()
            return json.loads(resp.text)
        except json.JSONDecodeError as error:
            raise RuntimeError("通知列表返回了无法解析的 JSON。") from error
        except Exception as error:
            raise RuntimeError(f"抓取通知列表失败：{error}") from error

    body = urlencode(data).encode("utf-8")
    request = Request(url, data=body, headers=BASE_HEADERS)
    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8", errors="ignore"))
    except HTTPError as error:
        raise RuntimeError(f"抓取通知列表失败：HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"抓取通知列表失败：{error.reason}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("通知列表返回了无法解析的 JSON。") from error


def fetch_html(url: str) -> str:
    if _SESSION is not None:
        try:
            resp = _SESSION.get(url, headers=HTML_HEADERS, timeout=DEFAULT_TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp.text
        except Exception as error:
            raise RuntimeError(f"抓取通知详情失败：{error}") from error

    request = Request(url, headers=HTML_HEADERS)
    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", errors="ignore")
    except HTTPError as error:
        raise RuntimeError(f"抓取通知详情失败：HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"抓取通知详情失败：{error.reason}") from error


# ---------------------------------------------------------------------------
# 文本清洗
# ---------------------------------------------------------------------------
def clean_text(value: str) -> str:
    """把 HTML 片段转成纯文本（去标签、解转义、压空白）。"""
    text = html.unescape(value or "")
    text = text.replace("\xa0", " ").replace("　", " ")
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"(?i)</tr\s*>", "\n", text)
    text = re.sub(r"(?i)</h[1-6]\s*>", "\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def shorten_text(value: str, limit: int = 180) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def build_detail_url(news_id: str) -> str:
    return f"{DETAIL_ENDPOINT}?xwbh={news_id}"


# ---------------------------------------------------------------------------
# 列表解析
# ---------------------------------------------------------------------------
def normalize_item(item: dict[str, object]) -> NoticeItem:
    """把接口返回的原始字段（xwbt/fbsj…）规整成 NoticeItem。"""
    title = str(item.get("xwbt", "")).strip()
    published_at = str(item.get("fbsj", "")).strip()
    publisher = str(item.get("xwfbr", "") or item.get("fbr", "")).strip()
    news_id = str(item.get("xwbh", "")).strip()
    category = str(item.get("gglb", "")).strip()
    content_text = clean_text(str(item.get("fbnr", "") or ""))
    return NoticeItem(
        news_id=news_id,
        title=title,
        published_at=published_at,
        publisher=publisher,
        url=build_detail_url(news_id) if news_id else "",
        is_pinned=str(item.get("sfzd", "0")).strip() == "1",
        category=category,
        summary=shorten_text(content_text, 100),
        content_preview=shorten_text(content_text, 240),
    )


def fetch_list_page(page: int, page_size: int) -> dict[str, object]:
    payload = fetch_json(
        LIST_ENDPOINT,
        {
            "_search": "false",
            "nd": "0",
            "queryModel.showCount": str(page_size),
            "queryModel.currentPage": str(page),
            "queryModel.sortName": "sfzd desc,fbsj",
            "queryModel.sortOrder": "desc",
            "time": "0",
        },
    )
    if not isinstance(payload.get("items"), list):
        raise RuntimeError("通知列表返回格式异常：缺少 items。")
    return payload


def iter_list_items(all_pages: bool, page_size: int) -> Iterable[tuple[int, list[NoticeItem], dict[str, object]]]:
    first_payload = fetch_list_page(1, page_size)
    total_pages = int(first_payload.get("totalPage") or 1)
    yield 1, [normalize_item(it) for it in first_payload["items"]], first_payload
    if not all_pages:
        return
    upper_bound = min(max(total_pages, 1), MAX_AUTO_PAGES)
    for page in range(2, upper_bound + 1):
        payload = fetch_list_page(page, page_size)
        yield page, [normalize_item(it) for it in payload["items"]], payload


def filter_items(items: Iterable[NoticeItem], keyword: str) -> list[NoticeItem]:
    keyword = keyword.strip()
    if not keyword:
        return list(items)
    lowered = keyword.casefold()
    return [
        item for item in items
        if lowered in item.title.casefold()
        or lowered in item.summary.casefold()
        or lowered in item.content_preview.casefold()
    ]


# ---------------------------------------------------------------------------
# 详情解析
# ---------------------------------------------------------------------------
def extract_first_group(pattern: str, page_html: str) -> str:
    match = re.search(pattern, page_html, re.S)
    return clean_text(match.group(1)) if match else ""


def parse_detail(news_id: str, page_html: str) -> dict[str, object]:
    title = extract_first_group(r'<h3[^>]*class="text-center"[^>]*>(.*?)</h3>', page_html)
    publisher = extract_first_group(r"<span>\s*发布人：\s*(.*?)</span>", page_html)
    published_at = extract_first_group(r"<span>\s*发布时间：\s*(.*?)</span>", page_html)
    visit_count_text = extract_first_group(r"<span>\s*浏览人数：\s*(.*?)</span>", page_html)
    content_html_match = re.search(r'<div[^>]*class="news_con"[^>]*>(.*?)</div>\s*</div>', page_html, re.S)
    content_html = content_html_match.group(1).strip() if content_html_match else ""
    content_text = clean_text(content_html)
    if not title:
        raise RuntimeError("通知详情解析失败：未提取到标题。")
    visit_count = int(visit_count_text) if visit_count_text.isdigit() else None
    return {
        "news_id": news_id,
        "title": title,
        "published_at": published_at,
        "publisher": publisher,
        "visit_count": visit_count,
        "url": build_detail_url(news_id),
        "content": content_text,
        "content_preview": shorten_text(content_text, 240),
    }


def find_notice_in_list(news_id: str, page_size: int = 50) -> NoticeItem | None:
    for _page, items, _payload in iter_list_items(all_pages=True, page_size=page_size):
        for item in items:
            if item.news_id == news_id:
                return item
    return None


# ===========================================================================
# 对外入口
# ===========================================================================
def search_notices(keyword: str = "", limit: int = 10) -> dict[str, object]:
    """搜索教务网通知列表，keyword 留空时返回最新；返回含 count 与 items 的 dict。"""
    collected: list[NoticeItem] = []
    # 关键词搜索时翻多页找；不带关键词只看第一页（最新的）即可
    for _page, items, _payload in iter_list_items(all_pages=bool(keyword.strip()),
                                                  page_size=DEFAULT_PAGE_SIZE):
        collected.extend(items)
    filtered = filter_items(collected, keyword)
    if limit > 0:
        filtered = filtered[:limit]
    return {
        "ok": True,
        "keyword": keyword,
        "count": len(filtered),
        "items": [asdict(item) for item in filtered],
    }


def get_notice_detail(news_id: str) -> dict[str, object]:
    """按 news_id 抓取通知正文；详情页结构异常时回退为列表摘要并附 note 字段。"""
    news_id = (news_id or "").strip()
    if not news_id:
        raise RuntimeError("news_id 不能为空。")
    page_html = fetch_html(build_detail_url(news_id))
    try:
        return {"ok": True, "item": parse_detail(news_id, page_html)}
    except RuntimeError:
        fallback = find_notice_in_list(news_id)
        if fallback is None:
            raise
        return {
            "ok": True,
            "item": {
                "news_id": fallback.news_id,
                "title": fallback.title,
                "published_at": fallback.published_at,
                "publisher": fallback.publisher,
                "url": fallback.url,
                "content": fallback.content_preview,
                "note": "详情页未返回标准正文结构，已回退为列表摘要。",
            },
        }
