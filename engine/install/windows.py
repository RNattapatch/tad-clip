"""Windows: no admin rights, no package manager — the kit downloads its own copies into <home>/bin/.

These are the same files GitHub Actions downloads on windows-latest, so what CI tests is what users get.
"""
from __future__ import annotations

import http.client
import json
import re
import shutil
import urllib.request
import zipfile
from pathlib import Path

from engine import kit
from engine import install as inst

# BtbN builds have libass. The master nightly hung ffmpeg in an audio mix (digital silence + limiter) on windows-latest
# 2026-10-11 while the n8.1 release-branch build of the same day passed → use the oldest release branch BtbN still
# builds (most mature, always ≥ 5.1), the pinned n8.1 when the GitHub API is unreachable, master only as a last resort.
BTBN_RELEASE_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
FFMPEG_RELEASE_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-gpl-8.1.zip"
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
    """Verified download into <home>/bin (resumes inside this run, never glues onto a .part from an older run:
    these are "latest" files that may have changed since)."""
    return inst.download_resumable(url, dest, label or url, tries=3, resume_existing=False)


def _fetch_json(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def extract(zip_path, wanted: dict) -> None:
    """Copy members whose file name (any folder) is a key of `wanted` to wanted[name]."""
    keys = {k.lower(): Path(v) for k, v in wanted.items()}
    try:
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                base = info.filename.rsplit("/", 1)[-1].lower()
                if base in keys and not info.is_dir():
                    with z.open(info) as src, open(keys[base], "wb") as dst:
                        shutil.copyfileobj(src, dst)
    except (zipfile.BadZipFile, EOFError) as e:
        raise kit.KitError(f"ไฟล์ zip ที่โหลดมาเสีย ({Path(zip_path).name}: {e}) — มักเกิดจากเน็ตหลุดหรือเว็บตอบกลับเป็นหน้า error "
                           f"รันใหม่ได้เลย: {inst.setup_command()}") from e
    missing = [k for k, v in keys.items() if not v.exists()]
    if missing:
        raise kit.KitError(f"ในไฟล์ {Path(zip_path).name} ไม่มี {', '.join(missing)} — ไฟล์ที่โหลดมาอาจไม่ครบ "
                           f"รันใหม่: {inst.setup_command()}")


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


def ffmpeg_urls() -> list:
    """Download candidates in order: oldest maintained release branch (from the API), pinned n8.1, master."""
    urls = []
    try:
        rel = []
        for a in _fetch_json(BTBN_RELEASE_API).get("assets", []):
            m = re.fullmatch(r"ffmpeg-n(\d+)\.(\d+)-latest-win64-gpl-\1\.\2\.zip", a.get("name", ""))
            if m:
                rel.append(((int(m.group(1)), int(m.group(2))), a["browser_download_url"]))
        urls += [u for _, u in sorted(rel)[:1]]
    except (OSError, ValueError, KeyError, http.client.HTTPException):
        pass   # API unreachable or rate-limited → the pinned build
    out = []
    for u in urls + [FFMPEG_RELEASE_URL, FFMPEG_URL]:
        if u not in out:
            out.append(u)
    return out


AV_HINT = "โปรแกรมกันไวรัสอาจกักไฟล์ไว้ — เปิด Windows Security ดูประวัติการป้องกันแล้วกดอนุญาตไฟล์นี้"


def _runs(exe: Path, *args: str) -> bool:
    code, _ = inst._out([exe, *(args or ("--version",))], timeout=90)
    return code == 0


def ensure_ffmpeg() -> tuple:
    b = bin_dir()
    ff, fp = b / "ffmpeg.exe", b / "ffprobe.exe"
    if ff.exists() and fp.exists() and inst.has_libass(str(ff)):
        print(f"  ✓ ffmpeg มีอยู่แล้ว ({ff})", flush=True)
        return str(ff), str(fp)
    last = None
    for url in ffmpeg_urls():
        try:
            _from_zip(url, "ffmpeg (ไฟล์ zip ~200 MB)", {"ffmpeg.exe": ff, "ffprobe.exe": fp})
        except kit.KitError as e:
            last = e
            print(f"  ⚠️ {e} — ลองชุดถัดไป", flush=True)
            continue
        if inst.has_libass(str(ff)):
            return str(ff), str(fp)
        last = kit.KitError(f"ffmpeg.exe ที่โหลดมาเปิดไม่ได้ — {AV_HINT} แล้วรันใหม่: {inst.setup_command()}")
        print(f"  ⚠️ {last}", flush=True)
    raise last


def ensure_node() -> str:
    node = bin_dir() / "node.exe"
    if node.exists() and inst.node_major(str(node)) >= inst.NODE_MIN:
        print(f"  ✓ Node.js มีอยู่แล้ว ({node})", flush=True)
        return str(node)
    try:
        index = _fetch_json(NODE_INDEX_URL)
    except (OSError, ValueError, http.client.HTTPException) as e:   # URLError (offline, DNS, proxy) is an OSError
        raise kit.KitError(f"ต่อ nodejs.org ไม่ได้ ({e}) — เช็คอินเทอร์เน็ต แล้วรันใหม่: {inst.setup_command()}") from e
    v, url = pick_node(index)
    _from_zip(url, f"Node.js {v} (~35 MB)", {"node.exe": node})
    if inst.node_major(str(node)) < inst.NODE_MIN:
        raise kit.KitError(f"node.exe ที่โหลดมาเปิดไม่ได้ — {AV_HINT} แล้วรันใหม่: {inst.setup_command()}")
    return str(node)


def _drop(exe: Path) -> None:
    try:
        exe.unlink()
    except OSError:
        pass   # locked or quarantined: the download below replaces it or reports why


def ensure_uv() -> str:
    uv = bin_dir() / "uv.exe"
    if uv.exists() and _runs(uv):
        print(f"  ✓ uv มีอยู่แล้ว ({uv})", flush=True)
        return str(uv)
    _drop(uv)
    _from_zip(UV_URL, "uv (~20 MB)", {"uv.exe": uv})
    if not _runs(uv):
        raise kit.KitError(f"uv.exe ที่โหลดมาเปิดไม่ได้ — {AV_HINT} แล้วรันใหม่: {inst.setup_command()}")
    return str(uv)


def ensure_ytdlp() -> str:
    """yt-dlp only fetches web clips: if antivirus blocks it the rest of the kit still works → warn, don't stop."""
    exe = bin_dir() / "yt-dlp.exe"
    if exe.exists() and _runs(exe):
        print(f"  ✓ yt-dlp มีอยู่แล้ว ({exe})", flush=True)
        return str(exe)
    _drop(exe)
    download(YTDLP_URL, exe, "yt-dlp (~18 MB)")
    if not _runs(exe):
        print(f"  ⚠️ yt-dlp.exe เปิดไม่ได้ ({AV_HINT}) — ตัดคลิปได้ปกติ แค่โหลดคลิปจากเว็บไม่ได้จนกว่าจะอนุญาต", flush=True)
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
