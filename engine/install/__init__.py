"""Probes shared by setup.py and doctor.py — standard library only, Python 3.9+ (macOS's own python3 is 3.9).

Each probe answers one question about the machine and never raises for a missing tool.
"""
from __future__ import annotations

import ctypes
import http.client
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from engine import kit

NODE_MIN = 22
FFMPEG_MIN = (5, 1)   # -fps_mode (constant-fps output) arrived in ffmpeg 5.1
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
}
# download size in MB, for the "this needs N GB" message before installing
WHISPER_MB = {"tiny": 75, "base": 145, "small": 485, "medium": 1530, "large-v1": 3090, "large-v2": 3090, "large-v3": 3090, "large": 3090}
MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")
MODELS = ("large-v3", "medium", "small", "base", "tiny")   # what setup offers (large-v3-turbo transcribes Thai badly)
CONFIG_ERRORS = (ValueError, AttributeError, TypeError)     # config.json that is not JSON, or JSON that is not an object
# whisper.cpp models (macOS backend, Metal), pinned to one revision of huggingface.co/ggerganov/whisper.cpp so the
# exact sizes below (read from that revision's file tree) stay true and a download can be verified byte for byte
GGML_REV = "5359861c739e955e79d9a303bcbc70fb988958b1"
GGML_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/" + GGML_REV + "/ggml-{model}.bin"
GGML_BYTES = {"tiny": 77691713, "base": 147951465, "small": 487601967, "medium": 1533763059,
              "large-v2": 3094623691, "large-v3": 3095033483}
GGML_MAGIC = b"lmgg"   # first 4 bytes of every whisper.cpp model (0x67676d6c little-endian)


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


def ffmpeg_version(ffmpeg: str) -> tuple:
    """(version label, new enough?) — release numbers compared directly; git builds ("N-121083-g…") by libavutil,
    which was 57.28 in ffmpeg 5.1. Unreadable → (label, True): do not block on a string we cannot parse."""
    code, out = _out([ffmpeg, "-version"], timeout=60)
    m = re.search(r"ffmpeg version (\S+)", out or "")
    label = m.group(1) if m else "?"
    rel = re.match(r"n?(\d+)\.(\d+)", label)
    if rel:
        return label, (int(rel.group(1)), int(rel.group(2))) >= FFMPEG_MIN
    lib = re.search(r"libavutil\s+(\d+)\.\s*(\d+)", out or "")
    if lib:
        return label, (int(lib.group(1)), int(lib.group(2))) >= (57, 28)
    return label, True


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


def asr_backend(cfg: dict = None) -> str:
    """config.whisper.backend: auto → whisper.cpp (Metal) on macOS, faster-whisper elsewhere."""
    if cfg is None:
        try:
            cfg = kit.load_config()
        except CONFIG_ERRORS:
            cfg = {}
    b = (cfg.get("whisper") or {}).get("backend") or "auto"
    if b == "auto":
        return "whispercpp" if kit.IS_MAC else "faster-whisper"
    return b


def ggml_path(model: str) -> Path:
    return kit.models_dir() / f"ggml-{model}.bin"


def ggml_state(model: str) -> tuple:
    """(complete?, Thai reason) for models_dir()/ggml-<model>.bin."""
    f = ggml_path(model)
    if not f.is_file():
        return False, f"ยังไม่ได้โหลด {f.name}"
    want, have = GGML_BYTES.get(model), f.stat().st_size
    if (want and have != want) or have < 1_000_000:
        return False, f"{f.name} โหลดมาไม่ครบ ({have / 1e6:.0f} จาก {(want or 0) / 1e6:.0f} MB)"
    with open(f, "rb") as h:
        if h.read(4) != GGML_MAGIC:
            return False, f"{f.name} เสีย ไม่ใช่ไฟล์โมเดล whisper.cpp (อาจเป็นหน้าเว็บที่โหลดมาแทน)"
    return True, str(f)


def _size(n: int) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n} ไบต์"


