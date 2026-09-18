"""离线自测：不依赖 AstrBot 运行时与服务器，可在开发机直接运行。"""

from __future__ import annotations

import json
import math
import os
import sys

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


def test_config() -> None:
    print("- 配置模块")
    from core.config import PluginConfig
    conf = PluginConfig({
        "bots": [{"name": "t", "bots": ["1234567890", "test-instance"],
                  "enable_video": True, "enable_image": False}],
        "video_enabled": False,
    })
    flags = conf.resolve_feature_flags("test-instance", "1234567890")
    check("bot 覆写命中（video 开）", flags["video_enabled"] is True)
    check("bot 覆写命中（image 关）", flags["image_enabled"] is False)
    other = conf.resolve_feature_flags("other", "123")
    check("未命中使用全局（video 关）", other["video_enabled"] is False)
    check("默认值兜底", conf.int("audio_spectrum_seg", 5) == 5)


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


def test_prompt() -> None:
    print("- 图片增强提示词")
    from core.caption_patch import DEFAULT_TEMPLATE, _build_prompt
    out = _build_prompt(DEFAULT_TEMPLATE, "这是什么？")
    check("占位符替换", "这是什么？" in out and "{user_message}" not in out)
    out2 = _build_prompt("请描述图片", "问题")
    check("无占位符时追加", "问题" in out2)


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


def test_env_manager() -> None:
    print("- 环境管理")
    from core.config import PluginConfig
    from core.env_manager import format_bytes, resolve_work_dir
    check("format_bytes", format_bytes(1536).startswith("1.5"))
    conf = PluginConfig({})
    check("workdir 默认", resolve_work_dir(conf, "/tmp/x") == "/tmp/x/tmp")
    conf2 = PluginConfig({"env_work_dir": "/data/w"})
    check("workdir 自定义", resolve_work_dir(conf2, "/tmp/x") == "/data/w")


def test_pipeline_detect() -> None:
    print("- 管线检测（模拟事件）")
    from core.config import PluginConfig
    from core.logger import PluginLogger
    from core.pipeline import MediaPipeline

    class Stub:
        pass

    plugin = Stub()
    plugin.conf = PluginConfig({"video_enabled": True})
    plugin.log = PluginLogger("stub", max_lines=50)
    plugin.workdir_base = "/tmp/mme_test"
    pipeline = MediaPipeline(plugin)

    event = Stub()
    event.message_obj = Stub()
    event.message_obj.message = []
    event.message_obj.message_id = "1"
    event.unified_msg_origin = "umo"
    event.message_str = ("看看 https://music.163.com/song?id=12345 "
                         "和 https://www.bilibili.com/video/BV1xx411c7mD")
    det = pipeline.detect(event, {"audio_enabled": True, "video_enabled": True})
    check("识别音乐链接", len(det.music_refs) == 1)
    check("识别 B 站链接", len(det.bili_urls) == 1)
    check("takes_time 判定", det.takes_time is True)


def main() -> None:
    print("== 多模态理解增强 · 离线自测 ==")
    test_config()
    test_logger()
    test_prompt()
    test_ncm_links()
    test_spectrum()
    test_env_manager()
    test_pipeline_detect()
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()