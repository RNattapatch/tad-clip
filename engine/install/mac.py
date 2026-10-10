"""macOS: Homebrew installs ffmpeg-full (keg-only, has libass), node, uv, yt-dlp — anything already present is kept."""
from __future__ import annotations

import subprocess
from pathlib import Path

from engine import kit
from engine import install as inst

BREW_INSTALL = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
FORMULA = {"ffmpeg": "ffmpeg-full", "node": "node", "uv": "uv", "yt-dlp": "yt-dlp"}


def brew() -> str | None:
    return inst.which("brew", ["/opt/homebrew/bin", "/usr/local/bin"])


def brew_prefix(b: str) -> Path:
    code, out = inst._out([b, "--prefix"], timeout=60)
    lines = out.strip().splitlines()
    return Path(lines[-1]) if code == 0 and lines else Path(b).parent.parent


def _dirs(prefix: Path) -> list:
    home = Path.home()
    return [prefix / "bin", home / ".local" / "bin", home / ".cargo" / "bin"]


def find(prefix: Path) -> dict:
    """Current path of every tool, or None. ffmpeg must be ffmpeg-full: brew's plain ffmpeg has no libass."""
    keg = Path(prefix) / "opt" / "ffmpeg-full" / "bin"
    ff = keg / "ffmpeg"
    out = {"ffmpeg": str(ff) if ff.exists() else None,
           "ffprobe": str(keg / "ffprobe") if (keg / "ffprobe").exists() else None}
    dirs = _dirs(Path(prefix))
    node = None
    # opt/node is the keg path: it exists even when an older linked node@20 owns bin/node
    for cand in (str(Path(prefix) / "opt" / "node" / "bin" / "node"), str(Path(prefix) / "bin" / "node"), inst.which("node", dirs)):
        if cand and Path(cand).exists() and inst.node_major(cand) >= inst.NODE_MIN:
            node = cand
            break
    out["node"] = node
    out["uv"] = inst.which("uv", dirs)
    out["yt-dlp"] = inst.which("yt-dlp", dirs)
    return out


def missing(found: dict) -> list:
    return [FORMULA[k] for k in ("ffmpeg", "node", "uv", "yt-dlp") if not found.get(k)]


def brew_install(b: str, formulas: list) -> int:
    print(f"  brew install {' '.join(formulas)}  (ครั้งแรกใช้เวลาหลายนาที)", flush=True)
    code = subprocess.call([b, "install", *formulas])
    if code != 0:   # e.g. a link conflict with an older node@20 — the keg can still be usable, so judge by find()
        print(f"  ⚠️ brew install จบด้วย exit {code} — ตรวจว่าได้ครบไหม", flush=True)
    return code


def require_brew() -> str:
    b = brew()
    if not b:
        raise kit.KitError(
            "เครื่องนี้ยังไม่มี Homebrew (ตัวติดตั้งโปรแกรมของ Mac) — เปิดแอป Terminal แล้ววางบรรทัดนี้ "
            "(มันจะถามรหัสผ่านเครื่อง พิมพ์แล้วกด Enter ตัวอักษรจะไม่ขึ้นให้เห็น เป็นเรื่องปกติ):\n\n"
            f"{BREW_INSTALL}\n\nเสร็จแล้วกลับมาบอก Claude ว่า \"ติดตั้ง tad-clip ต่อ\"")
    return b


def install_tools() -> dict:
    b = require_brew()
    prefix = brew_prefix(b)
    found = find(prefix)
    todo = missing(found)
    for k, v in found.items():
        if v:
            print(f"  ✓ {k} มีอยู่แล้ว ({v})", flush=True)
    code = 0
    if todo:
        code = brew_install(b, todo)
        found = find(prefix)
    still = [k for k, v in found.items() if not v]
    if still:
        raise kit.KitError(f"brew install {' '.join(todo)} ไม่สำเร็จ (exit {code}) ยังขาด {', '.join(still)} — "
                           "ลองรัน `brew update` แล้วสั่งติดตั้งใหม่")
    return found
