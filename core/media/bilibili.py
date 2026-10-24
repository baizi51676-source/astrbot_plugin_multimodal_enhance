"""B站解析：官方 API 直连（绕开 yt-dlp 在部分服务器上的 412 风控）。

- 元数据：/x/web-interface/view；若被 -412 风控，回退到搜索 / 分P列表 / 简介接口
- 播放地址：/x/player/playurl（低清 durl 直链 mp4，无需登录）
- 下载时附带 Referer/UA，直链 CDN 可正常拉取。
"""

from __future__ import annotations

import os
import re
import time
import uuid
from urllib.parse import quote

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
    """获取视频元数据（标题/简介/时长/UP主/发布时间/cid）。

    优先官方 view 接口；若被风控（-412）则依次回退到搜索、分P列表与简介接口，
    尽量在数据中心 IP 上也能拿到可用信息。
    """
    ref = await resolve_ref(url_or_text)
    if not ref:
        return None
    key, value = ref

    info = await _fetch_view(key, value)
    if info:
        return info

    # 回退通道：view 被风控时仍可用的接口
    item = await _fetch_search(key, value)
    page = await _fetch_pagelist(key, value)
    desc = await _fetch_desc(key, value)
    if not any([item, page, desc]):
        return None

    item = item or {}
    page = page or {}
    bvid = str(item.get("bvid") or (value if key == "bvid" else "") or "")
    aid = item.get("aid") or (int(value) if key == "aid" else None)
    duration = page.get("duration") or _parse_duration(item.get("duration"))
    title = _clean_title(item.get("title") or page.get("part") or "")
    return {
        "bvid": bvid,
        "aid": aid,
        "cid": page.get("cid"),
        "title": title,
        "desc": str(desc or item.get("description") or "")[:800],
        "pubdate": item.get("pubdate") or page.get("ctime"),
        "duration": duration,
        "owner": str(item.get("author") or ""),
        "cover": str(item.get("pic") or ""),
    }


async def _fetch_view(key: str, value: str) -> dict | None:
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
        "cover": str(d.get("pic") or ""),
    }


async def _fetch_search(key: str, value: str) -> dict | None:
    """搜索接口：数据中心 IP 上通常未被 view 的 -412 风控覆盖。"""
    keyword = value if key == "bvid" else f"av{value}"
    url = ("https://api.bilibili.com/x/web-interface/search/type?"
           f"search_type=video&keyword={quote(keyword)}")
    data = await http_get_json(url, headers=BILI_HEADERS)
    if not isinstance(data, dict) or data.get("code") != 0:
        return None
    result = (data.get("data") or {}).get("result") or []
    for item in result:
        if not isinstance(item, dict):
            continue
        if key == "bvid" and str(item.get("bvid") or "") == value:
            return item
        if key == "aid" and str(item.get("aid") or "") == value:
            return item
    return result[0] if result and isinstance(result[0], dict) else None


async def _fetch_pagelist(key: str, value: str) -> dict | None:
    url = (f"https://api.bilibili.com/x/player/pagelist?"
           f"{'bvid' if key == 'bvid' else 'aid'}={value}")
    data = await http_get_json(url, headers=BILI_HEADERS)
    if not isinstance(data, dict) or data.get("code") != 0:
        return None
    pages = data.get("data") or []
    return pages[0] if pages and isinstance(pages[0], dict) else None


async def _fetch_desc(key: str, value: str) -> str:
    url = (f"https://api.bilibili.com/x/web-interface/archive/desc?"
           f"{'bvid' if key == 'bvid' else 'aid'}={value}")
    data = await http_get_json(url, headers=BILI_HEADERS)
    if not isinstance(data, dict) or data.get("code") != 0:
        return ""
    return str(data.get("data") or "")


def _parse_duration(value) -> int:
    """把搜索接口的 '5:52' / '1:02:03' 转为秒。"""
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        parts = [int(p) for p in text.split(":")]
    except ValueError:
        return 0
    total = 0
    for part in parts:
        total = total * 60 + part
    return total


def _clean_title(value) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


async def fetch_play_url(info: dict, qn: int = 64) -> str | None:
    """获取可下载的直链（低清 durl，单文件含音轨）。"""
    if not info.get("cid"):
        return None
    if info.get("bvid"):
        id_part = f"bvid={info['bvid']}"
    elif info.get("aid"):
        id_part = f"avid={info['aid']}"
    else:
        return None
    for q in (qn, 32, 16):
        api = (f"https://api.bilibili.com/x/player/playurl?"
               f"{id_part}&cid={info.get('cid')}"
               f"&qn={q}&fnval=1&fnver=0&fourk=1")
        data = await http_get_json(api, headers=BILI_HEADERS)
        if not isinstance(data, dict) or data.get("code") != 0:
            continue
        d = data.get("data") or {}
        durl = d.get("durl") or []
        if durl and durl[0].get("url"):
            return str(durl[0]["url"])
    return None


async def download_video(info: dict, dest_dir: str, max_bytes: int,
                         timeout: float = 120) -> str | None:
    """下载视频（API 直链通道）。返回文件路径或 None。"""
    url = await fetch_play_url(info)
    if not url:
        return None
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"bili_{info.get('bvid') or uuid.uuid4().hex[:6]}.mp4")
    ok, _msg = await download_url(
        url, dest, max_bytes, headers=BILI_HEADERS, timeout=timeout)
    return dest if ok else None


def format_pubdate(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.localtime(int(ts)))
    except Exception:
        return ""
