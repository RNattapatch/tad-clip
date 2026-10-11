"""roughcut — raw talking-head clip -> tight cut MP4 (same size, SDR, constant fps).

  python -m engine.roughcut phrases <video> --out phrases.json [--whisper] [--force]
  python -m engine.roughcut render  <video> <drops.json> <out.mp4> [--force]

phrases.json = [{"i", "s", "e", "text", "gap"}]. Phrases come from audio energy, split at every dip >= 80 ms.
Whisper's word times can be off by up to ~1 s on raw takes, so judge by phrase text + order, never cut on word times.
`--whisper` re-hears every phrase alone (exact text); without it the text comes from one whole-clip
transcription cached as <out dir>/asr.json.

drops.json = {"drop": [phrase numbers to remove: retakes / false starts / filler], "gap": 0.30, "split": {"25": 55.21}}
Kept phrases closer than `gap` s stay joined; longer pauses and dropped phrases are cut out (a little air kept on
each side). "split" cuts phrase 25 at 55.21 s into 25a/25b (a retake that starts with no pause) -> drop "25b".
render writes <out> and <out>.segments.json = {"source", "drop", "keep"} (the raw ranges kept).
Every step skips when its output already exists unless --force, so re-running never clobbers hand edits.
"""
import argparse
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np

try:
    from engine import asr, kit
except ImportError:  # started as a plain script instead of `python -m engine.roughcut`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from engine import asr, kit

KitError = kit.KitError

SR, HOP = 16000, 160   # 10 ms frames
DIP, MIN_ON, PRE, POST = 0.08, 0.06, 0.10, 0.14
FADE = 0.008           # 8 ms fade at every cut so joins never click
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
STANDARD_FPS = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
                Fraction(50), Fraction(60000, 1001), Fraction(60), Fraction(120)]

HDR_CHAIN = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable,"
             "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
# ffmpeg 8 ignores -colorspace/-color_trc flags for frames that carry no tags, so the tags are put on the frames
BT709_TAGS = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
UNTAGGED = {None, "", "unknown", "unspecified", "reserved"}


# ---------------------------------------------------------------- phrases (audio energy)

