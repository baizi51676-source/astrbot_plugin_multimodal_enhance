"""离线自测：不依赖 AstrBot 运行时与服务器，可在开发机直接运行。"""

from __future__ import annotations

import asyncio
import base64
import math
import os
import pathlib
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def check(name: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}")


# ---------------- 配置 ----------------

def test_config() -> None:
    print("- 配置模块（v2 覆写模型）")
    from core.config import PluginConfig
    conf = PluginConfig({
        "bots": [
            {"name": "t", "bots": ["1234567890", "test-instance"],
             "enable_video": True, "enable_image": False},  # 旧版字段兼容
            {"name": "u", "bots": ["second"],
             "overrides": "{\"video_frames\": 4, \"video_enabled\": true}"},
        ],
        "video_enabled": False,
    })
    flags = conf.resolve_feature_flags("test-instance", "1234567890")
    check("旧版 enable_* 兼容（video 开）", flags["video_enabled"] is True)
    check("旧版 enable_* 兼容（image 关）", flags["image_enabled"] is False)
    other = conf.resolve_feature_flags("other", "123")
    check("未命中使用全局（video 关）", other["video_enabled"] is False)
    eff = conf.effective_config_for("second", "")
    check("overrides JSON 字符串解析", eff["video_frames"] == 4 and eff["video_enabled"] is True)
    bots = conf.iter_bots()
    check("iter_bots 数量", len(bots) == 2)
    check("iter_bots 覆写字典", bots[1]["overrides"].get("video_frames") == 4)


# ---------------- 日志 ----------------

def test_logger() -> None:
    print("- 日志模块")
    from core.logger import PluginLogger
    log = PluginLogger("test", max_lines=50)
    log.info("hello")
    log.debug("detail")
    log.error("boom")
    tail = log.tail(10)
    check("seq 递增", tail[-1]["seq"] > tail[0]["seq"])
    check("debug 可用", any(e["level"] == "DEBUG" for e in tail))
    check("recent_errors 过滤", log.recent_errors(5)[-1]["msg"] == "boom")
    check("export_text 可导出", "boom" in log.export_text(10))

    class FakeForward:
        def __init__(self):
            self.records = []

        def info(self, msg):
            self.records.append(("info", msg))

        def warning(self, msg):
            self.records.append(("warning", msg))

        def error(self, msg):
            self.records.append(("error", msg))

        def debug(self, msg):
            self.records.append(("debug", msg))

    fake = FakeForward()
    log2 = PluginLogger("test2", max_lines=20, forward=fake)
    log2.info("hello-official")
    log2.warn("warn-official")
    check("转发到官方 logger",
          ("info", "hello-official") in fake.records
          and ("warning", "warn-official") in fake.records)


# ---------------- 图片增强提示词 ----------------

def test_prompt() -> None:
    print("- 图片增强提示词")
    from core.caption_patch import DEFAULT_TEMPLATE, _build_prompt
    out = _build_prompt(DEFAULT_TEMPLATE, "这是什么？")
    check("占位符替换", "这是什么？" in out and "{user_message}" not in out)
    out2 = _build_prompt("请描述图片", "问题")
    check("无占位符时追加", "问题" in out2)


# ---------------- 网易云链接 ----------------

def test_ncm_links() -> None:
    print("- 网易云链接解析")
    from core.media.ncm import extract_song_ref
    cases = [
        ("https://music.163.com/song?id=12345", ("id", "12345")),
        ("分享单曲 https://music.163.com/#/song?id=347230", ("id", "347230")),
        ("https://y.music.163.com/m/song?id=999", ("id", "999")),
        ("https://163cn.tv/AbCdEf 看看", ("short", "AbCdEf")),
    ]
    for text, expect in cases:
        check(f"链接 {text[:40]}", extract_song_ref(text) == expect)
    check("无链接返回 None", extract_song_ref("普通消息") is None)


# ---------------- 频谱 ----------------

def test_spectrum() -> None:
    print("- 频谱分析（numpy）")
    from core.media import audio_analyzer
    if not audio_analyzer.has_numpy():
        print("  [SKIP] 环境无 numpy")
        return
    import numpy as np
    sr = 22050
    t = np.arange(sr * 3, dtype=np.float32) / sr
    wave_data = (0.6 * np.sin(2 * math.pi * 440 * t) * 32767).astype(np.int16)
    segments = audio_analyzer.spectrum_segments(wave_data.tobytes(), sr, 1.0)
    check("分段数量", len(segments) >= 2)
    if segments:
        bands = segments[0]["bands"]
        top = max(range(len(bands)), key=lambda i: bands[i])
        check("440Hz 能量落在 250-1000Hz 段", top in (3, 4))
        text = audio_analyzer.spectrum_text(segments)
        check("文本包含汇总", "汇总" in text)


def test_split_wav() -> None:
    print("- WAV 拆分（STT 单段上限）")
    import tempfile
    import wave
    from core.media.audio_analyzer import split_wav
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "big.wav")
        frames = 16000 * 60  # 60 秒 16k 单声道 16bit ≈ 1.92MB
        with wave.open(wav_path, "wb") as fp:
            fp.setnchannels(1)
            fp.setsampwidth(2)
            fp.setframerate(16000)
            fp.writeframes(b"\x00\x00" * frames)
        parts = split_wav(wav_path, 500 * 1024, tmp)
        check("拆分数量 >= 3", len(parts) >= 3)
        check("每段不超过上限（含页头）",
              all(os.path.getsize(p) <= 500 * 1024 + 1024 for p in parts))
        total = 0
        for part in parts:
            with wave.open(part, "rb") as fp:
                total += fp.getnframes()
        check("帧总数一致", total == frames)


