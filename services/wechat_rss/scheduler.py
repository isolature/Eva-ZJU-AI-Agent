"""
公众号通知后台轮询：拉 RSS → 去重入库 → LLM 总结 → 高重要性主动推送。

和 agent 主对话解耦：用户提问只查本地摘要（tools/wechat.py），不在提问时实时爬取。
"""

from __future__ import annotations

import threading

from services.wechat_rss import summarizer
from services.wechat_rss.fetcher import WechatRssFetcher
from services.wechat_rss.store import WeChatRssStore


class WeChatRssScheduler:
    def __init__(
        self,
        store: WeChatRssStore,
        client,
        notifier=None,
        owner_userid: str | None = None,
        feed_url: str = "http://127.0.0.1:8001/feed/all.json",
        poll_seconds: int = 1800,
        limit: int = 20,
        summarize_per_round: int = 5,
    ):
        self.store = store
        self.client = client
        self.notifier = notifier
        self.owner_userid = (owner_userid or "").strip()
        self.feed_url = feed_url
        self.poll_seconds = max(60, int(poll_seconds))
        self.limit = int(limit)
        self.fetcher = WechatRssFetcher(feed_url, self.limit)
        self.summarize_per_round = int(summarize_per_round)
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="wechat-rss", daemon=True)
        self._thread.start()
        print(f"[公众号] 已启动，轮询间隔 {self.poll_seconds} 秒，源 {self.feed_url}")

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as e:
                print(f"[公众号] 轮询失败：{e}")
            self._stop.wait(self.poll_seconds)

    def run_once(self):
        # 1) 拉取 + 去重入库（失败/空都打日志，便于排查）
        res = self.fetcher.fetch_latest()
        if not res["ok"]:
            print(f"[公众号] 拉取失败：{res['error']}（URL={self.feed_url}）")
            articles = []
        else:
            articles = res["items"]
            if not articles:
                print(f"[公众号] 拉取成功但 items 为空（URL={self.feed_url}）。")
        new_count = refreshed = 0
        for a in articles:
            if self.store.save_new_article(a):
                new_count += 1
            elif self.store.refresh_if_better(a):
                refreshed += 1
        if new_count or refreshed:
            print(f"[公众号] 新增 {new_count} 篇，回填正文 {refreshed} 篇。")

        # 2) 对未总结的文章做 LLM 总结
        for art in self.store.list_pending_summary(limit=self.summarize_per_round):
            try:
                summary = summarizer.summarize_article(self.client, art)
                self.store.update_summary(art["id"], summary)
            except Exception as e:
                self.store.mark_summary_failed(art["id"])
                print(f"[公众号] 总结失败 #{art['id']}：{e}")

        # 3) 高重要性通知主动推送
        if self.notifier and self.notifier.enabled and self.owner_userid:
            for art in self.store.list_pending_notify(importance=("high",)):
                try:
                    self.notifier.send_markdown(
                        self.owner_userid,
                        f"学校通知：{art['title']}",
                        self._format(art),
                    )
                    self.store.mark_notified(art["id"])
                except Exception as e:
                    print(f"[公众号] 推送失败 #{art['id']}：{e}")

    @staticmethod
    def _format(art: dict) -> str:
        lines = [
            "# 学校公众号通知",
            f"**来源**：{art.get('account','')}",
            f"**标题**：{art.get('title','')}",
            f"**摘要**：{art.get('summary','')}",
        ]
        if art.get("action_required"):
            lines.append(f"**你需要做**：{art['action_required']}")
        if art.get("deadline"):
            lines.append(f"**截止**：{art['deadline']}")
        if art.get("url"):
            lines.append(f"[查看原文]({art['url']})")
        return "\n\n".join(lines)
