"""ติดตั้ง tad-clip: tools → Python venv → (cut-out add-on) → browser → whisper model → config → doctor.

  python3 engine/install/setup.py --yes [--with-cutout] [--model large-v3]            # macOS
  uv run --no-project --python 3.12 engine/install/setup.py --yes [...]               # Windows (uv bootstrapped first)

Safe to run again: everything already installed is detected and skipped. Standard library only, Python 3.9+.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from engine import kit  # noqa: E402
from engine import install as inst  # noqa: E402
from engine.install import mac, windows  # noqa: E402

HERE = Path(__file__).resolve().parent
PY_VERSION = "3.12"
DEFAULT_MODEL = "large-v3"
MODELS = ("large-v3", "medium", "small", "base", "tiny")   # what setup offers; large-v3-turbo transcribes Thai badly
GB = 1024 ** 3
CHROME_TIMEOUT = 20   # same limit as doctor
# approximate disk use in MB (installed), for the plan shown before anything is downloaded
SIZE_MB = {
    "mac": {"ffmpeg": 1000, "node": 250, "uv": 50, "yt-dlp": 350, "whisper-cli": 40},
    "windows": {"ffmpeg": 300, "node": 90, "uv": 45, "yt-dlp": 20},
}
VENV_MB = 700        # Python 3.12 + faster-whisper/ctranslate2/onnxruntime + pythainlp + uv cache
CHROME_MB = 300
CUTOUT_MB = {"cpu": 2000, "cuda": 5000, "mac": 1500}
# faster-whisper on an NVIDIA card needs cuBLAS + cuDNN 9 for CUDA 12; the pip wheels drop the DLLs in
# site-packages/nvidia/*/bin, which ctranslate2 does not search → a .pth puts them on the DLL path for every venv run
CUDA_WHEELS = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]
CUDA_MB = 1500
CUDA_PTH = ("import os, sys, glob; _tc = [d for d in glob.glob(os.path.join(sys.prefix, 'Lib', 'site-packages', 'nvidia', '*', 'bin')) "
            "if os.path.isdir(d)]; [os.add_dll_directory(d) for d in _tc if hasattr(os, 'add_dll_directory')]; "
            "os.environ['PATH'] = os.pathsep.join(_tc + [os.environ.get('PATH', '')])")
VERIFY_CUDA = ("import sys, numpy as np\n"
               "from faster_whisper import WhisperModel\n"
               "m = WhisperModel(sys.argv[1], device='cuda', compute_type='float16', download_root=sys.argv[2], local_files_only=True)\n"
               "segments, _ = m.transcribe(np.zeros(16000, dtype=np.float32), language='th', vad_filter=False)\n"
               "list(segments)\nprint('cuda ok')")


def say(msg: str = "") -> None:
    print(msg, flush=True)


def read_raw_config() -> dict:
    """The user's own config.json. A file that is not valid JSON is kept as config.json.broken-<time> (the user's
    name, colours and glossary are still in there) and setup carries on from the defaults."""
    f = kit.home() / "config.json"
    if not f.exists():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (ValueError, UnicodeDecodeError):
        pass
    backup = f.with_name(f"config.json.broken-{time.strftime('%Y%m%d-%H%M%S')}")
    os.replace(f, backup)
    say(f"⚠️ ไฟล์ตั้งค่า config.json เปิดไม่ได้ (เนื้อในเสีย) — เก็บไว้เป็น {backup.name} แล้วเริ่มจากค่าเริ่มต้น\n"
        f"   ชื่อ สี และคำศัพท์ที่เคยตั้งไว้ยังอยู่ในไฟล์นั้น ให้ Claude ย้ายกลับมาได้ (skill tad-clip-setup ข้อ B)")
    return {}


def choose_model(arg) -> str:
    if arg:
        return arg
    configured = (read_raw_config().get("whisper") or {}).get("model")
    if configured in MODELS:
        return configured
    if configured:
        say(f"⚠️ โมเดล {configured} ในไฟล์ตั้งค่าไม่อยู่ในชุดที่รองรับ ({' · '.join(MODELS)}) — ใช้ค่าเริ่มต้นแทน")
    return inst.LIGHT_MODEL if inst.low_ram(inst.ram_bytes()) else DEFAULT_MODEL


def update_config(tools: dict, model: str, device: str = None) -> None:
    """Write only what setup owns into the user's raw config (never the merged one: that would freeze preset colours)."""
    raw = read_raw_config()
    raw.setdefault("version", 1)
    raw["tools"] = {**(raw.get("tools") or {}), **tools}
    raw["whisper"] = {**(raw.get("whisper") or {}), "model": model, **({"device": device} if device else {})}
    kit.save_config(raw)


