"""ตรวจเครื่องก่อนตัดคลิป — and the final QC of a rendered clip.

  python engine/doctor.py                    # full check, Thai table + one fix command per problem
  python engine/doctor.py --quick            # fast: files exist (used before every "ตัดคลิป")
  python engine/doctor.py --json             # same data for machines (schema version 1)
  python engine/doctor.py --qc final.mp4 [--expect-duration 14.2] [--source my-cut.mp4]
                                   # no --expect-duration → roughcut.mp4 next to it (or --source) gives the length
  python engine/doctor.py --probe "<video>" [--json]                # what is this clip + is there disk space for it

Standard library only and Python 3.9+, so it runs before the venv exists. Exit 0 = nothing red.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import kit  # noqa: E402
from engine import install as inst  # noqa: E402

CHROME_TIMEOUT = 20
DISK_MIN_GB = 15
VENV_MODULES = ("faster_whisper", "pythainlp", "numpy", "PIL")
QC_SIZES = ((1080, 1920), (1440, 2560))
QC_DURATION_TOL = 0.25
QC_BLACK_MAX = 0.5
QC_PEAK_MAX = -0.5
ICON = {"ok": "✅", "fail": "❌", "warn": "⚠️", "info": "ℹ️"}
GB = 1024 ** 3


def _os() -> str:
    return inst.os_name()


def item(id_: str, name: str, status: str, detail: str = "", fix: str = "") -> dict:
    return {"id": id_, "name": name, "status": status, "detail": detail, "fix": fix}


def _cfg() -> dict:
    """The merged config, or the defaults when config.json is broken (check_setup reports that one)."""
    try:
        return kit.load_config()
    except inst.CONFIG_ERRORS:
        return json.loads(json.dumps(kit.DEFAULTS))


def _tool(name: str):
    try:
        return kit.tool(name), None
    except kit.KitError:
        return None, f"ไม่พบ {name} ในเครื่อง"
    except inst.CONFIG_ERRORS:
        return None, "อ่านไฟล์ตั้งค่าไม่ได้"


def _fix() -> str:
    """Every missing or broken tool is fixed by re-running setup: it installs only what is missing and records the
    paths. (A bare `brew …` would fail right after a Homebrew install, while brew is not on PATH yet.)"""
    return inst.setup_command()


AV_HINT = " (โปรแกรมกันไวรัสอาจกักไฟล์ไว้ — เปิด Windows Security ดูประวัติการป้องกันแล้วกดอนุญาต)"


def _no_answer(exe: str) -> str:
    return f"เรียก {exe} แล้วไม่ตอบ" + (AV_HINT if _os() == "windows" else "")


# ---------------------------------------------------------------- checks
def check_setup() -> dict:
    name = "การติดตั้ง"
    f = kit.home() / "config.json"
    tools = {}
    if f.exists():
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            raw = None
        if not isinstance(raw, dict):   # not JSON, or JSON that is not an object ([], "x", 42)
            return item("setup", name, "fail", f"ไฟล์ตั้งค่าเสีย อ่านไม่ได้: {f} — รัน setup แล้วมันจะเก็บไฟล์เดิมไว้ให้แล้วเริ่มใหม่",
                        inst.setup_command())
        tools = raw.get("tools") or {}
    if not tools:
        return item("setup", name, "fail", f"ยังไม่ได้ติดตั้ง (ไม่มี {f})",
                    f'พิมพ์ "ติดตั้ง tad-clip" หรือรัน: {inst.setup_command()}')
    return item("setup", name, "ok", str(kit.home()))


def check_gitbash() -> dict:
    name = "Git Bash (ที่ Claude ใช้รันคำสั่ง)"
    if _os() != "windows":
        return item("gitbash", name, "info", "ไม่ต้องใช้บน macOS")
    exe = inst.git_bash()
    if not exe:
        return item("gitbash", name, "fail",
                    "ไม่พบ Git for Windows (bash.exe) — Claude Code บน Windows ใช้ตัวนี้รันคำสั่ง ถ้าไม่มี คำสั่งของ kit จะรันไม่ได้",
                    f"ติดตั้ง Git for Windows ฟรีจาก {inst.GIT_BASH_URL} (กด Next ตามค่าเริ่มต้นทุกหน้า) "
                    "แล้วปิดแอป Claude เปิดใหม่ แล้วพิมพ์ \"ติดตั้ง tad-clip\" อีกครั้ง")
    return item("gitbash", name, "ok", exe)


def check_ffmpeg(quick: bool = False) -> dict:
    name = "ffmpeg (ตัดต่อ + ฝังซับ)"
    fix = _fix()
    ff, why = _tool("ffmpeg")
    if not ff:
        return item("ffmpeg", name, "fail", why, fix)
    if quick:
        return item("ffmpeg", name, "ok", ff)
    filters = inst.ffmpeg_filters(ff)
    if not filters:
        return item("ffmpeg", name, "fail", _no_answer(ff), fix)
    label, new_enough = inst.ffmpeg_version(ff)
    if not new_enough:
        need = ".".join(map(str, inst.FFMPEG_MIN))
        return item("ffmpeg", name, "fail", f"ffmpeg {label} เก่าเกินไป ต้องการ {need} ขึ้นไป — {ff}", fix)
    missing = [f for f in inst.LIBASS_FILTERS if f not in filters]
    if missing:
        return item("ffmpeg", name, "fail",
                    f"ffmpeg ตัวนี้ฝังซับไม่ได้ (ไม่มี libass: ขาดฟิลเตอร์ {' '.join(missing)}) — {ff}", fix)
    return item("ffmpeg", name, "ok", f"{label} · {ff}")


def check_ffprobe(quick: bool = False) -> dict:
    name = "ffprobe (อ่านข้อมูลไฟล์)"
    fix = _fix()
    fp, why = _tool("ffprobe")
    if not fp:
        return item("ffprobe", name, "fail", why, fix)
    if not quick:
        code, _ = inst._out([fp, "-version"], timeout=30)
        if code != 0:
            return item("ffprobe", name, "fail", _no_answer(fp), fix)
    return item("ffprobe", name, "ok", fp)


def check_node(quick: bool = False) -> dict:
    name = f"Node.js {inst.NODE_MIN}+"
    fix = _fix()
    node, why = _tool("node")
    if not node:
        return item("node", name, "fail", why, fix)
    if quick:
        return item("node", name, "ok", node)
    v = inst.node_version(node)
    if not v:
        return item("node", name, "fail", _no_answer(node), fix)
    if int(v[1:].split(".")[0]) < inst.NODE_MIN:
        return item("node", name, "fail", f"ต้องการ Node {inst.NODE_MIN} ขึ้นไป (เครื่องนี้เป็น {v} ที่ {node})", fix)
    return item("node", name, "ok", f"{v} · {node}")


def check_python(quick: bool = False) -> dict:
    # on a whisper.cpp Mac the venv still holds pythainlp etc. (and faster-whisper only as the fallback)
    name = "Python + แพ็กเกจของ kit" if inst.asr_backend(_cfg()) == "whispercpp" else "Python + ตัวถอดเสียง (faster-whisper)"
    py = kit.venv_python()
    if not py.exists():
        return item("python", name, "fail", f"ยังไม่มี venv ที่ {py.parent.parent}", inst.setup_command())
    if quick:
        return item("python", name, "ok", str(py))
    probe = ("import importlib, json\nbad = {}\n"
             f"for m in {list(VENV_MODULES)!r}:\n"
             "    try:\n        importlib.import_module(m)\n"
             "    except Exception as e:\n        bad[m] = f'{type(e).__name__}: {e}'[:200]\n"
             "print(json.dumps(bad))")
    code, out = inst._out([py, "-c", probe], timeout=180)
    try:
        bad = json.loads(out.strip().splitlines()[-1]) if code == 0 else None
    except (ValueError, IndexError):
        bad = None
    if bad is None:
        return item("python", name, "fail", f"venv เปิดไม่ได้: {out.strip()[-200:]}", inst.setup_command())
    if bad:
        detail = " · ".join(f"{m} ({e})" for m, e in bad.items())
        return item("python", name, "fail", f"import ไม่ผ่าน: {detail}", inst.setup_command())
    return item("python", name, "ok", f"{', '.join(VENV_MODULES)} · {py}")


def check_asr(quick: bool = False) -> dict:
    cfg = _cfg()
    backend = inst.asr_backend(cfg)
    name = "ตัวถอดเสียง"
    if backend != "whispercpp":
        device = cfg["whisper"].get("device") or "auto"
        return item("asr", name, "ok", f"faster-whisper · device {device}")
    fix = _fix()
    exe, why = _tool("whisper-cli")
    if not exe:
        return item("asr", name, "fail", f"{why} (whisper.cpp)", fix)
    if not quick:
        code, out = inst._out([exe, "--help"], timeout=60)
        if code != 0:
            return item("asr", name, "fail", f"whisper-cli เรียกแล้วไม่ตอบ (exit {code}) — {exe}", fix)
    return item("asr", name, "ok", f"whisper.cpp (ชิปกราฟิกของ Mac) · {exe}")


def _model_ready(name: str, model: str, where: str) -> dict:
    """The model is installed. medium works, but it mishears Thai much more than large-v3 and differently on different CPUs."""
    if model == "medium":
        return item("model", name, "warn",
                    f"{where} · ภาษาไทยแม่นน้อยกว่า large-v3 ชัดเจน (ซับจะมีคำพลาดมาก แต่ละเครื่องไม่เท่ากัน) — ใช้เป็นทางสุดท้ายเท่านั้น",
                    inst.setup_command("--model large-v3"))
    return item("model", name, "ok", where)


def check_model() -> dict:
    model = _cfg()["whisper"]["model"]
    if inst.asr_backend(_cfg()) == "whispercpp":
        name = f"โมเดลถอดเสียง {model} (whisper.cpp)"
        if model not in inst.MODELS:
            return item("model", name, "fail",
                        f"โมเดล {model} ไม่อยู่ในชุดที่รองรับ ({' · '.join(inst.MODELS)})", inst.setup_command())
        ok, why = inst.ggml_state(model)
        if not ok:
            return item("model", name, "fail", why, inst.setup_command(f"--model {model}"))
        return _model_ready(name, model, why)
    name = f"โมเดลถอดเสียง {model}"
    if model not in inst.MODELS:   # setup falls back to the default model by itself
        return item("model", name, "fail",
                    f"โมเดล {model} ไม่อยู่ในชุดที่รองรับ ({' · '.join(inst.MODELS)})", inst.setup_command())
    found = inst.model_dir(model, kit.models_dir())
    if not found:
        mb = inst.WHISPER_MB.get(model)
        size = f" (~{mb / 1024:.1f} GB)" if mb else ""
        return item("model", name, "fail", f"ยังไม่ได้โหลดโมเดล {model}{size} ลง {kit.models_dir()}",
                    inst.setup_command(f"--model {model}"))
    return _model_ready(name, model, str(found))


def check_chrome(quick: bool = False) -> dict:
    name = "เบราว์เซอร์วาดการ์ด (Chrome/Edge)"
    fix = inst.setup_command()
    cands = inst.chrome_candidates()
    if not cands:
        return item("chrome", name, "fail", "ไม่พบ Chrome, Edge หรือ chrome-headless-shell", fix)
    browser = cands[0]  # the visual engine uses the first one, so that is the one to test
    if quick:
        return item("chrome", name, "ok", browser)
    ok, why = inst.chrome_ready(browser, CHROME_TIMEOUT)
    if not ok:
        return item("chrome", name, "fail", f"{Path(browser).name} {why} — {browser}", fix)
    return item("chrome", name, "ok", browser)


def check_ytdlp() -> dict:
    name = "yt-dlp (โหลดคลิปจากเว็บ)"
    fix = _fix()
    exe, why = _tool("yt-dlp")
    if not exe:
        return item("ytdlp", name, "fail", why, fix)
    code, out = inst._out([exe, "--version"], timeout=90)
    if code != 0:
        return item("ytdlp", name, "fail", _no_answer(exe), fix)
    return item("ytdlp", name, "ok", f"{out.strip().splitlines()[-1]} · {exe}")


def _venv_has(module: str) -> bool:
    py = kit.venv_python()
    if not py.exists():
        return False
    code, _ = inst._out([py, "-c", f"import importlib.util as u, sys; sys.exit(u.find_spec({module!r}) is None)"], timeout=60)
    return code == 0


def check_cutout() -> dict:
    name = "ตัดพื้นหลังคน (cutout)"
    engine = _cfg()["cutout"]["engine"]
    osn = _os()
    if engine == "auto":
        engine = "vision" if osn == "mac" else "tb" if _venv_has("transparent_background") else "off"
    if engine == "off":
        return item("cutout", name, "info", "ปิดอยู่ (ไม่บังคับ) — ช็อตแบบ cutout จะใช้แบบ split แทน",
                    inst.setup_command("--with-cutout"))
    if engine == "vision":
        if osn != "mac":
            return item("cutout", name, "warn", "Apple Vision ใช้ได้เฉพาะ Mac", inst.setup_command("--with-cutout"))
        sc = inst.swiftc()
        if not sc:
            return item("cutout", name, "fail",
                        "ยังไม่มี Xcode Command Line Tools (ใช้ตัดพื้นหลังคนด้วย Apple Vision) — "
                        "สั่งแล้วจะมีหน้าต่างให้กด \"ติดตั้ง\" รอราว 5–10 นาที",
                        "xcode-select --install")
        return item("cutout", name, "ok", f"Apple Vision · xcrun swiftc ({sc})")
    if not _venv_has("transparent_background"):
        return item("cutout", name, "warn", "ตั้งไว้เป็น transparent-background แต่ยังไม่ได้ติดตั้ง",
                    inst.setup_command("--with-cutout"))
    return item("cutout", name, "ok", "transparent-background")


def check_disk() -> dict:
    name = "พื้นที่ว่าง"
    free = inst.free_bytes(kit.home()) / GB
    if free < DISK_MIN_GB:
        return item("disk", name, "warn", f"เหลือ {free:.1f} GB (แนะนำอย่างน้อย {DISK_MIN_GB} GB) — ไฟล์งานระหว่างตัดใช้ที่เยอะ",
                    "ลบไฟล์ที่ไม่ใช้ หรือย้ายคลิปเก่าออกไปไว้ไดรฟ์อื่น")
    return item("disk", name, "ok", f"เหลือ {free:.0f} GB")


def check_ram() -> dict:
    name = "หน่วยความจำ (RAM)"
    ram = inst.ram_bytes()
    if ram is None:
        return item("ram", name, "info", "อ่านขนาด RAM ไม่ได้")
    model = _cfg()["whisper"]["model"]
    if inst.low_ram(ram) and model.startswith("large"):
        return item("ram", name, "warn",
                    f"RAM {ram / GB:.0f} GB (น้อยกว่า 8 GB) — โมเดล {model} อาจช้า ปิดโปรแกรมอื่นก่อนถอดเสียง "
                    "ถ้ารันไม่ไหวจริงๆ ค่อยใช้ --model medium เป็นทางสุดท้าย (ฟังภาษาไทยพลาดมาก)",
                    "ปิดโปรแกรมอื่นก่อนถอดเสียง")
    return item("ram", name, "ok", f"{ram / GB:.0f} GB")


def collect(quick: bool = False) -> list:
    if quick:
        return [check_setup(), check_gitbash(), check_ffmpeg(True), check_ffprobe(True), check_node(True),
                check_python(True), check_asr(True), check_model(), check_chrome(True)]
    return [check_setup(), check_gitbash(), check_ffmpeg(), check_ffprobe(), check_node(), check_python(), check_asr(),
            check_model(), check_chrome(), check_ytdlp(), check_cutout(), check_disk(), check_ram()]


# ---------------------------------------------------------------- QC of a finished clip
def _scan(ff: str, video: str, has_audio: bool) -> tuple:
    """One decode pass: black runs (blackdetect) + true peak (ebur128)."""
    cmd = [ff, "-hide_banner", "-nostats", "-i", video, "-map", "0:v:0", "-vf", f"blackdetect=d={QC_BLACK_MAX}:pix_th=0.10"]
    if has_audio:
        cmd += ["-map", "0:a:0", "-af", "ebur128=peak=true"]
    log = kit.run(cmd + ["-f", "null", "-"]).stderr
    blacks = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+)\s+black_end:([\d.]+)", log)]
    peak = None
    tail = log.rsplit("True peak:", 1)
    if len(tail) == 2:
        m = re.search(r"Peak:\s+(-?inf|-?[\d.]+)", tail[1])
        peak = float(m.group(1)) if m else None
    return blacks, peak


def _ffprobe(video: str) -> dict:
    return json.loads(kit.run([kit.tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format",
                               "-show_streams", video]).stdout)


def _duration(info: dict) -> float:
    return float(info.get("format", {}).get("duration") or 0)


def display_size(stream: dict) -> tuple:
    """(width, height) as played: a ±90° rotation (phone clips) swaps them."""
    w, h = int(stream.get("width", 0)), int(stream.get("height", 0))
    rot = 0
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    tag = (stream.get("tags") or {}).get("rotate")
    if not rot and tag:
        rot = int(float(tag))
    return (h, w) if abs(rot) % 180 == 90 else (w, h)


def _video_stream(info: dict):
    return next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)


def _work_dirs(video: str) -> tuple:
    v = Path(video).resolve()
    return v.parent, v.parent.parent


def roughcut_beside(video: str):
    """The clip's work folder keeps roughcut.mp4 next to final.mp4 (out/… renders sit one level down)."""
    v = Path(video).resolve()
    for d in (v.parent, v.parent.parent):
        rc = d / "roughcut.mp4"
        if rc.is_file() and rc.resolve() != v:
            return rc
    return None


