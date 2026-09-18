"""ffmpeg / ffprobe 工具封装（零第三方依赖）。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil


def find_tool(name: str, configured: str = "") -> str | None:
    """优先使用配置路径，其次在 PATH 中查找。"""
    if configured and os.path.exists(configured):
        return configured
    return shutil.which(name)


async def run_proc(cmd: list[str], timeout: float = 120) -> tuple[int, bytes, bytes]:
    """执行外部命令，返回 (returncode, stdout, stderr)。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return -1, b"", f"command not found: {cmd[0]}".encode()
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return -9, b"", b"timeout"
    return proc.returncode or 0, out, err


async def probe_media(path: str, ffprobe: str = "ffprobe") -> dict | None:
    """ffprobe 获取媒体信息（JSON）。"""
    rc, out, _ = await run_proc(
        [ffprobe, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        timeout=60,
    )
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out.decode("utf-8", "ignore"))
    except Exception:
        return None


def summarize_probe(probe: dict) -> dict:
    """从 ffprobe 结果提取常用字段。"""
    fmt = probe.get("format") or {}
    streams = probe.get("streams") or []
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    info: dict = {
        "format": fmt.get("format_name", ""),
        "duration": _safe_float(fmt.get("duration")),
        "size": _safe_int(fmt.get("size")),
        "bit_rate": _safe_int(fmt.get("bit_rate")),
        "video_streams": len(video_streams),
        "audio_streams": len(audio_streams),
    }
    if video_streams:
        vs = video_streams[0]
        info["video_codec"] = vs.get("codec_name", "")
        info["width"] = _safe_int(vs.get("width"))
        info["height"] = _safe_int(vs.get("height"))
    if audio_streams:
        aus = audio_streams[0]
        info["audio_codec"] = aus.get("codec_name", "")
        info["sample_rate"] = _safe_int(aus.get("sample_rate"))
    return info


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def extract_frame(video: str, ts: float, out_path: str, width: int,
                        ffmpeg: str = "ffmpeg") -> bool:
    """按时间点抽取单帧（等比缩放到宽度上限）。"""
    vf = f"scale=min({int(width)}" + "\\,iw):-2"
    rc, _, _ = await run_proc(
        [ffmpeg, "-y", "-ss", f"{ts:.2f}", "-i", video,
         "-frames:v", "1", "-vf", vf, "-q:v", "3", out_path],
        timeout=90,
    )
    return rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0


async def extract_audio_track(video: str, out_wav: str, ffmpeg: str = "ffmpeg",
                              sr: int = 16000) -> bool:
    """抽取视频音轨为 wav（单声道）。"""
    rc, _, _ = await run_proc(
        [ffmpeg, "-y", "-i", video, "-vn", "-ac", "1", "-ar", str(sr), out_wav],
        timeout=180,
    )
    return rc == 0 and os.path.exists(out_wav) and os.path.getsize(out_wav) > 0


async def decode_pcm(path: str, sr: int = 22050, ffmpeg: str = "ffmpeg") -> bytes:
    """解码为 16bit 单声道 PCM（供频谱分析）。"""
    rc, out, _ = await run_proc(
        [ffmpeg, "-v", "error", "-i", path, "-vn",
         "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
        timeout=240,
    )
    return out if rc == 0 and out else b""