def plan(osn: str, model: str, with_cutout: bool, gpu, have: dict, cuda: bool = None) -> tuple:
    sizes = SIZE_MB[osn]
    names = {"ffmpeg": "ffmpeg (ตัดต่อ + ฝังซับ)", "node": "Node.js 22", "uv": "uv (ตัวติดตั้ง Python)", "yt-dlp": "yt-dlp (โหลดคลิปจากเว็บ)"}
    if osn == "mac":
        names["whisper-cli"] = "whisper.cpp (ถอดเสียงด้วยชิปกราฟิกของ Mac)"
    how = "ผ่าน Homebrew" if osn == "mac" else "โหลดไฟล์ลง " + str(kit.home() / "bin")
    lines, total = [], 0
    for k, label in names.items():
        if have.get(k):
            lines.append(f"- {label}: มีแล้ว ข้าม")
        else:
            lines.append(f"- {label} {how} (~{sizes[k]} MB)")
            total += sizes[k]
    pkgs = "pythainlp + faster-whisper (สำรอง)" if osn == "mac" else "faster-whisper + pythainlp"
    if kit.venv_python().exists():
        lines.append(f"- Python {PY_VERSION} + {pkgs}: มีแล้ว (เช็คแพ็กเกจให้ครบ)")
    else:
        lines.append(f"- Python {PY_VERSION} + {pkgs} ใน {kit.home() / 'venv'} (~{VENV_MB} MB)")
        total += VENV_MB
    if osn == "mac" and inst.asr_backend() == "whispercpp":
        mb = inst.GGML_BYTES.get(model, 3_100_000_000) / 1e6
        done, label = inst.ggml_state(model)[0], f"โมเดลถอดเสียง {model} สำหรับ whisper.cpp (ggml-{model}.bin)"
    else:
        mb = inst.WHISPER_MB.get(model, 3090)
        done, label = bool(inst.model_dir(model, kit.models_dir())), f"โมเดลถอดเสียง {model}"
    if done:
        lines.append(f"- {label}: มีแล้ว ข้าม")
    else:
        lines.append(f"- {label} (~{mb / 1000:.1f} GB) ลง {kit.models_dir()}")
        total += mb
    if cuda is None:
        cuda = osn == "windows" and bool(gpu)
    if cuda:
        lines.append(f"- ไลบรารี CUDA ให้ถอดเสียงด้วยการ์ดจอ {gpu} (~{CUDA_MB / 1024:.1f} GB · ถ้าใช้ไม่ได้จะถอดด้วย CPU แทน)")
        total += CUDA_MB
    if osn == "windows":
        lines.append(f"- เบราว์เซอร์วาดการ์ด: ใช้ Edge ที่มีในเครื่อง ถ้าเปิดไม่ได้จะโหลด chrome-headless-shell (~{CHROME_MB} MB)")
    else:
        lines.append(f"- เบราว์เซอร์วาดการ์ด: chrome-headless-shell จาก Google (~{CHROME_MB} MB · เปิดเร็วกว่า Chrome ตัวเต็ม)")
        total += CHROME_MB
    if with_cutout:
        kind = "mac" if osn == "mac" else "cuda" if gpu else "cpu"
        torch = {"mac": "PyTorch", "cuda": f"PyTorch แบบ CUDA (การ์ดจอ {gpu})", "cpu": "PyTorch แบบ CPU"}[kind]
        lines.append(f"- ตัวตัดพื้นหลัง transparent-background + {torch} (~{CUTOUT_MB[kind] / 1024:.1f} GB)")
        total += CUTOUT_MB[kind]
    return lines, total


