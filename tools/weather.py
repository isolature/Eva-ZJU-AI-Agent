"""
天气查询工具，数据源为和风天气 QWeather，仅依赖标准库。

查询分两步：先经 GeoAPI 将城市名解析为 LocationID，再用该 ID 拉取实时天气。
鉴权通过请求头 X-QW-Api-Key 完成，需在 .env 配置 QWEATHER_API_KEY 与 QWEATHER_API_HOST。
"""

import gzip
import json
import os
import urllib.parse
import urllib.request


def _qweather_get(path: str, params: dict) -> dict:
    """对和风接口发起 GET 请求并返回解析后的 dict。

    key/host 取自环境变量；响应默认 gzip 压缩，按 Content-Encoding 决定是否解压。
    """
    api_key = os.getenv("QWEATHER_API_KEY")
    api_host = os.getenv("QWEATHER_API_HOST")
    if not api_key or not api_host:
        raise RuntimeError("没配置 QWEATHER_API_KEY / QWEATHER_API_HOST，请检查 .env")

    # 容错处理 host 被连同 scheme 一起填写的情况
    api_host = api_host.replace("https://", "").replace("http://", "").strip("/")

    url = f"https://{api_host}{path}?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={
            "X-QW-Api-Key": api_key,
            "Accept-Encoding": "gzip",
            "User-Agent": "ding-agent/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def get_weather(city: str) -> str:
    """查询城市实时天气，返回一句中文描述；查询失败时返回提示文本。"""
    # 城市名 → LocationID（和风以字符串 "200" 表示成功）
    geo = _qweather_get("/geo/v2/city/lookup", {"location": city, "number": 1, "lang": "zh"})
    if geo.get("code") != "200":
        return f"查询城市「{city}」失败（和风返回码 {geo.get('code')}）。"
    locations = geo.get("location") or []
    if not locations:
        return f"没查到「{city}」这个城市，请换个说法或确认城市名。"

    place = locations[0]
    location_id = place["id"]
    nice_name = " ".join(x for x in [place.get("adm1"), place.get("name")] if x)

    # LocationID → 实时天气
    wx = _qweather_get("/v7/weather/now", {"location": location_id, "lang": "zh"})
    if wx.get("code") != "200":
        return f"查询「{nice_name}」天气失败（和风返回码 {wx.get('code')}）。"

    now = wx.get("now", {})
    text = now.get("text")
    temp = now.get("temp")
    feels = now.get("feelsLike")
    humidity = now.get("humidity")
    wind_dir = now.get("windDir")
    wind_scale = now.get("windScale")
    wind_speed = now.get("windSpeed")

    return (
        f"{nice_name} 当前天气：{text}，"
        f"气温 {temp}℃（体感 {feels}℃），湿度 {humidity}%，"
        f"{wind_dir} {wind_scale} 级（{wind_speed} km/h）。"
    )