def download_resumable(url: str, dest, label: str, expected: int = None, tries: int = 5,
                       resume_existing: bool = True) -> Path:
    """Download that survives dropped connections, verified before it is renamed into place.

    Bytes go to <dest>.part. A connection that dies or simply closes early leaves the .part on disk and the next
    attempt asks for the rest with `Range: bytes=<size>-` (backoff 1, 2, 4, 8 s). The finished size must equal
    `expected` or what the server announced (Content-Length / Content-Range). A .part larger than that, or a 416
    for one, is thrown away and the download restarts. `resume_existing=False` drops a .part left by an earlier
    run first (for "latest" files that may have changed since).
    """
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    if not resume_existing and part.exists():
        part.unlink()
    last, want = None, expected
    for attempt in range(1, tries + 1):
        if attempt > 1:
            time.sleep(min(2 ** (attempt - 2), 30))
        have = part.stat().st_size if part.exists() else 0
        if want and have > want:   # not ours / corrupt → start over
            part.unlink()
            have = 0
        headers = {"User-Agent": "tad-clip-setup"}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                rng = r.headers.get("Content-Range") or ""
                length = int(r.headers.get("Content-Length") or 0)
                if have and getattr(r, "status", 200) == 206 and rng:
                    total, mode = int(rng.rsplit("/", 1)[1]), "ab"
                    body = total - have
                else:   # the server sent the whole file → start over
                    total, mode, body = length, "wb", length
                if expected and ((total and total != expected) or (length and length != body)):
                    # a captive portal / error page or a different file: never write it next to (or over) the model
                    last = f"เซิร์ฟเวอร์ส่งข้อมูล {_size(length)} มาแทนไฟล์ {_size(expected)} (อาจเป็นหน้าเว็บ ไม่ใช่ตัวไฟล์)"
                    continue
                if mode == "wb":
                    have = 0
                want = expected or total or want
                print(f"  โหลด {label} {_size(want) if want else ''}" + (f" (ต่อจากเดิม {_size(have)})" if have else "") + " …", flush=True)
                got, step = have, 5
                with open(part, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if want and got * 100 // want >= step:
                            print(f"    {got * 100 // want}% ({got >> 20} / {want >> 20} MB)", flush=True)
                            step = got * 100 // want // 5 * 5 + 5
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 416:   # asked for bytes past the end: either already complete or a bad .part
                rng = (e.headers.get("Content-Range") if e.headers else "") or ""
                total = int(rng.rsplit("/", 1)[1]) if rng.rsplit("/", 1)[-1].isdigit() else 0
                if have and have == (expected or total):
                    os.replace(part, dest)
                    return dest
                if part.exists():
                    part.unlink()
            continue
        except (OSError, ValueError, http.client.HTTPException) as e:   # reset, timeout, offline, IncompleteRead: keep the .part
            last = e
            continue
        size = part.stat().st_size if part.exists() else 0
        if want and size < want:     # clean early close: keep the .part and ask for the rest
            last = f"ได้ {_size(size)} จาก {_size(want)}"
            continue
        if want and size > want:
            part.unlink()
            last = f"ได้ไฟล์ใหญ่เกิน ({size} จาก {want} ไบต์)"
            continue
        os.replace(part, dest)
        return dest
    raise kit.KitError(f"โหลด {label} ไม่สำเร็จ ({last}) — เช็คอินเทอร์เน็ต แล้วรันใหม่ "
                       f"(ส่วนที่โหลดแล้วไม่หาย): {setup_command()}")


def chrome_candidates() -> list:
    """Browsers that can draw the cards, in the order the visual engine tries them (config first)."""
    found = []
    try:
        configured = (kit.load_config().get("tools") or {}).get("chrome")
    except CONFIG_ERRORS:   # broken config.json — doctor reports it separately
        configured = None
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


def chrome_ready(browser: str, timeout: float = 20) -> tuple:
    """chrome_works, retried once after a timeout: a big browser's very first launch can be slow
    (cold disk, Gatekeeper or antivirus scan) — the second launch is the realistic one."""
    ok, why = chrome_works(browser, timeout)
    if not ok and why.startswith("เปิดแล้วไม่ตอบ"):
        ok, why = chrome_works(browser, timeout)
    return ok, why


def swiftc() -> str | None:
    """Swift compiler for the Apple Vision cut-out, from Xcode Command Line Tools — or None.

    /usr/bin/swiftc is a shim that exists even without CLT, so it proves nothing. `xcode-select -p` is asked first
    because it never pops the "install developer tools" dialog; then `xcrun --find swiftc` gives the real compiler.
    Compile with `xcrun swiftc …`: CLT's swiftc called by absolute path cannot find the Swift standard library.
    """
    if not kit.IS_MAC:
        return None
    code, out = _out(["xcode-select", "-p"], timeout=30)
    if code != 0 or not out.strip():
        return None
    code, out = _out(["xcrun", "--find", "swiftc"], timeout=60)
    path = out.strip().splitlines()[-1] if code == 0 and out.strip() else ""
    return path if path and Path(path).is_file() else None


GIT_BASH_DEFAULT = r"C:\Program Files\Git\bin\bash.exe"
GIT_BASH_URL = "https://git-scm.com/download/win"


def git_bash() -> str | None:
    """Git for Windows' bash.exe — the shell Claude Code runs every command in on Windows (without it Claude Code
    falls back to PowerShell and no skill snippet works). System32's bash.exe and the WindowsApps alias
    are WSL launchers, not Git Bash, so they do not count."""
    env = os.environ
    cands = [env.get("CLAUDE_CODE_GIT_BASH_PATH", "")]
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        if env.get(var):
            cands.append(os.path.join(env[var], "Git", "bin", "bash.exe"))
    if env.get("LOCALAPPDATA"):
        cands.append(os.path.join(env["LOCALAPPDATA"], "Programs", "Git", "bin", "bash.exe"))   # "only for me" install
    cands.append(GIT_BASH_DEFAULT)
    cands += [os.path.join(d, "bash.exe") for d in env.get("PATH", "").split(os.pathsep) if d]
    for c in cands:
        low = c.replace("/", "\\").lower()
        if not c or "\\windows\\system32\\" in low or "\\windowsapps\\" in low:
            continue
        if os.path.isfile(c):
            return c
    return None


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
        py = kit.venv_python()
        # setup.py is stdlib-only: the kit's own Python 3.12 runs it — but only a venv that actually starts
        if py.exists() and _out([py, "-c", "pass"], timeout=60)[0] == 0:
            return f'"{py.as_posix()}" "{setup}"{tail}'
        uv = kit.home() / "bin" / "uv.exe"
        try:
            uv = Path(kit.tool("uv"))
        except (kit.KitError,) + CONFIG_ERRORS:   # broken config.json is reported by doctor's setup row
            pass
        h = kit.home().as_posix()   # same dirs as setup.kit_dirs(): uv's Python + cache stay inside the kit home
        return (f'UV_PYTHON_INSTALL_DIR="{h}/python" UV_PYTHON_BIN_DIR="{h}/python/bin" UV_CACHE_DIR="{h}/cache/uv" '
                f'UV_PYTHON_INSTALL_REGISTRY=0 "{uv.as_posix()}" run --no-project --python 3.12 "{setup}"{tail}')
    return f'python3 "{setup}"{tail}'


def platform_label() -> str:
    if kit.IS_MAC:
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    if kit.IS_WINDOWS:
        return f"Windows {platform.release()} ({platform.machine()})"
    return f"{platform.system()} {platform.release()}"