def _read_json(path, hint=""):
    """UTF-8 JSON, BOM tolerated (Windows Notepad adds one)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise KitError(f"อ่าน {path} ไม่ได้ — ไม่ใช่ JSON ที่ถูกต้อง ({e}){hint}") from e


def _need_file(path, what="ไฟล์"):
    if not Path(path).is_file():
        raise KitError(f"ไม่พบ{what} {path}")


def decode_audio(video):
    """16 kHz mono int16 samples (as float32) of any media file."""
    _need_file(video)
    # aresample first_pts=0: pad the start so sample 0 is container time 0, the clock render's trim/atrim use
    res = subprocess.run([kit.tool("ffmpeg"), "-v", "error", "-nostdin", "-i", str(video), "-vn",
                          "-af", "aresample=async=1:first_pts=0", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True, stdin=subprocess.DEVNULL)
    a = np.frombuffer(res.stdout[: len(res.stdout) // 2 * 2], np.int16).astype(np.float32)
    if res.returncode != 0 or len(a) < HOP:
        tail = "\n".join(res.stderr.decode("utf-8", "replace").strip().splitlines()[-5:])
        raise KitError(f"ไม่พบเสียงพูดใน {Path(video).name} — roughcut ต้องมีเสียงเพื่อหาช่วงที่จะตัด\n{tail}".rstrip())
    return a


def phrases_from_audio(a):
    n = len(a) // HOP
    db = 20 * np.log10(np.sqrt((a[:n * HOP].reshape(n, HOP) ** 2).mean(axis=1)) + 1e-6)
    floor, loud = np.percentile(db, 10), np.percentile(db, 90)
    on = db > floor + 0.35 * (loud - floor)          # speech = well above the room floor
    runs, start = [], None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        if not v and start is not None:
            runs.append([start / 100, i / 100])
            start = None
    if start is not None:
        runs.append([start / 100, n / 100])
    runs = [r for r in runs if r[1] - r[0] >= MIN_ON]
    out = []
    for r in runs:
        if out and r[0] - out[-1][1] < DIP:
            out[-1][1] = r[1]
        else:
            out.append(r)
    return out


def phrases(video):
    """-> ([[start, end], ...] in seconds, clip duration in seconds)"""
    a = decode_audio(video)
    return phrases_from_audio(a), len(a) / SR


def asr_words(asr_json):
    """whisper.cpp-shaped dict -> [(start s, end s, text)] per token."""
    return [(t["offsets"]["from"] / 1000, t["offsets"]["to"] / 1000, t["text"])
            for seg in asr_json.get("transcription", []) for t in seg["tokens"]]


def per_phrase_asr(video, ph, prompt=None):
    """--whisper: transcribe every phrase on its own (model loaded once) -> {phrase index: exact text}."""
    ffmpeg = kit.tool("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="tad-clip-rc-", ignore_cleanup_errors=True) as tmp:
        files = []
        for i, (s, e) in enumerate(ph):
            f = Path(tmp) / f"p{i:03d}.wav"
            files.append(f)
            a = max(0.0, s - 0.05)
            kit.run([ffmpeg, "-v", "error", "-nostdin", "-y", "-ss", f"{a:.3f}", "-t", f"{e + 0.08 - a:.3f}", "-i", video,
                     "-vn", "-ac", "1", "-ar", "16000", "-af", "apad=pad_dur=0.6", f])
        return dict(enumerate(asr.transcribe_many(files, prompt=prompt)))


# ---------------------------------------------------------------- plan / split

def plan(ph, dur, drop, gap):
    """Phrases to keep -> [[start, end]] ranges of the raw clip (with PRE/POST air, never into a neighbour phrase)."""
    keep = [i for i in range(len(ph)) if i not in drop]
    segs = []
    for i in keep:
        s, e = ph[i]
        if segs and segs[-1][2] == i - 1 and s - segs[-1][1] < gap:
            segs[-1][1] = e
            segs[-1][2] = i
        else:
            segs.append([s, e, i])
    out = []
    for s, e, last in segs:
        first = next(i for i in keep if ph[i][0] >= s - 1e-6)
        lo = ph[first - 1][1] if first > 0 else 0          # never pad into the neighbouring phrase
        hi = ph[last + 1][0] if last + 1 < len(ph) else dur
        out.append([max(lo, s - PRE, 0), min(hi, e + POST, dur)])
    for k in range(1, len(out)):   # pauses shorter than PRE + POST: share the air, never play it twice
        if out[k][0] < out[k - 1][1]:
            out[k - 1][1] = out[k][0] = (out[k][0] + out[k - 1][1]) / 2
    return out


def split_phrases(ph, split):
    """{"25": 55.21} cuts phrase 25 at that time into "25a"/"25b". -> (new phrase list, ids as strings)."""
    ph = [list(p) for p in ph]
    ids = [str(i) for i in range(len(ph))]
    jobs = []
    if split is not None and not isinstance(split, dict):
        raise KitError("split ต้องเป็น object แบบ {\"เลขวลี\": เวลาเป็นวินาที} เช่น {\"25\": 55.21}")
    for k, t in (split or {}).items():
        try:
            jobs.append((int(k), str(k), float(t)))
        except (TypeError, ValueError):
            raise KitError(f"split \"{k}\": {t} ไม่ถูกต้อง — รูปแบบคือ {{\"เลขวลี\": เวลาเป็นวินาที}} เช่น {{\"25\": 55.21}}") from None
    for i, k, t in sorted(jobs, reverse=True):
        if not 0 <= i < len(ph) or not ph[i][0] < t < ph[i][1]:
            where = f"{ph[i][0]:.2f}-{ph[i][1]:.2f}" if 0 <= i < len(ph) else f"มีวลี 0-{len(ph) - 1}"
            raise KitError(f"split วลี {k} ที่ {t} วินาที ไม่ถูกต้อง — เวลาต้องอยู่ในช่วงของวลีนั้น ({where})")
        s0, e0 = ph[i]
        ph[i:i + 1] = [[s0, t], [t, e0]]
        ids[i:i + 1] = [str(i) + "a", str(i) + "b"]
    return ph, ids


def resolve_keep(ph, dur, spec):
    """drops.json content -> the raw ranges to keep (split, drop and gap applied)."""
    if not isinstance(spec, dict):
        raise KitError("drops.json ต้องเป็น object แบบ {\"drop\": [เลขวลี…], \"gap\": 0.3, \"split\": {\"25\": 55.21}}")
    drop_list, gap = spec.get("drop"), spec.get("gap")
    if drop_list is not None and not (isinstance(drop_list, list) and all(
            isinstance(d, str) or (isinstance(d, int) and not isinstance(d, bool)) for d in drop_list)):
        raise KitError("drops.json: \"drop\" ต้องเป็นรายการเลขวลี เช่น [4, 5, \"25b\"]")
    if gap is not None and (isinstance(gap, bool) or not isinstance(gap, (int, float))):
        raise KitError("drops.json: \"gap\" ต้องเป็นตัวเลขวินาที เช่น 0.3")
    ph, ids = split_phrases(ph, spec.get("split"))
    drop = {str(d) for d in drop_list or []}
    unknown = sorted(drop - set(ids))
    if unknown:
        raise KitError(f"drops.json มีเลขวลีที่ไม่มีอยู่: {', '.join(unknown)} (วลีที่มี: {ids[0] if ids else '-'} ถึง {ids[-1] if ids else '-'}"
                       " — วลีที่ใช้ split จะกลายเป็น Na/Nb)")
    keep = plan(ph, dur, {i for i, x in enumerate(ids) if x in drop}, float(0.30 if gap is None else gap))
    if not keep:
        raise KitError("drops.json ตัดทุกวลีออกหมด ไม่เหลือช่วงให้ render — ลดรายการ drop แล้วสั่งใหม่")
    return keep


# ---------------------------------------------------------------- phrases CLI

def _print_phrases(rows, heard=False, dur=None):
    for r in rows:
        txt = f"⟨{r['text']}⟩" if heard and r["text"] else r["text"]
        pause = f"   ⏸ {r['gap']:.2f}" if r["gap"] >= 0.3 else ""
        print(f"{r['i']:3d}  {r['s']:7.2f}-{r['e']:7.2f}  {txt}{pause}")
    print(f"phrases {len(rows)}" + (f" · raw {dur:.1f}s" if dur is not None else ""))


def run_phrases(video, out, whisper=False, force=False):
    out = Path(out)
    if out.exists() and not force:
        print(f"ข้าม — มี {out} อยู่แล้ว (ใช้ --force เพื่อสร้างใหม่ เช่นตอนเปลี่ยนมาใช้ --whisper) รายการเดิม:")
        _print_phrases(_read_json(out, " ใช้ --force เพื่อสร้างใหม่"))
        return 0
    ph, dur = phrases(video)
    prompt = asr.glossary_prompt()
    if whisper:
        print(f"ถอดเสียงทีละวลี {len(ph)} วลี …", file=sys.stderr, flush=True)
        heard = per_phrase_asr(video, ph)   # no hint here: on 0.1 s phrases faster-whisper echoes it back (whisper.cpp -mc 0 ignores it)
        texts = [heard.get(i, "") for i in range(len(ph))]
    else:
        cache = out.parent / "asr.json"
        if cache.exists() and not force:
            data = _read_json(cache, " ใช้ --force เพื่อถอดเสียงใหม่")
        else:
            print(f"ถอดเสียงทั้งคลิป (เก็บไว้ที่ {cache}) …", file=sys.stderr, flush=True)
            data = asr.transcribe(video, prompt=prompt)
            asr.write_json_atomic(cache, data)
        ws = asr_words(data)
        texts = ["".join(t for f, to, t in ws if s - 0.05 <= (f + to) / 2 < e + 0.05).strip() for s, e in ph]
    rows = [{"i": i, "s": round(s, 3), "e": round(e, 3), "text": (texts[i] or "").strip(),
             "gap": round(ph[i + 1][0] - e, 3) if i + 1 < len(ph) else 0}
            for i, (s, e) in enumerate(ph)]
    asr.write_text_atomic(out, "[\n" + ",\n".join("  " + json.dumps(r, ensure_ascii=False) for r in rows) + "\n]\n")  # one phrase per line
    _print_phrases(rows, heard=whisper, dur=dur)
    return 0


# ---------------------------------------------------------------- render

def probe_video(video):
    res = kit.run([kit.tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                   "stream=avg_frame_rate,r_frame_rate,color_transfer,color_space,color_primaries,pix_fmt,width,height", "-of", "json", video])
    streams = json.loads(res.stdout).get("streams") or []
    if not streams:
        raise KitError(f"{Path(video).name} ไม่มีภาพวิดีโอ — roughcut ใช้กับไฟล์วิดีโอเท่านั้น")
    return streams[0]


def source_fps(info):
    """Source average fps as an ffmpeg rate string, snapped to the nearest standard rate when within 1%."""
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            f = Fraction(info.get(key) or "0")
        except (ValueError, ZeroDivisionError):
            continue
        if 0 < f <= 240:   # mkv/webm report a 1000 fps timebase when they do not know the real rate
            near = min(STANDARD_FPS, key=lambda s: abs(s - f))
            if abs(near - f) / near <= Fraction(1, 100):
                f = near
            else:
                f = f.limit_denominator(1001)
            return str(f)
    return "30"


@functools.lru_cache(maxsize=None)
def _has_filter(ffmpeg, name):
    try:
        text = kit.run([ffmpeg, "-hide_banner", "-nostdin", "-filters"]).stdout
    except KitError:
        return False
    return re.search(rf"^\s*\S+\s+{re.escape(name)}\s", text, re.M) is not None


_FILTER_OPTION = None   # "-/filter_complex" (ffmpeg 7+) or "-filter_complex_script", learned on first use


def run_with_filter_script(before, script, after):
    """Run ffmpeg with the filtergraph read from a file. Tries `-/filter_complex` and, when this ffmpeg does not know
    it (older builds), `-filter_complex_script`. No version parsing, so git/nightly builds work too."""
    global _FILTER_OPTION
    options = [_FILTER_OPTION] if _FILTER_OPTION else ["-/filter_complex", "-filter_complex_script"]
    for i, opt in enumerate(options):
        try:
            kit.run([*before, opt, script, *after])
        except KitError as e:
            if i + 1 < len(options) and ("nrecognized option" in str(e) or "Option not found" in str(e)):
                continue
            raise
        _FILTER_OPTION = opt
        return


def video_filter(info, has_zscale):
    """The post-concat chain that turns any source into SDR bt709 yuv420p (tone-mapping iPhone HDR).
    A source with no colour tags is read as bt709 from 720p up and bt601 below (ffmpeg would assume bt601 for all)."""
    if info.get("color_transfer") in HDR_TRANSFERS:
        if has_zscale:
            return HDR_CHAIN
        print("คำเตือน: ffmpeg ตัวนี้ไม่มีฟิลเตอร์ zscale จึงแปลงคลิป HDR เป็น SDR ด้วยวิธีธรรมดา สีอาจดูจืด "
              "— ติดตั้ง ffmpeg ตัวเต็ม (ที่มี zscale) เพื่อให้สีถูกต้อง", file=sys.stderr)
        return "format=yuv420p"
    if info.get("color_space") in UNTAGGED:
        try:
            matrix = "bt601" if int(info.get("height")) < 720 else "bt709"
        except (TypeError, ValueError):
            matrix = "bt709"
    else:
        matrix = "auto"
    return f"scale=in_range=auto:in_color_matrix={matrix}:out_color_matrix=bt709:out_range=tv,format=yuv420p,{BT709_TAGS}"


def build_filter_script(keep, vf):
    fc = []
    for i, (s, e) in enumerate(keep):
        f = min(FADE, (e - s) / 2)
        fc.append(f"[0:v]trim={s:.3f}:{e:.3f},setpts=PTS-STARTPTS[v{i}]")
        fc.append(f"[0:a]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS,afade=t=in:d={f:.3f},"
                  f"afade=t=out:st={e - s - f:.3f}:d={f:.3f}[a{i}]")
    n = len(keep)
    fc.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][a]")
    fc.append(f"[vc]{vf}[v]")
    return ";\n".join(fc)


def _remove(path):
    try:
        os.remove(path)
    except OSError:   # missing, or briefly locked by an antivirus on Windows -- never fail a finished render for it
        pass


def render(video, drops, out, force=False):
    out = Path(out)
    if out.exists() and not force:
        print(f"ข้าม — มี {out} อยู่แล้ว (ใช้ --force เพื่อ render ใหม่)")
        return 0
    _need_file(video)
    _need_file(drops, "ไฟล์ drops.json")
    spec = _read_json(drops)
    ph, dur = phrases(video)
    keep = resolve_keep(ph, dur, spec)
    info = probe_video(video)
    ffmpeg = kit.tool("ffmpeg")
    vf = video_filter(info, _has_filter(ffmpeg, "zscale"))

    out.parent.mkdir(parents=True, exist_ok=True)
    script = Path(str(out) + ".filter")
    part = out.with_name(out.stem + ".partial" + out.suffix)
    try:
        script.write_bytes(build_filter_script(keep, vf).encode("utf-8"))   # bytes: LF endings on every OS
        run_with_filter_script(
            [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", video], script,
            ["-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-crf", "15", "-preset", "medium", "-pix_fmt", "yuv420p",
             "-r", source_fps(info), "-fps_mode", "cfr",
             "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", part])
        seg = {"source": str(Path(video).resolve()), "drop": [str(d) for d in spec.get("drop") or []],
               "keep": [[round(s, 3), round(e, 3)] for s, e in keep]}
        asr.write_json_atomic(Path(str(out) + ".segments.json"), seg, indent=None)
        os.replace(part, out)
    finally:
        _remove(script)
        _remove(part)
    print(f"✓ {out} · {len(keep)} pieces · {sum(e - s for s, e in keep):.2f}s (raw {dur:.1f}s)")
    return 0


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engine.roughcut", description="ตัดคลิปพูดหน้ากล้องให้กระชับ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("phrases", help="หาวลีจากพลังเสียง + ถอดข้อความ -> phrases.json และพิมพ์รายการเลข")
    p.add_argument("video")
    p.add_argument("--out", required=True, help="ไฟล์ phrases.json ที่จะเขียน")
    p.add_argument("--whisper", action="store_true", help="ถอดเสียงทีละวลี (ข้อความแม่นกว่า ช้ากว่า)")
    p.add_argument("--force", action="store_true", help="ทำใหม่แม้มีไฟล์ผลลัพธ์แล้ว")
    r = sub.add_parser("render", help="ตัดตาม drops.json -> out.mp4 (+ out.mp4.segments.json)")
    r.add_argument("video")
    r.add_argument("drops")
    r.add_argument("out")
    r.add_argument("--force", action="store_true", help="render ใหม่แม้มีไฟล์ผลลัพธ์แล้ว")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "phrases":
            return run_phrases(args.video, args.out, whisper=args.whisper, force=args.force)
        return render(args.video, args.drops, args.out, force=args.force)
    except KitError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    asr.utf8_stdio()
    sys.exit(main())
