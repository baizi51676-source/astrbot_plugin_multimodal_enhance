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
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()