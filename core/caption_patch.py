"""图片理解增强：包装 AstrBot 的图片转述流程，注入用户当前消息。

原理（已对照 AstrBot v4.27.4 源码验证）：
- astrbot/core/astr_main_agent.py：
    _ensure_img_caption(event, req, cfg, plugin_context, image_caption_provider)
  在聊天主模型不支持图片输入时，把图片转述结果写入 req.extra_user_content_parts；
  转述提示词取自 cfg["image_caption_prompt"]。
- 本模块不改动 AstrBot 源码文件，仅在运行时包装 _ensure_img_caption：
  调用原函数前，基于用户当前消息组装增强后的转述提示词（模板可配置），
  使转述模型同时看到「用户到底想问什么」。

说明：
- 仅注入用户当前消息原文（不处理引用消息场景）；
- 支持运行时卸载还原（插件重载/停用时不残留）。
"""

from __future__ import annotations

import sys
from typing import Any

DEFAULT_TEMPLATE = (
    "现在需要你作为图片转述模型给另一个非多模态模型描述这个图片，"
    "该模型现在需要回复QQ群里的人的消息“{user_message}”，"
    "请你结合这个消息，分析用户需求。先整体描述，再结合用户需求重点描述所需细节。"
)

_original_ensure: Any = None
_wrapper: Any = None
_installed = False


def _target_module():
    try:
        import astrbot.core.astr_main_agent as mod  # type: ignore
        return mod
    except Exception:
        return None


def _iter_astrbot_modules():
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        if name.startswith("astrbot"):
            yield module


def _replace_refs(target: Any, replacement: Any) -> int:
    """把所有 astrbot 模块中指向 target 的引用替换为 replacement。"""
    count = 0
    for module in _iter_astrbot_modules():
        try:
            items = list(vars(module).items())
        except Exception:
            continue
        for attr, value in items:
            if value is target:
                try:
                    setattr(module, attr, replacement)
                    count += 1
                except Exception:
                    pass
    return count


def _build_prompt(template: str, user_message: str) -> str:
    tpl = (template or "").strip() or DEFAULT_TEMPLATE
    if "{user_message}" in tpl:
        return tpl.replace("{user_message}", user_message)
    return f"{tpl}\n用户当前消息：{user_message}"


def install(plugin: Any) -> bool:
    """安装包装（幂等）。

    plugin 需提供：
    - conf: core.config.PluginConfig
    - log: core.logger.PluginLogger
    - resolve_flags_for_event(event) -> dict（含 image_enabled 等布尔开关）
    """
    global _original_ensure, _wrapper, _installed
    if _installed:
        return True
    mod = _target_module()
    if mod is None:
        plugin.log.warn("图片增强：未找到 AstrBot 主对话模块，跳过安装。")
        return False
    original = getattr(mod, "_ensure_img_caption", None)
    if not callable(original):
        plugin.log.warn("图片增强：未找到 _ensure_img_caption 入口，跳过安装。")
        return False

    async def wrapper(event, req, cfg, plugin_context, image_caption_provider, *args, **kwargs):
        new_cfg = cfg
        try:
            flags = plugin.resolve_flags_for_event(event)
            if flags.get("image_enabled"):
                user_message = (getattr(event, "message_str", "") or "").strip()
                if user_message:
                    template = plugin.conf.str("image_prompt") or DEFAULT_TEMPLATE
                    prompt = _build_prompt(template, user_message)
                    new_cfg = dict(cfg or {})
                    new_cfg["image_caption_prompt"] = prompt
        except Exception as exc:  # 增强失败不影响原始转述
            try:
                plugin.log.warn(f"图片增强：构建注入提示词失败：{exc}")
            except Exception:
                pass
        return await original(event, req, new_cfg, plugin_context,
                              image_caption_provider, *args, **kwargs)

    try:
        wrapper.__name__ = getattr(original, "__name__", "wrapper")
        wrapper.__qualname__ = getattr(original, "__qualname__", wrapper.__name__)
    except Exception:
        pass

    patched = _replace_refs(original, wrapper)
    try:
        setattr(mod, "_ensure_img_caption", wrapper)
    except Exception:
        pass

    _original_ensure = original
    _wrapper = wrapper
    _installed = True
    plugin.log.info(f"图片增强：已安装转述注入补丁（{patched} 处引用）。")
    return True


def uninstall(plugin: Any | None = None) -> None:
    """卸载包装，还原原始函数引用。"""
    global _installed, _original_ensure, _wrapper
    if not _installed or _original_ensure is None or _wrapper is None:
        return
    _replace_refs(_wrapper, _original_ensure)
    mod = _target_module()
    if mod is not None:
        try:
            if getattr(mod, "_ensure_img_caption", None) is _wrapper:
                setattr(mod, "_ensure_img_caption", _original_ensure)
        except Exception:
            pass
    _installed = False
    _original_ensure = None
    _wrapper = None
    if plugin is not None:
        try:
            plugin.log.info("图片增强：已卸载转述注入补丁。")
        except Exception:
            pass