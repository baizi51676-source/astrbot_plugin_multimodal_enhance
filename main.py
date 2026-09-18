"""astrbot_plugin_multimodal_enhance - 多模态理解增强：插件入口。

功能总览：
- 图片理解增强（包装 AstrBot 图片转述流程，注入用户当前消息）；
- 音频理解（语音/音频转文本；音乐链接元数据+歌词+频谱数据）；
- 视频理解（抽帧逐帧描述+音轨解析；B站链接下载解析）；
- 插件页面：总览 / 配置 / 插件日志（含 SSE 实时流与导出）/ 环境配置。

许可证：MIT
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .core import caption_patch, env_manager
from .core.config import DEFAULTS, PluginConfig, _to_bool, _to_int
from .core.logger import PluginLogger
from .core.pipeline import MediaPipeline

try:
    from astrbot.api.web import (
        error_response,
        file_response,
        json_response,
        request,
        stream_response,
    )
    _WEB_AVAILABLE = True
except Exception:  # pragma: no cover - 旧版 AstrBot 无插件页面 API
    _WEB_AVAILABLE = False

PLUGIN_NAME = "astrbot_plugin_multimodal_enhance"
PLUGIN_VERSION = "v0.1.1"

# 插件页面配置表（也用于保存时的类型校验）
_CONFIG_META = [
    {"key": "enabled", "group": "基础", "label": "总开关", "type": "bool",
     "hint": "关闭后本插件不做任何解析与注入。"},
    {"key": "notice_enabled", "group": "基础", "label": "分析中提示", "type": "bool",
     "hint": "解析耗时较长时，先向用户发送「正在分析中」提示。"},
    {"key": "notice_provider", "group": "基础", "label": "提示生成模型", "type": "provider",
     "hint": "留空 = 使用当前会话主模型。"},
    {"key": "image_enabled", "group": "图片理解增强", "label": "图片理解增强", "type": "bool",
     "hint": "转述图片时注入用户当前消息。"},
    {"key": "image_prompt", "group": "图片理解增强", "label": "转述注入模板", "type": "textarea",
     "hint": "占位符 {user_message} 会被替换为用户当前消息原文。"},
    {"key": "audio_enabled", "group": "音频理解", "label": "音频理解", "type": "bool"},
    {"key": "audio_file_enabled", "group": "音频理解", "label": "语音/音频文件转文本",
     "type": "bool", "hint": "未配置「语音转文本」模型时自动跳过。"},
    {"key": "audio_links_enabled", "group": "音频理解", "label": "音乐链接解析",
     "type": "bool", "hint": "目前支持网易云音乐链接。"},
    {"key": "audio_ncm_cookie", "group": "音频理解", "label": "网易云 Cookie（可选）",
     "type": "string"},
    {"key": "audio_deep_mode", "group": "音频理解", "label": "声纹包式深度分析",
     "type": "select", "options": [
         {"value": "auto", "label": "自动（检测到 librosa 时启用）"},
         {"value": "on", "label": "强制启用"},
         {"value": "off", "label": "关闭"},
     ]},
    {"key": "audio_spectrum_enabled", "group": "音频理解", "label": "频谱数据（文本）",
     "type": "bool"},
    {"key": "audio_spectrum_seg", "group": "音频理解", "label": "频谱分段秒数", "type": "int"},
    {"key": "video_enabled", "group": "视频理解", "label": "视频理解", "type": "bool",
     "hint": "聊天主模型本身支持视频输入时建议保持关闭。"},
    {"key": "video_frames", "group": "视频理解", "label": "抽帧数量", "type": "int"},
    {"key": "video_frame_width", "group": "视频理解", "label": "抽帧宽度上限（像素）",
     "type": "int"},
    {"key": "video_max_minutes", "group": "视频理解", "label": "视频最大时长（分钟）",
     "type": "int"},
    {"key": "video_max_size_mb", "group": "视频理解", "label": "视频最大体积（MB）",
     "type": "int"},
    {"key": "video_bili_enabled", "group": "视频理解", "label": "B站链接解析", "type": "bool"},
    {"key": "video_caption_provider", "group": "视频理解", "label": "帧描述（图转文）模型",
     "type": "provider", "hint": "留空 = 跟随 AstrBot 的「图片描述」设置。"},
    {"key": "env_ffmpeg_path", "group": "环境", "label": "FFmpeg 路径", "type": "string",
     "hint": "留空 = 自动查找 PATH。"},
    {"key": "env_ytdlp_path", "group": "环境", "label": "yt-dlp 路径", "type": "string",
     "hint": "留空 = 自动查找 PATH。"},
    {"key": "env_pip_index", "group": "环境", "label": "pip 镜像源", "type": "string"},
    {"key": "env_work_dir", "group": "环境", "label": "工作目录", "type": "string",
     "hint": "留空 = 插件数据目录下 tmp。"},
    {"key": "env_keep_temp", "group": "环境", "label": "保留临时文件", "type": "bool"},
    {"key": "log_enabled", "group": "日志", "label": "记录插件日志", "type": "bool"},
    {"key": "log_max_lines", "group": "日志", "label": "日志缓冲行数", "type": "int"},
]
# 系统级配置项（仅全局页展示，不参与 bot 覆写）
for _item in _CONFIG_META:
    if _item["key"].startswith(("env_", "log_")):
        _item["scope"] = "system"

_TYPE_BY_KEY = {item["key"]: item["type"] for item in _CONFIG_META}


class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        if config is not None:
            self.config = config
        else:
            try:
                self.config = AstrBotConfig({})
            except Exception:
                self.config = {}
        self.conf = PluginConfig(dict(self.config))
        try:
            self.data_dir = str(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:
            self.data_dir = os.path.join(os.getcwd(), "data", "plugin_data", PLUGIN_NAME)
        os.makedirs(self.data_dir, exist_ok=True)
        self.log = PluginLogger(PLUGIN_NAME, max_lines=self.conf.int("log_max_lines", 2000))
        self.log.configure(
            self.conf.bool("log_enabled", True),
            os.path.join(self.data_dir, "logs", "plugin.log"),
            self.conf.int("log_max_lines", 2000),
        )
        self.workdir_base = env_manager.resolve_work_dir(self.conf, self.data_dir)
        os.makedirs(self.workdir_base, exist_ok=True)
        self.pipeline = MediaPipeline(self)
        self._register_web_apis()
        try:
            caption_patch.install(self)
        except Exception as exc:
            self.log.warn(f"图片增强补丁安装异常：{exc}")
        self.log.info(f"插件初始化完成（{PLUGIN_VERSION}）。工作目录：{self.workdir_base}")

    # ---------------- 辅助 ----------------

    def resolve_flags_for_event(self, event) -> dict:
        keys = ("image_enabled", "audio_enabled", "video_enabled", "notice_enabled")
        try:
            if not self.conf.is_globally_enabled():
                return {k: False for k in keys}
            return self.conf.resolve_feature_flags(
                self._platform_id(event), self._self_id(event), self._umo(event))
        except Exception:
            return {k: False for k in keys}

    @staticmethod
    def _umo(event) -> str:
        return str(getattr(event, "unified_msg_origin", "") or "")

    @staticmethod
    def _platform_id(event) -> str:
        for fn in ("get_platform_id", "get_platform_name"):
            func = getattr(event, fn, None)
            if callable(func):
                try:
                    value = func()
                    if value:
                        return str(value)
                except Exception:
                    pass
        return ""

    @staticmethod
    def _self_id(event) -> str:
        func = getattr(event, "get_self_id", None)
        if callable(func):
            try:
                value = func()
                if value:
                    return str(value)
            except Exception:
                pass
        try:
            return str(event.message_obj.self_id or "")
        except Exception:
            return ""

    # ---------------- 钩子 ----------------

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        try:
            caption_patch.install(self)
        except Exception:
            pass
        self.log.info(
            "AstrBot 已加载。图片增强补丁：" + ("已安装" if caption_patch.installed() else "未安装"))

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_message(self, event: AstrMessageEvent):
        await self.pipeline.on_message(event)

    @filter.on_waiting_llm_request()
    async def on_waiting_llm_request(self, event: AstrMessageEvent):
        await self.pipeline.finish_pending(event)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req):
        try:
            self.pipeline.inject(event, req)
        except Exception as exc:
            self.log.warn(f"注入异常：{exc}")

    async def terminate(self):
        try:
            caption_patch.uninstall(self)
        except Exception:
            pass
        self.log.info("插件已停止。")

    # ---------------- 页面 API ----------------

    def _register_web_apis(self) -> None:
        if not _WEB_AVAILABLE:
            self.log.warn("当前 AstrBot 版本缺少插件页面 API，页面功能不可用。")
            return
        register = getattr(self.context, "register_web_api", None)
        if not callable(register):
            self.log.warn("context.register_web_api 不可用，页面功能不可用。")
            return
        routes = [
            ("state", "GET", self._api_state, "总览状态"),
            ("config", "GET", self._api_config_get, "读取配置"),
            ("config", "POST", self._api_config_save, "保存配置"),
            ("bots/add", "POST", self._api_bots_add, "新增 Bot 配置"),
            ("bots/save", "POST", self._api_bots_save, "保存 Bot 配置"),
            ("bots/delete", "POST", self._api_bots_delete, "删除 Bot 配置"),
            ("providers", "GET", self._api_providers, "可选模型列表"),
            ("logs", "GET", self._api_logs, "读取日志"),
            ("logs/stream", "GET", self._api_logs_stream, "实时日志流"),
            ("logs/export", "GET", self._api_logs_export, "导出日志"),
            ("env/check", "GET", self._api_env_check, "环境检测"),
            ("env/install", "POST", self._api_env_install, "安装可选依赖"),
            ("env/install/status", "GET", self._api_env_install_status, "安装状态"),
            ("env/clean", "POST", self._api_env_clean, "清理临时文件"),
        ]
        try:
            for route, method, handler, desc in routes:
                register(f"/{PLUGIN_NAME}/{route}", handler, [method], desc)
            self.log.info("插件页面 API 已注册。")
        except Exception as exc:
            self.log.warn(f"注册页面 API 失败：{exc}")

    def _public_config(self) -> dict:
        return {item["key"]: self.conf.get(item["key"]) for item in _CONFIG_META}

    def caption_provider_id(self) -> str:
        """AstrBot 全局「图片描述」模型 ID（视频帧描述等复用）。"""
        try:
            cfg = self.context.get_config()
            settings = cfg.get("provider_settings", {}) if hasattr(cfg, "get") else {}
            return str(settings.get("default_image_caption_provider_id") or "")
        except Exception:
            return ""

    def effective_settings_for(self, event) -> dict:
        """按 bot 解析最终生效配置（全局默认 + 覆写）。"""
        try:
            return self.conf.effective_config_for(
                self._platform_id(event), self._self_id(event), self._umo(event))
        except Exception:
            return {key: self.conf.get(key) for key in DEFAULTS.keys()}

    async def _api_state(self):
        stt_configured = False
        try:
            stt_configured = (await self.context.get_using_stt_provider_async()) is not None
        except Exception:
            pass
        from .core.media import audio_analyzer
        state = {
            "version": PLUGIN_VERSION,
            "enabled": self.conf.is_globally_enabled(),
            "features": {
                "image": self.conf.bool("image_enabled", True),
                "audio": self.conf.bool("audio_enabled", True),
                "video": self.conf.bool("video_enabled", False),
                "notice": self.conf.bool("notice_enabled", True),
            },
            "caption_provider": self.caption_provider_id(),
            "stt_configured": stt_configured,
            "numpy": audio_analyzer.has_numpy(),
            "librosa": audio_analyzer.has_librosa(),
            "workdir": self.workdir_base,
            "caption_patch_installed": caption_patch.installed(),
            "pipeline": self.pipeline.stats(),
            "recent_errors": self.log.recent_errors(5),
            "bots": self._bots_overview(),
        }
        return json_response(state)

    def _bots_overview(self) -> list:
        result = []
        for bot in self.conf.iter_bots():
            effective = self.conf.effective_with_overrides(bot.get("overrides") or {})
            result.append({
                "index": bot.get("index"),
                "name": bot.get("name") or f"Bot {bot.get('index', 0) + 1}",
                "targets": bot.get("targets") or [],
                "overrides": bot.get("overrides") or {},
                "features": {
                    "image": _to_bool(effective.get("image_enabled"), True),
                    "audio": _to_bool(effective.get("audio_enabled"), True),
                    "video": _to_bool(effective.get("video_enabled"), False),
                    "notice": _to_bool(effective.get("notice_enabled"), True),
                },
            })
        return result

    async def _api_config_get(self):
        return json_response({"config": self._public_config(), "meta": _CONFIG_META,
                              "bots": self._bots_overview()})

    async def _api_config_save(self):
        payload = await request.json(default={})
        patch = payload.get("patch") if isinstance(payload, dict) else None
        if not isinstance(patch, dict) or not patch:
            return error_response("patch 无效", status_code=400)
        cleaned: dict = {}
        for key, value in patch.items():
            if key not in _TYPE_BY_KEY:
                continue
            kind = _TYPE_BY_KEY[key]
            if kind == "bool":
                cleaned[key] = _to_bool(value, False)
            elif kind == "int":
                cleaned[key] = _to_int(value, DEFAULTS.get(key, 0))
            elif kind == "json":
                if not isinstance(value, list):
                    return error_response(f"{key} 需要 JSON 列表", status_code=400)
                cleaned[key] = value
            else:
                cleaned[key] = "" if value is None else str(value)
        if not cleaned:
            return error_response("没有可保存的配置项", status_code=400)
        try:
            self.config.update(cleaned)
        except Exception:
            for key, value in cleaned.items():
                self.config[key] = value
        saved = False
        try:
            saver = getattr(self.config, "save_config_async", None)
            if callable(saver):
                await saver()
                saved = True
            else:
                self.config.save_config()
                saved = True
        except Exception as exc:
            self.log.warn(f"配置保存失败：{exc}")
        self.conf = PluginConfig(dict(self.config))
        self.log.configure(
            self.conf.bool("log_enabled", True),
            os.path.join(self.data_dir, "logs", "plugin.log"),
            self.conf.int("log_max_lines", 2000),
        )
        self.log.info("配置已更新" + ("并保存。" if saved else "（保存失败，仅本次生效）。"))
        return json_response({"saved": saved, "config": self._public_config()})

    async def _api_providers(self):
        providers = []
        try:
            for prov in self.context.get_all_providers():
                pid, model = "", ""
                try:
                    meta = prov.meta() if callable(getattr(prov, "meta", None)) else None
                    if meta is not None:
                        pid = str(getattr(meta, "id", "") or "")
                        model = str(getattr(meta, "model", "") or "")
                except Exception:
                    pass
                if not pid:
                    try:
                        pid = str((getattr(prov, "provider_config", {}) or {}).get("id", ""))
                    except Exception:
                        pid = ""
                if not pid:
                    continue
                label = f"{pid}（{model}）" if model else pid
                providers.append({"id": pid, "label": label})
        except Exception as exc:
            self.log.warn(f"获取模型列表失败：{exc}")
        return json_response({"providers": providers})

    async def _api_logs(self):
        try:
            n = request.query.get("n", 200, type=int)
            since = request.query.get("since", 0, type=int)
        except Exception:
            n, since = 200, 0
        entries = self.log.tail(n)
        if since:
            entries = [e for e in entries if int(e.get("seq", 0)) > since]
        last_seq = entries[-1]["seq"] if entries else since
        return json_response({"logs": entries, "last_seq": last_seq,
                              "enabled": self.log.enabled})

    async def _api_logs_stream(self):
        queue = self.log.subscribe()

        async def gen():
            try:
                while True:
                    try:
                        entry = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield "data: " + json.dumps(entry, ensure_ascii=False) + "\n\n"
            finally:
                self.log.unsubscribe(queue)

        return stream_response(gen())

    async def _api_logs_export(self):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        export_path = os.path.join(self.data_dir, "logs", f"export_{stamp}.txt")
        os.makedirs(os.path.dirname(export_path), exist_ok=True)
        try:
            with open(export_path, "w", encoding="utf-8") as fp:
                fp.write(self.log.export_text(4000))
        except OSError as exc:
            return error_response(f"导出失败：{exc}", status_code=500)
        return file_response(export_path, filename=f"multimodal_enhance_logs_{stamp}.txt",
                             content_type="text/plain; charset=utf-8")

    # ---------------- Bot 配置管理 ----------------

    def _bots_rules(self) -> list:
        rules = self.config.get("bots") if hasattr(self.config, "get") else None
        return rules if isinstance(rules, list) else []

    async def _persist_config(self) -> bool:
        saved = False
        try:
            saver = getattr(self.config, "save_config_async", None)
            if callable(saver):
                await saver()
                saved = True
            else:
                self.config.save_config()
                saved = True
        except Exception as exc:
            self.log.warn(f"配置保存失败：{exc}")
        self.conf = PluginConfig(dict(self.config))
        self.log.configure(
            self.conf.bool("log_enabled", True),
            os.path.join(self.data_dir, "logs", "plugin.log"),
            self.conf.int("log_max_lines", 2000),
        )
        return saved

    async def _api_bots_add(self):
        payload = await request.json(default={})
        name = str(payload.get("name") or "新Bot")
        targets = [str(t).strip() for t in (payload.get("bots") or []) if str(t).strip()]
        rules = self._bots_rules()
        rules.append({"name": name, "bots": targets, "overrides": "{}"})
        self.config["bots"] = rules
        saved = await self._persist_config()
        self.log.info(f"已新增 Bot 配置：{name}")
        return json_response({"saved": saved, "index": len(rules) - 1})

    async def _api_bots_save(self):
        payload = await request.json(default={})
        try:
            index = int(payload.get("index"))
        except (TypeError, ValueError):
            return error_response("index 无效", status_code=400)
        rules = self._bots_rules()
        if index < 0 or index >= len(rules):
            return error_response("index 越界", status_code=400)
        overrides = payload.get("overrides")
        cleaned_ov = {}
        if isinstance(overrides, dict):
            for key, value in overrides.items():
                if key in DEFAULTS:
                    cleaned_ov[key] = value
        rule = rules[index]
        rule["name"] = str(payload.get("name") or rule.get("name") or "")
        rule["bots"] = [str(t).strip() for t in (payload.get("bots") or []) if str(t).strip()]
        rule["overrides"] = json.dumps(cleaned_ov, ensure_ascii=False)
        for legacy in ("enable_image", "enable_audio", "enable_video", "enable_notice"):
            rule.pop(legacy, None)
        self.config["bots"] = rules
        saved = await self._persist_config()
        self.log.info(f"已保存 Bot 配置：{rule['name']}（覆写 {len(cleaned_ov)} 项）")
        return json_response({"saved": saved})

    async def _api_bots_delete(self):
        payload = await request.json(default={})
        try:
            index = int(payload.get("index"))
        except (TypeError, ValueError):
            return error_response("index 无效", status_code=400)
        rules = self._bots_rules()
        if index < 0 or index >= len(rules):
            return error_response("index 越界", status_code=400)
        removed = rules.pop(index)
        self.config["bots"] = rules
        saved = await self._persist_config()
        self.log.info(f"已删除 Bot 配置：{removed.get('name')}")
        return json_response({"saved": saved})

    async def _api_env_check(self):
        data = await env_manager.check_environment(self.conf, self.data_dir)
        data["disk"]["free_human"] = env_manager.format_bytes(data["disk"].get("free"))
        data["disk"]["total_human"] = env_manager.format_bytes(data["disk"].get("total"))
        return json_response(data)

    async def _api_env_install(self):
        payload = await request.json(default={})
        packages = payload.get("packages") if isinstance(payload, dict) else None
        if not isinstance(packages, list) or not packages:
            packages = list(env_manager.ALL_PACKAGES)
        index = self.conf.str("env_pip_index")
        ok, message = env_manager.start_install([str(p) for p in packages], index)
        return json_response({"started": ok, "message": message})

    async def _api_env_install_status(self):
        state = env_manager.get_install_state()
        if state.get("running"):
            state["output"] = str(state.get("output") or "")[-4000:]
        return json_response(state)

    async def _api_env_clean(self):
        result = env_manager.clean_temp(self.workdir_base)
        result["removed_human"] = env_manager.format_bytes(result.get("removed_bytes"))
        self.log.info(f"已清理临时文件：{result['removed_files']} 个（{result['removed_human']}）")
        return json_response(result)