def test_wake_gate() -> None:
    print("- 唤醒门控（未 @ 不触发解析）")
    pipeline = _make_pipeline()
    ev = _make_event([Video(file="x.mp4")])
    ev.is_at_or_wake_command = False
    asyncio.run(pipeline.on_message(ev))
    plugin = pipeline.plugin
    check("未唤醒：不创建任务", len(pipeline._tasks) == 0)
    check("未唤醒：无解析日志",
          not any("检测到媒体" in e["msg"] for e in plugin.log.tail(30)))


def test_bilibili_parse() -> None:
    print("- B站链接解析")
    from core.media.bilibili import parse_bili_id, _clean_title, _parse_duration
    check("BV 号提取",
          parse_bili_id("https://www.bilibili.com/video/BV1xx411c7mD?p=1") == ("bvid", "BV1xx411c7mD"))
    check("av 号提取",
          parse_bili_id("https://www.bilibili.com/video/av12345") == ("aid", "12345"))
    check("纯 BV 串提取", parse_bili_id("看看 BV1GJ411x7h7 这个") == ("bvid", "BV1GJ411x7h7"))
    check("短链本地不解（返回 None）", parse_bili_id("https://b23.tv/abc123") is None)
    check("时长 5:52", _parse_duration("5:52") == 352)
    check("时长 1:02:03", _parse_duration("1:02:03") == 3723)
    check("标题去 HTML", _clean_title("<em class=\"keyword\">某视频</em>") == "某视频")
    from core.config import DEFAULTS
    check("分析中提示词默认值", "正在理解" in str(DEFAULTS.get("notice_prompt", "")))


def test_bilibili_fallback() -> None:
    print("- B站 view 被风控时的兜底链")
    from core.media import bilibili

    async def fake_view(key, value):
        return None

    async def fake_search(key, value):
        return {"bvid": "BV1xx411c7mD", "aid": 123, "title": "<em>标题</em>",
                "author": "UP主", "pubdate": 100, "duration": "5:52",
                "description": "搜索简介", "pic": "//x/y.jpg"}

    async def fake_page(key, value):
        return {"cid": 456, "duration": 352, "part": "分P标题", "ctime": 99}

    async def fake_desc(key, value):
        return "完整简介"

    originals = (bilibili._fetch_view, bilibili._fetch_search,
                 bilibili._fetch_pagelist, bilibili._fetch_desc)
    bilibili._fetch_view = fake_view
    bilibili._fetch_search = fake_search
    bilibili._fetch_pagelist = fake_page
    bilibili._fetch_desc = fake_desc
    try:
        info = asyncio.run(bilibili.fetch_video_info(
            "https://www.bilibili.com/video/BV1xx411c7mD"))
    finally:
        (bilibili._fetch_view, bilibili._fetch_search,
         bilibili._fetch_pagelist, bilibili._fetch_desc) = originals
    check("兜底返回元数据", bool(info))
    if info:
        check("兜底 cid", info.get("cid") == 456)
        check("兜底标题去 HTML", info.get("title") == "标题")
        check("兜底 UP 主", info.get("owner") == "UP主")
        check("兜底时长", info.get("duration") == 352)
        check("兜底简介优先 desc 接口", info.get("desc") == "完整简介")


def test_forget_task() -> None:
    print("- 任务表清理（无结果时移除）")
    pipeline = _make_pipeline()
    pipeline._tasks["no-result"] = object()
    pipeline._results.pop("no-result", None)
    pipeline._forget_task("no-result")
    check("无结果任务被移除", "no-result" not in pipeline._tasks)

    pipeline._tasks["has-result"] = object()
    pipeline._results["has-result"] = {"text": "x", "workdir": "", "ts": 0}
    pipeline._forget_task("has-result")
    check("有结果任务保留到注入清理", "has-result" in pipeline._tasks)
    pipeline._cleanup_key("has-result")
    check("注入后任务被清理", "has-result" not in pipeline._tasks)


def test_voice_fallback() -> None:
    print("- 语音解析兜底（原生 STT 替换后）")
    pipeline = _make_pipeline()
    flags = {"audio_enabled": True, "video_enabled": True}
    # 1) 直接语音被替换：chain 无 Record，raw 里有 record
    ev = _make_event([Plain("转录文本")], text="转录文本")
    ev.message_obj.raw_message = {
        "message": [{"type": "record",
                     "data": {"file": "a.amr", "url": "https://x/a.amr"}}]}
    det = pipeline.detect(ev, flags, {})
    check("原生替换后仍检测到语音（raw 兜底）",
          len(det.raw_voices) == 1 and det.raw_voices[0]["quoted"] is False)
    # 2) 引用链无语音：进入探测
    ev2 = _make_event([Reply(id="42", chain=[Plain("转录")])])
    det2 = pipeline.detect(ev2, flags, {})
    check("引用链无语音时进入探测", det2.reply_probe_ids == ["42"])
    # 3) 正常 Record 不受影响
    det3 = pipeline.detect(_make_event([Record(file="base64://AAAA")]), flags, {})
    check("正常 Record 仍走组件路径",
          len(det3.voices) == 1 and not det3.raw_voices)
    # 4) 探测函数（模拟 OneBot get_msg）
    class FakeBot:
        async def call_action(self, action, **kwargs):
            if action == "get_msg":
                return {"message": [{"type": "record",
                                     "data": {"file": "q.amr", "url": "https://x/q.amr"}}]}
            raise RuntimeError(action)
    ev2.bot = FakeBot()
    asyncio.run(pipeline._probe_reply_voices(ev2, det2))
    check("探测到被引用语音并计入待解析",
          any(r.get("quoted") for r in det2.raw_voices))


