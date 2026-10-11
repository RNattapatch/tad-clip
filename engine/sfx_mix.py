"""sfx_mix — วางเสียงเอฟเฟกต์ใต้เสียงพูดบนคลิปที่เสร็จแล้ว (ภาพไม่แตะ copy ตรง · เสียงมิกซ์ใหม่)

  python -m engine.sfx_mix <video> <sfx.json> <out.mp4> [--force]

sfx.json = [{"file": "boom.mp3", "at": 52.0, "dur": 1.0, "db": -8, "why": "punchline"}, ...]
  file  ไฟล์เสียง · path แบบ relative หาในโฟลเดอร์เดียวกับ sfx.json ก่อน แล้วค่อยหาใน config.library_dir
  at    วินาทีที่เริ่มเล่นบนคลิป
  dur   ตัดเสียงให้ยาวเท่านี้ (เฟดท้าย 0.15 วิ) · ไม่ใส่ = เล่นจนจบไฟล์
  db    ความดังของเอฟเฟกต์เทียบกับเสียงพูด ณ จังหวะนั้น (ค่าเริ่ม -8 dB) — วัดจริง ไม่เดา:
        ไฟล์เสียงจากคลังแต่ละไฟล์ดังต่างกันได้ 25 dB ขึ้นไป ถ้าตั้ง volume คงที่ บางตัวจะเบากว่าเสียงพูดเป็นสิบ dB
เสียงพูดไม่ถูกแตะ (ไม่ปรับความดัง) · ปิดท้ายด้วย limiter 0.89 ที่เดียวของระบบที่คุมพีค — sfx.json ว่าง ([]) ก็ผ่าน limiter เหมือนกัน

รันซ้ำ: ถ้า out ใหม่กว่า video, sfx.json และไฟล์เสียงทุกไฟล์ จะข้าม (ใส่ --force เพื่อมิกซ์ใหม่)
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from engine import kit

SR = 44100
FADE = 0.15           # วินาที เฟดท้ายเมื่อใช้ dur
DEFAULT_DB = -8.0
LIMIT = 0.89          # -1.0 dBFS · วัดแล้วหลัง AAC ได้ ~ -1.0 dBTP ผ่านเกณฑ์ doctor --qc (true peak <= -0.5 dBTP)
MIN_VOICE_WINDOW = 0.3
SILENT_DB = -50.0     # เสียงพูดช่วงนั้นเบากว่านี้ = ช่วงเงียบ → ใช้ระดับของทั้งคลิปแทน
GAIN_RANGE = (-60.0, 40.0)


def _ffmpeg():
    return kit.tool("ffmpeg")


def _avg(n: int) -> str:
    """pan expression: mean of n channels (a centred stereo voice keeps its per-channel level; ffmpeg's default downmix does not)."""
    return "+".join(f"{1 / n:.6g}*c{i}" for i in range(n))


def channels_of(path) -> int:
    """Channel count of the first audio stream (0 when ffprobe cannot tell)."""
    res = subprocess.run([kit.tool("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return int(res.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def pcm(path, ss=None, dur=None, channels=None) -> np.ndarray:
    """Decode to mono float32 at SR (mean of the channels). Raises KitError when ffmpeg cannot read the file."""
    n = channels_of(path) if channels is None else channels
    cmd = [_ffmpeg(), "-v", "error"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    cmd += ["-i", str(path)]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vn"] + (["-af", f"pan=mono|c0={_avg(n)}"] if n > 1 else ["-ac", "1"]) + ["-ar", str(SR), "-f", "s16le", "-"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        tail = res.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise kit.KitError(f"อ่านเสียงจาก {Path(str(path)).name} ไม่ได้\n" + "\n".join(tail))
    return np.frombuffer(res.stdout, np.int16).astype(np.float32) / 32768


def to_layout(n: int, layout: str) -> str:
    """Filter fragment (with trailing comma) that turns an n-channel effect into the voice's layout at unity gain per channel,
    the same level pcm() measured. ffmpeg's own up/down-mix is -3 dB / +3 dB off that."""
    if n <= 0:
        return f"aformat=channel_layouts={layout},"
    if layout == "mono":
        return "" if n == 1 else f"pan=mono|c0={_avg(n)},"
    if n == 2:
        return ""
    src = "c0" if n == 1 else _avg(n)
    return f"pan=stereo|c0={src}|c1={src},"


def rms_db(x: np.ndarray) -> float:
    x = x[np.abs(x) > 1e-4]  # ข้ามช่วงเงียบสนิท ไม่งั้นเสียงสั้นๆ จะถูกวัดต่ำเกินจริง
    return float(20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-9)) if len(x) else -90.0


def gain_db(voice_db: float, sound_db: float, target_db: float = DEFAULT_DB) -> float:
    """dB to apply to the sound so it lands target_db under the voice. Clamped so a near-silent file cannot blow up."""
    return float(min(max(voice_db + target_db - sound_db, GAIN_RANGE[0]), GAIN_RANGE[1]))


def resolve_file(name: str, spec_dir: Path) -> Path:
    """Absolute → as is. Relative → next to sfx.json, then config.library_dir."""
    p = Path(name).expanduser()
    if p.is_absolute():
        if p.is_file():
            return p
        raise kit.KitError(f"ไม่พบไฟล์เสียง {name}")
    tried = [Path(spec_dir) / p]
    lib = kit.load_config().get("library_dir")
    if lib:
        tried.append(Path(lib).expanduser() / p)
    for c in tried:
        if c.is_file():
            return c
    raise kit.KitError(f"ไม่พบไฟล์เสียง {name}\nหาแล้วที่: " + " · ".join(str(t) for t in tried)
                       + ("" if lib else "\n(ยังไม่ได้ตั้ง library_dir ใน config.json)"))


def load_spec(spec_path: Path) -> list:
    try:
        items = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise kit.KitError(f"ไม่พบไฟล์ {spec_path}") from None
    except json.JSONDecodeError as e:
        raise kit.KitError(f"{Path(spec_path).name} ไม่ใช่ JSON ที่อ่านได้: {e}") from None
    if not isinstance(items, list):
        raise kit.KitError(f"{Path(spec_path).name} ต้องเป็นรายการ [ {{\"file\": ..., \"at\": ...}}, ... ]")
    for i, s in enumerate(items, 1):
        if not isinstance(s, dict) or not isinstance(s.get("file"), str) or not s["file"]:
            raise kit.KitError(f"รายการที่ {i} ใน {Path(spec_path).name} ต้องมี \"file\"")
        for key in ("at", "dur", "db"):
            v = s.get(key)
            if v is None and key != "at":
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise kit.KitError(f"รายการที่ {i}: \"{key}\" ต้องเป็นตัวเลข" + (" (จำเป็นต้องมี)" if key == "at" else ""))
        if s["at"] < 0:
            raise kit.KitError(f"รายการที่ {i}: \"at\" ต้องไม่ติดลบ")
        if s.get("dur") is not None and s["dur"] <= 0:
            raise kit.KitError(f"รายการที่ {i}: \"dur\" ต้องมากกว่า 0")
    return items


def probe(video: Path) -> dict:
    ffprobe = kit.tool("ffprobe")
    res = kit.run([ffprobe, "-v", "error", "-print_format", "json", "-show_entries",
                   "format=duration:stream=codec_type,codec_name,sample_rate,channels", "-i", video])
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams", [])
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    vid = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not vid:
        raise kit.KitError(f"{Path(video).name} ไม่มีภาพ — ต้องเป็นไฟล์วิดีโอ")
    if not audio:
        raise kit.KitError(f"{Path(video).name} ไม่มีเสียงพูดให้วางเอฟเฟกต์ใต้ — ตรวจว่าขั้นก่อนหน้า export เสียงมาด้วย")
    return {
        "duration": float(data.get("format", {}).get("duration") or 0),
        "sample_rate": int(audio.get("sample_rate") or SR),
        "channels": int(audio.get("channels") or 2),
        "vcodec": vid.get("codec_name", ""),
    }


def _fresh(out: Path, inputs: list) -> bool:
    return out.exists() and all(out.stat().st_mtime_ns >= Path(p).stat().st_mtime_ns for p in inputs)


def mix(video, spec_path, out, force: bool = False) -> list:
    """Mix the effects under the voice. Returns one dict per applied effect ([] when skipped or none)."""
    video, spec_path, out = Path(video), Path(spec_path), Path(out)
    if not video.is_file():
        raise kit.KitError(f"ไม่พบวิดีโอ {video}")
    if out.resolve() == video.resolve():
        raise kit.KitError("ไฟล์ปลายทางต้องไม่ใช่ไฟล์เดียวกับวิดีโอต้นทาง")
    items = load_spec(spec_path)
    files = [resolve_file(s["file"], spec_path.parent) for s in items]
    if not force and _fresh(out, [video, spec_path, *files]):
        print(f"ข้าม: {out.name} ใหม่กว่า sfx.json และต้นทางอยู่แล้ว (ใส่ --force เพื่อมิกซ์ใหม่)")
        return []

    info = probe(video)
    sr = info["sample_rate"]
    layout = "mono" if info["channels"] == 1 else "stereo"
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg()
    tag = ["-tag:v", "hvc1"] if info["vcodec"] == "hevc" else []  # QuickTime/iPhone เปิด HEVC ใน mp4 ได้เมื่อมีแท็กนี้

    applied, args, chains, labels = [], [ffmpeg, "-v", "error", "-y", "-i", str(video)], [], []
    clip_db = None
    for s, path in zip(items, files):
        at, dur, target = float(s["at"]), s.get("dur"), float(s.get("db", DEFAULT_DB))
        name = path.name
        if info["duration"] and at >= info["duration"]:
            print(f"  ข้าม {name}: at={at:.2f}s เลยท้ายคลิป ({info['duration']:.2f}s)")
            continue
        fch = channels_of(path)
        snd = pcm(path, dur=dur, channels=fch)
        if len(snd) == 0:
            raise kit.KitError(f"ไฟล์เสียง {name} ว่างเปล่า")
        sound_db = rms_db(snd)
        if sound_db <= -80:
            print(f"  ข้าม {name}: ไฟล์เงียบสนิท")
            continue
        voice_db = rms_db(pcm(video, ss=at, dur=max(len(snd) / SR, MIN_VOICE_WINDOW), channels=info["channels"]))
        note = ""
        if voice_db < SILENT_DB:  # เอฟเฟกต์ตกช่วงเงียบ: อ้างอิงระดับเสียงพูดทั้งคลิปแทน -90 dB
            if clip_db is None:
                clip_db = rms_db(pcm(video, channels=info["channels"]))
            voice_db, note = clip_db, " · ช่วงเงียบ ใช้ระดับทั้งคลิป"
        g = gain_db(voice_db, sound_db, target)
        actual = len(snd) / SR
        i = len(labels) + 1
        trim = ""
        if dur:
            trim = f"atrim=0:{dur:g},afade=t=out:st={max(0.0, min(float(dur), actual) - FADE):.3f}:d={FADE},"
        ms = int(round(at * 1000))
        args += ["-i", str(path)]
        chains.append(f"[{i}:a]aresample={sr},{to_layout(fch, layout)}aformat=channel_layouts={layout},{trim}volume={g:.1f}dB,adelay={ms}:all=1[s{i}]")
        labels.append(f"[s{i}]")
        applied.append({"file": str(path), "at": at, "gain_db": round(g, 1), "voice_db": round(voice_db, 1), "target_db": target, "why": s.get("why", "")})
        print(f"  {at:6.2f}s {name} → {g:+.1f} dB (เสียงพูด {voice_db:.1f} · เป้า {target:g} dB ใต้เสียงพูด{note})")

    voice_chain = f"[0:a]aformat=sample_rates={sr}:channel_layouts={layout}[v0]"
    limiter = f"alimiter=limit={LIMIT}:level=false:latency=true"
    if labels:
        tail = f"[v0]{''.join(labels)}amix=inputs={len(labels) + 1}:duration=first:normalize=0,{limiter}[a]"
    else:  # ไม่มีเอฟเฟกต์ก็ยังผ่าน limiter: ที่เดียวที่คุมพีคของเสียงสุดท้าย (ห้าม -c:a copy)
        tail = f"[v0]{limiter}[a]"
    graph = ";".join([voice_chain, *chains, tail])
    audio = ["-filter_complex", graph, "-map", "0:v:0", "-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    cmd = args + audio + ["-c:v", "copy", *tag, "-movflags", "+faststart", str(tmp)]
    try:
        kit.run(cmd)
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    print(f"✓ {out} · {len(applied)} เสียงเอฟเฟกต์ (ผ่าน limiter)")
    return applied


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m engine.sfx_mix", description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("sfx_json")
    ap.add_argument("out")
    ap.add_argument("--force", action="store_true", help="มิกซ์ใหม่แม้ out จะใหม่กว่าต้นทาง")
    a = ap.parse_args(argv)
    try:
        mix(a.video, a.sfx_json, a.out, force=a.force)
    except kit.KitError as e:
        print(f"ผิดพลาด: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"ผิดพลาด: เขียนไฟล์ไม่ได้ ({e.filename or a.out}: {e.strerror or e}) — เช็คว่าโฟลเดอร์ปลายทางมีอยู่จริง ไฟล์ไม่ได้เปิดค้างในโปรแกรมอื่น และมีสิทธิ์เขียน", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
