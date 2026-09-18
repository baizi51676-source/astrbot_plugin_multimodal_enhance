"""环境检测与依赖管理（供插件页面「环境配置」使用）。"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import sys
import time

from .media.ffmpeg_tools import find_tool, run_proc

OPTIONAL_PACKAGES = ["librosa", "scipy", "soundfile"]
BASE_PACKAGES = ["numpy"]

_install_state: dict = {
    "running": False,
    "started_at": None,
    "packages": [],
    "output": "",
    "ok": None,
}


def format_bytes(num) -> str:
    try:
        num = float(num)
    except Exception:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}TB"


def resolve_work_dir(conf, data_dir: str) -> str:
    wd = (conf.str("env_work_dir") or "").strip()
    return wd or os.path.join(data_dir, "tmp")


def module_status(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


async def _which_version(cmd: list[str], name: str, timeout: float = 15) -> dict:
    path = shutil.which(cmd[0])
    if not path:
        return {"name": name, "ok": False, "path": "", "version": ""}
    rc, out, err = await run_proc(cmd, timeout=timeout)
    text = (out or err).decode("utf-8", "ignore").strip().splitlines()
    return {"name": name, "ok": rc == 0, "path": path,
            "version": (text[0][:90] if text else "")}


async def check_environment(conf, data_dir: str) -> dict:
    ffmpeg = find_tool("ffmpeg", conf.str("env_ffmpeg_path")) or "ffmpeg"
    ffprobe = find_tool("ffprobe", "") or "ffprobe"
    ytdlp = find_tool("yt-dlp", conf.str("env_ytdlp_path")) or "yt-dlp"

    ffmpeg_info = await _which_version([ffmpeg, "-version"], "ffmpeg")
    ffprobe_info = await _which_version([ffprobe, "-version"], "ffprobe")
    ytdlp_info = await _which_version([ytdlp, "--version"], "yt-dlp")

    modules = {name: module_status(name) for name in (BASE_PACKAGES + OPTIONAL_PACKAGES)}
    workdir = resolve_work_dir(conf, data_dir)
    try:
        os.makedirs(workdir, exist_ok=True)
        usage = shutil.disk_usage(workdir)
        disk = {"total": usage.total, "free": usage.free}
    except Exception:
        disk = {"total": 0, "free": 0}
    return {
        "ffmpeg": ffmpeg_info,
        "ffprobe": ffprobe_info,
        "ytdlp": ytdlp_info,
        "modules": modules,
        "optional_packages": OPTIONAL_PACKAGES,
        "base_packages": BASE_PACKAGES,
        "python": sys.version.split()[0],
        "workdir": workdir,
        "disk": disk,
        "install": get_install_state(),
    }


def get_install_state() -> dict:
    return dict(_install_state)


async def _run_install(packages: list[str], index_url: str) -> None:
    _install_state.update({
        "running": True,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "packages": list(packages),
        "output": "安装中……",
        "ok": None,
    })
    cmd = [sys.executable, "-m", "pip", "install", "-U", *packages,
           "--disable-pip-version-check"]
    if index_url:
        cmd += ["-i", index_url]
    rc, out, err = await run_proc(cmd, timeout=1200)
    tail = (out + b"\n" + err).decode("utf-8", "ignore")[-8000:]
    _install_state.update({"running": False, "output": tail, "ok": rc == 0})


def start_install(packages: list[str], index_url: str = "") -> tuple[bool, str]:
    if _install_state.get("running"):
        return False, "已有安装任务进行中"
    pkgs = [p.strip() for p in packages if p and p.strip()]
    if not pkgs:
        return False, "未提供要安装的包"
    try:
        asyncio.get_event_loop().create_task(_run_install(pkgs, index_url))
    except RuntimeError:
        return False, "无事件循环，无法启动安装"
    return True, "安装已启动"


def clean_temp(workdir: str, keep_root: bool = True) -> dict:
    removed_files = 0
    removed_bytes = 0
    if not workdir or not os.path.isdir(workdir):
        return {"removed_files": 0, "removed_bytes": 0}
    for entry in os.listdir(workdir):
        path = os.path.join(workdir, entry)
        try:
            if os.path.isdir(path):
                for root, _dirs, files in os.walk(path):
                    for f in files:
                        try:
                            removed_bytes += os.path.getsize(os.path.join(root, f))
                            removed_files += 1
                        except OSError:
                            pass
                shutil.rmtree(path, ignore_errors=True)
            else:
                removed_bytes += os.path.getsize(path)
                removed_files += 1
                os.remove(path)
        except OSError:
            pass
    return {"removed_files": removed_files, "removed_bytes": removed_bytes}