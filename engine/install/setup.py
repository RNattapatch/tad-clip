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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from engine import kit  # noqa: E402
from engine import install as inst  # noqa: E402
from engine.install import mac, windows  # noqa: E402

HERE = Path(__file__).resolve().parent
PY_VERSION = "3.12"
DEFAULT_MODEL = "large-v3"
GB = 1024 ** 3
# approximate disk use in MB (installed), for the plan shown before anything is downloaded
SIZE_MB = {
    "mac": {"ffmpeg": 1000, "node": 250, "uv": 50, "yt-dlp": 350},
    "windows": {"ffmpeg": 300, "node": 90, "uv": 45, "yt-dlp": 20},
}
VENV_MB = 700        # Python 3.12 + faster-whisper/ctranslate2/onnxruntime + pythainlp + uv cache
CHROME_MB = 300
CUTOUT_MB = {"cpu": 2000, "cuda": 5000, "mac": 1500}


def say(msg: str = "") -> None:
    print(msg, flush=True)


def choose_model(arg) -> str:
    if arg:
        return arg
    f = kit.home() / "config.json"
    raw = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    configured = (raw.get("whisper") or {}).get("model")
    if configured:
        return configured
    return "large-v3-turbo" if inst.low_ram(inst.ram_bytes()) else DEFAULT_MODEL


def update_config(tools: dict, model: str) -> None:
    """Write only what setup owns into the user's raw config (never the merged one: that would freeze preset colours)."""
    f = kit.home() / "config.json"
    raw = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    raw.setdefault("version", 1)
    raw["tools"] = {**(raw.get("tools") or {}), **tools}
    raw["whisper"] = {**(raw.get("whisper") or {}), "model": model}
    kit.save_config(raw)


def plan(osn: str, model: str, with_cutout: bool, gpu, have: dict) -> tuple:
    sizes = SIZE_MB[osn]
    names = {"ffmpeg": "ffmpeg (ตัดต่อ + ฝังซับ)", "node": "Node.js 22", "uv": "uv (ตัวติดตั้ง Python)", "yt-dlp": "yt-dlp (โหลดคลิปจากเว็บ)"}
    how = "ผ่าน Homebrew" if osn == "mac" else "โหลดไฟล์ลง " + str(kit.home() / "bin")
    lines, total = [], 0
    for k, label in names.items():
        if have.get(k):
            lines.append(f"- {label}: มีแล้ว ข้าม")
        else:
            lines.append(f"- {label} {how} (~{sizes[k]} MB)")
            total += sizes[k]
    lines.append(f"- Python {PY_VERSION} + faster-whisper + pythainlp ใน {kit.home() / 'venv'} (~{VENV_MB} MB)")
    total += VENV_MB
    mb = inst.WHISPER_MB.get(model, 3090)
    if inst.model_dir(model, kit.models_dir()):
        lines.append(f"- โมเดลถอดเสียง {model}: มีแล้ว ข้าม")
    else:
        lines.append(f"- โมเดลถอดเสียง {model} (~{mb / 1024:.1f} GB) ลง {kit.models_dir()}")
        total += mb
    lines.append(f"- เบราว์เซอร์วาดการ์ด: ใช้ {'Edge' if osn == 'windows' else 'Google Chrome'} ที่มีอยู่ "
                 f"ถ้าไม่มีจะโหลด chrome-headless-shell (~{CHROME_MB} MB)")
    if with_cutout:
        kind = "mac" if osn == "mac" else "cuda" if gpu else "cpu"
        torch = {"mac": "PyTorch", "cuda": f"PyTorch แบบ CUDA (การ์ดจอ {gpu})", "cpu": "PyTorch แบบ CPU"}[kind]
        lines.append(f"- ตัวตัดพื้นหลัง transparent-background + {torch} (~{CUTOUT_MB[kind] / 1024:.1f} GB)")
        total += CUTOUT_MB[kind]
    return lines, total


def _env() -> dict:
    env = dict(os.environ)
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


def ensure_chrome(node: str) -> str:
    for cand in inst.chrome_candidates():
        ok, why = inst.chrome_works(cand, 30)
        if ok:
            say(f"  ✓ ใช้ {cand}")
            return cand
        say(f"  ⚠️ {Path(cand).name} {why} — ลองตัวถัดไป")
    say("  โหลด chrome-headless-shell (~100 MB) …")
    code, out = inst._out([node, HERE / "get_chrome.mjs"], timeout=900)
    exe = out.strip().splitlines()[-1] if out.strip() else ""
    if code != 0 or not Path(exe).is_file():
        raise kit.KitError(f"ติดตั้งเบราว์เซอร์วาดการ์ดไม่สำเร็จ: {out.strip()[-300:]}")
    ok, why = inst.chrome_works(exe, 30)
    if not ok:
        raise kit.KitError(f"chrome-headless-shell {why}")
    say(f"  ✓ ใช้ {exe}")
    return exe


def download_model(py: Path, model: str) -> None:
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
    ap.add_argument("--model", help="โมเดลถอดเสียง (ค่าเริ่มต้น large-v3 · เครื่อง RAM น้อยใช้ large-v3-turbo)")
    a = ap.parse_args(argv)

    osn = inst.os_name()
    if osn not in ("mac", "windows"):
        say("tad-clip รองรับ macOS และ Windows เท่านั้น")
        return 1
    try:
        model = choose_model(a.model)
        gpu = inst.nvidia_gpu() if osn == "windows" else None
        if osn == "mac":
            have = {k: bool(v) for k, v in mac.find(mac.brew_prefix(mac.require_brew())).items()}
        else:
            have = windows.have()
        lines, need_mb = plan(osn, model, a.with_cutout, gpu, have)
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
            if not sys.stdin.isatty():
                say("\nยังไม่ได้ติดตั้งอะไร — เพิ่ม --yes ต่อท้ายคำสั่งเพื่อเริ่มติดตั้ง")
                return 2
            if input("\nติดตั้งเลยไหม? [y/N] ").strip().lower() not in ("y", "yes"):
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
    except kit.KitError as e:
        say(f"\n❌ {e}")
        return 1
    except KeyboardInterrupt:
        say("\nหยุดกลางทาง — สั่งติดตั้งใหม่ได้ มันจะทำต่อจากที่ค้าง")
        return 130

    say("\n[5/5] ตรวจเครื่อง\n")
    return subprocess.call([sys.executable, str(kit.KIT_ROOT / "engine" / "doctor.py")], env=_env())


if __name__ == "__main__":
    sys.exit(main())
