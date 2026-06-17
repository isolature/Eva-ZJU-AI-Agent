"""
教务网（zdbk.zju.edu.cn）访问的 session 构造。该站点仅校内可达，部署在校外时需经
浙大 WebVPN 借道，且只对教务通知生效，不影响天气、搜索等公网请求。

底层复用 ZJUWebVPN 包：其 ZJUWebVPNSession 继承 requests.Session 并重写 request()，
登录后自动改写 zdbk 地址，业务侧拿到 session 直接使用。

ZJU_WEBVPN_MODE：
  auto   自动探测，校园网直连、公网走 WebVPN（默认）
  vpn    强制 WebVPN
  direct 强制直连
"""

from __future__ import annotations

import os

# 只允许这些域名走 WebVPN，防止误把公网服务绕进去
CAMPUS_ONLY_HOSTS = {"zdbk.zju.edu.cn"}


def build_zju_session():
    """按当前模式产出一个用于访问 zdbk 的 session（普通 / WebVPN）。

    返回 requests.Session 或 ZJUWebVPNSession；失败时返回 None（上层退回 urllib 直连）。
    """
    mode = (os.getenv("ZJU_WEBVPN_MODE", "auto") or "auto").strip().lower()

    if mode == "direct":
        return _plain_session()

    try:
        import ZJUWebVPN  # 延迟导入：没装包也不影响其它功能
    except ImportError:
        print("[WebVPN] 未安装 ZJUWebVPN 包，教务通知将尝试直连。")
        return None if mode == "auto" else _plain_session()

    if mode == "auto":
        try:
            net = ZJUWebVPN.ZJUWebVPNSession.check_network()  # 0=非校园网 1/2=校园网
        except Exception:
            net = 0  # 探测失败保守当公网
        if net != 0:
            print("[WebVPN] 检测到校园网，直连 zdbk。")
            return _plain_session()

    user = os.getenv("ZJU_WEBVPN_USERNAME")
    pwd = os.getenv("ZJU_WEBVPN_PASSWORD")
    if not user or not pwd:
        print("[WebVPN] 当前需要 WebVPN，但缺少 ZJU_WEBVPN_USERNAME/PASSWORD，教务通知可能不可用。")
        return None
    try:
        session = ZJUWebVPN.ZJUWebVPNSession(user, pwd)
        print("[WebVPN] WebVPN 登录成功，教务通知将借道访问。")
        return session
    except Exception as e:
        print(f"[WebVPN] WebVPN 登录失败：{e}")
        return None


def install_zju_session():
    """启动时调用：构建 session 并注入给 zju_notices 工具。失败则保持 urllib 直连。"""
    session = build_zju_session()
    if session is not None:
        from tools import zju_notices
        zju_notices.set_session(session)
    return session


def _plain_session():
    try:
        import requests
        return requests.Session()
    except ImportError:
        return None
