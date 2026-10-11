"""Shared helpers for every tad-clip engine module: per-user home, config, tool lookup.

Standard library only — setup.py and doctor.py import this before the venv exists.
"""
import copy
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

DEFAULTS = {
    "version": 1,
    "speaker": {"name": "", "handle": ""},
    "brand": {"preset": "neutral", "font": "Prompt", "colors": {}},
    "subtitle": {"highlight": "#FF7F50", "size": 60, "margin_v": 340, "max_chars": 22},
    "output": {"preset": "1080p", "safe_zone": "tiktok"},
    "whisper": {"model": "large-v3", "device": "auto", "compute_type": "auto"},
    "library_dir": None,
    "glossary": {"fixes": [], "glue": [], "protect": [], "prompt_hint": ""},
    "cutout": {"engine": "auto"},
    "tools": {},
}

# Executable names per tool, tried on PATH when config.tools has no usable entry.
_WHICH = {
    "ffmpeg": ["ffmpeg"],
    "ffprobe": ["ffprobe"],
    "node": ["node"],
    "python": ["python3", "python"],
    "chrome": ["chrome-headless-shell", "google-chrome", "chromium", "msedge"],
    "yt-dlp": ["yt-dlp"],
    "uv": ["uv"],
}

_FIX_HINT = "พิมพ์ \"ตรวจเครื่อง tad-clip\" ให้ Claude ติดตั้งส่วนที่ขาดให้"


class KitError(Exception):
    """User-facing error. The message is Thai and says what to do next."""


def home() -> Path:
    p = Path(os.environ.get("TAD_CLIP_HOME") or Path.home() / ".tad-clip").expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def models_dir() -> Path:
    p = home() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = home() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def fonts_dir() -> Path:
    return KIT_ROOT / "assets" / "fonts"


def venv_python() -> Path:
    v = home() / "venv"
    return v / "Scripts" / "python.exe" if IS_WINDOWS else v / "bin" / "python"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def preset_colors(name: str) -> dict:
    f = KIT_ROOT / "presets" / f"{name}.json"
    if not f.exists():
        f = KIT_ROOT / "presets" / "neutral.json"
    return json.loads(f.read_text(encoding="utf-8"))["colors"]


def load_config() -> dict:
    f = home() / "config.json"
    user = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    cfg = _merge(DEFAULTS, user)
    # preset first, then any colours the user set by hand win
    cfg["brand"]["colors"] = {**preset_colors(cfg["brand"]["preset"]), **(user.get("brand", {}).get("colors") or {})}
    return cfg


def save_config(cfg: dict) -> None:
    f = home() / "config.json"
    f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tool(name: str) -> str:
    """Absolute path of an external tool. config.tools[name] wins when that file exists."""
    configured = (load_config().get("tools") or {}).get(name)
    if configured and Path(configured).exists():
        return str(configured)
    if name == "python" and venv_python().exists():
        return str(venv_python())
    local_bin = home() / "bin"
    for exe in _WHICH.get(name, [name]):
        for cand in ([local_bin / (exe + ".exe")] if IS_WINDOWS else []) + [local_bin / exe]:
            if cand.exists():
                return str(cand)
        found = shutil.which(exe)
        if found:
            return found
    raise KitError(f"ไม่พบโปรแกรม {name} ในเครื่อง — {_FIX_HINT}")


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    """subprocess.run with UTF-8 text; failures become a KitError carrying the last 20 stderr lines."""
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    kw.setdefault("stdin", subprocess.DEVNULL)   # ffmpeg on Windows otherwise waits on an inherited stdin pipe
    kw.setdefault("env", {**os.environ, "PYTHONIOENCODING": "utf-8"})   # child pythons write what we decode (utf-8)
    try:
        res = subprocess.run([str(c) for c in cmd], **kw)
    except FileNotFoundError as e:
        raise KitError(f"เรียกโปรแกรม {cmd[0]} ไม่ได้ — {_FIX_HINT}") from e
    if res.returncode != 0:
        tail = "\n".join((res.stderr or "").strip().splitlines()[-20:])
        raise KitError(f"คำสั่ง {Path(str(cmd[0])).name} ล้มเหลว (exit {res.returncode})\n{tail}")
    return res