def qc(video: str, expect_duration: float = None, source: str = None) -> list:
    """source = the clip this render was made from (default: roughcut.mp4 beside it). It gives the expected length,
    and — when the work folder has no plan.json (subtitles only, nothing re-laid-out) — the expected picture size."""
    ff = kit.tool("ffmpeg")
    info = _ffprobe(video)
    streams = info.get("streams", [])
    v = _video_stream(info)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    dur = _duration(info)
    out = []

    src = Path(source) if source else roughcut_beside(video)
    if source and not src.is_file():
        raise kit.KitError(f"ไม่พบไฟล์ต้นทาง {source}")
    src_info = _ffprobe(str(src)) if src else None
    has_plan = any((d / "plan.json").is_file() for d in _work_dirs(video))

    ref = ""
    if expect_duration is None and src_info:
        expect_duration, ref = _duration(src_info), f" เทียบกับ {src.name}"
    if expect_duration is None:
        out.append(item("duration", "ความยาว", "info", f"{dur:.2f} วิ (ไม่มี roughcut.mp4 ให้เทียบ)"))
    elif abs(dur - expect_duration) <= QC_DURATION_TOL:
        out.append(item("duration", "ความยาว", "ok", f"{dur:.2f} วิ (ควรได้ {expect_duration:.2f}{ref})"))
    else:
        out.append(item("duration", "ความยาว", "fail",
                        f"{dur:.2f} วิ แต่ควรได้ {expect_duration:.2f} วิ{ref} (คลาด {dur - expect_duration:+.2f})",
                        "เช็คว่า plan.json ครอบทั้งคลิป แล้วเรนเดอร์ใหม่"))

    presets = " หรือ ".join(f"{a}×{b}" for a, b in QC_SIZES)
    src_v = _video_stream(src_info) if src_info else None
    if not v:
        out.append(item("size", "ขนาดภาพ", "fail", "ไม่มีภาพในไฟล์", "เรนเดอร์ใหม่"))
    elif src_v and not has_plan:   # subtitles only: the picture keeps the source size (horizontal, 4K …)
        w, h = display_size(v)
        sw, sh = display_size(src_v)
        ok = (w, h) == (sw, sh) or (w, h) in QC_SIZES
        rule = f"ไม่มี plan.json จึงใช้ขนาดคลิปต้นทาง {src.name} ({sw}×{sh}) หรือ {presets}"
        out.append(item("size", "ขนาดภาพ", "ok" if ok else "fail", f"{w}×{h} · {rule}",
                        "" if ok else "ขนาดไม่ตรงต้นทาง — เรนเดอร์ใหม่จากคลิปต้นทาง"))
    else:
        w, h = display_size(v)
        ok = (w, h) in QC_SIZES
        why = "มี plan.json (ประกอบภาพใหม่ทั้งคลิป)" if has_plan else "ไม่มีคลิปต้นทางให้เทียบ"
        out.append(item("size", "ขนาดภาพ", "ok" if ok else "fail", f"{w}×{h} · {why} จึงต้องเป็น {presets}",
                        "" if ok else "เช็ค output.preset ใน config แล้วเรนเดอร์ใหม่"))

    out.append(item("audio", "มีเสียง", "ok", "มี") if has_audio
               else item("audio", "มีเสียง", "fail", "ไฟล์นี้ไม่มีเสียง", "เช็คว่าคลิปต้นทางมีเสียง แล้วเรนเดอร์ใหม่"))

    blacks, peak = _scan(ff, video, has_audio) if v else ([], None)
    if blacks:
        spans = ", ".join(f"{a:.2f}–{b:.2f}" for a, b in blacks[:5])
        out.append(item("black", "ไม่มีจอดำ", "fail", f"จอดำเกิน {QC_BLACK_MAX:g} วิ ที่ {spans} วิ",
                        "ดูช็อตช่วงนั้นใน plan.json (ภาพหาย/ไฟล์ภาพไม่เจอ) แล้วเรนเดอร์ใหม่"))
    else:
        out.append(item("black", "ไม่มีจอดำ", "ok", f"ไม่มีช่วงจอดำเกิน {QC_BLACK_MAX:g} วิ"))

    if not has_audio or peak is None:
        out.append(item("peak", "เสียงไม่พีค", "fail" if has_audio else "info",
                        "วัดเสียงไม่ได้" if has_audio else "ไม่มีเสียงให้วัด"))
    elif peak <= QC_PEAK_MAX:
        out.append(item("peak", "เสียงไม่พีค", "ok", f"true peak {peak:.1f} dBTP"))
    else:
        out.append(item("peak", "เสียงไม่พีค", "fail",
                        f"true peak {peak:.1f} dBTP (เกิน {QC_PEAK_MAX:g} dBTP) — เสียงอาจแตกตอนแพลตฟอร์มแปลงไฟล์",
                        "รัน engine.sfx_mix อีกครั้ง (มี limiter) — ถ้าคลิปไม่มีเสียงเอฟเฟกต์ ใช้ sfx.json ที่เป็น [] ก็ได้"))
    return out


