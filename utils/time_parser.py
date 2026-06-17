from __future__ import annotations

from datetime import datetime, timedelta, timezone


SHANGHAI_TZ = timezone(timedelta(hours=8))
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DISPLAY_FORMAT = "%Y-%m-%d %H:%M"
_SUPPORTED_INPUT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)


def now_shanghai() -> datetime:
    """返回带时区的当前上海时间。"""
    return datetime.now(SHANGHAI_TZ)


def parse_user_datetime(value: str) -> datetime:
    """将日期时间字符串解析为上海时区时间。

    只接受常见的绝对时间格式；无法识别（如未展开的相对时间）时抛出 ValueError。
    """
    raw = (value or "").strip()
    if not raw:
        raise ValueError("event_time 不能为空，请提供明确的日期和时间。")

    normalized = raw.replace("T", " ")
    if normalized.endswith("Z"):
        # 允许偶尔出现的 UTC 结尾格式，先按 UTC 解析再转北京时间。
        dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        return dt.astimezone(SHANGHAI_TZ)

    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        dt = None

    if dt is not None:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=SHANGHAI_TZ)
        return dt.astimezone(SHANGHAI_TZ)

    for fmt in _SUPPORTED_INPUT_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=SHANGHAI_TZ)
        except ValueError:
            continue

    raise ValueError(
        "时间格式无法识别，请使用类似 2026-06-11 15:00:00 的北京时间绝对时间。"
    )


def to_storage_string(value: datetime) -> str:
    """格式化为定长存储字符串（北京时间）。"""
    return value.astimezone(SHANGHAI_TZ).strftime(DATETIME_FORMAT)


def to_display_string(value: datetime | str) -> str:
    """格式化为面向展示的字符串，接受 datetime 或存储字符串。"""
    dt = value if isinstance(value, datetime) else parse_user_datetime(value)
    return dt.astimezone(SHANGHAI_TZ).strftime(DISPLAY_FORMAT)
