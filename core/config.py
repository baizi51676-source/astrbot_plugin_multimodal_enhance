"""配置读取与 bot 级别覆写解析。

配置来源于 AstrBot 的插件配置（AstrBotConfig，本质为 dict）。
本模块不依赖任何 AstrBot 运行时对象，便于单元测试。
"""

from __future__ import annotations

from typing import Any

DEFAULT_IMAGE_PROMPT = (
    "现在需要你作为图片转述模型给另一个非多模态模型描述这个图片，"
    "该模型现在需要回复QQ群里的人的消息“{user_message}”，"
    "请你结合这个消息，分析用户需求。先整体描述，再结合用户需求重点描述所需细节。"
)

DEFAULT_ENV_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"

# 兜底默认值（与 _conf_schema.json 保持一致）
DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "notice_enabled": True,
    "notice_provider": "",
    "image_enabled": True,
    "image_prompt": DEFAULT_IMAGE_PROMPT,
    "audio_enabled": True,
    "audio_file_enabled": True,
    "audio_links_enabled": True,
    "audio_ncm_cookie": "",
    "audio_deep_mode": "auto",
    "audio_spectrum_enabled": True,
    "audio_spectrum_seg": 5,
    "video_enabled": False,
    "video_frames": 6,
    "video_frame_width": 640,
    "video_max_minutes": 10,
    "video_max_size_mb": 100,
    "video_bili_enabled": True,
    "video_caption_provider": "",
    "env_ffmpeg_path": "",
    "env_ytdlp_path": "",
    "env_pip_index": DEFAULT_ENV_PIP_INDEX,
    "env_work_dir": "",
    "env_keep_temp": False,
    "log_enabled": True,
    "log_max_lines": 2000,
    "bots": [],
}

# per-bot 覆写字段映射：全局配置键 -> 规则项键
_OVERRIDE_KEYS = {
    "image_enabled": "enable_image",
    "audio_enabled": "enable_audio",
    "video_enabled": "enable_video",
    "notice_enabled": "enable_notice",
}


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "开", "开启", "启用"}
    return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_str(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


class PluginConfig:
    """插件配置的只读视图（含多 bot 匹配）。"""

    def __init__(self, raw: dict[str, Any] | None):
        self.raw: dict[str, Any] = dict(raw or {})

    # ---------- 基础读取 ----------

    def get(self, key: str, default: Any = None) -> Any:
        value = self.raw.get(key, None)
        if value is None:
            value = DEFAULTS.get(key, default)
        return value

    def bool(self, key: str, default: bool = False) -> bool:
        return _to_bool(self.get(key), default)

    def int(self, key: str, default: int = 0) -> int:
        return _to_int(self.get(key), default)

    def str(self, key: str, default: str = "") -> str:
        return _to_str(self.get(key), default)

    def is_globally_enabled(self) -> bool:
        return self.bool("enabled", True)

    # ---------- 多 bot 规则 ----------

    def _iter_bot_rules(self):
        bots = self.raw.get("bots")
        if isinstance(bots, list):
            for item in bots:
                if isinstance(item, dict):
                    yield item
        elif isinstance(bots, dict):
            # 兼容 {"rule": [ ... ]} 形态
            for group in bots.values():
                if isinstance(group, list):
                    for item in group:
                        if isinstance(item, dict):
                            yield item

    def match_bot_rule(self, platform_id: str = "", self_id: str = "",
                       umo: str | None = None) -> dict[str, Any] | None:
        """按 平台实例 ID / QQ 号 / UMO 匹配第一条 bot 覆写规则。"""
        candidates = {str(platform_id or ""), str(self_id or "")}
        if umo:
            candidates.add(str(umo))
        candidates.discard("")
        if not candidates:
            return None
        for rule in self._iter_bot_rules():
            targets = [str(t).strip() for t in (rule.get("bots") or []) if str(t).strip()]
            if not targets:
                continue
            if any(t in candidates for t in targets):
                return rule
        return None

    def resolve_feature_flags(self, platform_id: str = "", self_id: str = "",
                              umo: str | None = None) -> dict[str, bool]:
        """解析某个 bot 的最终功能开关（全局默认 + 规则覆写）。"""
        flags: dict[str, bool] = {}
        for cfg_key in _OVERRIDE_KEYS:
            flags[cfg_key] = _to_bool(self.get(cfg_key), bool(DEFAULTS.get(cfg_key, False)))
        rule = self.match_bot_rule(platform_id, self_id, umo)
        if rule:
            for cfg_key, rule_key in _OVERRIDE_KEYS.items():
                if rule_key in rule and rule[rule_key] is not None:
                    flags[cfg_key] = _to_bool(rule[rule_key], flags[cfg_key])
        return flags