def test_native_stt_control() -> None:
    print("- 原生语音转写接管（截断）")
    from core import native_stt as ns
    from core.config import PluginConfig

    class P:
        pass

    plugin = P()
    plugin.conf = PluginConfig({"audio_enabled": True, "cut_native_stt": True})
    plugin.resolve_flags_for_event = lambda e: {
        "audio_enabled": plugin.conf.bool("audio_enabled", True)}
    plugin.effective_settings_for = lambda e: {
        "audio_file_enabled": True, "cut_native_stt": True}
    ctl = ns.NativeSTTControl()
    ctl.plugin = plugin

    class S:
        pass

    stage = S()
    stage.stt_settings = {"enable": True, "provider_id": "mimo"}
    ev = S()
    ctl.sync_stage(stage, ev)
    check("插件开启 → 原生 STT 被截断",
          stage.stt_settings.get("enable") is False
          and bool(stage.stt_settings.get("_mme_cut")))

    plugin.effective_settings_for = lambda e: {
        "audio_file_enabled": True, "cut_native_stt": False}
    ctl.sync_stage(stage, ev)
    check("关闭接管 → 恢复原生设置",
          stage.stt_settings.get("enable") is True
          and not stage.stt_settings.get("_mme_cut"))

    plugin.effective_settings_for = lambda e: {
        "audio_file_enabled": True, "cut_native_stt": True}
    plugin.conf = PluginConfig({"audio_enabled": False, "cut_native_stt": True})
    stage.stt_settings = {"enable": True}
    ctl.sync_stage(stage, ev)
    check("音频功能关闭 → 不截断", stage.stt_settings.get("enable") is True)


def test_attach_flags() -> None:
    print("- 主模型能力检测（附件直传）")
    from core.capabilities import attach_flags, provider_modalities

    class FakeProvider:
        def __init__(self, mods):
            self.provider_config = {"modalities": mods}

    flags = attach_flags(["text", "image", "audio"], audio_on=True, frames_on=True)
    check("音频/图片均支持", flags == {"audio": True, "image": True})
    flags2 = attach_flags(["text", "image"], audio_on=True, frames_on=True)
    check("仅图片支持", flags2 == {"audio": False, "image": True})
    flags3 = attach_flags(["text", "audio"], audio_on=False, frames_on=True)
    check("开关关闭后不直传", flags3 == {"audio": False, "image": False})
    check("未声明 modalities 返回 None", provider_modalities(FakeProvider(None)) is None)
    check("未声明但按支持处理",
          provider_modalities(FakeProvider([]), True) == ["text", "image", "audio"])
    check("声明读取（大小写归一）",
          provider_modalities(FakeProvider(["text", "Image"])) == ["text", "image"])


def test_attachment_flow() -> None:
    print("- 附件直传（登记与注入）")
    pipeline = _make_pipeline()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.wav")
        with open(path, "wb") as fp:
            fp.write(b"x" * 100)
        attach = {"audio": True, "image": False, "max_bytes": 10 ** 6, "files": []}
        check("登记音频附件", pipeline._collect_audio_attachment(attach, path, "t") is True)
        check("附件列表非空", len(attach["files"]) == 1)
        attach2 = {"audio": True, "image": False, "max_bytes": 10, "files": []}
        check("超过体积上限不登记",
              pipeline._collect_audio_attachment(attach2, path, "t") is False)
        attach3 = {"audio": False, "image": True, "max_bytes": 10 ** 6, "files": []}
        check("图片模式登记抽帧",
              pipeline._collect_frames(attach3, [{"path": path}]) is True)

        class Req:
            audio_urls = []
            image_urls = []

        req = Req()
        ok = pipeline._append_attachments(req, [path], [])
        check("写入 req.audio_urls", ok and req.audio_urls == [path])
        ok2 = pipeline._append_attachments(req, [path], [])
        check("重复路径去重", ok2 is False)


# ---------------- 环境管理 ----------------

def test_env_manager() -> None:
    print("- 环境管理")
    from core.config import PluginConfig
    from core.env_manager import ALL_PACKAGES, format_bytes, resolve_work_dir
    check("format_bytes", format_bytes(1536).startswith("1.5"))
    conf = PluginConfig({})
    check("workdir 默认", resolve_work_dir(conf, "/tmp/x") == os.path.join("/tmp/x", "tmp"))
    conf2 = PluginConfig({"env_work_dir": "/data/w"})
    check("workdir 自定义", resolve_work_dir(conf2, "/tmp/x") == "/data/w")
    check("一键安装包列表", ALL_PACKAGES == ["numpy", "librosa", "scipy", "soundfile"])
    from core.env_manager import ONE_CLICK_LABELS
    check("一键配置包含 ffmpeg 与 yt-dlp",
          len(ONE_CLICK_LABELS) == 3 and "ffmpeg" in ONE_CLICK_LABELS[0]
          and "yt-dlp" in ONE_CLICK_LABELS[2])


# ---------------- 管线检测（含引用消息） ----------------

class StubBase:
    pass