# ---------------------------------------------------------------- probe: what is this clip, is there room for it
HDR_TRANSFERS = ("smpte2084", "arib-std-b67")   # PQ (HDR10 / Dolby Vision) and HLG (iPhone)


def _rate(r: str) -> float:
    try:
        n, d = (r or "0/1").split("/")
        return float(n) / float(d) if float(d) else 0.0
    except ValueError:
        return 0.0


def stream_traits(s: dict) -> dict:
    """fps (average), variable frame rate (average ≠ nominal by > 1 %), HDR (PQ/HLG transfer)."""
    avg, nominal = _rate(s.get("avg_frame_rate")), _rate(s.get("r_frame_rate"))
    vfr = bool(avg and nominal and abs(avg - nominal) / nominal > 0.01)
    return {"fps_avg": round(avg, 3), "fps_variable": vfr, "hdr": s.get("color_transfer") in HDR_TRANSFERS}


def probe(video: str) -> dict:
    f = Path(video)
    if not f.is_file():
        raise kit.KitError(f"ไม่พบไฟล์ {video}")
    info = _ffprobe(str(f))
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        raise kit.KitError(f"ไฟล์ {f.name} ไม่มีภาพวิดีโอ")
    size = f.stat().st_size
    need = 3 * size + 5 * GB
    free_video, free_home = inst.free_bytes(f.resolve().parent), inst.free_bytes(kit.home())
    return {"file": str(f), "duration": round(_duration(info), 3), "width": int(v.get("width", 0)),
            "height": int(v.get("height", 0)), **stream_traits(v), "codec": v.get("codec_name", "?"),
            "size_bytes": size, "free_video_bytes": free_video, "free_home_bytes": free_home,
            "need_bytes": need, "space_ok": free_video >= need}


