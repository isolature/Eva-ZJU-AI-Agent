"""
程序入口。

依次完成：加载 .env、初始化 LLM 客户端与 SQLite 存储、导入课表、配置教务网
WebVPN、启动通知器及各后台调度器（提醒/公众号/简报），最后注册钉钉回调并阻塞运行。
"""

import json
import os

import dingtalk_stream
from openai import OpenAI

from communication.bot_handler import AgentBotHandler
from communication.notifier import DingTalkNotifier
from communication import campus_http
from services.memory.store import MemoryStore
from services.reminder.store import ReminderStore
from services.reminder.service import ReminderService
from services.reminder.scheduler import ReminderScheduler


def load_dotenv(path: str = ".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _import_courses(memory_store: MemoryStore, owner_userid: str):
    path = os.getenv("COURSES_JSON_PATH", "config/courses.json")
    if not owner_userid or not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            courses = json.load(f)
        result = memory_store.replace_courses(owner_userid, courses)
        print(f"[课表] 导入 {path}：{result.get('message', result)}")
    except Exception as e:
        print(f"[课表] 导入失败：{e}")


def main():
    load_dotenv()

    app_key = os.getenv("DINGTALK_APP_KEY")
    app_secret = os.getenv("DINGTALK_APP_SECRET")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    owner_userid = os.getenv("DINGTALK_OWNER_USERID", "").strip()

    if not app_key or not app_secret:
        print("错误：缺少 DINGTALK_APP_KEY / DINGTALK_APP_SECRET，请检查 .env。")
        return
    if not deepseek_key:
        print("错误：缺少 DEEPSEEK_API_KEY，请检查 .env。")
        return

    client = OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")

    # ---- 存储（共用同一个 SQLite 文件）----
    db_path = os.getenv("MEMORY_DB_PATH", "data/agent.db")
    memory_store = MemoryStore(db_path)
    reminder_store = ReminderStore(os.getenv("REMINDER_DB_PATH", db_path))

    # ---- 课表导入 ----
    _import_courses(memory_store, owner_userid)

    # ---- 教务网 WebVPN（校外才需要；失败不影响其它功能）----
    campus_http.install_zju_session()

    # ---- 主动通知器 ----
    notifier = DingTalkNotifier(app_key=app_key, app_secret=app_secret, agent_id=os.getenv("DINGTALK_AGENT_ID"))
    reminder_service = ReminderService(reminder_store, notifications_enabled=notifier.enabled)

    schedulers = []

    # ---- 日程提醒调度 ----
    if notifier.enabled:
        rsched = ReminderScheduler(reminder_store, notifier,
                                   poll_seconds=int(os.getenv("REMINDER_POLL_SECONDS", "30")),
                                   max_retries=int(os.getenv("REMINDER_MAX_RETRIES", "3")))
        rsched.start()
        schedulers.append(rsched)
    else:
        print("警告：未配置 DINGTALK_AGENT_ID，提醒会保存但不会主动发送。")

    # ---- 公众号通知（消费 we-mp-rss；配置了 WECHAT_RSS_URL 才启用，留空则跳过）----
    wechat_store = None
    wechat_url = os.getenv("WECHAT_RSS_URL", "").strip()
    if wechat_url:
        from services.wechat_rss.store import WeChatRssStore
        from services.wechat_rss.scheduler import WeChatRssScheduler
        wechat_store = WeChatRssStore(db_path)
        wsched = WeChatRssScheduler(
            wechat_store, client, notifier=notifier, owner_userid=owner_userid,
            feed_url=wechat_url,
            poll_seconds=int(os.getenv("WECHAT_RSS_POLL_SECONDS", "1800")),
            limit=int(os.getenv("WECHAT_RSS_LIMIT", "20")),
        )
        wsched.start()
        schedulers.append(wsched)
    else:
        print("提示：未配置 WECHAT_RSS_URL，公众号通知功能已跳过。")

    # ---- 每日简报 ----
    if _truthy("DAILY_BRIEFING_ENABLED", "true"):
        from services.briefing.service import DailyBriefingService
        from services.briefing.scheduler import DailyBriefingScheduler
        briefing = DailyBriefingService(
            client, memory_store=memory_store, reminder_store=reminder_store,
            wechat_store=wechat_store, notifier=notifier, owner_userid=owner_userid,
            city=os.getenv("DAILY_BRIEFING_CITY", "杭州"),
        )
        bsched = DailyBriefingScheduler(briefing, send_time=os.getenv("DAILY_BRIEFING_TIME", "07:30"))
        bsched.start()
        schedulers.append(bsched)

    # ---- 钉钉接入 ----
    handler = AgentBotHandler(
        client, reminder_service=reminder_service, memory_store=memory_store,
        wechat_store=wechat_store,
    )
    credential = dingtalk_stream.Credential(app_key, app_secret)
    stream_client = dingtalk_stream.DingTalkStreamClient(credential)
    stream_client.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, handler)

    print("已连接钉钉，等待群里 @机器人……（Ctrl+C 退出）")
    try:
        stream_client.start_forever()
    finally:
        for s in schedulers:
            s.stop()


if __name__ == "__main__":
    main()