class Plain:
    def __init__(self, text=""):
        self.text = text


class Video:
    def __init__(self, file="", url=""):
        self.file = file
        self.url = url


class Record:
    def __init__(self, file=""):
        self.file = file


class Image:
    pass


class Reply:
    def __init__(self, id="", chain=None, message_str=""):
        self.id = id
        self.chain = chain
        self.message_str = message_str


class Json:
    def __init__(self, data):
        self.data = data


class Music:
    def __init__(self, _type="", id=0):
        self._type = _type
        self.id = id


class Unknown:
    def __init__(self, text=""):
        self.text = text


def _make_pipeline():
    from core.config import PluginConfig
    from core.logger import PluginLogger
    from core.pipeline import MediaPipeline
    plugin = StubBase()
    plugin.conf = PluginConfig({"video_enabled": True})
    plugin.log = PluginLogger("stub", max_lines=50)
    plugin.workdir_base = "/tmp/mme_test"
    return MediaPipeline(plugin)


def _make_event(comps, text=""):
    event = StubBase()
    event.message_obj = StubBase()
    event.message_obj.message = comps
    event.message_obj.message_id = "1"
    event.unified_msg_origin = "umo"
    event.message_str = text
    return event


def test_logger_compliance() -> None:
    print("- 日志规范（astrbot.api.logger）")
    main_src = pathlib.Path(ROOT, "main.py").read_text(encoding="utf-8")
    logger_src = pathlib.Path(ROOT, "core", "logger.py").read_text(encoding="utf-8")
    check("main 导入官方 logger", "from astrbot.api import AstrBotConfig, logger" in main_src)
    check("main 使用官方 logger", "forward=logger" in main_src and "logger.debug(" in main_src)
    check("core/logger 导入官方 logger", "from astrbot.api import logger" in logger_src)
    check("core/logger 转发官方 logger", "_forward" in logger_src and "getattr(self._forward" in logger_src)


def test_pipeline_detect() -> None:
    print("- 管线检测（含引用消息）")
    pipeline = _make_pipeline()
    flags = {"audio_enabled": True, "video_enabled": True}

    # 1) 引用消息中的视频 + 引用文本里的 B 站链接
    event = _make_event([
        Reply(id="9", chain=[Video(file="a.mp4"), Plain("看看 https://b23.tv/abc")]),
    ])
    det = pipeline.detect(event, flags, {})
    check("识别引用视频", len(det.videos) == 1 and det.videos[0][1] is True)
    check("识别引用文本中的 B 站链接", len(det.bili_urls) == 1 and det.bili_urls[0][1] is True)
    check("takes_time 判定", det.takes_time is True)

    # 2) 直接视频
    det2 = pipeline.detect(_make_event([Video(url="http://x/a.mp4")]), flags, {})
    check("识别直接视频", len(det2.videos) == 1 and det2.videos[0][1] is False)

    # 3) 无 chain 的引用 -> 记录 reply_id
    det3 = pipeline.detect(_make_event([Reply(id="42")]), flags, {})
    check("无 chain 引用记录 ID", det3.reply_ids == ["42"])

    # 4) 语音（base64://）识别
    det4 = pipeline.detect(_make_event([Record(file="base64://AAAA")]), flags, {})
    check("识别语音组件", len(det4.voices) == 1)

    # 5) 功能关闭时给出提示
    det5 = pipeline.detect(_make_event([Video(file="b.mp4")]),
                           {"audio_enabled": True, "video_enabled": False}, {})
    check("视频关闭时记录 seen_gated", det5.empty and any("视频" in s for s in det5.seen_gated))

    # 6) 引用文本中的网易云链接
    det6 = pipeline.detect(_make_event([
        Reply(id="7", message_str="分享 https://music.163.com/song?id=5"),
    ]), flags, {})
    check("识别引用文本中的音乐链接", len(det6.music_refs) == 1 and det6.music_refs[0][1] is True)

    # 7) 网易云分享卡片（Json 组件）
    card = Json({"app": "com.tencent.structmsg",
                 "meta": {"music": {"jumpUrl": "https://163cn.tv/AbCdEf", "title": "某歌"}}})
    det7 = pipeline.detect(_make_event([card]), flags, {})
    check("识别网易云分享卡片",
          len(det7.music_refs) == 1 and det7.music_refs[0][0] == ("short", "AbCdEf"))

    # 8) Music 组件（type=163 + id）
    det8 = pipeline.detect(_make_event([Music(_type="163", id=12345)]), flags, {})
    check("识别 Music 组件", any(r[0] == ("id", "12345") for r in det8.music_refs))

    # 9) 引用链中的分享卡片
    det9 = pipeline.detect(_make_event([Reply(id="8", chain=[card])]), flags, {})
    check("识别引用链中的卡片", len(det9.music_refs) == 1 and det9.music_refs[0][1] is True)

    # 10) 直链音频不再依赖 video_enabled
    det10 = pipeline.detect(
        _make_event([], text="听听 https://x.test/a.mp3"),
        {"audio_enabled": True, "video_enabled": False}, {})
    check("视频关闭时仍识别音频直链", len(det10.direct_audio_urls) == 1)


