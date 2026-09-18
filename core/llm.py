"""LLM 辅助：通过 AstrBot Provider 生成提示语、描述画面、文本处理。"""

from __future__ import annotations

import asyncio

DEFAULT_NOTICE = "正在解析中，请稍等……"


async def _get_provider(context, umo: str = "", provider_id: str = ""):
    if provider_id:
        try:
            prov = context.get_provider_by_id(provider_id)
            if prov is not None:
                return prov
        except Exception:
            pass
    try:
        return await context.get_using_provider_async(umo)
    except Exception:
        return None


async def text_chat(context, umo: str, prompt: str, provider_id: str = "",
                    image_urls=None, timeout: float = 90) -> str:
    """统一的 text_chat 调用入口，失败返回空串。"""
    prov = await _get_provider(context, umo, provider_id)
    if prov is None:
        return ""
    try:
        kwargs = {"prompt": prompt}
        if image_urls:
            kwargs["image_urls"] = list(image_urls)
        resp = await asyncio.wait_for(prov.text_chat(**kwargs), timeout=timeout)
        return (getattr(resp, "completion_text", "") or "").strip()
    except Exception:
        return ""


async def generate_notice(context, umo: str = "", provider_id: str = "") -> str:
    """让模型生成一句自然的「正在分析中」提示。"""
    prompt = (
        "群里有人发来了一条包含音频/视频/链接的消息，我正在后台解析它，可能需要几十秒。"
        "请用自然、口语化的一句中文告诉对方你正在分析中（不超过30字），只输出这句话本身。"
    )
    text = await text_chat(context, umo, prompt, provider_id, timeout=30)
    if text:
        text = text.strip().strip("“”\"'").splitlines()[0].strip()
        return text[:60] or DEFAULT_NOTICE
    return DEFAULT_NOTICE


async def describe_image(context, umo: str, image_path: str, provider_id: str = "",
                         instruction: str = "") -> str:
    """调用图转文模型描述一张本地图片。"""
    prompt = instruction or "请用一两句简洁的中文描述这张图片的画面内容（人物/场景/动作/文字）。"
    return await text_chat(context, umo, prompt, provider_id,
                           image_urls=[image_path], timeout=90)