"""Thai speech-to-text for tad-clip, on faster-whisper (CPU int8 by default, CUDA float16 when a GPU exists).

`transcribe()` returns whisper.cpp `-ojf`-compatible JSON — one token per word — so the subtitle step can read it:
  {"transcription": [{"offsets": {"from": ms, "to": ms}, "text": str,
                      "tokens": [{"text": str, "offsets": {"from": ms, "to": ms}}]}]}

  python -m engine.asr transcribe <media> --out asr.json [--model M] [--prompt TEXT] [--force]

Run from the kit root. ffmpeg decodes every input (video or audio, any format) to 16 kHz mono first.
"""
import argparse
import functools
import json
import os
import sys
import tempfile
import wave
from pathlib import Path

try:
    from engine import kit
except ImportError:  # started as a plain script instead of `python -m engine.asr`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from engine import kit

KitError = kit.KitError
SR = 16000


def utf8_stdio():
    """Thai output must not crash a Windows console that defaults to cp1252/cp874."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def write_text_atomic(path, text):
    """Write via a temp file so an interrupted run never leaves half a file that later runs would skip."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def write_json_atomic(path, obj, indent=2):
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=indent) + "\n")


# ---------------------------------------------------------------- model

def _cuda_devices() -> int:
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def settings(model=None):
    """(model name, device, compute_type) from config.whisper; env TAD_CLIP_MODEL beats the config model."""
    cfg = kit.load_config().get("whisper") or {}
    name = model or os.environ.get("TAD_CLIP_MODEL") or cfg.get("model") or "large-v3"
    device = cfg.get("device") or "auto"
    compute = cfg.get("compute_type") or "auto"
    if device == "auto":
        device = "cuda" if _cuda_devices() > 0 else "cpu"
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    return name, device, compute


@functools.lru_cache(maxsize=None)
def _load_cached(name, device, compute, root):
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from faster_whisper import WhisperModel
    kw = dict(device=device, compute_type=compute, download_root=root)
    try:  # already downloaded -> no network round-trip, works offline
        return WhisperModel(name, local_files_only=True, **kw)
    except Exception:
        pass
    print(f"กำลังดาวน์โหลดโมเดล {name} ครั้งแรก (ไฟล์ใหญ่ ใช้เวลาสักพัก) ครั้งต่อไปใช้งานออฟไลน์ได้", file=sys.stderr, flush=True)
    try:
        return WhisperModel(name, local_files_only=False, **kw)
    except Exception as e:
        raise KitError(f"โหลดโมเดล {name} ไม่สำเร็จ — ครั้งแรกต้องต่ออินเทอร์เน็ตเพื่อโหลดโมเดล แล้วสั่งใหม่อีกครั้ง\n"
                       f"สาเหตุ: {type(e).__name__}: {e}") from e


def load_model():
    """The WhisperModel for this machine, loaded once per process (models live in ~/.tad-clip/models)."""
    name, device, compute = settings()
    return _load_cached(name, device, compute, str(kit.models_dir()))


# ---------------------------------------------------------------- audio in

def extract_wav(media, wav):
    """Any video/audio file -> 16 kHz mono PCM WAV via ffmpeg."""
    media = Path(media)
    if not media.is_file():
        raise KitError(f"ไม่พบไฟล์ {media}")
    try:
        kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-i", media, "-vn", "-ac", "1", "-ar", str(SR),
                 "-c:a", "pcm_s16le", wav])
    except KitError as e:
        if "ไม่พบโปรแกรม" in str(e):
            raise
        raise KitError(f"ดึงเสียงจาก {media.name} ไม่ได้ — ไฟล์อาจเสียหรือไม่มีเสียงพูด\n{e}") from e


def _audio_input(wav):
    """16 kHz mono 16-bit WAV -> float32 array (skips faster-whisper's own decoder and its path handling).
    Any other WAV is handed over as a path and decoded by faster-whisper."""
    try:
        with wave.open(str(wav), "rb") as w:
            if (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, SR, 2):
                import numpy as np
                return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    except (wave.Error, EOFError):
        pass
    return str(wav)