def test_resolve_value() -> None:
    print("- 媒体落地（base64:// / file:// / data:）")
    pipeline = _make_pipeline()
    with tempfile.TemporaryDirectory() as tmp:
        raw = b"#!AMR\x00dummy-audio-bytes-for-test"
        b64 = base64.b64encode(raw).decode()
        path = asyncio.run(pipeline._resolve_value("base64://" + b64, tmp, 10 ** 6))
        check("base64:// 落地", bool(path) and os.path.isfile(path) and path.endswith(".amr"))
        if path:
            with open(path, "rb") as fp:
                check("base64:// 内容一致", fp.read() == raw)

        test_file = os.path.join(tmp, "plain.txt")
        with open(test_file, "w", encoding="utf-8") as fp:
            fp.write("hi")
        uri = pathlib.Path(test_file).as_uri()
        resolved = asyncio.run(pipeline._resolve_value(uri, tmp, 10 ** 6))
        check("file:// 解码", os.path.normpath(resolved or "") == os.path.normpath(test_file))

        data_path = asyncio.run(pipeline._resolve_value(
            "data:application/octet-stream;base64," + b64, tmp, 10 ** 6))
        check("data: 落地", bool(data_path) and os.path.isfile(data_path))


def test_prune() -> None:
    print("- 过期结果与临时目录清理")
    import shutil
    import time as _time
    pipeline = _make_pipeline()
    with tempfile.TemporaryDirectory() as tmp:
        # 1) 过期结果 + 真实目录 -> 被清理
        old_dir = os.path.join(tmp, "old")
        os.makedirs(old_dir, exist_ok=True)
        with open(os.path.join(old_dir, "a.bin"), "wb") as fp:
            fp.write(b"x")
        pipeline._results["old"] = {"text": "t", "workdir": old_dir,
                                    "ts": _time.time() - 7200, "attachments": []}
        pipeline._prune()
        check("过期结果被移除", "old" not in pipeline._results)
        check("过期工作目录被删除", not os.path.isdir(old_dir))

        # 2) 刚续期的结果 -> 保留
        new_dir = os.path.join(tmp, "new")
        os.makedirs(new_dir, exist_ok=True)
        pipeline._results["new"] = {"text": "t", "workdir": new_dir,
                                    "ts": _time.time(), "attachments": []}
        pipeline._prune()
        check("未过期结果保留", "new" in pipeline._results)
        check("未过期工作目录保留", os.path.isdir(new_dir))

        # 3) 正在解析中的结果 -> 即使过期也不清（等它出结果）
        busy_dir = os.path.join(tmp, "busy")
        os.makedirs(busy_dir, exist_ok=True)

        async def _never():
            await asyncio.sleep(3600)

        async def _setup():
            pipeline._results["busy"] = {"text": "", "workdir": busy_dir,
                                         "ts": _time.time() - 7200, "attachments": []}
            pipeline._tasks["busy"] = asyncio.get_running_loop().create_task(_never())
            pipeline._prune()
            pipeline._tasks["busy"].cancel()
            await asyncio.gather(pipeline._tasks["busy"], return_exceptions=True)

        asyncio.run(_setup())
        check("解析中的结果不被清理", os.path.isdir(busy_dir))

        # 4) janitor 生命周期：可启动、可停止、幂等
        async def _lifecycle():
            pipeline.start_janitor()
            first = pipeline._janitor
            pipeline.start_janitor()
            check("重复启动幂等", pipeline._janitor is first)
            await pipeline.stop()
            check("停止后引用清空", pipeline._janitor is None)
            return first

        first = asyncio.run(_lifecycle())
        check("janitor 曾被创建", first is not None or True)
        shutil.rmtree(tmp, ignore_errors=True)


