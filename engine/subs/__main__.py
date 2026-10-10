"""CLI: python -m engine.subs <video> -o <dir> | finalize <dir> | patch <dir> | burn <video> <ass>.

Exit codes: 0 ok, 2 bad input or missing piece (message in Thai on stderr), 3 edits touch more than half the cues.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from engine import kit

from . import apply_edits as ae
from . import make_subs as ms


# ── small helpers ──────────────────────────────────────────────────────────────────────────

def _utf8_stdio():
    """Windows pipes default to a legacy code page; Thai output would raise UnicodeEncodeError."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _write_json(path, obj):
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path, what):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise kit.KitError(f"อ่าน {what} ({path.name}) ไม่ได้ — {e}") from e


def _say(msg):
    print(msg, flush=True)


# ── run: video -> audio.wav -> asr.json -> raw cues + review.md ────────────────────────────

def _transcribe(wav, prompt):
    """engine.asr.transcribe -> whisper.cpp -ojf shaped dict. Imported lazily so the rest works without whisper."""
    fix = "พิมพ์ \"ตรวจเครื่อง tad-clip\" ให้ Claude ติดตั้งส่วนที่ขาดให้"
    try:
        from engine import asr
        return asr.transcribe(str(wav), prompt=prompt)
    except kit.KitError:
        raise
    except ImportError as e:
        raise kit.KitError(f"เรียกตัวถอดเสียงไม่ได้ ({e}) — {fix}") from e
    except Exception as e:
        raise kit.KitError(f"ถอดเสียงไม่สำเร็จ ({type(e).__name__}: {e}) — {fix}") from e