def _ms(seconds) -> int:
    return int(round(float(seconds) * 1000))


def _segment(seg) -> dict:
    a, b = _ms(seg.start), _ms(seg.end)
    tokens = [{"text": w.word, "offsets": {"from": _ms(w.start), "to": max(_ms(w.start), _ms(w.end))}}
              for w in (getattr(seg, "words", None) or [])]
    if not tokens:  # no word timings: keep the text as one token so nothing is lost downstream
        tokens = [{"text": seg.text, "offsets": {"from": a, "to": b}}]
    return {"offsets": {"from": a, "to": max(a, b)}, "text": seg.text, "tokens": tokens}


# ---------------------------------------------------------------- public API

def transcribe(path, prompt=None) -> dict:
    """Whole clip -> whisper.cpp -ojf-shaped dict (one token per recognised word, times in ms)."""
    media = Path(path)
    with tempfile.TemporaryDirectory(prefix="tad-clip-asr-", ignore_cleanup_errors=True) as tmp:
        wav = Path(tmp) / "audio.wav"
        extract_wav(media, wav)
        audio = _audio_input(wav)
        model = load_model()
        segments, _ = model.transcribe(audio, language="th", word_timestamps=True, vad_filter=True,
                                       vad_parameters={"min_silence_duration_ms": 500}, beam_size=5,
                                       initial_prompt=prompt)
        return {"transcription": [_segment(s) for s in segments]}


def transcribe_many(wav_paths, prompt=None) -> list:
    """Short WAV clips -> one text each. One model load; no text carried between clips, no VAD."""
    paths = list(wav_paths)
    if not paths:
        return []
    model = load_model()
    out = []
    for p in paths:
        segments, _ = model.transcribe(_audio_input(Path(p)), language="th", beam_size=5,
                                       condition_on_previous_text=False, vad_filter=False, initial_prompt=prompt)
        out.append(" ".join(t for t in (s.text.strip() for s in segments) if t))
    return out


def glossary_prompt():
    """The user's names/terms hint from config.glossary.prompt_hint (None when empty)."""
    return (kit.load_config().get("glossary") or {}).get("prompt_hint") or None


# ---------------------------------------------------------------- CLI

def _cmd_transcribe(args) -> int:
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"ข้าม — มี {out} อยู่แล้ว (ใช้ --force เพื่อถอดเสียงใหม่)")
        return 0
    prompt = args.prompt if args.prompt is not None else glossary_prompt()
    previous = os.environ.get("TAD_CLIP_MODEL")
    if args.model:
        os.environ["TAD_CLIP_MODEL"] = args.model
    try:
        name, device, compute = settings()
        print(f"ถอดเสียง {Path(args.media).name} · โมเดล {name} ({device}/{compute}) …", file=sys.stderr, flush=True)
        result = transcribe(args.media, prompt=prompt)
    finally:
        if args.model:
            if previous is None:
                os.environ.pop("TAD_CLIP_MODEL", None)
            else:
                os.environ["TAD_CLIP_MODEL"] = previous
    write_json_atomic(out, result)
    segs = result["transcription"]
    print(f"✓ {out} · {len(segs)} segments · {sum(len(s['tokens']) for s in segs)} tokens")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engine.asr", description="ถอดเสียงไทยด้วย faster-whisper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("transcribe", help="ถอดเสียงวิดีโอ/เสียง -> asr.json (รูปแบบเดียวกับ whisper.cpp -ojf)")
    t.add_argument("media")
    t.add_argument("--out", required=True, help="ไฟล์ asr.json ที่จะเขียน")
    t.add_argument("--model", help="ชื่อโมเดล (ค่าเริ่มต้นจาก config.whisper.model)")
    t.add_argument("--prompt", help="คำใบ้ชื่อ/ศัพท์เฉพาะ (ค่าเริ่มต้นจาก config.glossary.prompt_hint)")
    t.add_argument("--force", action="store_true", help="ทำใหม่แม้มีไฟล์ผลลัพธ์แล้ว")
    args = ap.parse_args(argv)
    try:
        return _cmd_transcribe(args)
    except KitError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    utf8_stdio()
    sys.exit(main())
