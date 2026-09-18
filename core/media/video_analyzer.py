"""视频解析：抽帧、音轨与元数据。"""

from __future__ import annotations

import os

from .ffmpeg_tools import extract_audio_track, extract_frame, probe_media, summarize_probe


async def analyze_video_source(path: str, workdir: str, ffmpeg: str, ffprobe: str,
                               frame_count: int = 6, frame_width: int = 640) -> dict:
    """对本地视频：探测元数据、均匀抽帧、抽取音轨。"""
    probe = await probe_media(path, ffprobe)
    meta = summarize_probe(probe) if probe else {"duration": None}

    frames: list[dict] = []
    duration = meta.get("duration") or 0
    if duration and duration > 0.5:
        count = max(1, min(24, int(frame_count)))
        for i in range(count):
            ts = duration * (i + 0.5) / count
            out = os.path.join(workdir, f"frame_{i + 1}.jpg")
            ok = await extract_frame(path, ts, out, frame_width, ffmpeg)
            if ok:
                frames.append({"time": round(ts, 1), "path": out})

    audio_path = None
    has_audio = (meta.get("audio_streams") or 0) > 0
    if has_audio:
        wav = os.path.join(workdir, "audio_track.wav")
        if await extract_audio_track(path, wav, ffmpeg):
            audio_path = wav

    return {
        "path": path,
        "meta": meta,
        "frames": frames,
        "audio_path": audio_path,
        "has_audio": has_audio,
    }


def meta_line(meta: dict) -> str:
    parts = []
    duration = meta.get("duration")
    if duration:
        parts.append(f"时长{duration:.1f}s")
    if meta.get("width") and meta.get("height"):
        parts.append(f"{meta['width']}x{meta['height']}")
    if meta.get("format"):
        parts.append(str(meta["format"]).split(",")[0])
    if meta.get("size"):
        parts.append(f"{meta['size'] / 1024 / 1024:.1f}MB")
    if meta.get("video_streams") is not None:
        parts.append(f"视频流{meta.get('video_streams')}/音频流{meta.get('audio_streams')}")
    return " · ".join(parts)