"""Windows: no admin rights, no package manager — the kit downloads its own copies into <home>/bin/.

These are the same files GitHub Actions downloads on windows-latest, so what CI tests is what users get.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from engine import kit
from engine import install as inst

FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
NODE_INDEX_URL = "https://nodejs.org/dist/index.json"
UV_URL = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
# PyPI's Windows torch wheels are CPU-only, so an NVIDIA card needs the CUDA index to be any faster
TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu126"
UA = {"User-Agent": "tad-clip-setup"}


def bin_dir() -> Path:
    d = kit.home() / "bin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download(url: str, dest, label: str = "") -> Path:
    """Stream to <dest>.part and rename when complete; 3 tries; progress every 10 %."""
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    last = None
    for attempt in range(1, 4):
        try:
            print(f"  โหลด {label or url} …", flush=True)
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r, open(part, "wb") as f:
                total, got, step = int(r.headers.get("Content-Length") or 0), 0, 10
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total and got * 100 // total >= step:
                        print(f"    {got * 100 // total}% ({got >> 20} / {total >> 20} MB)", flush=True)
                        step = got * 100 // total // 10 * 10 + 10
            os.replace(part, dest)
            return dest
        except (OSError, urllib.error.URLError) as e:
            last = e
            if part.exists():
                part.unlink()
            time.sleep(2 * attempt)
    raise kit.KitError(f"โหลด {label or url} ไม่สำเร็จ ({last}) — เช็คอินเทอร์เน็ต แล้วสั่งติดตั้งใหม่อีกครั้ง")


def _fetch_json(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def extract(zip_path, wanted: dict) -> None:
    """Copy members whose file name (any folder) is a key of `wanted` to wanted[name]."""
    keys = {k.lower(): Path(v) for k, v in wanted.items()}
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            base = info.filename.rsplit("/", 1)[-1].lower()
            if base in keys and not info.is_dir():
                with z.open(info) as src, open(keys[base], "wb") as dst:
                    shutil.copyfileobj(src, dst)
    missing = [k for k, v in keys.items() if not v.exists()]
    if missing:
        raise kit.KitError(f"ในไฟล์ {Path(zip_path).name} ไม่มี {', '.join(missing)} — ไฟล์ที่โหลดมาอาจไม่ครบ ลองติดตั้งใหม่")


def _from_zip(url: str, label: str, wanted: dict) -> None:
    z = download(url, bin_dir() / (label.split()[0].lower() + ".zip"), label)
    try:
        extract(z, wanted)
    finally:
        if Path(z).exists():
            Path(z).unlink()


def pick_node(index: list) -> tuple:
    for rel in index:
        v = rel.get("version", "")
        if v.startswith(f"v{inst.NODE_MIN}.") and "win-x64-zip" in rel.get("files", []):
            return v, f"https://nodejs.org/dist/{v}/node-{v}-win-x64.zip"
    raise kit.KitError(f"หา Node {inst.NODE_MIN} สำหรับ Windows ใน nodejs.org ไม่เจอ — ลองติดตั้งใหม่อีกครั้งภายหลัง")


def ensure_ffmpeg() -> tuple:
    b = bin_dir()
    ff, fp = b / "ffmpeg.exe", b / "ffprobe.exe"
    if ff.exists() and fp.exists() and inst.has_libass(str(ff)):
        print(f"  ✓ ffmpeg มีอยู่แล้ว ({ff})", flush=True)
        return str(ff), str(fp)
    _from_zip(FFMPEG_URL, "ffmpeg (ไฟล์ zip ~200 MB)", {"ffmpeg.exe": ff, "ffprobe.exe": fp})
    return str(ff), str(fp)


def ensure_node() -> str:
    node = bin_dir() / "node.exe"
    if node.exists() and inst.node_major(str(node)) >= inst.NODE_MIN:
        print(f"  ✓ Node.js มีอยู่แล้ว ({node})", flush=True)
        return str(node)
    v, url = pick_node(_fetch_json(NODE_INDEX_URL))
    _from_zip(url, f"Node.js {v} (~35 MB)", {"node.exe": node})
    return str(node)


def ensure_uv() -> str:
    uv = bin_dir() / "uv.exe"
    if uv.exists():
        print(f"  ✓ uv มีอยู่แล้ว ({uv})", flush=True)
        return str(uv)
    _from_zip(UV_URL, "uv (~20 MB)", {"uv.exe": uv})
    return str(uv)


def ensure_ytdlp() -> str:
    exe = bin_dir() / "yt-dlp.exe"
    if exe.exists():
        print(f"  ✓ yt-dlp มีอยู่แล้ว ({exe})", flush=True)
        return str(exe)
    download(YTDLP_URL, exe, "yt-dlp (~18 MB)")
    return str(exe)


def torch_index(gpu) -> str:
    return TORCH_CUDA_INDEX if gpu else TORCH_CPU_INDEX


def install_tools() -> dict:
    ff, fp = ensure_ffmpeg()
    return {"ffmpeg": ff, "ffprobe": fp, "node": ensure_node(), "uv": ensure_uv(), "yt-dlp": ensure_ytdlp()}


def have() -> dict:
    """What is already in <home>/bin (for the size estimate before installing)."""
    b = kit.home() / "bin"
    return {k: (b / f).exists() for k, f in
            (("ffmpeg", "ffmpeg.exe"), ("node", "node.exe"), ("uv", "uv.exe"), ("yt-dlp", "yt-dlp.exe"))}
