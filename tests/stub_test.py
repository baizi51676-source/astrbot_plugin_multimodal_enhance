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
    log.error("boom")
    tail = log.tail(10)
    check("seq 递增", tail[-1]["seq"] > tail[0]["seq"])
    check("recent_errors 过滤", log.recent_errors(5)[-1]["msg"] == "boom")
    check("export_text 可导出", "boom" in log.export_text(10))


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
    from core.media.bilibili import parse_bili_id
    check("BV 号提取",
          parse_bili_id("https://www.bilibili.com/video/BV1xx411c7mD?p=1") == ("bvid", "BV1xx411c7mD"))
    check("av 号提取",
          parse_bili_id("https://www.bilibili.com/video/av12345") == ("aid", "12345"))
    check("纯 BV 串提取", parse_bili_id("看看 BV1GJ411x7h7 这个") == ("bvid", "BV1GJ411x7h7"))
    check("短链本地不解（返回 None）", parse_bili_id("https://b23.tv/abc123") is None)
    from core.config import DEFAULTS
    check("分析中提示词默认值", "正在理解" in str(DEFAULTS.get("notice_prompt", "")))


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
    check("workdir 默认", resolve_work_dir(conf, "/tmp/x") == "/tmp/x/tmp")
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
        check("file:// 解码", resolved == test_file)

        data_path = asyncio.run(pipeline._resolve_value(
            "data:application/octet-stream;base64," + b64, tmp, 10 ** 6))
        check("data: 落地", bool(data_path) and os.path.isfile(data_path))


def main() -> None:
    print("== 多模态理解增强 · 离线自测 ==")
    test_config()
    test_logger()
    test_prompt()
    test_ncm_links()
    test_spectrum()
    test_env_manager()
    test_pipeline_detect()
    test_resolve_value()
    test_split_wav()
    test_wake_gate()
    test_bilibili_parse()
    test_voice_fallback()
    test_native_stt_control()
    test_attach_flags()
    test_attachment_flow()
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()