def _gb(n: int) -> str:
    return f"{n / GB:.1f} GB" if n >= GB / 10 else f"{n / 1024 ** 2:.1f} MB"


def render_probe(p: dict) -> str:
    fps = f"{p['fps_avg']:g} fps " + ("(เฟรมเรตไม่คงที่ — จะทำให้คงที่ตอนตัด)" if p["fps_variable"] else "(คงที่)")
    hdr = "HDR: ใช่ (จะแปลงเป็นสีปกติ SDR ให้ตอนตัด)" if p["hdr"] else "HDR: ไม่ใช่"
    lines = [f"คลิป: {Path(p['file']).name}",
             f"- ยาว {p['duration']:.1f} วิ · {p['width']}×{p['height']} · {fps} · {p['codec'].upper()} · {hdr} · ไฟล์ {_gb(p['size_bytes'])}",
             f"- ที่ว่าง: ไดรฟ์ของคลิป {_gb(p['free_video_bytes'])} · ที่เก็บ kit {_gb(p['free_home_bytes'])} "
             f"(คลิปนี้ควรมีที่ว่างอย่างน้อย {_gb(p['need_bytes'])})"]
    if not p["space_ok"]:
        lines.append(f"⚠️ ที่ว่างบนไดรฟ์ของคลิปน้อยกว่าที่ต้องใช้ ({_gb(p['free_video_bytes'])} จาก {_gb(p['need_bytes'])}) — "
                     "ไฟล์ระหว่างทำงานอาจเขียนไม่ครบ ลบไฟล์ที่ไม่ใช้ หรือย้ายคลิปไปไดรฟ์ที่ว่างมากกว่า")
    return "\n".join(lines)


