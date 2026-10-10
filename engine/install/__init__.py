"""Probes shared by setup.py and doctor.py — standard library only, Python 3.9+ (macOS's own python3 is 3.9).

Each probe answers one question about the machine and never raises for a missing tool.
"""
from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from engine import kit

NODE_MIN = 22
LIBASS_FILTERS = ("subtitles", "ass")

# faster-whisper short names → Hugging Face repo (faster_whisper/utils.py _MODELS)
WHISPER_REPOS = {
    "tiny.en": "Systran/faster-whisper-tiny.en", "tiny": "Systran/faster-whisper-tiny",
    "base.en": "Systran/faster-whisper-base.en", "base": "Systran/faster-whisper-base",
    "small.en": "Systran/faster-whisper-small.en", "small": "Systran/faster-whisper-small",
    "medium.en": "Systran/faster-whisper-medium.en", "medium": "Systran/faster-whisper-medium",
    "large-v1": "Systran/faster-whisper-large-v1", "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3", "large": "Systran/faster-whisper-large-v3",
    "distil-large-v2": "Systran/faster-distil-whisper-large-v2",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
# download size in MB, for the "this needs N GB" message before installing
WHISPER_MB = {"tiny": 75, "base": 145, "small": 485, "medium": 1530, "large-v3-turbo": 1620, "turbo": 1620,
              "large-v1": 3090, "large-v2": 3090, "large-v3": 3090, "large": 3090}
MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")


def utf8_stdio() -> None:
    """Thai + emoji output must not crash a Windows console/pipe that defaults to cp1252/cp874."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def os_name() -> str:
    return "mac" if kit.IS_MAC else "windows" if kit.IS_WINDOWS else "linux"


def _kill_tree(p: subprocess.Popen) -> None:
    if kit.IS_WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    else:
        try:
            os.killpg(p.pid, 9)
        except OSError:
            p.kill()
    p.wait()


def _out(cmd: list, timeout: float = 60) -> tuple:
    """(returncode, stdout+stderr) — never raises; a tool that cannot start returns (None, reason).

    Output goes to a temp file, not a pipe: a hung browser's helper processes keep a pipe open forever,
    and the whole process tree is killed on timeout.
    """
    with tempfile.TemporaryFile() as log:
        try:
            p = subprocess.Popen([str(c) for c in cmd], stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=not kit.IS_WINDOWS)
        except OSError as e:
            return None, str(e)
        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(p)
            return None, f"timeout {timeout:g}s"
        log.seek(0)
        return code, log.read().decode("utf-8", errors="replace")


def which(name: str, extra_dirs=()) -> str | None:
    """PATH lookup plus the places installers put things even when PATH was never updated."""
    dirs = [str(d) for d in extra_dirs] + [os.environ.get("PATH", "")]
    return shutil.which(name, path=os.pathsep.join(dirs))


def node_version(node: str) -> str | None:
    code, out = _out([node, "--version"], timeout=30)
    m = re.search(r"v(\d+)\.\d+\.\d+", out or "")
    return m.group(0) if code == 0 and m else None


def node_major(node: str) -> int:
    v = node_version(node)
    return int(v[1:].split(".")[0]) if v else 0


def ffmpeg_filters(ffmpeg: str) -> set:
    code, out = _out([ffmpeg, "-hide_banner", "-filters"], timeout=60)
    if code != 0:
        return set()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and "->" in parts[2]:
            names.add(parts[1])
    return names


def has_libass(ffmpeg: str) -> bool:
    return set(LIBASS_FILTERS) <= ffmpeg_filters(ffmpeg)


def model_dir(model: str, models: Path) -> Path | None:
    """Snapshot folder holding a complete faster-whisper model (Hugging Face cache layout), or a plain folder path."""
    direct = Path(model).expanduser()
    if direct.is_dir() and all((direct / f).exists() for f in MODEL_FILES):
        return direct
    repo = WHISPER_REPOS.get(model, model)
    snaps = Path(models) / ("models--" + repo.replace("/", "--")) / "snapshots"
    for snap in sorted(snaps.glob("*")) if snaps.is_dir() else []:
        if all((snap / f).exists() for f in MODEL_FILES):
            return snap
    return None


def chrome_candidates() -> list:
    """Browsers that can draw the cards, in the order the visual engine tries them (config first)."""
    found = []
    configured = (kit.load_config().get("tools") or {}).get("chrome")
    if configured:
        found.append(configured)
    shells = sorted((kit.home() / "chrome").glob("**/chrome-headless-shell" + (".exe" if kit.IS_WINDOWS else "")))
    if kit.IS_MAC:
        found += [str(p) for p in shells]
        found += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    elif kit.IS_WINDOWS:
        pf86, pf, local = (os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"))
        found += [os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"),
                  os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
                  os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
                  os.path.join(local, r"Google\Chrome\Application\chrome.exe")]
        found += [str(p) for p in shells]
    else:
        found += [str(p) for p in shells]
        found += [w for w in (shutil.which(n) for n in ("chrome-headless-shell", "google-chrome", "chromium")) if w]
    out = []
    for p in found:
        if p and Path(p).is_file() and p not in out:
            out.append(p)
    return out


def headless_flag(browser: str) -> str:
    return "--headless=new" if Path(browser).name.lower().startswith("msedge") else "--headless"


def chrome_works(browser: str, timeout: float = 20) -> tuple:
    """(ok, why) — start the browser headless, print about:blank's DOM, and exit within `timeout` s."""
    profile = tempfile.mkdtemp(prefix="tadclip-chrome-")
    try:
        code, out = _out([browser, headless_flag(browser), "--disable-gpu", "--no-first-run",
                          "--no-default-browser-check", f"--user-data-dir={profile}", "--dump-dom", "about:blank"],
                         timeout=timeout)
    finally:
        shutil.rmtree(profile, ignore_errors=True)  # Windows may still hold a lock for a moment — harmless
    if code is None and out.startswith("timeout"):
        return False, f"เปิดแล้วไม่ตอบภายใน {timeout:g} วิ"
    if code is None:
        return False, f"เปิดไม่ได้ ({out})"
    if code != 0 or "<html" not in out.lower():
        tail = " ".join(out.strip().splitlines()[-2:])[:160]
        return False, f"เปิดแล้วปิดตัว (exit {code}) {tail}".strip()
    return True, ""


def swiftc() -> str | None:
    """Swift compiler for the Apple Vision cut-out (comes with Xcode Command Line Tools)."""
    if not kit.IS_MAC:
        return None
    found = shutil.which("swiftc")
    if found:
        return found
    code, out = _out(["xcrun", "--find", "swiftc"], timeout=30)
    return out.strip().splitlines()[-1] if code == 0 and out.strip() else None


def nvidia_gpu() -> str | None:
    """Name of the first NVIDIA GPU (via nvidia-smi, which ships with the driver), else None."""
    smi = shutil.which("nvidia-smi")
    if not smi and kit.IS_WINDOWS:
        cand = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        smi = str(cand) if cand.exists() else None
    if not smi:
        return None
    code, out = _out([smi, "-L"], timeout=30)
    m = re.search(r"GPU \d+: ([^(\n]+)", out or "")
    return m.group(1).strip() if code == 0 and m else None


def ram_bytes() -> int | None:
    try:
        if kit.IS_MAC:
            code, out = _out(["sysctl", "-n", "hw.memsize"], timeout=10)
            return int(out.strip()) if code == 0 else None
        if kit.IS_WINDOWS:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            return int(st.ullTotalPhys) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)) else None
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError, AttributeError):
        return None


def free_bytes(path) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(str(p)).free


def low_ram(ram: int | None) -> bool:
    # an "8 GB" Windows laptop reports ~7.8 GiB after hardware reservations → 7.5 GiB is the line
    return ram is not None and ram < 7.5 * 1024 ** 3


def setup_command(extra: str = "") -> str:
    """The one command that installs/repairs everything (idempotent) — what doctor prints as the fix."""
    setup = (kit.KIT_ROOT / "engine" / "install" / "setup.py").as_posix()
    tail = f' --yes{" " + extra if extra else ""}'
    if kit.IS_WINDOWS:
        uv = kit.home() / "bin" / "uv.exe"
        try:
            uv = Path(kit.tool("uv"))
        except kit.KitError:
            pass
        return f'"{uv.as_posix()}" run --no-project --python 3.12 "{setup}"{tail}'
    return f'python3 "{setup}"{tail}'


def platform_label() -> str:
    if kit.IS_MAC:
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    if kit.IS_WINDOWS:
        return f"Windows {platform.release()} ({platform.machine()})"
    return f"{platform.system()} {platform.release()}"
