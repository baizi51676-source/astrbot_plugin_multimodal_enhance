"""原生语音转写接管：插件启用时，截断 AstrBot「PreProcessStage」中的原生「语音转文字」。

原理：包装 `PreProcessStage.process`，在事件进入预处理前，将该实例的 `stt_settings`
替换为关闭状态（仅替换实例属性，不修改共享配置对象），从而跳过原生 STT；
插件卸载或关闭「接管语音转写」时自动恢复。

Gating 依据（按事件解析，支持多 bot 覆写）：
- 插件全局启用；
- 该 bot / 会话的有效设置中 `cut_native_stt`（默认 True）与 `audio_file_enabled`（默认 True）；
- 该 bot 的音频功能开关（`audio_enabled`）。
"""

from __future__ import annotations

import asyncio
import gc

from .config import _to_bool


class NativeSTTControl:
    """原生 STT 接管控制器（每个插件实例持有 1 个）。"""

    def __init__(self) -> None:
        self.plugin = None

    # ---------- 决策 ----------
    def should_cut(self, event) -> bool:
        p = self.plugin
        if p is None:
            return False
        conf = getattr(p, "conf", None)
        if conf is None:
            return False
        try:
            if not conf.is_globally_enabled():
                return False
        except Exception:
            return False
        try:
            settings = p.effective_settings_for(event)
        except Exception:
            settings = {}
        if not _to_bool(settings.get("cut_native_stt"), True):
            return False
        if not _to_bool(settings.get("audio_file_enabled"), True):
            return False
        try:
            flags = p.resolve_flags_for_event(event)
        except Exception:
            flags = {}
        return bool(flags.get("audio_enabled"))

    # ---------- 应用 / 恢复 ----------
    def sync_stage(self, stage, event) -> None:
        """进入预处理前设置本事件所需状态（只负责「设」，不负责「还原」）。"""
        stt = getattr(stage, "stt_settings", None)
        is_cut = isinstance(stt, dict) and bool(stt.get("_mme_cut"))
        desired = self.should_cut(event)
        if desired and not is_cut:
            if isinstance(stt, dict) and stt.get("enable"):
                stage._mme_stt_orig = stt
                replacement = {"enable": False, "_mme_cut": True}
                if isinstance(stt.get("provider_id"), str):
                    replacement["provider_id"] = stt["provider_id"]
                stage.stt_settings = replacement
        elif not desired and is_cut:
            self.restore_stage(stage)

    def restore_stage(self, stage) -> None:
        """处理完立即还原，避免把本事件的决策留给下一个事件。"""
        stt = getattr(stage, "stt_settings", None)
        if not (isinstance(stt, dict) and stt.get("_mme_cut")):
            # 已被别的路径还原过，清掉残留标记即可
            try:
                delattr(stage, "_mme_stt_orig")
            except Exception:
                pass
            return
        orig = getattr(stage, "_mme_stt_orig", None)
        if isinstance(orig, dict):
            stage.stt_settings = orig
        else:
            stt["enable"] = True
        try:
            delattr(stage, "_mme_stt_orig")
        except Exception:
            pass

    def restore_all(self) -> None:
        """恢复所有曾被接管的阶段实例。"""
        cls = self._stage_cls()
        if cls is None:
            return
        for obj in list(gc.get_objects()):
            try:
                if not isinstance(obj, cls):
                    continue
                stt = getattr(obj, "stt_settings", None)
                if not (isinstance(stt, dict) and stt.get("_mme_cut")):
                    continue
                orig = getattr(obj, "_mme_stt_orig", None)
                if isinstance(orig, dict):
                    obj.stt_settings = orig
                else:
                    stt["enable"] = True
                try:
                    delattr(obj, "_mme_stt_orig")
                except Exception:
                    pass
            except Exception:
                continue

    # ---------- 安装 / 卸载 ----------
    @staticmethod
    def _stage_cls():
        try:
            import astrbot.core.pipeline.preprocess_stage.stage as pp_mod  # type: ignore
            return pp_mod.PreProcessStage
        except Exception:
            return None

    def install(self, plugin) -> bool:
        cls = self._stage_cls()
        if cls is None:
            return False
        self.plugin = plugin
        if getattr(cls, "_mme_orig_process", None) is None:
            cls._mme_orig_process = cls.process

            async def patched(self, event):
                ctl = getattr(type(self), "_mme_control", None)
                if ctl is not None:
                    lock = ctl._stage_lock()
                    if lock is None:
                        try:
                            ctl.sync_stage(self, event)
                            return await cls._mme_orig_process(self, event)
                        finally:
                            ctl.restore_stage(self)
                    # 临界区：改设置 -> 原预处理 -> 立即还原，避免并发事件互相污染
                    async with lock:
                        try:
                            ctl.sync_stage(self, event)
                            return await cls._mme_orig_process(self, event)
                        finally:
                            ctl.restore_stage(self)
                return await cls._mme_orig_process(self, event)

            patched.__name__ = "process"
            patched.__doc__ = "MME: 原生 STT 接管（按事件门控）"
            cls.process = patched
        cls._mme_control = self
        self._drop_stale_lock(cls)
        return True

    def _stage_lock(self) -> asyncio.Lock | None:
        """每 stage 一把锁；无事件循环时返回 None（由调用方退化为无锁路径）。"""
        cls = self._stage_cls()
        if cls is None:
            return None
        loop = getattr(cls, "_mme_stt_lock_loop", None)
        lock = getattr(cls, "_mme_stt_lock", None)
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            return None
        if lock is None or loop is None or loop is not current:
            lock = asyncio.Lock()
            cls._mme_stt_lock = lock
            cls._mme_stt_lock_loop = current
        return lock

    @staticmethod
    def _drop_stale_lock(cls) -> None:
        """插件重载后事件循环可能更换，旧锁作废重建。"""
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            return
        if getattr(cls, "_mme_stt_lock_loop", None) is not current:
            cls._mme_stt_lock = None
            cls._mme_stt_lock_loop = None

    def uninstall(self) -> None:
        cls = self._stage_cls()
        if cls is not None:
            try:
                cls._mme_control = None
            except Exception:
                pass
            orig = getattr(cls, "_mme_orig_process", None)
            if orig is not None:
                try:
                    cls.process = orig
                except Exception:
                    pass
                try:
                    delattr(cls, "_mme_orig_process")
                except Exception:
                    pass
        self.restore_all()
        self.plugin = None