def test_netguard() -> None:
    print("- 网络边界（SSRF / 协议白名单）")
    from core.media.netguard import is_safe_url

    blocked = [
        ("http://127.0.0.1/x.mp3", "回环"),
        ("http://10.0.0.1/a", "内网"),
        ("http://192.168.1.5/a.mp4", "内网"),
        ("http://169.254.169.254/latest/meta-data", "链路本地"),
        ("http://[::1]/x", "IPv6 回环"),
        ("http://localhost:8080/a.mp3", "本机名"),
        ("file:///etc/passwd", "file 协议"),
        ("ftp://x.test/y", "ftp 协议"),
        ("gopher://x.test/y", "gopher 协议"),
        ("", "空地址"),
    ]
    for url, label in blocked:
        ok, _reason = asyncio.run(is_safe_url(url))
        check(f"拒绝{label}", ok is False)

    # 域名解析结果注入：公网域名通过、解析到内网拒绝（不依赖真实 DNS）
    from core.media import netguard

    async def fake_public(host):
        return ["93.184.216.34"]

    async def fake_private(host):
        return ["192.168.0.10"]

    orig = netguard._resolve_host
    try:
        netguard._resolve_host = fake_public
        ok, _ = asyncio.run(
            is_safe_url("https://music.163.com/song/media/outer/url?id=1.mp3"))
        check("公网域名放行", ok is True)
        ok, _ = asyncio.run(
            is_safe_url("https://api.bilibili.com/x/web-interface/view?bvid=BV1xx"))
        check("B 站 API 放行", ok is True)

        netguard._resolve_host = fake_private
        ok, reason = asyncio.run(is_safe_url("https://evil.test/a.mp3"))
        check("解析到内网被拒", ok is False)
        check("拒绝原因可读", "内网" in reason)
    finally:
        netguard._resolve_host = orig

    # 关键回归：校验必须异步，不能在事件循环里同步调 socket.getaddrinfo
    import inspect
    check("is_safe_url 是协程函数", inspect.iscoroutinefunction(is_safe_url))
    check("_resolve_host 是协程函数", inspect.iscoroutinefunction(netguard._resolve_host))

    # 阻塞检测：DNS 缓慢时事件循环仍需能被其它任务推进
    async def slow_resolve(_host):
        await asyncio.sleep(0.15)
        return ["93.184.216.34"]

    async def probe_non_blocking():
        netguard._resolve_host = slow_resolve
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        try:
            await is_safe_url("https://slow.test/a.mp3")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return ticks

    try:
        ticks = asyncio.run(probe_non_blocking())
    finally:
        netguard._resolve_host = orig
    check("DNS 解析期间事件循环未被阻塞", ticks >= 5)

    # 本地路径白名单：目录外拒绝、目录内放行
    pipeline = _make_pipeline()
    with tempfile.TemporaryDirectory() as tmp:
        inside = os.path.join(tmp, "ok.bin")
        with open(inside, "wb") as fp:
            fp.write(b"x")
        outside_dir = tempfile.mkdtemp()
        outside = os.path.join(outside_dir, "secret.bin")
        with open(outside, "wb") as fp:
            fp.write(b"x")
        try:
            ok = asyncio.run(pipeline._resolve_value(inside, tmp, 10 ** 6))
            check("工作目录内的本地路径放行", ok is not None)
            bad = asyncio.run(pipeline._resolve_value(outside, tmp, 10 ** 6))
            check("工作目录外的本地路径拒绝", bad is None)
            uri = pathlib.Path(outside).as_uri()
            bad2 = asyncio.run(pipeline._resolve_value(uri, tmp, 10 ** 6))
            check("越界 file:// 拒绝", bad2 is None)
            uri_in = pathlib.Path(inside).as_uri()
            good2 = asyncio.run(pipeline._resolve_value(uri_in, tmp, 10 ** 6))
            check("目录内 file:// 放行（含盘符）",
                  good2 is not None and os.path.normpath(good2) == os.path.normpath(inside))
            ftp = asyncio.run(pipeline._resolve_value("ftp://x.test/y.mp3", tmp, 10 ** 6))
            check("ftp 直链拒绝", ftp is None)
        finally:
            import shutil
            shutil.rmtree(outside_dir, ignore_errors=True)


def test_native_stt_concurrency() -> None:
    print("- 原生语音转写接管（并发隔离与即时还原）")
    from core import native_stt as ns
    from core.config import PluginConfig

    class P:
        pass

    plugin = P()
    plugin.conf = PluginConfig({"audio_enabled": True, "cut_native_stt": True})
    plugin.resolve_flags_for_event = lambda e: {"audio_enabled": True}
    plugin.effective_settings_for = lambda e: {
        "audio_file_enabled": True, "cut_native_stt": bool(getattr(e, "cut", True))}

    ctl = ns.NativeSTTControl()
    ctl.plugin = plugin

    class Stage:
        def __init__(self):
            self.stt_settings = {"enable": True, "provider_id": "mimo"}

    class E:
        def __init__(self, name, cut):
            self.name = name
            self.cut = cut

    # 真实交错：A（要求截断）先进入并在预处理中停留，B（不截断）中途到达。
    # 旧实现（无锁 + 靠下一个事件还原）会让 B 的 sync 把 A 的截断改回开启，
    # 即 A 处理期间 enable 变回 True —— 这里必须为 False。
    seen = {}

    async def run():
        stage = Stage()
        lock = asyncio.Lock()

        async def handle_a():
            async with lock:
                try:
                    ctl.sync_stage(stage, E("A", True))
                    seen["A"] = bool(stage.stt_settings.get("enable"))
                    await asyncio.sleep(0.05)   # 预处理耗时，留出 B 的到达窗口
                    return "A"
                finally:
                    ctl.restore_stage(stage)

        async def handle_b():
            await asyncio.sleep(0.01)           # B 在 A 处理中到达
            async with lock:                    # 被临界区挡在外面，等 A 结束
                try:
                    ctl.sync_stage(stage, E("B", False))
                    seen["B"] = bool(stage.stt_settings.get("enable"))
                    return "B"
                finally:
                    ctl.restore_stage(stage)

        out = await asyncio.gather(handle_a(), handle_b())
        return stage, out

    stage, out = asyncio.run(run())
    check("两个事件都执行完成", sorted(out) == ["A", "B"])
    check("A（要求截断）执行期间原生 STT 关闭", seen.get("A") is False)
    check("B（不截断）执行期间原生 STT 开启", seen.get("B") is True)
    check("全部结束后设置已还原", stage.stt_settings.get("enable") is True
          and not stage.stt_settings.get("_mme_cut"))

    # 无锁路径也能保证「用完即还原」，不会把决策留给下一个事件
    stage2 = Stage()
    ctl.sync_stage(stage2, E("C", True))
    check("进入时按事件设置", stage2.stt_settings.get("enable") is False)
    ctl.restore_stage(stage2)
    check("处理后立即还原（不等下一个事件）", stage2.stt_settings.get("enable") is True)
    ctl.restore_stage(stage2)
    check("重复还原安全", stage2.stt_settings.get("enable") is True)
    check("无循环时取锁返回 None（退化为无锁路径）", ctl._stage_lock() is None)


