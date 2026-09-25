"""B站解析：官方 API 直连（绕开 yt-dlp 在部分服务器上的 412 风控）。

- 元数据：/x/web-interface/view
- 播放地址：/x/player/playurl（低清 durl 直链 mp4，无需登录）
- 下载时附带 Referer/UA，直链 CDN 可正常拉取。
"""

from __future__ import annotations

import os
import re
import time
import uuid

from .downloader import download_url, http_get_final_url, http_get_json

BILI_HEADERS = {
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}

_BV_RE = re.compile(r"BV[0-9A-Za-z]+")
_AV_RE = re.compile(r"(?:/video/|^)av(\d+)", re.IGNORECASE)


def parse_bili_id(text: str) -> tuple[str, str] | None:
    """从文本/URL 中提取 B站视频标识。返回 ("bvid", "BV...") 或 ("aid", "数字")。"""
    if not text:
        return None
    m = _BV_RE.search(text)
    if m:
        return "bvid", m.group(0)
    m = _AV_RE.search(text)
    if m:
        return "aid", m.group(1)
    return None


async def resolve_ref(url_or_text: str) -> tuple[str, str] | None:
    ref = parse_bili_id(url_or_text)
    if ref:
        return ref
    if "b23.tv" in url_or_text or "bili2233" in url_or_text:
        final = await http_get_final_url(url_or_text, headers=BILI_HEADERS)
        if final:
            return parse_bili_id(final)
    return None


async def fetch_video_info(url_or_text: str) -> dict | None:
    """获取视频元数据（标题/简介/时长/UP主/发布时间/cid）。失败返回 None。"""
    ref = await resolve_ref(url_or_text)
    if not ref:
        return None
    key, value = ref
    api = (f"https://api.bilibili.com/x/web-interface/view?"
           f"{'bvid' if key == 'bvid' else 'aid'}={value}")
    data = await http_get_json(api, headers=BILI_HEADERS)
    if not isinstance(data, dict) or data.get("code") != 0:
        return None
    d = data.get("data") or {}
    cid = d.get("cid")
    if not cid:
        pages = d.get("pages") or []
        if pages:
            cid = pages[0].get("cid")
    return {
        "bvid": str(d.get("bvid") or ""),
        "aid": d.get("aid"),
        "cid": cid,
        "title": str(d.get("title") or ""),
        "desc": str(d.get("desc") or "")[:800],
        "pubdate": d.get("pubdate"),
        "duration": d.get("duration"),
        "owner": str((d.get("owner") or {}).get("name") or ""),
    }


async def fetch_play_url(info: dict, qn: int = 64) -> str | None:
    """获取可下载的直链（低清 durl，单文件含音轨）。"""
    if not info.get("cid"):
        return None
    for q in (qn, 32, 16):
        api = (f"https://api.bilibili.com/x/player/playurl?"
               f"bvid={info.get('bvid')}&cid={info.get('cid')}"
               f"&qn={q}&fnval=1&fnver=0&fourk=1")
        data = await http_get_json(api, headers=BILI_HEADERS)
        if not isinstance(data, dict) or data.get("code") != 0:
            continue
        d = data.get("data") or {}
        durl = d.get("durl") or []
        if durl and durl[0].get("url"):
            return str(durl[0]["url"])
    return None


async def download_video(info: dict, dest_dir: str, max_bytes: int) -> str | None:
    """下载视频（API 直链通道）。返回文件路径或 None。"""
    url = await fetch_play_url(info)
    if not url:
        return None
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"bili_{info.get('bvid') or uuid.uuid4().hex[:6]}.mp4")
    ok, _msg = await download_url(url, dest, max_bytes, headers=BILI_HEADERS)
    return dest if ok else None


def format_pubdate(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.localtime(int(ts)))
    except Exception:
        return ""
