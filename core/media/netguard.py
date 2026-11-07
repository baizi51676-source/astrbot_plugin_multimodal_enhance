"""网络边界校验：拒绝内网 / 回环 / 链路本地 / 元数据地址与非 http(s) 协议。

媒体地址来自用户消息（直链、分享卡片、组件字段），属于不可信输入，
下载前一律先过这里，避免插件被诱导访问内网服务或主机本地文件。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")

# 明确指向本机的常见域名后缀 / 主机名
_BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost"}


def _reason_for_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """不安全地址返回原因，安全则返回 None。"""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_unspecified:
        return "未指定地址"
    if ip.is_loopback:
        return "回环地址"
    if ip.is_link_local:
        return "链路本地地址"
    if ip.is_private:
        return "内网地址"
    if ip.is_reserved:
        return "保留地址"
    if ip.is_multicast:
        return "组播地址"
    return None


def _check_host(host: str) -> str | None:
    """校验主机名 / IP 字面量，不安全返回原因。"""
    host = host.strip().strip("[]").lower().rstrip(".")
    if not host:
        return "缺少主机名"
    if host in _BLOCKED_HOSTS or host.endswith(".local"):
        return "本机地址"

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return _reason_for_ip(literal)

    # 域名：解析出全部地址，任一不安全即拒绝（挡住把域名指向内网的常见做法）
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return "域名无法解析"
    resolved = False
    for info in infos:
        addr = info[4][0] if len(info) > 4 else ""
        try:
            ip = ipaddress.ip_address(str(addr).split("%")[0])
        except ValueError:
            continue
        resolved = True
        reason = _reason_for_ip(ip)
        if reason:
            return f"域名解析到{reason}"
    if not resolved:
        return "域名无法解析"
    return None


def is_safe_url(url: str, allow_hosts: set[str] | frozenset[str] = frozenset()) -> tuple[bool, str]:
    """判断 URL 是否允许下载。返回 (是否安全, 原因)。"""
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False, "地址无法解析"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        return False, f"协议不允许（{scheme or '空'}）"
    host = parsed.hostname or ""
    if not host:
        return False, "缺少主机名"
    if allow_hosts and host.lower() in {h.lower() for h in allow_hosts}:
        return True, ""
    reason = _check_host(host)
    if reason:
        return False, reason
    return True, ""


def masked_host(url: str) -> str:
    """用于日志：只保留主机名，避免把完整可疑 URL 打进日志。"""
    try:
        return urlparse(str(url or "")).hostname or "?"
    except Exception:
        return "?"
