"""插件日志：内存环形缓冲 + 文件追加 + 订阅（用于页面 SSE 实时日志流）。"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any


class PluginLogger:
    """轻量日志器。可在事件循环内调用，也可在普通协程/线程里调用 log()。"""

    def __init__(self, name: str = "multimodal_enhance", max_lines: int = 2000):
        self.name = name
        self._buf: list[dict[str, Any]] = []
        self._max_lines = max(200, int(max_lines or 2000))
        self._subs: list[asyncio.Queue] = []
        self._enabled = True
        self._file_path: str | None = None
        self._seq = 0

    # ---------- 配置 ----------

    def configure(self, enabled: bool, file_path: str | None, max_lines: int = 2000) -> None:
        self._enabled = bool(enabled)
        self._max_lines = max(200, int(max_lines or 2000))
        if file_path:
            try:
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
            except OSError:
                pass
        self._file_path = file_path or None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def file_path(self) -> str | None:
        return self._file_path

    # ---------- 写入 ----------

    def _append(self, level: str, msg: str) -> dict[str, Any]:
        self._seq += 1
        entry = {
            "seq": self._seq,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "msg": str(msg),
        }
        self._buf.append(entry)
        if len(self._buf) > self._max_lines:
            del self._buf[: len(self._buf) - self._max_lines]
        if self._enabled and self._file_path:
            try:
                with open(self._file_path, "a", encoding="utf-8") as fp:
                    fp.write(f"[{entry['time']}][{level}] {entry['msg']}\n")
            except OSError:
                pass
        for queue in list(self._subs):
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:
                pass
            except Exception:
                pass
        return entry

    def info(self, msg: str) -> dict[str, Any]:
        return self._append("INFO", msg)

    def warn(self, msg: str) -> dict[str, Any]:
        return self._append("WARN", msg)

    def error(self, msg: str) -> dict[str, Any]:
        return self._append("ERROR", msg)

    # ---------- 读取 ----------

    def tail(self, n: int = 200) -> list[dict[str, Any]]:
        return list(self._buf[-max(1, int(n)):])

    def recent_errors(self, n: int = 5) -> list[dict[str, Any]]:
        errors = [e for e in self._buf if e["level"] in ("WARN", "ERROR")]
        return errors[-max(1, int(n)):]

    def export_text(self, n: int = 2000) -> str:
        return "\n".join(
            f"[{e['time']}][{e['level']}] {e['msg']}" for e in self.tail(n)
        )

    # ---------- 订阅（SSE） ----------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subs.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        try:
            self._subs.remove(queue)
        except ValueError:
            pass