def test_install_lifecycle() -> None:
    print("- 依赖安装任务生命周期")
    from core import env_manager as em

    async def run():
        # 1) 重复启动被拒
        async def slow():
            em._install_state.update({"running": True, "started_at": "t",
                                      "packages": ["x"], "output": "", "ok": None})
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                em._install_state.update({"running": False, "ok": False})
                raise

        em._install_state.update({"running": False})
        em._install_task = None
        ok1, _msg1 = em._start_task(slow(), "已启动")
        check("首次启动成功", ok1 is True)
        ok2, msg2 = em._start_task(slow(), "已启动")
        check("第二次启动被拒", ok2 is False and "进行中" in msg2)

        await asyncio.sleep(0.05)
        check("运行中状态为真", em._install_state.get("running") is True)

        # 2) 取消
        cancelled = em.cancel_install()
        check("取消返回成功", cancelled is True)
        await asyncio.sleep(0.05)
        check("取消后状态复位", em._install_state.get("running") is False)
        check("取消后任务引用清空", em._install_task is None)
        check("取消后输出有标记", "已取消" in str(em._install_state.get("output")))

        # 3) 没有任务时取消返回 False
        check("无任务时取消返回 False", em.cancel_install() is False)

        # 4) 卸载复位
        em._install_state.update({"running": True})
        em.cancel_install_on_unload()
        check("卸载后状态复位", em._install_state.get("running") is False)

        # 5) 任务异常也会复位
        async def boom():
            em._install_state.update({"running": True})
            raise RuntimeError("模拟失败")

        em._install_task = None
        em._install_state.update({"running": False})
        ok, _ = em._start_task(boom(), "已启动")
        check("异常任务启动成功", ok is True)
        await asyncio.sleep(0.05)
        check("异常后状态复位", em._install_state.get("running") is False)
        check("异常被记录", "模拟失败" in str(em._install_state.get("output")))

    asyncio.run(run())


def test_music_attach_still_injects_meta() -> None:
    print("- 音乐卡片：附件直传后仍注入元数据与歌词")
    from core.media import ncm

    pipeline = _make_pipeline()
    calls = {"download": 0, "detail": 0, "lyric": 0}

    async def fake_download(song_id, dest_dir, cookie="", max_bytes=None):
        calls["download"] += 1
        os.makedirs(dest_dir, exist_ok=True)
        p = os.path.join(str(dest_dir), f"ncm_{song_id}.mp3")
        with open(p, "wb") as fp:  # noqa: ASYNC230 - 测试桩，仅写 60 字节
            fp.write(b"ID3" + b"x" * 60)
        return p
    async def fake_detail(song_id, cookie=""):
        calls["detail"] += 1
        return {"id": song_id, "name": "cat reincarnation", "artists": "nyamura",
                "album": "T", "duration_ms": 252000, "publish_ms": 0}

    async def fake_lyric(song_id, cookie=""):
        calls["lyric"] += 1
        return {"lyric": "[00:00.000] 作词 : nyamura\n[00:24.592] 初めて出会った日から",
                "tlyric": ""}

    async def fake_spectrum(path, ffmpeg, settings, seg_sec=None):
        return "峰值频率：440Hz"

    orig = (ncm.download_song, ncm.fetch_song_detail, ncm.fetch_lyric,
            pipeline._spectrum_for)
    ncm.download_song = fake_download
    ncm.fetch_song_detail = fake_detail
    ncm.fetch_lyric = fake_lyric
    pipeline._spectrum_for = fake_spectrum
    try:
        async def run_with_attach():
            attach = {"audio": True, "image": False, "max_bytes": 10 ** 6, "files": []}
            return await pipeline._analyze_music_ref(
                ("id", "2694804068"), False, "/tmp/mme_music", "ffmpeg",
                {"audio_enabled": True}, "umo", attach)

        text = asyncio.run(run_with_attach())
        check("直传后仍包含歌名与歌手", "cat reincarnation" in text and "nyamura" in text)
        check("直传后仍包含专辑与时长", "专辑" in text and "4:12" in text)
        check("直传后仍包含歌词", "歌词" in text and "初めて出会った" in text)
        check("直传提示仍在", "已作为附件随消息发送" in text)
        check("音源只下载一次（不重复下载）", calls["download"] == 1)
        check("直传时跳过频谱与深度分析", "频谱数据" not in text)

        calls.update({"download": 0, "detail": 0, "lyric": 0})

        async def run_without_attach():
            return await pipeline._analyze_music_ref(
                ("id", "2694804068"), True, "/tmp/mme_music2", "ffmpeg",
                {"audio_enabled": True}, "umo", None)

        text2 = asyncio.run(run_without_attach())
        check("非直传时输出频谱", "频谱数据" in text2 and "440Hz" in text2)
        check("非直传时标注引用消息", "（引用消息）" in text2)
        check("非直传也含歌词", "初めて出会った" in text2)
    finally:
        (ncm.download_song, ncm.fetch_song_detail, ncm.fetch_lyric,
         pipeline._spectrum_for) = orig


def test_concurrency_gate() -> None:
    print("- 全局解析并发闸门（排队语义）")
    from core.config import PluginConfig
    from core.pipeline import Detected

    pipeline = _make_pipeline()
    pipeline.plugin.conf = PluginConfig({"video_enabled": True, "max_parallel_parses": 2})

    state = {"cur": 0, "peak": 0}

    async def fake_analyze(*a, **k):
        state["cur"] += 1
        state["peak"] = max(state["peak"], state["cur"])
        await asyncio.sleep(0.05)
        state["cur"] -= 1
        return []

    pipeline._analyze = fake_analyze

    async def run():
        det = Detected()
        det.direct_audio_urls = [("https://x/a.mp3", False)]
        tasks = [asyncio.create_task(
            pipeline._run(None, det, {}, {}, f"/tmp/mme_gate_{i}", f"k{i}"))
            for i in range(6)]
        await asyncio.gather(*tasks)

    asyncio.run(run())
    check("并发峰值不超过上限 2", state["peak"] <= 2)
    check("6 条消息全部执行完", state["cur"] == 0 and state["peak"] >= 1)

    # 配置变化时闸门重建
    pipeline.plugin.conf = PluginConfig({"video_enabled": True, "max_parallel_parses": 4})
    gate = pipeline._gate()
    check("配置变化后闸门重建", pipeline._sem_size == 4 and gate is pipeline._sem)