# ---------------------------------------------------------------- output
def _cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def render(title: str, checks: list) -> str:
    lines = [title, "", "| สถานะ | รายการ | ผล |", "|---|---|---|"]
    lines += [f"| {ICON[c['status']]} | {_cell(c['name'])} | {_cell(c['detail'])} |" for c in checks]
    fixes = [c for c in checks if c["status"] in ("fail", "warn") and c["fix"]]
    if fixes:
        lines += ["", "วิธีแก้"]
        lines += [f"- {c['name']}: `{c['fix']}`" for c in fixes]
    bad = sum(c["status"] == "fail" for c in checks)
    lines += ["", "ผล: พร้อมใช้ ✅" if not bad else f"ผล: ยังไม่พร้อม — แก้ {bad} ข้อที่เป็น ❌"]
    return "\n".join(lines)


def main(argv=None) -> int:
    inst.utf8_stdio()
    ap = argparse.ArgumentParser(description="ตรวจเครื่อง tad-clip")
    ap.add_argument("--quick", action="store_true", help="ตรวจเร็ว (แค่มีไฟล์ครบไหม)")
    ap.add_argument("--json", action="store_true", help="ผลลัพธ์เป็น JSON")
    ap.add_argument("--qc", metavar="VIDEO", help="ตรวจคลิปที่เรนเดอร์เสร็จแล้ว")
    ap.add_argument("--expect-duration", type=float, metavar="S", help="ความยาวที่ควรได้ (วินาที) สำหรับ --qc")
    ap.add_argument("--source", metavar="VIDEO", help="คลิปต้นทางของไฟล์ที่ตรวจ (ค่าเริ่มต้น roughcut.mp4 ข้างๆ) สำหรับ --qc")
    ap.add_argument("--probe", metavar="VIDEO", help="ดูข้อมูลคลิป (ยาว ขนาด fps HDR) และที่ว่างในเครื่อง")
    a = ap.parse_args(argv)

    if a.probe:
        try:
            p = probe(a.probe)
        except (kit.KitError, ValueError) as e:
            msg = str(e).splitlines()[0]
            print(json.dumps({"version": 1, "mode": "probe", "ok": False, "error": msg}, ensure_ascii=False)
                  if a.json else f"❌ {msg}")
            return 1
        print(json.dumps({"version": 1, "mode": "probe", "ok": True, "probe": p}, ensure_ascii=False, indent=2)
              if a.json else render_probe(p))
        return 0

    if a.qc:
        mode, title = "qc", f"ตรวจคลิป {a.qc}"
        try:
            checks = qc(a.qc, a.expect_duration, a.source)
        except (kit.KitError, ValueError) as e:
            checks = [item("file", "อ่านไฟล์", "fail", str(e).splitlines()[0], "เช็คว่าไฟล์มีอยู่จริงและเล่นได้")]
    else:
        mode = "quick" if a.quick else "full"
        title = f"ตรวจเครื่อง tad-clip · {inst.platform_label()} · ที่เก็บ {kit.home()}"
        checks = collect(a.quick)

    ok = not any(c["status"] == "fail" for c in checks)
    if a.json:
        print(json.dumps({"version": 1, "ok": ok, "mode": mode, "os": _os(), "home": str(kit.home()),
                          "kit": str(kit.KIT_ROOT), "checks": checks}, ensure_ascii=False, indent=2))
    else:
        print(render(title, checks))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
