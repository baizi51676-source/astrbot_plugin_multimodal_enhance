"""网络下载：普通 URL（大小上限）与 yt-dlp（B站等）。"""

from __future__ import annotations

import asyncio
import glob
import ipaddress
import json
import os
import socket
from urllib.parse import urljoin, urlparse

try:
    import aiohttp
except Exception:  # pragma: no cover
    aiohttp = None  # type: ignore

from .ffmpeg_tools import run_proc
from .netguard import is_safe_url, is_safe_url_async, masked_host

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

MAX_REDIRECTS = 3


async def _guard(url: str) -> tuple[bool, str]:
    """下载前的异步边界校验（DNS 在线程池里解析，不阻塞事件循环）。"""
    return await is_safe_url(url)


async def _guard_pinned(url: str) -> tuple[bool, str, list[str]]:
    """校验并返回解析到的安全地址，供连接阶段固定使用。"""
    return await is_safe_url_async(url)


class _PinnedResolver(aiohttp.abc.AbstractResolver if aiohttp else object):
    """把主机名固定到已校验的 IP。

    先校验再交给 aiohttp 自行连接存在 TOCTOU 窗口：DNS rebinding 可以在
    校验通过后把同一域名重新解析到内网地址。这里把校验得到的 IP 直接用于
    建连，连接阶段不再做第二次解析。
    """

    def __init__(self, host: str, addresses: list[str]) -> None:
        self._host = (host or '').lower()
        self._addresses = addresses

    async def resolve(self, host, port=0, family=socket.AF_INET):
        if (host or '').lower() != self._host or not self._addresses:
            raise OSError(f'未固定的主机：{host}')
        out = []
        for addr in self._addresses:
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            out.append({
                'hostname': host,
                'host': str(ip),
                'port': port,
                'family': socket.AF_INET6 if ip.version == 6 else socket.AF_INET,
                'proto': 0,
                'flags': socket.AI_NUMERICHOST,
            })
        if not out:
            raise OSError(f'无可用地址：{host}')
        return out

    async def close(self) -> None:
        return None


async def http_get_json(url: str, headers: dict | None = None,
                        timeout: float = 20) -> dict | list | None:
    if aiohttp is None:
        return None
    ok, _reason, addresses = await _guard_pinned(url)
    if not ok:
        return None
    try:
        connector = aiohttp.TCPConnector(
            resolver=_PinnedResolver(urlparse(url).hostname or '', addresses))
        async with aiohttp.ClientSession(
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json, text/plain, */*",
                    **(headers or {}),
                },
                connector=connector) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status >= 400:
                    return None
                return await resp.json(content_type=None)
    except Exception:
        return None


async def http_get_final_url(url: str, headers: dict | None = None,
                             timeout: float = 15) -> str | None:
    """跟随重定向，返回最终 URL（用于短链解析）。每一跳都做网络边界校验。"""
    if aiohttp is None:
        return None
    ok, _reason, addresses = await _guard_pinned(url)
    if not ok:
        return None
    current = url
    try:
        for _hop in range(MAX_REDIRECTS + 1):
            connector = aiohttp.TCPConnector(
                resolver=_PinnedResolver(urlparse(current).hostname or '', addresses))
            async with aiohttp.ClientSession(
                    headers={"User-Agent": UA, **(headers or {})},
                    connector=connector) as session:
                async with session.get(current, timeout=aiohttp.ClientTimeout(total=timeout),
                                       allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            return str(resp.url)
                        current = urljoin(str(resp.url), location)
                        allowed, _why, next_addresses = await _guard_pinned(current)
                        if not allowed:
                            return None
                        addresses = next_addresses
                        continue
                    return str(resp.url)
        return None
    except Exception:
        return None


async def download_url(url: str, dest: str, max_bytes: int,
                       headers: dict | None = None, timeout: float = 120) -> tuple[bool, str]:
    """流式下载，执行大小上限；重定向逐跳校验。

    每一跳都用「校验得到的 IP」建连（_PinnedResolver），避免校验与连接之间
    被 DNS rebinding 换到内网地址。
    """
    if aiohttp is None:
        return False, "aiohttp 不可用"
    ok, reason, addresses = await _guard_pinned(url)
    if not ok:
        return False, f"地址被拒绝（{reason}）"
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    target = url
    try:
        for _hop in range(MAX_REDIRECTS + 1):
            connector = aiohttp.TCPConnector(
                resolver=_PinnedResolver(urlparse(target).hostname or '', addresses))
            async with aiohttp.ClientSession(
                    headers={"User-Agent": UA, **(headers or {})},
                    connector=connector) as session:
                async with session.get(target, timeout=aiohttp.ClientTimeout(total=timeout),
                                       allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            return False, "重定向缺少 Location"
                        next_url = urljoin(str(resp.url), location)
                        allowed, why, next_addresses = await _guard_pinned(next_url)
                        if not allowed:
                            return False, f"重定向被拒绝（{masked_host(next_url)}：{why}）"
                        target = next_url
                        addresses = next_addresses
                        continue
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
        return False, "重定向次数过多"
    except asyncio.CancelledError:
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except OSError:
            pass
        raise
    except Exception as exc:
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except OSError:
            pass
        return False, f"下载失败：{exc}"


def looks_audio(path: str) -> bool:
    """粗略校验下载物像音频文件（防止把错误页面存成文件）。

    支持 ID3 / MP3 帧头 / FLAC / OggS；体积小于 50KB 直接判为可疑。
    """
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
