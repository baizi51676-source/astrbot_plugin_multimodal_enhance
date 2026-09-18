"""网易云音乐接口（复刻 ncmapi 常用端点，供音乐链接解析使用）。"""

from __future__ import annotations

import os
import re

from .downloader import download_url, http_get_final_url, http_get_json

NCM_HEADERS = {"Referer": "https://music.163.com/", "Origin": "https://music.163.com"}

_ID_PATTERNS = [
    re.compile(r"music\.163\.com/(?:#/)?song\?id=(\d+)"),
    re.compile(r"y\.music\.163\.com/m/song\?id=(\d+)"),
    re.compile(r"music\.163\.com/song/media/outer/url\?id=(\d+)"),
    re.compile(r"music\.163\.com/m/song\?id=(\d+)"),
]
_SHORT_PATTERN = re.compile(r"163cn\.tv/([A-Za-z0-9]+)")
SONG_URL_PATTERN = re.compile(r"(?:music\.163\.com|163cn\.tv)[^\s\"'<>]*")


def extract_song_ref(text: str) -> tuple[str, str] | None:
    """从文本中提取网易云歌曲引用。返回 ("id", 歌曲ID) 或 ("short", 短码)。"""
    for pat in _ID_PATTERNS:
        m = pat.search(text)
        if m:
            return "id", m.group(1)
    m = _SHORT_PATTERN.search(text)
    if m:
        return "short", m.group(1)
    return None


async def resolve_song_id(ref: tuple[str, str]) -> str | None:
    kind, value = ref
    if kind == "id":
        return value
    final = await http_get_final_url(f"https://163cn.tv/{value}")
    if not final:
        return None
    m = re.search(r"id=(\d+)", final)
    return m.group(1) if m else None


async def fetch_song_detail(song_id: str, cookie: str = "") -> dict | None:
    data = await http_get_json(
        f"https://music.163.com/api/song/detail/?ids=[{song_id}]",
        headers={**NCM_HEADERS, "Cookie": cookie},
    )
    songs = (data or {}).get("songs") if isinstance(data, dict) else None
    if not songs:
        return None
    song = songs[0]
    artists = "/".join(a.get("name", "") for a in (song.get("artists") or []))
    album = (song.get("album") or {}).get("name", "")
    return {
        "id": song_id,
        "name": song.get("name", ""),
        "artists": artists,
        "album": album,
        "duration_ms": song.get("duration"),
        "publish_ms": song.get("publishTime"),
    }


async def fetch_lyric(song_id: str, cookie: str = "") -> dict | None:
    data = await http_get_json(
        f"https://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1",
        headers={**NCM_HEADERS, "Cookie": cookie},
    )
    if not isinstance(data, dict):
        return None
    return {
        "lyric": (data.get("lrc") or {}).get("lyric", "") or "",
        "tlyric": (data.get("tlyric") or {}).get("lyric", "") or "",
    }


async def fetch_song_url_api(song_id: str, cookie: str = "") -> str | None:
    data = await http_get_json(
        f"https://music.163.com/api/song/enhance/player/url?ids=[{song_id}]&br=320000",
        headers={**NCM_HEADERS, "Cookie": cookie},
    )
    try:
        url = (data or {}).get("data", [{}])[0].get("url")
        return url or None
    except Exception:
        return None


async def download_song(song_id: str, dest_dir: str, cookie: str = "",
                        max_bytes: int = 50 * 1024 * 1024) -> str | None:
    """下载歌曲音频到 dest_dir，返回文件路径或 None。"""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"ncm_{song_id}.mp3")
    headers = {**NCM_HEADERS, "Cookie": cookie} if cookie else dict(NCM_HEADERS)

    ok, _ = await download_url(
        f"https://music.163.com/song/media/outer/url?id={song_id}.mp3",
        dest, max_bytes, headers=headers,
    )
    if ok and _looks_audio(dest):
        return dest

    # 回退：官方播放接口（部分资源需要 cookie）
    url = await fetch_song_url_api(song_id, cookie)
    if url:
        ok, _ = await download_url(url, dest, max_bytes, headers=headers)
        if ok and _looks_audio(dest):
            return dest
    return None


def _looks_audio(path: str) -> bool:
    try:
        if os.path.getsize(path) < 50 * 1024:
            return False
        with open(path, "rb") as fp:
            head = fp.read(4)
        return head[:3] == b"ID3" or head[0] == 0xFF or head[:4] == b"fLaC"
    except Exception:
        return False


def format_duration(duration_ms) -> str:
    try:
        total = int(duration_ms) // 1000
        return f"{total // 60}:{total % 60:02d}"
    except Exception:
        return "未知"