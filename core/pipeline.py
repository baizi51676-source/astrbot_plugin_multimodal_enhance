"""解析管线（v2）：媒体检测（含引用消息）-> 后台解析 -> LLM 请求前注入。

关键点（对照 AstrBot v4.27.4 源码）：
- 引用消息：Reply 组件自带 chain（被引用消息的组件列表）；
  引用视频/语音/文件、引用文本中的链接都会被解析（chain 为空时用 OneBot get_msg 兜底）。
- 媒体落地：优先使用组件自带 convert_to_file_path()/get_file()
  （自动处理 http / base64:// / file:// / data: 与本地路径），再走手动兜底。
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

from . import llm as llm_utils
from .config import _to_bool, _to_int, _to_str
from .media import audio_analyzer, ncm, video_analyzer
from .media.downloader import download_url, pick_video_meta, ytdlp_download, ytdlp_json
from .media.ffmpeg_tools import decode_pcm, find_tool, probe_media, run_proc, summarize_probe

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".amr", ".silk", ".opus"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv"}
GOOD_STT_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".opus"}

BILI_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?bilibili\.com/video/[A-Za-z0-9]+[^\s\"'<>）)]*"
    r"|(?:https?://)?b23\.tv/[A-Za-z0-9]+"
)
DIRECT_MEDIA_PATTERN = re.compile(
    r"https?://[^\s\"'<>）)]+?(?:\.(?:mp3|m4a|wav|flac|aac|ogg|opus|mp4|mov|mkv|webm|flv))"
    r"(?:\?[^\s\"'<>）)]*)?",
    re.IGNORECASE,
)

MAX_TOTAL_CHARS = 6000
AUDIO_MAX_BYTES = 30 * 1024 * 1024

_KNOWN_COMP_NAMES = {"plain", "at", "reply", "face", "poke", "image", "node", "nodes"}


def _sget_bool(settings: dict, key: str, default: bool) -> bool:
    return _to_bool(settings.get(key), default)


def _sget_int(settings: dict, key: str, default: int) -> int:
    return _to_int(settings.get(key), default)


def _sget_str(settings: dict, key: str, default: str = "") -> str:
    return _to_str(settings.get(key), default)


@dataclass
class Detected:
    voices: list = field(default_factory=list)          # (comp, quoted)
    audio_files: list = field(default_factory=list)     # (comp, quoted)
    videos: list = field(default_factory=list)          # (comp, quoted)
    reply_ids: list = field(default_factory=list)       # 无 chain 的引用消息 ID
    music_refs: list = field(default_factory=list)      # (ref, quoted)
    bili_urls: list = field(default_factory=list)       # (url, quoted)
    direct_audio_urls: list = field(default_factory=list)
    direct_video_urls: list = field(default_factory=list)
    seen_gated: list = field(default_factory=list)      # 功能未启用而跳过的媒体

    @property
    def empty(self) -> bool:
        return not any([
            self.voices, self.audio_files, self.videos, self.reply_ids,
            self.music_refs, self.bili_urls,
            self.direct_audio_urls, self.direct_video_urls,
        ])

    @property
    def takes_time(self) -> bool:
        return bool(self.videos or self.music_refs or self.bili_urls
                    or self.direct_audio_urls or self.direct_video_urls)


class MediaPipeline:
    def __init__(self, plugin):
        self.plugin = plugin
        self._tasks: dict[str, asyncio.Task] = {}
        self._results: dict[str, dict] = {}

    # ---------------- 工具 ----------------

    @staticmethod
    def _umo(event) -> str:
        return str(getattr(event, "unified_msg_origin", "") or "")

    def _key(self, event) -> str:
        mid = ""
        try:
            mid = str(event.message_obj.message_id or "")
        except Exception:
            pass
        return f"{self._umo(event)}|{mid}"

    def _caption_provider_id(self) -> str:
        try:
            return self.plugin.caption_provider_id()
        except Exception:
            return ""

    @staticmethod
    def _comp_ext(comp) -> str:
        for attr in ("name", "file", "url", "path"):
            value = getattr(comp, attr, None)
            if value:
                text = str(value)
                if attr == "file" and (text.startswith("base64://") or text.startswith("data:")):
                    continue
                if attr == "url":
                    text = text.split("?")[0]
                return os.path.splitext(text)[1].lower()
        return ""

    # ---------------- 检测 ----------------

    def detect(self, event, flags: dict, settings: dict) -> Detected:
        det = Detected()
        audio_on = bool(flags.get("audio_enabled")) and _sget_bool(settings, "audio_file_enabled", True)
        links_on = bool(flags.get("audio_enabled")) and _sget_bool(settings, "audio_links_enabled", True)
        video_on = bool(flags.get("video_enabled"))
        bili_on = video_on and _sget_bool(settings, "video_bili_enabled", True)

        def classify(comp, quoted: bool) -> None:
            name = type(comp).__name__.lower()
            kind = ""
            if "record" in name or "voice" in name:
                kind = "voice"
            elif "video" in name:
                kind = "video"
            else:
                ext = self._comp_ext(comp)
                if ext in AUDIO_EXTS:
                    kind = "audio"
                elif ext in VIDEO_EXTS:
                    kind = "video"
            suffix = "（引用）" if quoted else ""
            if kind == "voice":
                if audio_on:
                    det.voices.append((comp, quoted))
                else:
                    det.seen_gated.append("语音" + suffix)
            elif kind == "audio":
                if audio_on:
                    det.audio_files.append((comp, quoted))
                else:
                    det.seen_gated.append("音频" + suffix)
            elif kind == "video":
                if video_on:
                    det.videos.append((comp, quoted))
                else:
                    det.seen_gated.append("视频" + suffix)

        def scan_text(text: str, quoted: bool) -> None:
            if not text:
                return
            if links_on:
                ref = ncm.extract_song_ref(text)
                if ref and all(existing[0] != ref or existing[1] != quoted
                               for existing in det.music_refs):
                    det.music_refs.append((ref, quoted))
            if bili_on:
                for match in BILI_PATTERN.findall(text):
                    url = match if match.startswith("http") else f"https://{match}"
                    if all(existing[0] != url for existing in det.bili_urls):
                        det.bili_urls.append((url, quoted))
            if video_on:
                for url in DIRECT_MEDIA_PATTERN.findall(text):
                    if ncm.SONG_URL_PATTERN.search(url):
                        continue
                    ext = os.path.splitext(url.split("?")[0])[1].lower()
                    if ext in AUDIO_EXTS and links_on:
                        if url not in det.direct_audio_urls:
                            det.direct_audio_urls.append(url)
                    elif ext in VIDEO_EXTS:
                        if url not in det.direct_video_urls:
                            det.direct_video_urls.append(url)

        try:
            comps = list(getattr(event.message_obj, "message", []) or [])
        except Exception:
            comps = []

        for comp in comps:
            name = type(comp).__name__.lower()
            if name == "reply":
                chain = getattr(comp, "chain", None) or []
                if chain:
                    for sub in chain:
                        classify(sub, True)
                else:
                    rid = str(getattr(comp, "id", "") or "")
                    if rid:
                        det.reply_ids.append(rid)
                quoted_text = str(getattr(comp, "message_str", "") or "")
                quoted_text += "".join(
                    str(getattr(sub, "text", "") or "")
                    for sub in chain
                    if type(sub).__name__ == "Plain"
                )
                scan_text(quoted_text, True)
            elif name in _KNOWN_COMP_NAMES:
                continue
            else:
                classify(comp, False)

        scan_text(str(getattr(event, "message_str", "") or ""), False)

        # 诊断日志：有媒体但被关 / 有陌生组件
        if det.empty and det.seen_gated:
            self.plugin.log.info(f"检测到媒体但对应功能未启用：{'、'.join(sorted(set(det.seen_gated)))}")
        elif det.empty:
            unknown = [
                type(comp).__name__ for comp in comps
                if type(comp).__name__.lower() not in _KNOWN_COMP_NAMES
            ]
            if unknown:
                self.plugin.log.info(f"消息含未处理组件：{sorted(set(unknown))}")
        return det

    def stats(self) -> dict:
        active = sum(1 for t in self._tasks.values() if not t.done())
        return {"active_tasks": active, "pending_results": len(self._results)}

    # ---------------- 入口 ----------------

    async def on_message(self, event) -> None:
        try:
            if not self.plugin.conf.is_globally_enabled():
                return
            flags = self.plugin.resolve_flags_for_event(event)
            if not any(flags.get(k) for k in ("image_enabled", "audio_enabled", "video_enabled")):
                return
            settings = self.plugin.effective_settings_for(event)
            det = self.detect(event, flags, settings)
            if det.empty:
                return
            key = self._key(event)
            if key in self._tasks:
                return
            workdir = os.path.join(
                self.plugin.workdir_base,
                f"task_{int(time.time())}_{uuid.uuid4().hex[:6]}",
            )
            os.makedirs(workdir, exist_ok=True)
            if flags.get("notice_enabled") and det.takes_time:
                try:
                    notice = await llm_utils.generate_notice(
                        self.plugin.context, self._umo(event),
                        _sget_str(settings, "notice_provider", ""),
                    )
                    await event.send(event.plain_result(notice))
                except Exception:
                    pass
            self.plugin.log.info(f"检测到媒体内容，开始解析：{key}")
            self._tasks[key] = asyncio.create_task(
                self._run(event, det, flags, settings, workdir, key)
            )
        except Exception as exc:
            self.plugin.log.error(f"消息检测异常：{exc}")

    async def _run(self, event, det: Detected, flags: dict, settings: dict,
                   workdir: str, key: str) -> None:
        try:
            parts = await self._analyze(event, det, flags, settings, workdir)
            text = "\n\n".join(p for p in parts if p)
            if len(text) > MAX_TOTAL_CHARS:
                text = text[:MAX_TOTAL_CHARS] + "\n……（解析内容过长，已截断）"
            if text:
                self._results[key] = {"text": text, "workdir": workdir,
                                      "ts": time.time()}
                try:
                    setattr(event, "_mme_analysis", text)
                except Exception:
                    pass
                self.plugin.log.info(f"解析完成：{key}（{len(text)} 字）")
            else:
                self.plugin.log.info(f"解析完成但无可注入内容：{key}")
        except Exception as exc:
            self.plugin.log.error(f"解析失败：{key}：{exc}")
        finally:
            self._prune()

    # ---------------- 分析 ----------------

    async def _analyze(self, event, det: Detected, flags: dict, settings: dict,
                       workdir: str) -> list[str]:
        conf = self.plugin.conf
        umo = self._umo(event)
        ffmpeg = find_tool("ffmpeg", conf.str("env_ffmpeg_path")) or "ffmpeg"
        ffprobe = find_tool("ffprobe", "") or "ffprobe"
        ytdlp = find_tool("yt-dlp", conf.str("env_ytdlp_path")) or "yt-dlp"
        parts: list[str] = []

        for idx, (comp, quoted) in enumerate((det.voices + det.audio_files)[:3], 1):
            parts.append(await self._analyze_audio_component(
                comp, quoted, idx, workdir, ffmpeg, ffprobe, settings, umo))

        for ref, quoted in det.music_refs[:3]:
            parts.append(await self._analyze_music_ref(ref, quoted, workdir, ffmpeg, settings))

        for url in det.direct_audio_urls[:2]:
            parts.append(await self._analyze_audio_url(url, workdir, ffmpeg, ffprobe,
                                                       settings, umo))

        if flags.get("video_enabled"):
            for idx, (comp, quoted) in enumerate(det.videos[:2], 1):
                parts.append(await self._analyze_video_component(
                    comp, quoted, idx, workdir, ffmpeg, ffprobe, settings, umo))
            for url, quoted in det.bili_urls[:2]:
                parts.append(await self._analyze_bili(url, quoted, workdir, ffmpeg, ffprobe,
                                                      ytdlp, settings, umo))
            for url in det.direct_video_urls[:2]:
                parts.append(await self._analyze_direct_video(url, workdir, ffmpeg,
                                                              ffprobe, settings, umo))
            for rid in det.reply_ids[:2]:
                parts.append(await self._analyze_reply_id(event, rid, workdir, ffmpeg,
                                                          ffprobe, settings, umo))
        return parts

    # -------- 媒体落地（核心修复） --------

    async def _resolve_media(self, comp, workdir: str, max_bytes: int) -> str | None:
        """把组件/数据落地为本地文件。

        优先调用 AstrBot 组件自带解析（http / base64:// / file:// / data: / 本地路径）。
        """
        convert = getattr(comp, "convert_to_file_path", None)
        if callable(convert):
            try:
                path = await convert()
                if path and os.path.isfile(str(path)):
                    return str(path)
            except Exception as exc:
                self.plugin.log.warn(f"组件标准解析失败（{type(comp).__name__}）：{exc}")
        get_file = getattr(comp, "get_file", None)
        if callable(get_file):
            try:
                path = await get_file()
                if path and os.path.isfile(str(path)):
                    return str(path)
            except Exception as exc:
                self.plugin.log.warn(f"组件 get_file 失败（{type(comp).__name__}）：{exc}")
        for attr in ("file", "path", "url"):
            value = getattr(comp, attr, None)
            if value:
                resolved = await self._resolve_value(str(value), workdir, max_bytes)
                if resolved:
                    return resolved
        self.plugin.log.warn(
            f"媒体落地失败：{type(comp).__name__} "
            f"file={getattr(comp, 'file', '')!r} url={getattr(comp, 'url', '')!r} "
            f"path={getattr(comp, 'path', '')!r}")
        return None

    async def _resolve_value(self, value: str, workdir: str, max_bytes: int) -> str | None:
        """解析单个取值：base64:// / data: / file:// / http(s) / 本地路径。"""
        value = value.strip()
        if not value:
            return None
        if value.startswith("base64://"):
            return self._write_base64(value[len("base64://"):], workdir)
        if value.startswith("data:"):
            try:
                header, _, payload = value.partition(",")
                if "base64" in header:
                    return self._write_base64(payload, workdir)
                return None
            except Exception:
                return None
        if value.startswith("file://"):
            try:
                path = unquote(urlparse(value).path)
                return path if os.path.isfile(path) else None
            except Exception:
                return None
        if value.startswith(("http://", "https://")):
            suffix = os.path.splitext(urlparse(value).path)[1] or ".bin"
            dest = os.path.join(workdir, f"dl_{uuid.uuid4().hex[:8]}{suffix}")
            ok, _msg = await download_url(value, dest, max_bytes)
            return dest if ok else None
        if os.path.isfile(value):
            return value
        return None

    @staticmethod
    def _write_base64(payload: str, workdir: str) -> str | None:
        try:
            data = base64.b64decode(payload)
        except Exception:
            return None
        if not data:
            return None
        ext = ".bin"
        if data[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif data[:3] == b"ID3" or (data[0] == 0xFF and len(data) > 1):
            ext = ".mp3"
        elif data[:5] == b"#!AMR":
            ext = ".amr"
        elif data[:7] == b"#!SILK":
            ext = ".silk"
        elif data[4:8] == b"ftyp":
            ext = ".mp4"
        elif data[:4] == b"fLaC":
            ext = ".flac"
        elif data[:4] == b"OggS":
            ext = ".ogg"
        dest = os.path.join(workdir, f"b64_{uuid.uuid4().hex[:8]}{ext}")
        try:
            with open(dest, "wb") as fp:
                fp.write(data)
        except OSError:
            return None
        return dest

    # -------- 语音/音频 --------

    async def _analyze_audio_component(self, comp, quoted: bool, idx: int, workdir: str,
                                       ffmpeg: str, ffprobe: str, settings: dict,
                                       umo: str) -> str:
        label = "语音消息" if "record" in type(comp).__name__.lower() else "音频文件"
        if quoted:
            label = "引用消息中的" + label
        lines = [f"【音频解析 #{idx}】来源：{label}"]
        path = await self._resolve_media(comp, workdir, AUDIO_MAX_BYTES)
        if not path:
            lines.append("（无法获取到音频文件，已跳过）")
            return "\n".join(lines)
        probe = await probe_media(path, ffprobe)
        if probe:
            lines.append("元数据：" + (video_analyzer.meta_line(summarize_probe(probe)) or "未知"))
        text = await self._transcribe(umo, path, ffmpeg, workdir)
        if text:
            lines.append(f"转文本：{text[:2000]}")
        else:
            lines.append("（未配置语音转文本模型或转写为空，已跳过转文本）")
        return "\n".join(lines)

    async def _transcribe(self, umo: str, path: str, ffmpeg: str = "ffmpeg",
                          workdir: str | None = None) -> str:
        try:
            stt = await self.plugin.context.get_using_stt_provider_async(umo)
        except Exception:
            stt = None
        if stt is None:
            return ""
        candidates = [path]
        ext = os.path.splitext(path)[1].lower()
        if ext not in GOOD_STT_EXTS and workdir:
            wav = os.path.join(workdir, f"stt_{uuid.uuid4().hex[:6]}.wav")
            rc, _, _ = await run_proc(
                [ffmpeg, "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", wav],
                timeout=120)
            if rc == 0 and os.path.isfile(wav) and os.path.getsize(wav) > 0:
                candidates.insert(0, wav)
        for candidate in candidates:
            try:
                text = await asyncio.wait_for(stt.get_text(candidate), timeout=120)
                if text and str(text).strip():
                    return str(text).strip()
            except Exception as exc:
                self.plugin.log.warn(f"语音转文本失败：{exc}")
        return ""

    async def _analyze_audio_url(self, url: str, workdir: str, ffmpeg: str,
                                 ffprobe: str, settings: dict, umo: str) -> str:
        lines = ["【音频链接】"]
        dest = os.path.join(workdir, f"audiolink_{uuid.uuid4().hex[:6]}.bin")
        ok, msg = await download_url(url, dest, AUDIO_MAX_BYTES)
        if not ok:
            lines.append(f"下载失败：{msg}")
            return "\n".join(lines)
        probe = await probe_media(dest, ffprobe)
        if probe:
            lines.append("元数据：" + (video_analyzer.meta_line(summarize_probe(probe)) or "未知"))
        text = await self._transcribe(umo, dest, ffmpeg, workdir)
        if text:
            lines.append(f"转文本：{text[:2000]}")
        else:
            spectrum = await self._spectrum_for(dest, ffmpeg, settings)
            if spectrum:
                lines.append("频谱数据（文本，未获得转文本结果时的替代）：\n" + spectrum)
        return "\n".join(lines)

    async def _spectrum_for(self, path: str, ffmpeg: str, settings: dict,
                            seg_sec: int | None = None) -> str:
        if not _sget_bool(settings, "audio_spectrum_enabled", True):
            return ""
        if not audio_analyzer.has_numpy():
            return ""
        seg = seg_sec or _sget_int(settings, "audio_spectrum_seg", 5)
        pcm = await decode_pcm(path, 22050, ffmpeg)
        segments = audio_analyzer.spectrum_segments(pcm, 22050, seg)
        return audio_analyzer.spectrum_text(segments)

    # -------- 音乐链接 --------

    async def _analyze_music_ref(self, ref, quoted: bool, workdir: str, ffmpeg: str,
                                 settings: dict) -> str:
        cookie = _sget_str(settings, "audio_ncm_cookie", "")
        lines = ["【音乐链接解析】网易云音乐" + ("（引用消息）" if quoted else "")]
        song_id = await ncm.resolve_song_id(ref)
        if not song_id:
            lines.append("（短链解析失败，已跳过）")
            return "\n".join(lines)
        detail = await ncm.fetch_song_detail(song_id, cookie)
        if detail:
            lines.append(f"歌曲：{detail.get('name', '?')} - {detail.get('artists', '?')}")
            lines.append(
                f"专辑：{detail.get('album', '?')} · 时长 {ncm.format_duration(detail.get('duration_ms'))}")
        else:
            lines.append("（未获取到歌曲元数据）")
        path = await ncm.download_song(song_id, workdir, cookie)
        if path:
            spectrum = await self._spectrum_for(path, ffmpeg, settings)
            if spectrum:
                lines.append("频谱数据（文本）：\n" + spectrum)
            mode = _sget_str(settings, "audio_deep_mode", "auto")
            if mode != "off":
                if audio_analyzer.has_librosa():
                    summary = await asyncio.get_event_loop().run_in_executor(
                        None, audio_analyzer.deep_summary, path)
                    if summary:
                        lines.append("深度分析（声纹包式）：\n" + audio_analyzer.deep_summary_text(summary))
                elif mode == "on":
                    lines.append("（深度分析需要 librosa，可在插件页面「环境配置」中一键安装）")
        else:
            lines.append("（未能下载音源，跳过频谱/深度分析）")
        lyric = await ncm.fetch_lyric(song_id, cookie)
        if lyric and lyric.get("lyric"):
            text = lyric["lyric"].strip()
            if len(text) > 1500:
                text = text[:1500] + "……（歌词过长，已截断）"
            lines.append("歌词：\n" + text)
        return "\n".join(lines)

    # -------- 视频 --------

    async def _analyze_video_component(self, comp, quoted: bool, idx: int, workdir: str,
                                       ffmpeg: str, ffprobe: str, settings: dict,
                                       umo: str) -> str:
        max_bytes = _sget_int(settings, "video_max_size_mb", 100) * 1024 * 1024
        label = "引用消息中的视频" if quoted else "视频消息"
        lines = [f"【视频解析 #{idx}】来源：{label}"]
        path = await self._resolve_media(comp, workdir, max_bytes)
        if not path:
            lines.append("（无法获取到视频文件，已跳过）")
            return "\n".join(lines)
        lines.append(await self._analyze_video_file(path, workdir, ffmpeg, ffprobe, settings, umo))
        return "\n".join(lines)

    async def _analyze_video_file(self, path: str, workdir: str, ffmpeg: str,
                                  ffprobe: str, settings: dict, umo: str) -> str:
        result = await video_analyzer.analyze_video_source(
            path, workdir, ffmpeg, ffprobe,
            frame_count=_sget_int(settings, "video_frames", 6),
            frame_width=_sget_int(settings, "video_frame_width", 640),
        )
        lines: list[str] = []
        lines.append("元数据：" + (video_analyzer.meta_line(result["meta"]) or "未知"))
        frames = result.get("frames") or []
        caption_provider = _sget_str(settings, "video_caption_provider", "") \
            or self._caption_provider_id()
        if frames:
            descs = []
            for fr in frames[:10]:
                desc = await llm_utils.describe_image(
                    self.plugin.context, umo, fr["path"], caption_provider,
                    instruction=(f"这是视频中的一帧（约第{fr['time']}秒）。"
                                 "请用一两句简洁中文描述画面内容（人物/场景/动作/文字），不要额外解释。"),
                )
                if desc:
                    descs.append(f"{fr['time']}s：{desc}")
            if descs:
                lines.append(f"画面理解（逐帧描述）：\n" + "\n".join(descs))
            elif not caption_provider:
                lines.append("（未能描述画面：未配置图转文模型——请在 AstrBot「图片描述」"
                             "或本插件「帧描述模型」中指定）")
            else:
                lines.append(f"（画面描述调用失败，模型 {caption_provider}）")
        if result.get("audio_path"):
            audio_lines = ["音轨解析："]
            text = await self._transcribe(umo, result["audio_path"], ffmpeg, workdir)
            if text:
                audio_lines.append(f"转文本：{text[:2000]}")
            else:
                spectrum = await self._spectrum_for(result["audio_path"], ffmpeg, settings)
                if spectrum:
                    audio_lines.append("频谱数据（文本，未获得转文本结果时的替代）：\n" + spectrum)
                else:
                    audio_lines.append("（未配置语音转文本模型，音轨内容已跳过）")
            lines.append("\n".join(audio_lines))
        return "\n".join(lines)

    async def _analyze_bili(self, url: str, quoted: bool, workdir: str, ffmpeg: str,
                            ffprobe: str, ytdlp: str, settings: dict, umo: str) -> str:
        max_bytes = _sget_int(settings, "video_max_size_mb", 100) * 1024 * 1024
        max_minutes = _sget_int(settings, "video_max_minutes", 10)
        lines = ["【B站视频解析" + ("（引用消息）" if quoted else "") + "】"]
        info = await ytdlp_json(url, ytdlp)
        if not info:
            lines.append(f"（未能获取视频信息：{url}）")
            return "\n".join(lines)
        meta = pick_video_meta(info)
        if meta.get("title"):
            lines.append(f"标题：{meta['title']}")
        if meta.get("uploader"):
            lines.append(f"UP主：{meta['uploader']}")
        if meta.get("upload_date"):
            lines.append(f"发布时间：{meta['upload_date']}")
        if meta.get("description"):
            lines.append(f"简介：{meta['description']}")
        duration = meta.get("duration") or 0
        if duration and duration > max_minutes * 60:
            lines.append(f"（视频时长 {duration / 60:.1f} 分钟，超过上限 {max_minutes} 分钟，未下载解析）")
            return "\n".join(lines)
        path = await ytdlp_download(url, workdir, ytdlp, max_bytes)
        if not path:
            lines.append("（下载失败或超过体积上限，未进行画面/音轨解析）")
            return "\n".join(lines)
        lines.append(await self._analyze_video_file(path, workdir, ffmpeg, ffprobe, settings, umo))
        return "\n".join(lines)

    async def _analyze_direct_video(self, url: str, workdir: str, ffmpeg: str,
                                    ffprobe: str, settings: dict, umo: str) -> str:
        max_bytes = _sget_int(settings, "video_max_size_mb", 100) * 1024 * 1024
        lines = ["【视频链接解析】"]
        dest = os.path.join(workdir, f"videolink_{uuid.uuid4().hex[:6]}.bin")
        ok, msg = await download_url(url, dest, max_bytes)
        if not ok:
            lines.append(f"下载失败：{msg}")
            return "\n".join(lines)
        lines.append(await self._analyze_video_file(dest, workdir, ffmpeg, ffprobe, settings, umo))
        return "\n".join(lines)

    async def _analyze_reply_id(self, event, rid: str, workdir: str, ffmpeg: str,
                                ffprobe: str, settings: dict, umo: str) -> str:
        """兜底：Reply.chain 为空时，用 OneBot get_msg 拉取被引用消息再解析。"""
        lines = ["【引用消息解析（API 获取）】"]
        event_bot = getattr(event, "bot", None)
        if event_bot is None or not hasattr(event_bot, "call_action"):
            lines.append("（当前环境不支持引用消息获取，已跳过）")
            return "\n".join(lines)
        try:
            data = await asyncio.wait_for(
                event_bot.call_action("get_msg", message_id=int(rid)), timeout=15)
        except Exception as exc:
            lines.append(f"（引用消息获取失败：{exc}）")
            return "\n".join(lines)
        segments = data.get("message") if isinstance(data, dict) else None
        if not isinstance(segments, list):
            lines.append("（引用消息内容为空）")
            return "\n".join(lines)
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type", "")).lower()
            seg_data = seg.get("data") or {}
            if seg_type == "video":
                url = str(seg_data.get("url") or "")
                file_ref = str(seg_data.get("file") or seg_data.get("path") or "")
                path = None
                if url.startswith("http"):
                    max_bytes = _sget_int(settings, "video_max_size_mb", 100) * 1024 * 1024
                    dest = os.path.join(workdir, f"qv_{uuid.uuid4().hex[:6]}.mp4")
                    ok, _m = await download_url(url, dest, max_bytes)
                    if ok:
                        path = dest
                elif file_ref:
                    path = await self._resolve_value(file_ref, workdir,
                                                     _sget_int(settings, "video_max_size_mb", 100) * 1024 * 1024)
                if path:
                    lines.append(await self._analyze_video_file(path, workdir, ffmpeg,
                                                                ffprobe, settings, umo))
            elif seg_type in ("record", "voice"):
                url = str(seg_data.get("url") or "")
                file_ref = str(seg_data.get("file") or "")
                path = None
                if url.startswith("http"):
                    dest = os.path.join(workdir, f"qr_{uuid.uuid4().hex[:6]}.bin")
                    ok, _m = await download_url(url, dest, AUDIO_MAX_BYTES)
                    if ok:
                        path = dest
                elif file_ref:
                    path = await self._resolve_value(file_ref, workdir, AUDIO_MAX_BYTES)
                if path:
                    text = await self._transcribe(umo, path, ffmpeg, workdir)
                    if text:
                        lines.append(f"语音转文本：{text[:2000]}")
        if len(lines) == 1:
            lines.append("（引用消息中未发现可解析的媒体）")
        return "\n".join(lines)

    # ---------------- 等待与注入 ----------------

    async def finish_pending(self, event) -> None:
        key = self._key(event)
        task = self._tasks.get(key)
        if task and not task.done():
            try:
                await asyncio.shield(task)
            except Exception:
                pass

    def inject(self, event, req) -> None:
        text = getattr(event, "_mme_analysis", None)
        key = self._key(event)
        if not text:
            result = self._results.get(key)
            text = result.get("text") if result else None
        if not text:
            return
        try:
            from astrbot.core.agent.message import TextPart  # type: ignore
        except Exception:
            self.plugin.log.warn("注入失败：当前 AstrBot 版本缺少 TextPart")
            return
        block = f"<multimodal_analysis>\n{text}\n</multimodal_analysis>"
        try:
            part = TextPart(text=block)
            mark = getattr(part, "mark_as_temp", None)
            if callable(mark):
                part = mark()
            req.extra_user_content_parts.append(part)
            self.plugin.log.info("已向本轮 LLM 请求注入多模态解析结果。")
        except Exception as exc:
            self.plugin.log.warn(f"注入失败：{exc}")
            return
        try:
            delattr(event, "_mme_analysis")
        except Exception:
            pass
        self._cleanup_key(key)

    def _cleanup_key(self, key: str) -> None:
        result = self._results.pop(key, None)
        if result and not self.plugin.conf.bool("env_keep_temp", False):
            shutil.rmtree(result.get("workdir", ""), ignore_errors=True)
        self._tasks.pop(key, None)

    def _prune(self, max_age: int = 3600) -> None:
        now = time.time()
        for key in list(self._results.keys()):
            result = self._results.get(key) or {}
            if now - float(result.get("ts") or now) > max_age:
                self._cleanup_key(key)