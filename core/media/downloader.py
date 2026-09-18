"""网络下载：普通 URL（大小上限）与 yt-dlp（B站等）。"""

from __future__ import annotations

import glob
import json
import os
import time

try:
    import aiohttp
except Exception:  # pragma: no cover
    aiohttp = None  # type: ignore

from .ffmpeg_tools import run_proc

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def _session():
    if aiohttp is None:
        return None
    return aiohttp.ClientSession(headers={"User-Agent": UA})


async def http_get_json(url: str, headers: dict | None = None,
                        timeout: float = 20) -> dict | list | None:
    if aiohttp is None:
        return None
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": UA, **(headers or {})}) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status >= 400:
                    return None
                return await resp.json(content_type=None)
    except Exception:
        return None


async def http_get_final_url(url: str, headers: dict | None = None,
                             timeout: float = 15) -> str | None:
    """跟随重定向，返回最终 URL（用于短链解析）。"""
    if aiohttp is None:
        return None
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": UA, **(headers or {})}) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout),
                                   allow_redirects=True) as resp:
                return str(resp.url)
    except Exception:
        return None


async def download_url(url: str, dest: str, max_bytes: int,
                       headers: dict | None = None, timeout: float = 120) -> tuple[bool, str]:
    """流式下载，执行大小上限。返回 (是否成功, 说明)。"""
    if aiohttp is None:
        return False, "aiohttp 不可用"
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": UA, **(headers or {})}) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status >= 400:
                    return False, f"HTTP {resp.status}"
                length = resp.content_length
                if length and length > max_bytes:
                    return False, f"超过大小上限（{length} > {max_bytes}）"
                written = 0
                with open(dest, "wb") as fp:
                    async for chunk in resp.content.iter_chunked(256 * 1024):
                        written += len(chunk)
                        if written > max_bytes:
                            fp.close()
                            try:
                                os.remove(dest)
                            except OSError:
                                pass
                            return False, "超过大小上限，已中止"
                        fp.write(chunk)
        return True, f"{written} bytes"
    except Exception as exc:
        return False, f"下载失败：{exc}"


def _file_looks_audio(path: str) -> bool:
    """粗略校验下载物像音频文件（防止把 403 页面存成文件）。"""
    try:
        if os.path.getsize(path) < 50 * 1024:
            return False
        with open(path, "rb") as fp:
            head = fp.read(4)
        return head[:3] == b"ID3" or head[0] == 0xFF or head[:4] == b"fLaC" or head[:4] == b"OggS"
    except Exception:
        return False


async def ytdlp_json(url: str, exe: str, timeout: float = 90) -> dict | None:
    """yt-dlp -J：仅取元信息（不下载）。"""
    rc, out, _ = await run_proc(
        [exe, "-J", "--no-playlist", "--no-warnings", "--skip-download", url],
        timeout=timeout,
    )
    if rc != 0 or not out:
        return None
    try:
        data = json.loads(out.decode("utf-8", "ignore"))
    except Exception:
        return None
    if isinstance(data, dict) and data.get("entries"):
        entries = [e for e in data["entries"] if e]
        if entries:
            return entries[0]
    return data if isinstance(data, dict) else None


def pick_video_meta(info: dict) -> dict:
    return {
        "title": info.get("title") or "",
        "description": (info.get("description") or "")[:800],
        "upload_date": info.get("upload_date") or "",
        "duration": info.get("duration"),
        "webpage_url": info.get("webpage_url") or "",
        "uploader": info.get("uploader") or "",
    }


async def ytdlp_download(url: str, out_dir: str, exe: str, max_bytes: int,
                         timeout: float = 600) -> str | None:
    """下载视频到 out_dir（单文件），返回文件路径或 None。"""
    os.makedirs(out_dir, exist_ok=True)
    tmpl = os.path.join(out_dir, "video_%(id)s.%(ext)s")
    before = set(glob.glob(os.path.join(out_dir, "video_*")))
    rc, out, err = await run_proc(
        [exe, "-f", "bv*[height<=720]+ba/b[height<=720]/b",
         "--no-playlist", "--no-warnings", "--no-mtime",
         "--max-filesize", str(int(max_bytes)),
         "-o", tmpl, url],
        timeout=timeout,
    )
    after = set(glob.glob(os.path.join(out_dir, "video_*")))
    new_files = [p for p in (after - before) if os.path.getsize(p) > 0]
    if not new_files:
        return None
    new_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return new_files[0]