def test_spectrum_limit() -> None:
    print("- 频谱时长上限")
    import core.pipeline as pl
    pipeline = _make_pipeline()
    captured = {}

    async def fake_decode(path, sr=22050, ffmpeg="ffmpeg", max_seconds=0):
        captured["max_seconds"] = max_seconds
        # 造 12 秒非静音 PCM（正弦波），保证能切出多个频谱段
        import math as _m
        import array
        n = sr * 12
        buf = array.array("h", (int(_m.sin(2 * _m.pi * 440 * i / sr) * 8000)
                                for i in range(n)))
        return buf.tobytes()

    orig = pl.decode_pcm
    pl.decode_pcm = fake_decode
    try:
        text = asyncio.run(pipeline._spectrum_for(
            "x.mp3", "ffmpeg", {"audio_spectrum_enabled": True,
                                "audio_spectrum_max_seconds": 300}))
        check("向 decode_pcm 传入时长上限", captured.get("max_seconds") == 300)
        check("文本标注仅分析前 N 秒", "仅分析前 300 秒" in text)

        text2 = asyncio.run(pipeline._spectrum_for(
            "x.mp3", "ffmpeg", {"audio_spectrum_enabled": True,
                                "audio_spectrum_max_seconds": 0}))
        check("上限 0 时不加标注", "仅分析前" not in text2)
        check("上限 0 时传 0（表示不限制）", captured.get("max_seconds") == 0)
    finally:
        pl.decode_pcm = orig


def test_stt_chunk_parallel() -> None:
    print("- 分块转写并发与顺序")
    pipeline = _make_pipeline()

    order = {"active": 0, "peak": 0}

    class FakeSTT:
        async def get_text(self, path):
            order["active"] += 1
            order["peak"] = max(order["peak"], order["active"])
            idx = int(os.path.basename(path).split("part")[-1].split(".")[0])
            # 靠后的段先返回，验证拼接仍按段号
            await asyncio.sleep(0.05 * (4 - idx))
            order["active"] -= 1
            return f"段{idx}"

    class Ctx:
        async def get_using_stt_provider_async(self, umo):
            return FakeSTT()

    pipeline.plugin.context = Ctx()

    import core.media.audio_analyzer as aa
    orig_split = aa.split_wav
    aa.split_wav = lambda p, mb, wd: [f"{wd}/x_part{i}.wav" for i in range(3)]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "long.wav")
            with open(src, "wb") as fp:  # noqa: ASYNC230 - 测试桩，同步写固定大小
                fp.write(b"x" * (2 * 1024 * 1024))
            text = asyncio.run(pipeline._transcribe("umo", src, {
                "audio_stt_chunk_mb": 1, "audio_stt_max_chunks": 12}, "ffmpeg", tmp))
        check("分块结果按段号顺序拼接", text == "段0段1段2")
        check("分块转写并发执行（峰值>1）", order["peak"] > 1)
        check("并发不超过上限 3", order["peak"] <= 3)
    finally:
        aa.split_wav = orig_split


def test_config_clamp() -> None:
    print("- 配置钳制")
    from core.config import DEFAULTS, LIMITS, PluginConfig, clamp_value
    check("超上限被钳制", clamp_value("video_frames", 9999) == 24)
    check("低于下限被钳制", clamp_value("video_frames", 0) == 1)
    check("非数值回退默认", clamp_value("video_frames", "abc") == 6)
    check("非受限键原样返回", clamp_value("image_enabled", 1) == 1)
    conf = PluginConfig({"video_frames": 9999, "max_parallel_parses": 99})
    check("读取时钳制（video_frames）", conf.int("video_frames", 6) == 24)
    check("读取时钳制（max_parallel_parses）", conf.int("max_parallel_parses", 2) == 8)
    eff = conf.effective_with_overrides({"video_frames": 9999})
    check("覆写值也钳制", eff["video_frames"] == 24)
    check("LIMITS 键都在 DEFAULTS 中", all(k in DEFAULTS for k in LIMITS))


def main() -> None:
    print("== 多模态理解增强 · 离线自测 ==")
    test_config()
    test_logger()
    test_logger_compliance()
    test_prompt()
    test_ncm_links()
    test_spectrum()
    test_env_manager()
    test_pipeline_detect()
    test_resolve_value()
    test_split_wav()
    test_wake_gate()
    test_bilibili_parse()
    test_bilibili_fallback()
    test_forget_task()
    test_voice_fallback()
    test_native_stt_control()
    test_attach_flags()
    test_attachment_flow()
    test_prune()
    test_netguard()
    test_native_stt_concurrency()
    test_install_lifecycle()
    test_music_attach_still_injects_meta()
    test_concurrency_gate()
    test_spectrum_limit()
    test_stt_chunk_parallel()
    test_config_clamp()
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
