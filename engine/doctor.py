"""ตรวจเครื่องก่อนตัดคลิป — and the final QC of a rendered clip.

  python engine/doctor.py                    # full check, Thai table + one fix command per problem
  python engine/doctor.py --quick            # fast: files exist (used before every "ตัดคลิป")
  python engine/doctor.py --json             # same data for machines (schema version 1)
  python engine/doctor.py --qc final.mp4 [--expect-duration 14.2]

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
    except ValueError:
        return json.loads(json.dumps(kit.DEFAULTS))


def _tool(name: str):
    try:
        return kit.tool(name), None
    except kit.KitError:
        return None, f"ไม่พบ {name} ในเครื่อง"
    except ValueError:
        return None, "อ่านไฟล์ตั้งค่าไม่ได้"


def _brew_fix(formula: str) -> str:
    return f"brew install {formula} && {inst.setup_command()}"


# ---------------------------------------------------------------- checks
def check_setup() -> dict:
    name = "การติดตั้ง"
    f = kit.home() / "config.json"
    tools = {}
    if f.exists():
        try:
            tools = json.loads(f.read_text(encoding="utf-8")).get("tools") or {}
        except ValueError:
            return item("setup", name, "fail", f"ไฟล์ตั้งค่าเสีย อ่านไม่ได้: {f}", inst.setup_command())
    if not tools:
        return item("setup", name, "fail", f"ยังไม่ได้ติดตั้ง (ไม่มี {f})",
                    f'พิมพ์ "ติดตั้ง tad-clip" หรือรัน: {inst.setup_command()}')
    return item("setup", name, "ok", str(kit.home()))


def check_ffmpeg(quick: bool = False) -> dict:
    name = "ffmpeg (ตัดต่อ + ฝังซับ)"
    fix = _brew_fix("ffmpeg-full") if _os() == "mac" else inst.setup_command()
    ff, why = _tool("ffmpeg")
    if not ff:
        return item("ffmpeg", name, "fail", why, fix)
    if quick:
        return item("ffmpeg", name, "ok", ff)
    filters = inst.ffmpeg_filters(ff)
    if not filters:
        return item("ffmpeg", name, "fail", f"เรียก {ff} แล้วไม่ตอบ", fix)
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
    fix = _brew_fix("ffmpeg-full") if _os() == "mac" else inst.setup_command()
    fp, why = _tool("ffprobe")
    if not fp:
        return item("ffprobe", name, "fail", why, fix)
    if not quick:
        code, _ = inst._out([fp, "-version"], timeout=30)
        if code != 0:
            return item("ffprobe", name, "fail", f"เรียก {fp} แล้วไม่ตอบ", fix)
    return item("ffprobe", name, "ok", fp)


def check_node(quick: bool = False) -> dict:
    name = f"Node.js {inst.NODE_MIN}+"
    fix = _brew_fix("node") if _os() == "mac" else inst.setup_command()
    node, why = _tool("node")
    if not node:
        return item("node", name, "fail", why, fix)
    if quick:
        return item("node", name, "ok", node)
    v = inst.node_version(node)
    if not v:
        return item("node", name, "fail", f"เรียก {node} แล้วไม่ตอบ", fix)
    if int(v[1:].split(".")[0]) < inst.NODE_MIN:
        return item("node", name, "fail", f"ต้องการ Node {inst.NODE_MIN} ขึ้นไป (เครื่องนี้เป็น {v} ที่ {node})", fix)
    return item("node", name, "ok", f"{v} · {node}")


def check_python(quick: bool = False) -> dict:
    name = "Python + ตัวถอดเสียง (faster-whisper)"
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


def check_model() -> dict:
    model = _cfg()["whisper"]["model"]
    name = f"โมเดลถอดเสียง {model}"
    found = inst.model_dir(model, kit.models_dir())
    if not found:
        mb = inst.WHISPER_MB.get(model)
        size = f" (~{mb / 1024:.1f} GB)" if mb else ""
        return item("model", name, "fail", f"ยังไม่ได้โหลดโมเดล {model}{size} ลง {kit.models_dir()}",
                    inst.setup_command(f"--model {model}"))
    return item("model", name, "ok", str(found))


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
    fix = _brew_fix("yt-dlp") if _os() == "mac" else inst.setup_command()
    exe, why = _tool("yt-dlp")
    if not exe:
        return item("ytdlp", name, "fail", why, fix)
    code, out = inst._out([exe, "--version"], timeout=90)
    if code != 0:
        hint = " (โปรแกรมกันไวรัสอาจกักไฟล์ไว้)" if _os() == "windows" else ""
        return item("ytdlp", name, "fail", f"เรียก {exe} แล้วไม่ตอบ{hint}", fix)
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
            return item("cutout", name, "warn", "ไม่พบ swiftc (Xcode Command Line Tools) — ตัดพื้นหลังด้วย Apple Vision ไม่ได้",
                        "xcode-select --install")
        return item("cutout", name, "ok", f"Apple Vision · {sc}")
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
                    f"RAM {ram / GB:.0f} GB (น้อยกว่า 8 GB) — โมเดล {model} อาจช้าหรือค้าง เปลี่ยนเป็น {inst.LIGHT_MODEL} จะเบากว่า",
                    inst.setup_command(f"--model {inst.LIGHT_MODEL}"))
    return item("ram", name, "ok", f"{ram / GB:.0f} GB")


def collect(quick: bool = False) -> list:
    if quick:
        return [check_setup(), check_ffmpeg(True), check_ffprobe(True), check_node(True),
                check_python(True), check_model(), check_chrome(True)]
    return [check_setup(), check_ffmpeg(), check_ffprobe(), check_node(), check_python(), check_model(),
            check_chrome(), check_ytdlp(), check_cutout(), check_disk(), check_ram()]


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


def qc(video: str, expect_duration: float = None) -> list:
    ff, fp = kit.tool("ffmpeg"), kit.tool("ffprobe")
    info = json.loads(kit.run([fp, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", video]).stdout)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    dur = float(info.get("format", {}).get("duration") or 0)
    out = []

    if expect_duration is None:
        out.append(item("duration", "ความยาว", "info", f"{dur:.2f} วิ"))
    elif abs(dur - expect_duration) <= QC_DURATION_TOL:
        out.append(item("duration", "ความยาว", "ok", f"{dur:.2f} วิ (ควรได้ {expect_duration:.2f})"))
    else:
        out.append(item("duration", "ความยาว", "fail",
                        f"{dur:.2f} วิ แต่ควรได้ {expect_duration:.2f} วิ (คลาด {dur - expect_duration:+.2f})",
                        "เช็คว่า plan.json ครอบทั้งคลิป แล้วเรนเดอร์ใหม่"))

    if not v:
        out.append(item("size", "ขนาดภาพ", "fail", "ไม่มีภาพในไฟล์", "เรนเดอร์ใหม่"))
    else:
        w, h = int(v.get("width", 0)), int(v.get("height", 0))
        ok = (w, h) in QC_SIZES
        out.append(item("size", "ขนาดภาพ", "ok" if ok else "fail", f"{w}×{h}",
                        "" if ok else "ต้องเป็น 1080×1920 หรือ 1440×2560 — เช็ค output.preset แล้วเรนเดอร์ใหม่"))

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
    a = ap.parse_args(argv)

    if a.qc:
        mode, title = "qc", f"ตรวจคลิป {a.qc}"
        try:
            checks = qc(a.qc, a.expect_duration)
        except kit.KitError as e:
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