def kit_dirs() -> dict:
    """Everything the installer downloads stays inside <home>, so deleting ~/.tad-clip really frees the space:
    uv's cache and its Python builds, torch / Hugging Face caches, transparent-background's weights."""
    h, c = kit.home(), kit.home() / "cache"
    return {
        "UV_CACHE_DIR": str(c / "uv"), "UV_PYTHON_INSTALL_DIR": str(h / "python"),
        "UV_PYTHON_BIN_DIR": str(h / "python" / "bin"), "UV_TOOL_DIR": str(h / "uv-tools"),
        "UV_TOOL_BIN_DIR": str(h / "uv-tools" / "bin"), "UV_PYTHON_INSTALL_REGISTRY": "0",   # no Windows registry entries
        "TORCH_HOME": str(c / "torch"), "HF_HOME": str(c / "huggingface"),
        "TRANSPARENT_BACKGROUND_FILE_PATH": str(h),   # weights → <home>/.transparent-background/
    }


def _env() -> dict:
    env = dict(os.environ)
    env.update(kit_dirs())
    env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _call(cmd: list, what: str) -> None:
    code = subprocess.call([str(c) for c in cmd], env=_env())
    if code != 0:
        raise kit.KitError(f"{what} ไม่สำเร็จ (exit {code}) — ดูข้อความด้านบน แล้วสั่งติดตั้งใหม่อีกครั้ง")


def _py_version(py: Path) -> str:
    code, out = inst._out([py, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], timeout=60)
    return out.strip() if code == 0 else ""


def ensure_venv(uv: str) -> Path:
    py = kit.venv_python()
    venv = kit.home() / "venv"
    if py.exists() and _py_version(py) == PY_VERSION:
        say(f"  ✓ venv มีอยู่แล้ว ({venv})")
        return py
    if venv.exists():
        import shutil
        shutil.rmtree(venv)
    _call([uv, "python", "install", PY_VERSION], f"ติดตั้ง Python {PY_VERSION}")
    _call([uv, "venv", "--python", PY_VERSION, "--python-preference", "only-managed", venv], "สร้าง venv")
    return py


def pip(uv: str, py: Path, args: list, what: str) -> None:
    _call([uv, "pip", "install", "--python", py, *args], what)


def install_cutout(uv: str, py: Path, osn: str, gpu) -> None:
    if osn == "windows":
        pip(uv, py, ["torch", "torchvision", "--index-url", windows.torch_index(gpu)], "ติดตั้ง PyTorch")
    pip(uv, py, ["-r", HERE / "requirements-cutout.txt"], "ติดตั้ง transparent-background")
    say("  โหลดน้ำหนักโมเดลตัดพื้นหลัง (ครั้งเดียว ~370 MB) …")
    code = subprocess.call([str(py), "-c", "from transparent_background import Remover; Remover()"], env=_env())
    if code != 0:
        say("  ⚠️ ยังโหลดน้ำหนักโมเดลตัดพื้นหลังไม่ได้ — จะโหลดเองตอนใช้ครั้งแรก (ต้องต่อเน็ต)")


def _works(browser: str) -> bool:
    ok, why = inst.chrome_ready(browser, CHROME_TIMEOUT)
    say(f"  ✓ ใช้ {browser}" if ok else f"  ⚠️ {Path(browser).name} {why} — ลองตัวถัดไป")
    return ok


def _download_shell(node: str):
    say("  โหลด chrome-headless-shell จาก Chrome for Testing (~100 MB) …")
    code, out = inst._out([node, HERE / "get_chrome.mjs"], timeout=900)
    exe = out.strip().splitlines()[-1] if out.strip() else ""
    if code != 0 or not Path(exe).is_file():
        say(f"  ⚠️ โหลดไม่สำเร็จ: {out.strip()[-300:]}")
        return None
    return exe


def ensure_chrome(node: str) -> str:
    """Mac: chrome-headless-shell first (full Chrome starts slowly on a cold Mac and hangs on one with no display),
    Google Chrome as the fallback. Windows: Edge (on every machine) first, the downloaded shell as the fallback."""
    cands = inst.chrome_candidates()
    if kit.IS_MAC:
        shells = [c for c in cands if Path(c).name == "chrome-headless-shell"]
        for c in shells:
            if _works(c):
                return c
        exe = _download_shell(node)
        if exe and _works(exe):
            return exe
        cands = [c for c in cands if c not in shells]
    for c in cands:
        if _works(c):
            return c
    if not kit.IS_MAC:
        exe = _download_shell(node)
        if exe and _works(exe):
            return exe
    raise kit.KitError("หาเบราว์เซอร์สำหรับวาดการ์ดที่เปิดได้ไม่เจอ (Chrome/Edge/chrome-headless-shell) — เช็คอินเทอร์เน็ตแล้วสั่งติดตั้งใหม่")


