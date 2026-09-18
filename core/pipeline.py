"""解析管线：媒体检测 -> 后台解析 -> 在 LLM 请求前注入结果。

编排流程（与需求一致）：
- 监听到包含音频/视频/链接的消息：先发「正在分析中」提示（可配），后台解析；
- 在 on_waiting_llm_request 时等待解析完成；
- 在 on_llm_request 时把结果以临时内容块注入本轮请求（不污染历史）。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field

from . import llm as llm_utils
from .media import audio_analyzer, ncm, video_analyzer
from .media.downloader import download_url, pick_video_meta, ytdlp_download, ytdlp_json
from .media.ffmpeg_tools import decode_pcm, find_tool, probe_media, summarize_probe

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".amr", ".silk", ".opus"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv"}

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


@dataclass
class Detected:
    voices: list = field(default_factory=list)
    audio_files: list = field(default_factory=list)
    videos: list = field(default_factory=list)
    music_refs: list = field(default_factory=list)
    bili_urls: list = field(default_factory=list)
    direct_audio_urls: list = field(default_factory=list)
    direct_video_urls: list = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any([
            self.voices, self.audio_files, self.videos, self.music_refs,
            self.bili_urls, self.direct_audio_urls, self.direct_video_urls,
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

    # ---------------- 检测 ----------------

    def detect(self, event, flags: dict) -> Detected:
        det = Detected()
        conf = self.plugin.conf
        try:
            comps = list(getattr(event.message_obj, "message", []) or [])
        except Exception:
            comps = []
        audio_on = flags.get("audio_enabled") and conf.bool("audio_file_enabled", True)
        video_on = flags.get("video_enabled")
        for comp in comps:
            name = comp.__class__.__name__
            if name == "Record":
                if audio_on:
                    det.voices.append(comp)
            elif name == "Video":
                if video_on:
                    det.videos.append(comp)
            elif name == "File":
                fname = str(getattr(comp, "name", "") or getattr(comp, "file", "") or "")
                ext = os.path.splitext(fname)[1].lower()
                if ext in AUDIO_EXTS and audio_on:
                    det.audio_files.append(comp)
                elif ext in VIDEO_EXTS and video_on:
                    det.videos.append(comp)

        text = str(getattr(event, "message_str", "") or "")
        if audio_on and conf.bool("audio_links_enabled", True):
            ref = ncm.extract_song_ref(text)
            if ref:
                det.music_refs.append(ref)
        if video_on and conf.bool("video_bili_enabled", True):
            for m in BILI_PATTERN.findall(text):
                url = m if m.startswith("http") else f"https://{m}"
                if url not in det.bili_urls:
                    det.bili_urls.append(url)
        for url in DIRECT_MEDIA_PATTERN.findall(text):
            if ncm.SONG_URL_PATTERN.search(url):
                continue
            ext = os.path.splitext(url.split("?")[0])[1].lower()
            if ext in AUDIO_EXTS and audio_on:
                if url not in det.direct_audio_urls:
                    det.direct_audio_urls.append(url)
            elif ext in VIDEO_EXTS and video_on:
                if url not in det.direct_video_urls:
                    det.direct_video_urls.append(url)
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
            det = self.detect(event, flags)
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
                        self.plugin.conf.str("notice_provider"),
                    )
                    await event.send(event.plain_result(notice))
                except Exception:
                    pass
            self.plugin.log.info(f"检测到媒体内容，开始解析：{key}")
            self._tasks[key] = asyncio.create_task(
                self._run(event, det, flags, workdir, key)
            )
        except Exception as exc:
            self.plugin.log.error(f"消息检测异常：{exc}")

    async def _run(self, event, det: Detected, flags: dict, workdir: str, key: str) -> None:
        try:
            parts = await self._analyze(event, det, flags, workdir)
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

    async def _analyze(self, event, det: Detected, flags: dict, workdir: str) -> list[str]:
        conf = self.plugin.conf
        umo = self._umo(event)
        ffmpeg = find_tool("ffmpeg", conf.str("env_ffmpeg_path")) or "ffmpeg"
        ffprobe = find_tool("ffprobe", "") or "ffprobe"
        ytdlp = find_tool("yt-dlp", conf.str("env_ytdlp_path")) or "yt-dlp"
        parts: list[str] = []
        idx = 0

        if det.voices or det.audio_files:
            for comp in (det.voices + det.audio_files)[:3]:
                idx += 1
                parts.append(await self._analyze_audio_component(
                    comp, idx, workdir, ffmpeg, ffprobe, umo))

        for ref in det.music_refs[:3]:
            parts.append(await self._analyze_music_ref(ref, workdir, ffmpeg, umo))

        for url in det.direct_audio_urls[:2]:
            parts.append(await self._analyze_audio_url(url, workdir, ffmpeg, ffprobe, umo))

        if flags.get("video_enabled"):
            for comp in det.videos[:2]:
                idx += 1
                parts.append(await self._analyze_video_component(
                    comp, idx, workdir, ffmpeg, ffprobe, umo))
            if conf.bool("video_bili_enabled", True):
                for url in det.bili_urls[:2]:
                    parts.append(await self._analyze_bili(url, workdir, ffmpeg, ffprobe,
                                                          ytdlp, umo))
            for url in det.direct_video_urls[:2]:
                parts.append(await self._analyze_direct_video(url, workdir, ffmpeg,
                                                              ffprobe, umo))
        return parts

    async def _materialize(self, comp, workdir: str, max_bytes: int) -> str | None:
        """把消息组件落地为本地文件（优先本地路径，其次 URL 下载）。"""
        candidates = []
        for attr in ("file", "path"):
            value = getattr(comp, attr, None)
            if value:
                candidates.append(str(value))
        for cand in candidates:
            if os.path.isfile(cand):
                return cand
        url = str(getattr(comp, "url", "") or "")
        if url.startswith("http"):
            suffix = os.path.splitext(url.split("?")[0])[1] or ".bin"
            dest = os.path.join(workdir, f"comp_{uuid.uuid4().hex[:6]}{suffix}")
            ok, msg = await download_url(url, dest, max_bytes)
            if ok:
                return dest
            self.plugin.log.warn(f"媒体下载失败：{msg}")
        return None

    # -------- 音频/语音 --------

    async def _analyze_audio_component(self, comp, idx: int, workdir: str,
                                       ffmpeg: str, ffprobe: str, umo: str) -> str:
        label = "语音消息" if comp.__class__.__name__ == "Record" else "音频文件"
        lines = [f"【音频解析 #{idx}】来源：{label}"]
        path = await self._materialize(comp, workdir, AUDIO_MAX_BYTES)
        if not path:
            lines.append("（无法获取到音频文件，已跳过）")
            return "\n".join(lines)
        probe = await probe_media(path, ffprobe)
        if probe:
            meta = summarize_probe(probe)
            lines.append("元数据：" + (video_analyzer.meta_line(meta) or "未知"))
        text = await self._transcribe(umo, path)
        if text:
            lines.append(f"转文本：{text[:2000]}")
        else:
            lines.append("（未配置语音转文本模型或转写为空，已跳过转文本）")
        return "\n".join(lines)

    async def _transcribe(self, umo: str, path: str) -> str:
        try:
            stt = await self.plugin.context.get_using_stt_provider_async(umo)
        except Exception:
            stt = None
        if stt is None:
            return ""
        try:
            text = await asyncio.wait_for(stt.get_text(path), timeout=120)
            return (text or "").strip()
        except Exception as exc:
            self.plugin.log.warn(f"语音转文本失败：{exc}")
            return ""

    async def _analyze_audio_url(self, url: str, workdir: str, ffmpeg: str,
                                 ffprobe: str, umo: str) -> str:
        lines = ["【音频链接】"]
        dest = os.path.join(workdir, f"audiolink_{uuid.uuid4().hex[:6]}.bin")
        ok, msg = await download_url(url, dest, AUDIO_MAX_BYTES)
        if not ok:
            lines.append(f"下载失败：{msg}")
            return "\n".join(lines)
        probe = await probe_media(dest, ffprobe)
        if probe:
            lines.append("元数据：" + (video_analyzer.meta_line(summarize_probe(probe)) or "未知"))
        text = await self._transcribe(umo, dest)
        if text:
            lines.append(f"转文本：{text[:2000]}")
        else:
            spectrum = await self._spectrum_for(dest, ffmpeg)
            if spectrum:
                lines.append("频谱数据（文本，未获得转文本结果时的替代）：\n" + spectrum)
        return "\n".join(lines)

    async def _spectrum_for(self, path: str, ffmpeg: str,
                            seg_sec: int | None = None) -> str:
        if not self.plugin.conf.bool("audio_spectrum_enabled", True):
            return ""
        if not audio_analyzer.has_numpy():
            return ""
        seg = seg_sec or self.plugin.conf.int("audio_spectrum_seg", 5)
        pcm = await decode_pcm(path, 22050, ffmpeg)
        segments = audio_analyzer.spectrum_segments(pcm, 22050, seg)
        return audio_analyzer.spectrum_text(segments)

    # -------- 音乐链接 --------

    async def _analyze_music_ref(self, ref, workdir: str, ffmpeg: str, umo: str) -> str:
        conf = self.plugin.conf
        cookie = conf.str("audio_ncm_cookie")
        lines = ["【音乐链接解析】网易云音乐"]
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
            spectrum = await self._spectrum_for(path, ffmpeg)
            if spectrum:
                lines.append("频谱数据（文本）：\n" + spectrum)
            mode = conf.str("audio_deep_mode", "auto")
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

    async def _analyze_video_component(self, comp, idx: int, workdir: str,
                                       ffmpeg: str, ffprobe: str, umo: str) -> str:
        conf = self.plugin.conf
        max_bytes = conf.int("video_max_size_mb", 100) * 1024 * 1024
        lines = [f"【视频解析 #{idx}】来源：视频消息"]
        path = await self._materialize(comp, workdir, max_bytes)
        if not path:
            lines.append("（无法获取到视频文件，已跳过）")
            return "\n".join(lines)
        lines.append(await self._analyze_video_file(path, workdir, ffmpeg, ffprobe, umo))
        return "\n".join(lines)

    async def _analyze_video_file(self, path: str, workdir: str, ffmpeg: str,
                                  ffprobe: str, umo: str) -> str:
        conf = self.plugin.conf
        result = await video_analyzer.analyze_video_source(
            path, workdir, ffmpeg, ffprobe,
            frame_count=conf.int("video_frames", 6),
            frame_width=conf.int("video_frame_width", 640),
        )
        lines: list[str] = []
        lines.append("元数据：" + (video_analyzer.meta_line(result["meta"]) or "未知"))
        frames = result.get("frames") or []
        if frames:
            caption_provider = conf.str("video_caption_provider")
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
                lines.append("画面理解（逐帧描述）：\n" + "\n".join(descs))
            else:
                lines.append("（未能描述画面：未配置图转文模型或调用失败）")
        if result.get("audio_path"):
            audio_lines = ["音轨解析："]
            text = await self._transcribe(umo, result["audio_path"])
            if text:
                audio_lines.append(f"转文本：{text[:2000]}")
            else:
                spectrum = await self._spectrum_for(result["audio_path"], ffmpeg)
                if spectrum:
                    audio_lines.append("频谱数据（文本，未获得转文本结果时的替代）：\n" + spectrum)
                else:
                    audio_lines.append("（未配置语音转文本模型，音轨内容已跳过）")
            lines.append("\n".join(audio_lines))
        return "\n".join(lines)

    async def _analyze_bili(self, url: str, workdir: str, ffmpeg: str, ffprobe: str,
                            ytdlp: str, umo: str) -> str:
        conf = self.plugin.conf
        max_bytes = conf.int("video_max_size_mb", 100) * 1024 * 1024
        max_minutes = conf.int("video_max_minutes", 10)
        lines = ["【B站视频解析】"]
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
        lines.append(await self._analyze_video_file(path, workdir, ffmpeg, ffprobe, umo))
        return "\n".join(lines)

    async def _analyze_direct_video(self, url: str, workdir: str, ffmpeg: str,
                                    ffprobe: str, umo: str) -> str:
        conf = self.plugin.conf
        max_bytes = conf.int("video_max_size_mb", 100) * 1024 * 1024
        lines = ["【视频链接解析】"]
        dest = os.path.join(workdir, f"videolink_{uuid.uuid4().hex[:6]}.bin")
        ok, msg = await download_url(url, dest, max_bytes)
        if not ok:
            lines.append(f"下载失败：{msg}")
            return "\n".join(lines)
        lines.append(await self._analyze_video_file(dest, workdir, ffmpeg, ffprobe, umo))
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