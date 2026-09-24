"""模型能力检测：主模型是否支持音频 / 图片模态（用于「附件直传」）。

检测依据：AstrBot Provider 的 ``provider_config["modalities"]`` 列表
（例如 ["text", "image", "audio"]），与 AstrBot 内核
(astrbot/core/provider/modalities.py / astr_main_agent._provider_supports_modality)
使用同一字段。
"""

from __future__ import annotations


async def chat_modalities(context, umo: str, assume_when_unset: bool = False) -> list[str] | None:
    """获取当前会话主模型声明的 modalities 列表。

    返回 None 表示未声明 / 无法获取（此时默认不做附件直传，除非 assume_when_unset）。
    """
    try:
        provider = await context.get_using_provider_async(umo)
    except Exception:
        provider = None
    return provider_modalities(provider, assume_when_unset)


def provider_modalities(provider, assume_when_unset: bool = False) -> list[str] | None:
    """从 Provider 对象上读取 modalities（列表）；未声明时按需返回支持全集。"""
    mods = None
    if provider is not None:
        try:
            config = getattr(provider, "provider_config", None) or {}
            mods = config.get("modalities", None)
        except Exception:
            mods = None
    if isinstance(mods, list):
        values = [str(item).strip().lower() for item in mods if str(item).strip()]
        if values:
            return values
    if assume_when_unset:
        return ["text", "image", "audio"]
    return None


def attach_flags(modalities, *, audio_on: bool = True,
                 frames_on: bool = True) -> dict[str, bool]:
    """根据 modalities 计算附件直传开关。"""
    mods = {str(item).strip().lower() for item in (modalities or [])}
    return {
        "audio": bool(audio_on and "audio" in mods),
        "image": bool(frames_on and "image" in mods),
    }