def _site_packages(py: Path) -> Path:
    code, out = inst._out([py, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"], timeout=60)
    if code != 0 or not out.strip():
        raise kit.KitError(f"หาโฟลเดอร์แพ็กเกจของ venv ไม่เจอ: {out.strip()[-200:]}")
    return Path(out.strip().splitlines()[-1])


def setup_cuda(uv: str, py: Path, model: str) -> str:
    """Install cuBLAS/cuDNN, make them loadable, and transcribe 1 s of silence on the GPU. Returns the device to use."""
    say("  ติดตั้งไลบรารี CUDA ให้ถอดเสียงด้วยการ์ดจอ (~1.5 GB) …")
    try:
        pip(uv, py, CUDA_WHEELS, "ติดตั้งไลบรารี CUDA")
        (_site_packages(py) / "tadclip-cuda.pth").write_text(CUDA_PTH + "\n", encoding="utf-8")
    except kit.KitError as e:
        say(f"  ⚠️ {e} — ถอดเสียงด้วย CPU แทน")
        return "cpu"
    code, out = inst._out([py, "-c", VERIFY_CUDA, model, kit.models_dir()], timeout=900)
    if code == 0 and "cuda ok" in out:
        say("  ✓ ถอดเสียงด้วยการ์ดจอได้")
        return "cuda"
    tail = " ".join(out.strip().splitlines()[-2:])[:200]
    say(f"  ⚠️ ถอดเสียงด้วยการ์ดจอไม่ผ่าน ({tail}) — ตั้งให้ใช้ CPU แทน ได้ผลเท่ากันแต่ช้ากว่า")
    return "cpu"


def download_model(py: Path, model: str) -> None:
    if inst.asr_backend() == "whispercpp":   # macOS: whisper.cpp + Metal (faster-whisper stays in the venv as the fallback)
        if inst.ggml_state(model)[0]:
            say(f"  ✓ โมเดล ggml-{model}.bin มีอยู่แล้ว")
            return
        inst.download_resumable(inst.GGML_URL.format(model=model), inst.ggml_path(model), f"ggml-{model}.bin",
                                expected=inst.GGML_BYTES.get(model))
        ok, why = inst.ggml_state(model)
        if not ok:
            raise kit.KitError(f"{why} — สั่งติดตั้งใหม่อีกครั้ง (มันจะโหลดต่อจากเดิม)")
        return
    if inst.model_dir(model, kit.models_dir()):
        say(f"  ✓ โมเดล {model} มีอยู่แล้ว")
        return
    code = "import sys; from faster_whisper.utils import download_model; print(download_model(sys.argv[1], cache_dir=sys.argv[2]))"
    _call([py, "-c", code, model, kit.models_dir()], f"โหลดโมเดล {model}")
    if not inst.model_dir(model, kit.models_dir()):
        raise kit.KitError(f"โหลดโมเดล {model} แล้วไฟล์ไม่ครบ — สั่งติดตั้งใหม่อีกครั้ง (มันจะโหลดต่อจากเดิม)")


def main(argv=None) -> int:
    inst.utf8_stdio()
    ap = argparse.ArgumentParser(description="ติดตั้ง tad-clip")
    ap.add_argument("--yes", action="store_true", help="ติดตั้งเลยไม่ต้องถาม")
    ap.add_argument("--with-cutout", action="store_true", help="ติดตั้งตัวตัดพื้นหลัง transparent-background เพิ่ม")
    ap.add_argument("--no-cuda", action="store_true", help="ไม่ใช้การ์ดจอ NVIDIA ถอดเสียง (Windows)")
    ap.add_argument("--model", choices=MODELS, help="โมเดลถอดเสียง (ค่าเริ่มต้น large-v3 · เครื่อง RAM น้อยกว่า 8 GB ใช้ medium)")
    a = ap.parse_args(argv)

    osn = inst.os_name()
    if osn not in ("mac", "windows"):
        say("tad-clip รองรับ macOS และ Windows เท่านั้น")
        return 1
    try:
        read_raw_config()   # a broken config.json is set aside (and the user told) before anything else
        model = choose_model(a.model)
        gpu = inst.nvidia_gpu() if osn == "windows" else None
        if osn == "mac":
            have = {k: bool(v) for k, v in mac.find(mac.brew_prefix(mac.require_brew())).items()}
        else:
            have = windows.have()
        cuda = osn == "windows" and bool(gpu) and not a.no_cuda
        lines, need_mb = plan(osn, model, a.with_cutout, gpu, have, cuda)
        free_mb = inst.free_bytes(kit.home()) // (1024 * 1024)
        say(f"ติดตั้ง tad-clip · {inst.platform_label()} · ที่เก็บ {kit.home()}")
        say("ฟรีทั้งหมด ไม่ใช้ API key · สิ่งที่จะลงเครื่อง:")
        for line in lines:
            say(line)
        say(f"ใช้พื้นที่ประมาณ {need_mb / 1024:.1f} GB · ตอนนี้ว่าง {free_mb / 1024:.1f} GB")
        if free_mb < need_mb + 1024:
            raise kit.KitError(f"พื้นที่ว่างไม่พอ ต้องการประมาณ {(need_mb + 1024) / 1024:.1f} GB "
                               f"(เผื่อไฟล์งาน) แต่ว่าง {free_mb / 1024:.1f} GB — ลบไฟล์ที่ไม่ใช้แล้วสั่งติดตั้งใหม่")
        if not a.yes:
            answer = ""
            if sys.stdin and sys.stdin.isatty():   # Windows reports NUL as a tty too → EOFError below
                try:
                    answer = input("\nติดตั้งเลยไหม? [y/N] ").strip().lower()
                except EOFError:
                    answer = ""
            if answer not in ("y", "yes"):
                say("\nยังไม่ได้ติดตั้งอะไร — เพิ่ม --yes ต่อท้ายคำสั่งเพื่อเริ่มติดตั้ง")
                return 2

        say("\n[1/5] โปรแกรมหลัก")
        tools = mac.install_tools() if osn == "mac" else windows.install_tools()
        say(f"\n[2/5] Python {PY_VERSION} + ตัวถอดเสียง")
        py = ensure_venv(tools["uv"])
        pip(tools["uv"], py, ["-r", HERE / "requirements.txt"], "ติดตั้งแพ็กเกจ Python")
        tools["python"] = str(py)
        if a.with_cutout:
            say("\n[2b/5] ตัวตัดพื้นหลัง (transparent-background)")
            install_cutout(tools["uv"], py, osn, gpu)
        say("\n[3/5] เบราว์เซอร์วาดการ์ด")
        tools["chrome"] = ensure_chrome(tools["node"])
        update_config(tools, model)   # save now: the model download is the long part and may be interrupted
        say(f"\n[4/5] โมเดลถอดเสียง {model} (ครั้งแรกโหลดนาน แล้วใช้แบบไม่ต่อเน็ตได้)")
        download_model(py, model)
        device = setup_cuda(tools["uv"], py, model) if cuda else "cpu"
        update_config({}, model, device)
    except kit.KitError as e:
        say(f"\n❌ {e}")
        return 1
    except KeyboardInterrupt:
        say("\nหยุดกลางทาง — สั่งติดตั้งใหม่ได้ มันจะทำต่อจากที่ค้าง")
        return 130
    except (OSError, ValueError) as e:   # last net: an installer the user cannot read must never end in a traceback
        say(f"\n❌ ติดตั้งสะดุด ({type(e).__name__}: {e})\nรันคำสั่งเดิมอีกครั้งได้เลย ของที่ลงแล้วจะข้าม · "
            f"ถ้ายังเจอซ้ำ ให้ Claude อ่านข้อความนี้: {inst.setup_command()}")
        return 1

    say("\n[5/5] ตรวจเครื่อง\n")
    return subprocess.call([sys.executable, str(kit.KIT_ROOT / "engine" / "doctor.py")], env=_env())


if __name__ == "__main__":
    sys.exit(main())