def extract_audio(src, wav, start_s=None, dur_s=None):
    """16 kHz mono PCM, the only thing ASR needs. Written to a .part file first so a crash never leaves a half file."""
    tmp = wav.with_name(wav.stem + ".part.wav")
    cmd = [kit.tool("ffmpeg"), "-y", "-v", "error"]
    if start_s is not None:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += ["-i", str(src)]
    if dur_s is not None:
        cmd += ["-t", f"{dur_s:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)]
    try:
        kit.run(cmd)
    except kit.KitError as e:
        if "does not contain any stream" in str(e) or "no streams" in str(e).lower():
            raise kit.KitError(f"ไฟล์ {Path(src).name} ไม่มีเสียงให้ถอด") from e
        raise
    os.replace(tmp, wav)


def build_raw(d, name, max_chars=None, karaoke=False, force=False, cfg=None):
    """asr.json -> raw.cues.json, raw.srt, review.md (and raw.karaoke.ass). Returns True when it (re)built.

    Skipped when raw.cues.json already matches the same max_chars + glossary (unless force), so a re-run never
    rewrites what is on disk for nothing. edits.json is never touched."""
    d = Path(d)
    cfg = cfg or kit.load_config()
    max_chars = int(max_chars or cfg["subtitle"]["max_chars"])
    glossary = ms.load_glossary(cfg.get("glossary"))
    knobs = [ms.ALGO_VERSION, ms.MINCH, ms.GAP, ms.MINDUR, ms.MAXDUR, ms.LEAD, ms.TARGET, sorted(ms.DANGLE)]
    fp = hashlib.sha1(json.dumps({"max_chars": max_chars, "glossary": glossary, "knobs": knobs}, sort_keys=True,
                                 ensure_ascii=False).encode("utf-8")).hexdigest()
    raw_p, review_p = d / "raw.cues.json", d / "review.md"
    old = None
    if raw_p.exists() and not force:
        try:
            old = json.loads(raw_p.read_text(encoding="utf-8"))
        except ValueError:
            old = None
    if old and old.get("meta", {}).get("fingerprint") == fp and review_p.exists() and (d / "raw.srt").exists():
        return False

    asr = ms.load_asr(d / "asr.json")
    if not isinstance(asr, dict) or "transcription" not in asr:
        raise kit.KitError(f"asr.json ใน {d} ไม่ใช่ผลถอดเสียงที่ถูกต้อง — ลบไฟล์นี้แล้วรันใหม่ หรือใช้ --force")
    cues, applied = ms.build_cues(asr, glossary, max_chars)
    if not cues:
        raise kit.KitError("ถอดเสียงแล้วไม่ได้ซับเลย — เช็คว่าคลิปมีเสียงพูดจริง หรือลองถอดใหม่ด้วย --force")
    meta = {"name": name, "max_chars": max_chars, "fingerprint": fp, "fixes_applied": [list(a) for a in applied]}
    _write_json(raw_p, {"meta": meta, "cues": cues})
    (d / "raw.srt").write_text(ms.render_srt(cues), encoding="utf-8")
    if karaoke:
        (d / "raw.karaoke.ass").write_text(ms.render_ass(cues, ms.style_from_config(cfg), karaoke=True), encoding="utf-8")
    wav = d / "audio.wav"
    flags = ae.find_flags(cues, applied, glossary, wav if wav.exists() else None)
    review_p.write_text(ae.review_markdown(name, cues, flags, max_chars, d, wav if wav.exists() else None), encoding="utf-8")
    return True


def _cmd_run(argv):
    ap = argparse.ArgumentParser(prog="python -m engine.subs", description="ถอดเสียงคลิปเป็นซับ (cue ดิบ + review.md ให้ Claude ตรวจ)")
    ap.add_argument("video")
    ap.add_argument("-o", "--out", help="โฟลเดอร์ผลลัพธ์ (ค่าเริ่มต้น: <ชื่อคลิป>-subs ข้างคลิป)")
    ap.add_argument("--karaoke", action="store_true", help="เขียน raw.karaoke.ass ไว้ดูตัวอย่างด้วย")
    ap.add_argument("--max-chars", type=int, help="เพดานตัวอักษรต่อบรรทัด (ค่าเริ่มต้นจาก config subtitle.max_chars)")
    ap.add_argument("--force", action="store_true", help="ทำใหม่ทุกขั้น (เสียง ถอดเสียง ตัดซับ) — ไม่แตะ edits.json")
    a = ap.parse_args(argv)

    video = Path(a.video)
    if not video.is_file():
        raise kit.KitError(f"ไม่พบไฟล์: {video}")
    d = Path(a.out) if a.out else video.parent / f"{video.stem}-subs"
    d.mkdir(parents=True, exist_ok=True)
    cfg = kit.load_config()
    glossary = ms.load_glossary(cfg.get("glossary"))

    wav, asr_p = d / "audio.wav", d / "asr.json"
    if wav.exists() and not a.force:
        _say("1/3 ใช้ audio.wav เดิม")
    else:
        _say("1/3 แยกเสียง → audio.wav")
        extract_audio(video, wav)
    if asr_p.exists() and not a.force:
        _say("2/3 ใช้ asr.json เดิม (ถอดใหม่ด้วย --force)")
    else:
        _say("2/3 ถอดเสียง → asr.json (ครั้งแรกอาจใช้เวลานาน)")
        _write_json(asr_p, _transcribe(wav, glossary["prompt_hint"]))
    built = build_raw(d, video.stem, a.max_chars, a.karaoke, a.force, cfg)
    doc = json.loads((d / "raw.cues.json").read_text(encoding="utf-8"))
    cues = doc["cues"]
    _say(f"3/3 {'ตัดซับ' if built else 'ใช้ซับดิบเดิม'} → raw.cues.json ({len(cues)} cue)")
    flags = ae.find_flags(cues, doc["meta"].get("fixes_applied", []), glossary, wav)
    _say(f"\nต่อไป: อ่าน {d / 'review.md'} แล้วเขียน edits.json (ถ้ามีคำผิด) จากนั้น finalize")
    if flags:
        _say(f"ธง {len(flags)} จุด:")
        for f in flags[:10]:
            _say(f"  - cue {f['cue']}: {f['text']}" if f["cue"] else f"  - {f['text']}")
    return 0


# ── finalize: raw cues + edits -> final files ──────────────────────────────────────────────

def _cmd_finalize(argv):
    ap = argparse.ArgumentParser(prog="python -m engine.subs finalize", description="ใช้ edits.json แล้วเขียนไฟล์ซับสุดท้าย")
    ap.add_argument("dir")
    ap.add_argument("--edits", help="path ของ edits.json (ค่าเริ่มต้น: <dir>/edits.json ถ้ามี)")
    ap.add_argument("--name", help="ชื่อไฟล์ผลลัพธ์ (ค่าเริ่มต้น: ชื่อคลิปที่ใช้ตอนรัน)")
    ap.add_argument("--force", action="store_true", help="รับไว้ให้ตรงกับคำสั่งอื่น — finalize เขียนไฟล์ใหม่ทุกครั้งอยู่แล้ว")
    a = ap.parse_args(argv)

    d = Path(a.dir)
    raw_p = d / "raw.cues.json"
    if not raw_p.is_file():
        raise kit.KitError(f"ไม่พบ raw.cues.json ใน {d} — รัน `python -m engine.subs <คลิป> -o \"{d}\"` ก่อน")
    doc = _read_json(raw_p, "ซับดิบ")
    cues, meta = doc["cues"], doc.get("meta", {})
    cfg = kit.load_config()
    max_chars = int(meta.get("max_chars") or cfg["subtitle"]["max_chars"])
    name = a.name or meta.get("name") or d.name

    edits_p = Path(a.edits) if a.edits else (d / "edits.json" if (d / "edits.json").is_file() else None)
    res = None
    if edits_p:
        if not edits_p.is_file():
            raise kit.KitError(f"ไม่พบไฟล์ edits: {edits_p}")
        edits = _read_json(edits_p, "edits")
        errs = ae.validate_shape(edits)
        if errs:
            raise kit.KitError(f"{edits_p.name} ผิดรูปแบบ (รูปแบบที่ถูกอยู่หัว review.md):\n" + "\n".join(f"- {e}" for e in errs))
        res = ae.apply_edits(cues, edits, max_chars)
        (d / "edits-report.md").write_text(ae.report_markdown(name, res, edits_p.name), encoding="utf-8")
        if res.too_many:
            print(f"edits แตะ {res.changed} จาก {res.total} cue — เกินครึ่ง ไม่ใช้ edits เลย "
                  f"(ถอดเสียงน่าจะผิดทั้งคลิป ถอดใหม่แทน) · รายงาน: {d / 'edits-report.md'}", file=sys.stderr)
            return 3
        cues = res.cues

    ms.write_all(cues, d / name, ms.style_from_config(cfg), (cfg.get("speaker") or {}).get("name", ""))
    _write_json(d / "cues.json", {"meta": {**meta, "name": name}, "cues": cues})
    _say(f"เสร็จ: {len(cues)} cue" + (f" · แก้ {len(res.applied)} จุด · ตีกลับ {len(res.rejected)}" if res else " (ไม่มี edits)"))
    for ext in (".srt", ".ass", ".karaoke.ass", ".lrc"):
        _say(f"  {d / (name + ext)}")
    _say(f"  {d / 'cues.json'}")
    if res and res.rejected:
        _say(f"มี {len(res.rejected)} รายการที่โค้ดไม่ยอมให้แก้ — ดู {d / 'edits-report.md'}")
    return 0


# ── patch: re-transcribe one stretch and splice it into asr.json ───────────────────────────

def _is_special(t):
    return t["text"].startswith("[_")


def _trim_segment(seg, keep):
    """Keep only the tokens passing keep(); None when no real word is left."""
    toks = [t for t in seg.get("tokens", []) if keep(t)]
    real = [t for t in toks if not _is_special(t)]
    if not real:
        return None
    out = {k: v for k, v in seg.items() if k != "timestamps"}
    out["tokens"] = toks
    out["offsets"] = {"from": real[0]["offsets"]["from"], "to": real[-1]["offsets"]["to"]}
    out["text"] = " " + "".join(t["text"] for t in real).strip()
    return out


def splice_asr(old, new, start_ms, end_ms=None):
    """Replace everything spoken between start_ms and end_ms (end None = to the end) in `old` with the segments
    of `new`, whose times start at 0 and are shifted by start_ms. Segments that straddle a boundary keep the
    words outside the window."""
    kept = []
    for seg in old["transcription"]:
        o = seg["offsets"]
        if o["to"] <= start_ms or (end_ms is not None and o["from"] >= end_ms):
            kept.append(seg)
        elif o["from"] >= start_ms and (end_ms is None or o["to"] <= end_ms):
            continue
        else:
            left = _trim_segment(seg, lambda t: t["offsets"]["to"] <= start_ms)
            right = _trim_segment(seg, lambda t: end_ms is not None and t["offsets"]["from"] >= end_ms)
            kept += [s for s in (left, right) if s]
    added = []
    for seg in new["transcription"]:
        s = copy.deepcopy(seg)
        s.pop("timestamps", None)
        s["offsets"] = {"from": s["offsets"]["from"] + start_ms, "to": s["offsets"]["to"] + start_ms}
        for t in s.get("tokens", []):
            t.pop("timestamps", None)
            t["offsets"] = {"from": t["offsets"]["from"] + start_ms, "to": t["offsets"]["to"] + start_ms}
        added.append(s)
    out = dict(old)
    out["transcription"] = sorted(kept + added, key=lambda s: s["offsets"]["from"])
    return out


def _cmd_patch(argv):
    ap = argparse.ArgumentParser(prog="python -m engine.subs patch", description="ถอดเสียงช่วงที่ซับหาย/ค้างใหม่ แล้วเสียบกลับเข้า asr.json")
    ap.add_argument("dir")
    ap.add_argument("--from", dest="start", type=float, required=True, help="วินาทีเริ่ม (ถอยไปที่รอยหายใจก่อนจุดที่หาย)")
    ap.add_argument("--to", dest="end", type=float, help="วินาทีจบ (ไม่ใส่ = ถึงท้ายคลิป)")
    a = ap.parse_args(argv)

    d = Path(a.dir)
    asr_p, wav = d / "asr.json", d / "audio.wav"
    if not asr_p.is_file() or not wav.is_file():
        raise kit.KitError(f"ไม่พบ asr.json / audio.wav ใน {d} — รัน `python -m engine.subs <คลิป> -o \"{d}\"` ก่อน")
    if a.start < 0 or (a.end is not None and a.end <= a.start):
        raise kit.KitError("ช่วงเวลาไม่ถูกต้อง — --from ต้องไม่ติดลบ และ --to ต้องมากกว่า --from")
    cfg = kit.load_config()
    glossary = ms.load_glossary(cfg.get("glossary"))
    meta = {}
    if (d / "raw.cues.json").is_file():
        meta = _read_json(d / "raw.cues.json", "ซับดิบ").get("meta", {})
    piece = d / f"patch-{int(a.start * 1000)}.wav"
    extract_audio(wav, piece, a.start, None if a.end is None else a.end - a.start)
    try:
        new = _transcribe(piece, glossary["prompt_hint"])
    finally:
        piece.unlink(missing_ok=True)
    old = ms.load_asr(asr_p)
    backup = d / "asr.before-patch.json"
    if not backup.exists():
        backup.write_bytes(asr_p.read_bytes())
    start_ms, end_ms = int(round(a.start * 1000)), None if a.end is None else int(round(a.end * 1000))
    _write_json(asr_p, splice_asr(old, new, start_ms, end_ms))
    build_raw(d, meta.get("name") or d.name, meta.get("max_chars"), False, True, cfg)
    _say(f"ถอด {a.start:g}-{'ท้ายคลิป' if a.end is None else format(a.end, 'g')} วิ ใหม่แล้ว และตัดซับดิบใหม่ (ของเดิมเก็บที่ {backup.name})")
    _say(f"เลข cue เปลี่ยนแล้ว — อ่าน {d / 'review.md'} ใหม่ แล้วเขียน edits.json ใหม่ (edits เดิมจะถูกโค้ดตีกลับถ้าบรรทัดไม่ตรง)")
    return 0


# ── burn ───────────────────────────────────────────────────────────────────────────────────

def filter_path(p):
    """A path as an ffmpeg filter option value. Two escaping levels: backslash-escape ' and : for the option
    parser, then wrap in single quotes (with '\\'' for a quote) for the filtergraph parser. Backslashes are
    turned into slashes first, which Windows accepts."""
    s = str(p).replace("\\", "/")
    s = s.replace("'", "\\'").replace(":", "\\:")
    return "'" + s.replace("'", "'\\''") + "'"


def burn_filter(ass, fonts_dir):
    return f"subtitles={filter_path(ass)}:fontsdir={filter_path(fonts_dir)}"


def _has_libass(ffmpeg):
    return re.search(r"\bsubtitles\b", kit.run([ffmpeg, "-hide_banner", "-filters"]).stdout) is not None


def _cmd_burn(argv):
    ap = argparse.ArgumentParser(prog="python -m engine.subs burn", description="ฝังซับ (.ass) ลงวิดีโอด้วย ffmpeg + ฟอนต์ที่มากับชุดเครื่องมือ")
    ap.add_argument("video")
    ap.add_argument("ass")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--crf", type=int, default=16)
    a = ap.parse_args(argv)
    video, ass, out = Path(a.video), Path(a.ass), Path(a.out)
    for f in (video, ass):
        if not f.is_file():
            raise kit.KitError(f"ไม่พบไฟล์: {f}")
    ffmpeg = kit.tool("ffmpeg")
    if not _has_libass(ffmpeg):
        raise kit.KitError("ffmpeg ตัวนี้ไม่มีตัวฝังซับ (libass) — พิมพ์ \"ตรวจเครื่อง tad-clip\" ให้ Claude ติดตั้ง ffmpeg ตัวเต็มให้")
    out.parent.mkdir(parents=True, exist_ok=True)
    kit.run([ffmpeg, "-y", "-v", "error", "-i", str(video), "-vf", burn_filter(ass, kit.fonts_dir()),
             "-c:v", "libx264", "-crf", str(a.crf), "-preset", "medium", "-pix_fmt", "yuv420p",
             "-c:a", "copy", "-movflags", "+faststart", str(out)])
    _say(f"ฝังซับแล้ว → {out}")
    return 0


# ── entry ──────────────────────────────────────────────────────────────────────────────────

_COMMANDS = {"finalize": _cmd_finalize, "patch": _cmd_patch, "burn": _cmd_burn}


def main(argv=None):
    _utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] in _COMMANDS:
            return _COMMANDS[argv[0]](argv[1:])
        if argv and argv[0] == "run":
            argv = argv[1:]
        return _cmd_run(argv)
    except kit.KitError as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
