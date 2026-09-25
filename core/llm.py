"""LLM 辅助：通过 AstrBot Provider 生成提示语、描述画面、文本处理。"""

from __future__ import annotations

import asyncio

from .config import DEFAULT_NOTICE_PROMPT

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
                    image_urls=None, system_prompt: str = "", timeout: float = 90) -> str:
    """统一的 text_chat 调用入口，失败返回空串。"""
    prov = await _get_provider(context, umo, provider_id)
    if prov is None:
        return ""
    try:
        kwargs = {"prompt": prompt}
        if image_urls:
            kwargs["image_urls"] = list(image_urls)
        if system_prompt:
            kwargs["system_prompt"] = system_prompt
        resp = await asyncio.wait_for(prov.text_chat(**kwargs), timeout=timeout)
        return (getattr(resp, "completion_text", "") or "").strip()
    except Exception:
        return ""


async def generate_notice(context, umo: str = "", provider_id: str = "",
                          prompt_template: str = "", system_prompt: str = "") -> str:
    """让模型以当前会话人格生成一条「正在分析中」提示。"""
    prompt = (prompt_template or "").strip() or DEFAULT_NOTICE_PROMPT
    text = await text_chat(context, umo, prompt, provider_id,
                           system_prompt=system_prompt, timeout=45)
    if text:
        text = text.strip().strip("“”\"'").splitlines()[0].strip()
        return text[:120] or DEFAULT_NOTICE
    return DEFAULT_NOTICE


async def describe_image(context, umo: str, image_path: str, provider_id: str = "",
                         instruction: str = "") -> str:
    """调用图转文模型描述一张本地图片。"""
    prompt = instruction or "请用一两句简洁的中文描述这张图片的画面内容（人物/场景/动作/文字）。"
    return await text_chat(context, umo, prompt, provider_id,
                           image_urls=[image_path], timeout=90)
