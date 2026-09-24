"""配置读取与 per-bot 覆写解析（v2：overrides 模型）。

- 全局配置：所有键（含系统级 env_/log_）；
- 每个 bot 规则：{name, bots, overrides}，overrides 可为 JSON 字符串或 dict，
  仅覆盖被显式设置的键，其余沿用全局配置；
- 兼容旧版 enable_image/enable_audio/enable_video/enable_notice 字段。
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_IMAGE_PROMPT = (
    "现在需要你作为图片转述模型给另一个非多模态模型描述这个图片，"
    "该模型现在需要回复QQ群里的人的消息“{user_message}”，"
    "请你结合这个消息，分析用户需求。先整体描述，再结合用户需求重点描述所需细节。"
)

DEFAULT_ENV_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"

DEFAULT_NOTICE_PROMPT = (
    "视频/音频（视具体情况而定）正在解析中，需耗费时间较多，"
    "请你发一条消息表示你正在理解其内容"
)

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "notice_enabled": True,
    "notice_provider": "",
    "notice_prompt": DEFAULT_NOTICE_PROMPT,
    "cut_native_stt": True,
    "image_enabled": True,
    "image_prompt": DEFAULT_IMAGE_PROMPT,
    "audio_enabled": True,
    "audio_file_enabled": True,
    "audio_links_enabled": True,
    "audio_ncm_cookie": "",
    "audio_deep_mode": "auto",
    "audio_spectrum_enabled": True,
    "audio_spectrum_seg": 5,
    "audio_stt_chunk_mb": 7,
    "video_enabled": False,
    "video_frames": 6,
    "video_frame_width": 640,
    "video_max_minutes": 10,
    "video_max_size_mb": 100,
    "video_bili_enabled": True,
    "video_caption_provider": "",
    "max_items_per_type": 3,
    "audio_spectrum_always": False,
    "audio_stt_max_chunks": 12,
    "audio_deep_max_seconds": 300,
    "video_caption_concurrency": 3,
    "model_attach_audio": True,
    "model_attach_frames": True,
    "attach_when_unset": False,
    "attach_audio_max_mb": 20,
    "env_ffmpeg_path": "",
    "env_ytdlp_path": "",
    "env_pip_index": DEFAULT_ENV_PIP_INDEX,
    "env_work_dir": "",
    "env_keep_temp": False,
    "log_enabled": True,
    "log_max_lines": 2000,
    "bots": [],
}

# per-bot 覆写字段映射：全局配置键 -> 旧版规则项键（兼容读取）
_LEGACY_KEYS = {
    "image_enabled": "enable_image",
    "audio_enabled": "enable_audio",
    "video_enabled": "enable_video",
    "notice_enabled": "enable_notice",
}

_FEATURE_KEYS = ("image_enabled", "audio_enabled", "video_enabled", "notice_enabled")


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
    """插件配置的只读视图（含多 bot 覆写解析）。"""

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

    # ---------- bot 规则 ----------

    def _iter_bot_rules(self):
        bots = self.raw.get("bots")
        if isinstance(bots, list):
            for item in bots:
                if isinstance(item, dict):
                    yield item
        elif isinstance(bots, dict):
            for group in bots.values():
                if isinstance(group, list):
                    for item in group:
                        if isinstance(item, dict):
                            yield item

    @staticmethod
    def _overrides_of(rule: dict[str, Any] | None) -> dict[str, Any]:
        """解析规则中的覆写字典（兼容 JSON 字符串 / dict / 旧版 enable_* 字段）。"""
        if not rule:
            return {}
        raw = rule.get("overrides")
        overrides: dict[str, Any] = {}
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    overrides = dict(parsed)
            except (ValueError, TypeError):
                overrides = {}
        elif isinstance(raw, dict):
            overrides = dict(raw)
        for dst_key, legacy_key in _LEGACY_KEYS.items():
            if legacy_key in rule and dst_key not in overrides:
                overrides[dst_key] = rule[legacy_key]
        return overrides

    def iter_bots(self) -> list[dict[str, Any]]:
        """列出所有 bot 规则（供页面分页展示）。"""
        result: list[dict[str, Any]] = []
        for index, rule in enumerate(self._iter_bot_rules()):
            targets = [str(t).strip() for t in (rule.get("bots") or []) if str(t).strip()]
            result.append({
                "index": index,
                "name": str(rule.get("name") or ""),
                "targets": targets,
                "overrides": self._overrides_of(rule),
            })
        return result

    def match_bot_rule(self, platform_id: str = "", self_id: str = "",
                       umo: str | None = None) -> dict[str, Any] | None:
        """按 平台实例 ID / QQ 号 / UMO 匹配第一条 bot 规则。"""
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

    # ---------- 生效配置 ----------

    def effective_with_overrides(self, overrides: dict[str, Any] | None) -> dict[str, Any]:
        """全局默认值 + 覆写项 = 生效配置。"""
        effective = {key: self.get(key) for key in DEFAULTS.keys()}
        if overrides:
            for key, value in overrides.items():
                if key in DEFAULTS:
                    effective[key] = value
        return effective

    def effective_config_for(self, platform_id: str = "", self_id: str = "",
                             umo: str | None = None) -> dict[str, Any]:
        rule = self.match_bot_rule(platform_id, self_id, umo)
        return self.effective_with_overrides(self._overrides_of(rule))

    def resolve_feature_flags(self, platform_id: str = "", self_id: str = "",
                              umo: str | None = None) -> dict[str, bool]:
        """某 bot 的最终功能开关（全局默认 + 覆写）。"""
        effective = self.effective_config_for(platform_id, self_id, umo)
        return {
            key: _to_bool(effective.get(key), bool(DEFAULTS.get(key, False)))
            for key in _FEATURE_KEYS
        }