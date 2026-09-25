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
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

from . import capabilities, llm as llm_utils
from .config import _to_bool, _to_int, _to_str
from .media import audio_analyzer, bilibili, ncm, video_analyzer
from .media.downloader import download_url, pick_video_meta, ytdlp_download, ytdlp_json
from .media.ffmpeg_tools import decode_pcm, find_tool, probe_media, run_proc, summarize_probe

try:
    from astrbot.api.event import MessageChain  # type: ignore
except Exception:  # pragma: no cover
    MessageChain = None

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".amr", ".silk", ".opus"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv"}
BILI_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?bilibili\.com/video/[A-Za-z0-9]+[^\s\"'<>）)]*"
    r"|(?:https?://)?b23\.tv/[A-Za-z0-9]+"
    r"|(?:https?://)?bili2233\.cn/[A-Za-z0-9]+"
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
    reply_probe_ids: list = field(default_factory=list) # chain 无媒体的引用消息 ID（探测原生 STT 替换）
    raw_voices: list = field(default_factory=list)      # {"seg": dict, "quoted": bool, "source": str}
    music_refs: list = field(default_factory=list)      # (ref, quoted)
    bili_urls: list = field(default_factory=list)       # (url, quoted)
    direct_audio_urls: list = field(default_factory=list)
    direct_video_urls: list = field(default_factory=list)
    seen_gated: list = field(default_factory=list)      # 功能未启用而跳过的媒体
    attachments: list = field(default_factory=list)     # [{"kind": "audio"/"image", "path": ...}]

    @property
    def empty(self) -> bool:
        return not any([
            self.voices, self.audio_files, self.videos, self.reply_ids,
            self.raw_voices, self.music_refs, self.bili_urls,
            self.direct_audio_urls, self.direct_video_urls,
        ])

    @property
    def takes_time(self) -> bool:
        return bool(self.videos or self.voices or self.audio_files
                    or self.raw_voices or self.music_refs or self.bili_urls
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
                self._add_music_ref(det, ncm.extract_song_ref(text), quoted)
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

        unknown: list[str] = []

        def handle_comp(comp, quoted: bool) -> None:
            """处理普通/未知组件：尝试提取链接与媒体（Json 卡片 / Music / Unknown 等）。"""
            name = type(comp).__name__.lower()
            if name in ("plain", "at", "face", "poke", "image", "node", "nodes", "reply"):
                return
            before = self._det_counts(det)
            if name == "music":
                m_type = str(getattr(comp, "_type", "") or "")
                m_id = str(getattr(comp, "id", "") or "")
                if links_on and m_type == "163" and m_id.isdigit():
                    self._add_music_ref(det, ("id", m_id), quoted)
            blob = self._comp_blob(comp)
            if blob:
                scan_text(blob, quoted)
                if links_on:
                    self._add_music_ref(det, self._music_ref_from_blob(blob), quoted)
            classify(comp, quoted)
            if self._det_counts(det) == before:
                suffix = "（引用）" if quoted else ""
                unknown.append(type(comp).__name__ + suffix)

        for comp in comps:
            name = type(comp).__name__.lower()
            if name == "reply":
                rid = str(getattr(comp, "id", "") or "")
                chain = getattr(comp, "chain", None) or []
                chain_voice = False
                if chain:
                    before_voices = len(det.voices)
                    for sub in chain:
                        handle_comp(sub, True)
                    chain_voice = len(det.voices) > before_voices
                elif rid:
                    det.reply_ids.append(rid)
                # 原生 STT 可能已把被引用语音替换成文本：记下 ID 待异步探测
                if rid and chain and not chain_voice and audio_on:
                    det.reply_probe_ids.append(rid)
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
                handle_comp(comp, False)

        scan_text(str(getattr(event, "message_str", "") or ""), False)

        # 原生 STT 也可能把本条消息的语音替换成文本：从原始消息兜底找语音
        if audio_on and not det.voices:
            for seg in self._raw_segments(event):
                if not isinstance(seg, dict):
                    continue
                if str(seg.get("type", "")).lower() not in ("record", "voice"):
                    continue
                data = seg.get("data") or {}
                if isinstance(data, dict) and (data.get("url") or data.get("file")):
                    det.raw_voices.append(
                        {"seg": data, "quoted": False, "source": "raw"})

        # 诊断日志：有媒体但被关 / 有没提取出任何东西的组件
        if det.empty and det.seen_gated:
            self.plugin.log.info(f"检测到媒体但对应功能未启用：{'、'.join(sorted(set(det.seen_gated)))}")
        elif det.empty and unknown:
            self.plugin.log.info(f"消息含未处理组件：{sorted(set(unknown))}")
        return det

    @staticmethod
    def _det_counts(det: Detected) -> tuple:
        return (
            len(det.voices), len(det.audio_files), len(det.videos),
            len(det.reply_ids), len(det.reply_probe_ids), len(det.raw_voices),
            len(det.music_refs), len(det.bili_urls),
            len(det.direct_audio_urls), len(det.direct_video_urls),
        )

    @staticmethod
    def _raw_segments(event) -> list:
        """从原始消息对象中提取消息段列表（兼容 dict / Event / CQ 字符串）。"""
        try:
            raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        except Exception:
            return []
        if raw is None:
            return []
        segs = None
        if isinstance(raw, dict):
            segs = raw.get("message")
        if segs is None:
            segs = getattr(raw, "message", None)
        if isinstance(segs, list):
            return segs
        cq = None
        if isinstance(raw, dict):
            cq = raw.get("raw_message")
        if cq is None:
            cq = getattr(raw, "raw_message", None)
        if isinstance(cq, str) and "[cq:record" in cq.lower():
            m = re.search(r"\[CQ:record,([^\]]+)\]", cq, re.IGNORECASE)
            if m:
                params = dict(re.findall(r"([A-Za-z_]+)=([^,\]]+)", m.group(1)))
                return [{"type": "record", "data": params}]
        return []

    @staticmethod
    def _event_bot(event):
        """获取平台 Bot 客户端（用于 get_msg / get_record 调用）。"""
        candidates = [getattr(event, "bot", None)]
        try:
            raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
            candidates.append(getattr(raw, "bot", None))
        except Exception:
            pass
        for bot in candidates:
            if bot is not None and hasattr(bot, "call_action"):
                return bot
        return None

    @staticmethod
    def _add_music_ref(det: Detected, ref, quoted: bool) -> None:
        if not ref:
            return
        if any(existing[0] == ref and existing[1] == quoted for existing in det.music_refs):
            return
        det.music_refs.append((ref, quoted))

    @staticmethod
    def _comp_blob(comp) -> str:
        """把组件里可读的文本/JSON 抽取为一段文本（供链接扫描）。"""
        parts: list[str] = []
        data = getattr(comp, "data", None)
        if isinstance(data, dict):
            try:
                parts.append(json.dumps(data, ensure_ascii=False))
            except Exception:
                parts.append(str(data))
        elif isinstance(data, str) and data:
            parts.append(data)
        for attr in ("_type", "title", "content", "url", "audio", "text", "value", "raw"):
            value = getattr(comp, attr, None)
            if isinstance(value, str) and value:
                parts.append(f"{attr}={value}")
            elif isinstance(value, dict):
                try:
                    parts.append(json.dumps(value, ensure_ascii=False))
                except Exception:
                    pass
        comp_id = getattr(comp, "id", None)
        if comp_id is not None and str(comp_id).isdigit():
            parts.append(f"id={comp_id}")
        return "\n".join(parts)[:6000]

    @staticmethod
    def _music_ref_from_blob(blob: str):
        ref = ncm.extract_song_ref(blob)
        if ref:
            return ref
        m = re.search(r'"type"\s*:\s*"163"[^}]*?"id"\s*:\s*"?(\d+)', blob)
        if not m:
            m = re.search(r'"id"\s*:\s*"?(\d+)"?[^}]*?"type"\s*:\s*"163"', blob)
        if m:
            return ("id", m.group(1))
        return None

    async def _send_proactive(self, event, text: str) -> None:
        """主动发送消息（不触碰 event 的 _has_send_oper，避免主流程跳过 LLM 回复）。"""
        if MessageChain is None:
            self.plugin.log.warn("分析中提示未发送：缺少 MessageChain。")
            return
        try:
            chain = MessageChain().message(text)
            await self.plugin.context.send_message(self._umo(event), chain)
        except Exception as exc:
            self.plugin.log.warn(f"分析中提示发送失败：{exc}")

    def stats(self) -> dict:
        active = sum(1 for t in self._tasks.values() if not t.done())
        return {"active_tasks": active, "pending_results": len(self._results)}

    # ---------------- 入口 ----------------

    async def on_message(self, event) -> None:
        try:
            # 仅处理真正唤醒/@ 了 bot 的消息；避免群里的链接被误触发解析与「正在分析中」提示
            if not bool(getattr(event, "is_at_or_wake_command", True)):
                return
            if not self.plugin.conf.is_globally_enabled():
                return
            flags = self.plugin.resolve_flags_for_event(event)
            if not any(flags.get(k) for k in ("image_enabled", "audio_enabled", "video_enabled")):
                return
            settings = self.plugin.effective_settings_for(event)
            det = self.detect(event, flags, settings)
            if det.reply_probe_ids:
                try:
                    await self._probe_reply_voices(event, det, settings)
                except Exception as exc:
                    self.plugin.log.warn(f"引用语音探测失败：{exc}")
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
                    platform_name = ""
                    try:
                        platform_name = str(event.get_platform_name() or "")
                    except Exception as exc:
                        self.plugin.log.debug(f"获取平台名称失败：{exc}")
                    persona_prompt = ""
                    try:
                        persona_prompt = await self.plugin.persona_prompt_for(
                            self._umo(event), platform_name)
                    except Exception as exc:
                        self.plugin.log.warn(f"获取人格提示词失败（将使用默认）：{exc}")
                    notice = await llm_utils.generate_notice(
                        self.plugin.context, self._umo(event),
                        _sget_str(settings, "notice_provider", ""),
                        _sget_str(settings, "notice_prompt", ""),
                        persona_prompt,
                    )
                    # 重要：不能用 event.send()——它会把 _has_send_oper 置 True，
                    # 导致 AstrBot 的 process_stage 判定「已有发送操作」而跳过 LLM 回复。
                    await self._send_proactive(event, notice)
                except Exception as exc:
                    self.plugin.log.warn(f"分析中提示发送失败：{exc}")
            self.plugin.log.info(f"检测到媒体内容，开始解析：{key}")
            self._tasks[key] = asyncio.create_task(
                self._run(event, det, flags, settings, workdir, key)
            )
        except Exception as exc:
            self.plugin.log.error(f"消息检测异常：{exc}")

    async def _probe_reply_voices(self, event, det: Detected,
                                  settings: dict | None = None) -> None:
        """探测被引用消息中是否含语音（原生 STT 开启时 Record 会被替换成文本）。"""
        event_bot = self._event_bot(event)
        if event_bot is None:
            return
        limit = max(1, _sget_int(settings or {}, "max_items_per_type", 3))
        seen = {str(ref.get("seg", {}).get("file", "")) for ref in det.raw_voices}
        for rid in det.reply_probe_ids[:limit]:
            try:
                mid = int(rid)
            except Exception:
                continue
            try:
                data = await asyncio.wait_for(
                    event_bot.call_action("get_msg", message_id=mid), timeout=15)
            except Exception as exc:
                self.plugin.log.warn(f"引用消息探测失败：{exc}")
                continue
            segments = data.get("message") if isinstance(data, dict) else None
            if not isinstance(segments, list):
                continue
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                if str(seg.get("type", "")).lower() not in ("record", "voice"):
                    continue
                sd = seg.get("data") or {}
                if not isinstance(sd, dict):
                    continue
                key = str(sd.get("file") or sd.get("url") or "")
                if key and key in seen:
                    continue
                seen.add(key)
                det.raw_voices.append({"seg": sd, "quoted": True, "source": "reply"})

    async def _run(self, event, det: Detected, flags: dict, settings: dict,
                   workdir: str, key: str) -> None:
        try:
            parts = await self._analyze(event, det, flags, settings, workdir)
            text = "\n\n".join(p for p in parts if p)
            if len(text) > MAX_TOTAL_CHARS:
                text = text[:MAX_TOTAL_CHARS] + "\n……（解析内容过长，已截断）"
            atts = list(getattr(det, "attachments", []) or [])
            if text or atts:
                self._results[key] = {"text": text, "workdir": workdir,
                                      "ts": time.time(), "attachments": atts}
                if text:
                    try:
                        event._mme_analysis = text
                    except Exception:
                        pass
                extra = f"，附件 {len(atts)} 个" if atts else ""
                self.plugin.log.info(f"解析完成：{key}（{len(text)} 字{extra}）")
            else:
                self.plugin.log.info(f"解析完成但无可注入内容：{key}")
                if not self.plugin.conf.bool("env_keep_temp", False):
                    shutil.rmtree(workdir, ignore_errors=True)
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
        per_type = max(1, _sget_int(settings, "max_items_per_type", 3))
        mods = await capabilities.chat_modalities(
            self.plugin.context, umo,
            _sget_bool(settings, "attach_when_unset", False))
        attach = capabilities.attach_flags(
            mods,
            audio_on=_sget_bool(settings, "model_attach_audio", True),
            frames_on=_sget_bool(settings, "model_attach_frames", True),
        )
        attach["max_bytes"] = max(1, _sget_int(settings, "attach_audio_max_mb", 20)) * 1024 * 1024
        attach["files"] = []
        if attach.get("audio") or attach.get("image"):
            self.plugin.log.info(
                "主模型支持多模态输入，启用附件直传（音频=%s，图片=%s）。"
                % (attach.get("audio"), attach.get("image")))
        parts: list[str] = []
        for idx, (comp, quoted) in enumerate((det.voices + det.audio_files)[:per_type], 1):
            parts.append(await self._analyze_audio_component(
                comp, quoted, idx, workdir, ffmpeg, ffprobe, settings, umo, attach))
        for ref in det.raw_voices[:per_type]:
            parts.append(await self._analyze_raw_voice(
                event, ref, workdir, ffmpeg, ffprobe, settings, umo, attach))
        for ref, quoted in det.music_refs[:per_type]:
            parts.append(await self._analyze_music_ref(ref, quoted, workdir, ffmpeg,
                                                       settings, umo, attach))
        for url in det.direct_audio_urls[:per_type]:
            parts.append(await self._analyze_audio_url(url, workdir, ffmpeg, ffprobe,
                                                       settings, umo, attach))
        for rid in det.reply_ids[:per_type]:
            parts.append(await self._analyze_reply_id(event, rid, workdir, ffmpeg,
                                                      ffprobe, settings, umo, attach))

        if flags.get("video_enabled"):
            for idx, (comp, quoted) in enumerate(det.videos[:per_type], 1):
                parts.append(await self._analyze_video_component(
                    comp, quoted, idx, workdir, ffmpeg, ffprobe, settings, umo, attach))
            for url, quoted in det.bili_urls[:per_type]:
                parts.append(await self._analyze_bili(url, quoted, workdir, ffmpeg, ffprobe,
                                                      ytdlp, settings, umo, attach))
            for url in det.direct_video_urls[:per_type]:
                parts.append(await self._analyze_direct_video(url, workdir, ffmpeg,
                                                              ffprobe, settings, umo, attach))
        if attach.get("files"):
            det.attachments = list(attach["files"])
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
                                       umo: str, attach: dict | None = None) -> str:
        label = "语音消息" if "record" in type(comp).__name__.lower() else "音频文件"
        if quoted:
            label = "引用消息中的" + label
        lines = [f"【音频解析 #{idx}】来源：{label}"]
        path = await self._resolve_media(comp, workdir, AUDIO_MAX_BYTES)
        if not path:
            lines.append("（无法获取到音频文件，已跳过）")
            return "\n".join(lines)
        if self._collect_audio_attachment(attach, path, label):
            lines.append("（音频已作为附件随消息发送，主模型可直接理解）")
            return "\n".join(lines)
        lines.extend(await self._audio_lines(path, settings, ffmpeg, ffprobe, umo, workdir))
        return "\n".join(lines)

    # -------- 附件直传（主模型支持音频/图片时） --------

    def _collect_audio_attachment(self, attach: dict | None, path: str, label: str) -> bool:
        """附件直传模式：登记音频附件（受体积上限保护）。"""
        if not attach or not attach.get("audio"):
            return False
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        limit = int(attach.get("max_bytes") or 0)
        if size <= 0 or (limit and size > limit):
            self.plugin.log.info(f"音频过大或不可用（{size} bytes），改为文本解析：{label}")
            return False
        attach.setdefault("files", []).append({"kind": "audio", "path": path})
        return True

    def _collect_frames(self, attach: dict | None, frames: list) -> bool:
        """附件直传模式：登记抽帧图片附件。"""
        if not attach or not attach.get("image") or not frames:
            return False
        added = False
        for fr in frames:
            frame_path = fr.get("path") if isinstance(fr, dict) else None
            if frame_path and os.path.isfile(frame_path):
                attach.setdefault("files", []).append({"kind": "image", "path": frame_path})
                added = True
        return added

    async def _audio_lines(self, path: str, settings: dict, ffmpeg: str, ffprobe: str,
                           umo: str, workdir: str) -> list[str]:
        """音频文本解析：元数据 + 转文本 +（可选）频谱 +（可选）深度分析。"""
        lines: list[str] = []
        probe = await probe_media(path, ffprobe)
        if probe:
            lines.append("元数据：" + (video_analyzer.meta_line(summarize_probe(probe)) or "未知"))
        text = await self._transcribe(umo, path, settings, ffmpeg, workdir)
        if text:
            lines.append(f"转文本：{text[:2000]}")
        else:
            lines.append("（未配置语音转文本模型或转写为空，已跳过转文本）")
        if not text or _sget_bool(settings, "audio_spectrum_always", False):
            spectrum = await self._spectrum_for(path, ffmpeg, settings)
            if spectrum:
                prefix = ("频谱数据（文本）：" if text
                          else "频谱数据（文本，未获得转文本结果时的替代）：")
                lines.append(prefix + "\n" + spectrum)
        mode = _sget_str(settings, "audio_deep_mode", "auto")
        if mode != "off":
            if audio_analyzer.has_librosa():
                limit = _sget_int(settings, "audio_deep_max_seconds", 300)
                summary = await asyncio.get_event_loop().run_in_executor(
                    None, audio_analyzer.deep_summary, path, limit)
                if summary:
                    lines.append("深度分析（声纹包式）：\n" + audio_analyzer.deep_summary_text(summary))
            elif mode == "on":
                lines.append("（深度分析需要 librosa，可在插件页面「环境配置」中一键安装）")
        return lines

    async def _resolve_record_seg(self, event, seg_data: dict, workdir: str,
                                  max_bytes: int) -> str | None:
        """把原始消息段中的语音（url / file）落地为本地文件。"""
        url = str(seg_data.get("url") or "")
        if url.startswith("http"):
            dest = os.path.join(workdir, f"rv_{uuid.uuid4().hex[:6]}.amr")
            ok, _m = await download_url(url, dest, max_bytes)
            if ok:
                return dest
        file_ref = str(seg_data.get("file") or seg_data.get("path") or "")
        if file_ref:
            resolved = await self._resolve_value(file_ref, workdir, max_bytes)
            if resolved:
                return resolved
        event_bot = self._event_bot(event)
        if event_bot is not None and file_ref:
            try:
                data = await asyncio.wait_for(
                    event_bot.call_action("get_record", file=file_ref, out_format="wav"),
                    timeout=15)
            except Exception:
                data = None
            if isinstance(data, dict):
                for key in ("file", "path"):
                    p = str(data.get(key) or "")
                    if p and os.path.isfile(p):
                        return p
                u = str(data.get("url") or "")
                if u.startswith("http"):
                    dest = os.path.join(workdir, f"rv_{uuid.uuid4().hex[:6]}.wav")
                    ok, _m = await download_url(u, dest, max_bytes)
                    if ok:
                        return dest
        self.plugin.log.warn(f"语音落地失败（原始消息段）：{seg_data}")
        return None

    async def _analyze_raw_voice(self, event, ref: dict, workdir: str, ffmpeg: str,
                                 ffprobe: str, settings: dict, umo: str,
                                 attach: dict | None = None) -> str:
        """解析 raw 兜底找到的语音（原生 STT 已把 Record 替换成文本的场景）。"""
        quoted = bool(ref.get("quoted"))
        label = "引用消息中的语音消息" if quoted else "语音消息"
        lines = [f"【音频解析】来源：{label}"]
        path = await self._resolve_record_seg(
            event, ref.get("seg") or {}, workdir, AUDIO_MAX_BYTES)
        if not path:
            lines.append("（无法获取到语音文件，已跳过）")
            return "\n".join(lines)
        if self._collect_audio_attachment(attach, path, label):
            lines.append("（音频已作为附件随消息发送，主模型可直接理解）")
            return "\n".join(lines)
        lines.extend(await self._audio_lines(path, settings, ffmpeg, ffprobe, umo, workdir))
        return "\n".join(lines)

    async def _transcribe(self, umo: str, path: str, settings: dict,
                          ffmpeg: str = "ffmpeg",
                          workdir: str | None = None) -> str:
        try:
            stt = await self.plugin.context.get_using_stt_provider_async(umo)
        except Exception:
            stt = None
        if stt is None:
            return ""
        # 1) 统一转为 16k 单声道 wav（提升兼容性并便于按大小拆分）
        base = path
        ext = os.path.splitext(path)[1].lower()
        if workdir and ext != ".wav":
            wav = os.path.join(workdir, f"stt_{uuid.uuid4().hex[:6]}.wav")
            rc, _, _ = await run_proc(
                [ffmpeg, "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", wav],
                timeout=180)
            if rc == 0 and os.path.isfile(wav) and os.path.getsize(wav) > 0:
                base = wav
        # 2) 超过单段上限（如 MiMo STT 的 10MB）则自动拆分
        parts = [base]
        truncated = False
        max_chunks = max(1, _sget_int(settings, "audio_stt_max_chunks", 12))
        chunk_mb = _sget_int(settings, "audio_stt_chunk_mb", 7)
        if chunk_mb > 0 and workdir:
            max_bytes = chunk_mb * 1024 * 1024
            try:
                size = os.path.getsize(base)
            except OSError:
                size = 0
            if size > max_bytes:
                parts = audio_analyzer.split_wav(base, max_bytes, workdir)
                if len(parts) > max_chunks:
                    parts = parts[:max_chunks]
                    truncated = True
                if len(parts) > 1:
                    self.plugin.log.info(f"音频超过 {chunk_mb}MB，已拆分为 {len(parts)} 段转写。")
        # 3) 逐段调用 STT
        texts: list[str] = []
        for idx, part in enumerate(parts, 1):
            try:
                text = await asyncio.wait_for(stt.get_text(part), timeout=180)
                if text and str(text).strip():
                    texts.append(str(text).strip())
            except Exception as exc:
                self.plugin.log.warn(f"语音转文本失败（第{idx}/{len(parts)}段）：{exc}")
        # 4) 兜底：转码/拆分都未成功时，直接尝试原文件
        if not texts and base != path:
            try:
                text = await asyncio.wait_for(stt.get_text(path), timeout=180)
                if text and str(text).strip():
                    texts.append(str(text).strip())
            except Exception as exc:
                self.plugin.log.warn(f"语音转文本失败（原文件重试）：{exc}")
        joined = "".join(texts)
        if truncated:
            joined += f"……（音频过长，仅转写前 {max_chunks} 段）"
        return joined

    async def _analyze_audio_url(self, url: str, workdir: str, ffmpeg: str,
                                 ffprobe: str, settings: dict, umo: str,
                                 attach: dict | None = None) -> str:
        lines = ["【音频链接】"]
        dest = os.path.join(workdir, f"audiolink_{uuid.uuid4().hex[:6]}.bin")
        ok, msg = await download_url(url, dest, AUDIO_MAX_BYTES)
        if not ok:
            lines.append(f"下载失败：{msg}")
            return "\n".join(lines)
        if self._collect_audio_attachment(attach, dest, "音频链接"):
            lines.append("（音频已作为附件随消息发送，主模型可直接理解）")
            return "\n".join(lines)
        lines.extend(await self._audio_lines(dest, settings, ffmpeg, ffprobe, umo, workdir))
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
                                 settings: dict, umo: str = "",
                                 attach: dict | None = None) -> str:
        cookie = _sget_str(settings, "audio_ncm_cookie", "")
        lines = ["【音乐链接解析】网易云音乐" + ("（引用消息）" if quoted else "")]
        song_id = await ncm.resolve_song_id(ref)
        if not song_id:
            lines.append("（短链解析失败，已跳过）")
            return "\n".join(lines)
        if attach and attach.get("audio"):
            song_path = await ncm.download_song(song_id, workdir, cookie)
            if song_path and self._collect_audio_attachment(attach, song_path, "音乐音频"):
                lines.append("（歌曲音频已作为附件随消息发送，主模型可直接理解）")
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
                    limit = _sget_int(settings, "audio_deep_max_seconds", 300)
                    summary = await asyncio.get_event_loop().run_in_executor(
                        None, audio_analyzer.deep_summary, path, limit)
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
                                       umo: str, attach: dict | None = None) -> str:
        max_bytes = _sget_int(settings, "video_max_size_mb", 100) * 1024 * 1024
        label = "引用消息中的视频" if quoted else "视频消息"
        lines = [f"【视频解析 #{idx}】来源：{label}"]
        path = await self._resolve_media(comp, workdir, max_bytes)
        if not path:
            lines.append("（无法获取到视频文件，已跳过）")
            return "\n".join(lines)
        lines.append(await self._analyze_video_file(path, workdir, ffmpeg, ffprobe,
                                                    settings, umo, attach))
        return "\n".join(lines)

    async def _analyze_video_file(self, path: str, workdir: str, ffmpeg: str,
                                  ffprobe: str, settings: dict, umo: str,
                                  attach: dict | None = None) -> str:
        max_minutes = _sget_int(settings, "video_max_minutes", 10)
        probe = await probe_media(path, ffprobe)
        meta = summarize_probe(probe) if probe else {"duration": None}
        lines: list[str] = ["元数据：" + (video_analyzer.meta_line(meta) or "未知")]
        duration = meta.get("duration") or 0
        if duration and duration > max_minutes * 60:
            lines.append(f"（视频时长 {duration / 60:.1f} 分钟，超过上限 {max_minutes} 分钟，"
                         "未进行画面/音轨解析）")
            return "\n".join(lines)
        result = await video_analyzer.analyze_video_source(
            path, workdir, ffmpeg, ffprobe,
            frame_count=_sget_int(settings, "video_frames", 6),
            frame_width=_sget_int(settings, "video_frame_width", 640),
        )
        frames = result.get("frames") or []
        if frames and self._collect_frames(attach, frames):
            lines.append(f"（画面已作为 {len(frames)} 帧图片附件随消息发送，主模型可直接理解）")
        elif frames:
            caption_provider = _sget_str(settings, "video_caption_provider", "") \
                or self._caption_provider_id()
            concurrency = max(1, min(8, _sget_int(settings, "video_caption_concurrency", 3)))
            sem = asyncio.Semaphore(concurrency)

            async def _caption_one(fr: dict):
                async with sem:
                    desc = await llm_utils.describe_image(
                        self.plugin.context, umo, fr["path"], caption_provider,
                        instruction=(f"这是视频中的一帧（约第{fr['time']}秒）。"
                                     "请用一两句简洁中文描述画面内容（人物/场景/动作/文字），不要额外解释。"),
                    )
                return fr, desc

            results = await asyncio.gather(
                *[_caption_one(fr) for fr in frames], return_exceptions=True)
            descs: list[str] = []
            for item in results:
                if isinstance(item, Exception):
                    continue
                fr, desc = item
                if desc:
                    descs.append(f"{fr['time']}s：{desc}")
            if descs:
                lines.append("画面理解（逐帧描述）：\n" + "\n".join(descs))
            elif not caption_provider:
                lines.append("（未能描述画面：未配置图转文模型——请在 AstrBot「图片描述」"
                             "或本插件「帧描述模型」中指定）")
            else:
                lines.append(f"（画面描述调用失败，模型 {caption_provider}）")
        if result.get("audio_path"):
            if self._collect_audio_attachment(attach, result["audio_path"], "视频音轨"):
                lines.append("（音轨已作为音频附件随消息发送，主模型可直接理解）")
            else:
                audio_lines = ["音轨解析："]
                audio_lines.extend(await self._audio_lines(
                    result["audio_path"], settings, ffmpeg, ffprobe, umo, workdir))
                lines.append("\n".join(audio_lines))
        return "\n".join(lines)

    async def _analyze_bili(self, url: str, quoted: bool, workdir: str, ffmpeg: str,
                            ffprobe: str, ytdlp: str, settings: dict, umo: str,
                            attach: dict | None = None) -> str:
        max_bytes = _sget_int(settings, "video_max_size_mb", 100) * 1024 * 1024
        max_minutes = _sget_int(settings, "video_max_minutes", 10)
        lines = ["【B站视频解析" + ("（引用消息）" if quoted else "") + "】"]
        info = None
        try:
            info = await bilibili.fetch_video_info(url)
        except Exception as exc:
            self.plugin.log.warn(f"B站 API 解析失败：{exc}")

        if info:
            if info.get("title"):
                lines.append(f"标题：{info['title']}")
            if info.get("owner"):
                lines.append(f"UP主：{info['owner']}")
            if info.get("pubdate"):
                lines.append(f"发布时间：{bilibili.format_pubdate(info['pubdate'])}")
            if info.get("desc"):
                lines.append(f"简介：{info['desc']}")
            duration = info.get("duration") or 0
            if duration and duration > max_minutes * 60:
                lines.append(f"（视频时长 {duration / 60:.1f} 分钟，超过上限 {max_minutes} 分钟，未下载解析）")
                return "\n".join(lines)
            path = await bilibili.download_video(info, workdir, max_bytes)
            if not path and ytdlp:
                # 回退：yt-dlp 通道（少数场景可用）
                try:
                    path = await ytdlp_download(url, workdir, ytdlp, max_bytes)
                except Exception:
                    path = None
            if not path:
                lines.append("（视频下载失败或超过体积上限，未进行画面/音轨解析）")
                return "\n".join(lines)
            lines.append(await self._analyze_video_file(path, workdir, ffmpeg, ffprobe,
                                                        settings, umo, attach))
            return "\n".join(lines)

        # API 失败：回退 yt-dlp 旧通道
        legacy = await ytdlp_json(url, ytdlp) if ytdlp else None
        if not legacy:
            lines.append(f"（未能获取视频信息：{url}）")
            return "\n".join(lines)
        meta = pick_video_meta(legacy)
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
        lines.append(await self._analyze_video_file(path, workdir, ffmpeg, ffprobe, settings, umo, attach))
        return "\n".join(lines)

    async def _analyze_direct_video(self, url: str, workdir: str, ffmpeg: str,
                                    ffprobe: str, settings: dict, umo: str,
                                    attach: dict | None = None) -> str:
        max_bytes = _sget_int(settings, "video_max_size_mb", 100) * 1024 * 1024
        lines = ["【视频链接解析】"]
        dest = os.path.join(workdir, f"videolink_{uuid.uuid4().hex[:6]}.bin")
        ok, msg = await download_url(url, dest, max_bytes)
        if not ok:
            lines.append(f"下载失败：{msg}")
            return "\n".join(lines)
        lines.append(await self._analyze_video_file(dest, workdir, ffmpeg, ffprobe,
                                                    settings, umo, attach))
        return "\n".join(lines)

    async def _analyze_reply_id(self, event, rid: str, workdir: str, ffmpeg: str,
                                ffprobe: str, settings: dict, umo: str,
                                attach: dict | None = None) -> str:
        """兜底：Reply.chain 为空时，用 OneBot get_msg 拉取被引用消息再解析。"""
        lines = ["【引用消息解析（API 获取）】"]
        event_bot = self._event_bot(event)
        if event_bot is None:
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
                                                                ffprobe, settings, umo, attach))
            elif seg_type in ("record", "voice"):
                path = await self._resolve_record_seg(event, seg_data, workdir, AUDIO_MAX_BYTES)
                if path:
                    if self._collect_audio_attachment(attach, path, "引用语音"):
                        lines.append("（引用语音已作为附件随消息发送，主模型可直接理解）")
                    else:
                        lines.extend(await self._audio_lines(
                            path, settings, ffmpeg, ffprobe, umo, workdir))
        if len(lines) == 1:
            lines.append("（引用消息中未发现可解析的媒体）")
        return "\n".join(lines)

    # ---------------- 等待与注入 ----------------

    async def finish_pending(self, event) -> None:
        key = self._key(event)
        task = self._tasks.get(key)
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=240)
            except asyncio.TimeoutError:
                self.plugin.log.warn("解析等待超时（240s），先继续对话；解析仍在后台进行。")
            except Exception:
                pass

    def inject(self, event, req) -> None:
        """注入解析结果：文本块 +（可选）音频/图片附件直传。"""
        key = self._key(event)
        result = self._results.get(key) or {}
        text = getattr(event, "_mme_analysis", None) or result.get("text") or ""
        files = result.get("attachments") or []
        audio = [f["path"] for f in files
                 if isinstance(f, dict) and f.get("kind") == "audio" and f.get("path")]
        images = [f["path"] for f in files
                  if isinstance(f, dict) and f.get("kind") == "image" and f.get("path")]
        attached = self._append_attachments(req, audio, images)
        if text:
            try:
                from astrbot.core.agent.message import TextPart  # type: ignore
            except Exception:
                self.plugin.log.warn("注入失败：当前 AstrBot 版本缺少 TextPart")
                text = ""
        if text:
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
        if text or attached:
            try:
                delattr(event, "_mme_analysis")
            except Exception:
                pass
        if attached:
            # 附件需在模型调用期间保留文件：延后到 _prune 过期清理
            result["ts"] = time.time()
        else:
            self._cleanup_key(key)

    def _append_attachments(self, req, audio: list[str], images: list[str]) -> bool:
        """把音频/图片附件追加到本轮请求（供主模型直接理解）。"""
        attached = False

        def _existing(attr: str) -> set[str]:
            try:
                values = list(getattr(req, attr, None) or [])
            except Exception:
                return set()
            out: set[str] = set()
            for value in values:
                if isinstance(value, str) and value:
                    try:
                        out.add(os.path.realpath(value))
                    except OSError:
                        out.add(value)
            return out

        if audio:
            existing = _existing("audio_urls")
            new_files = [p for p in audio if os.path.realpath(p) not in existing]
            if new_files:
                try:
                    req.audio_urls = list(getattr(req, "audio_urls", None) or []) + new_files
                    attached = True
                    self.plugin.log.info(
                        f"已附加音频附件 {len(new_files)} 个（主模型可直接理解）。")
                except Exception as exc:
                    self.plugin.log.warn(f"音频附件附加失败：{exc}")
        if images:
            existing = _existing("image_urls")
            new_files = [p for p in images if os.path.realpath(p) not in existing]
            if new_files:
                try:
                    req.image_urls = list(getattr(req, "image_urls", None) or []) + new_files
                    attached = True
                    self.plugin.log.info(
                        f"已附加图片附件 {len(new_files)} 帧（主模型可直接理解）。")
                except Exception as exc:
                    self.plugin.log.warn(f"图片附件附加失败：{exc}")
        return attached

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
