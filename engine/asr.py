"""Thai speech-to-text for tad-clip. Two backends, one output shape.

  whispercpp      whisper.cpp (`whisper-cli`, Metal on a Mac) -- about 10x faster than faster-whisper on a Mac CPU
  faster-whisper  works everywhere; CPU int8, or CUDA float16 when an NVIDIA GPU exists

config.whisper.backend: "auto" (default) | "whispercpp" | "faster-whisper". auto = a Mac that has `whisper-cli` and
the ggml model file uses whisper.cpp, everything else faster-whisper.

`transcribe()` returns whisper.cpp `-ojf`-compatible JSON -- the same dict from both backends:
  {"transcription": [{"offsets": {"from": ms, "to": ms}, "text": str,
                      "tokens": [{"text": str, "offsets": {"from": ms, "to": ms}}]}]}

  python -m engine.asr transcribe <media> --out asr.json [--model M] [--prompt TEXT] [--force]

Run from the kit root. ffmpeg decodes every input (video or audio, any format) to 16 kHz mono first.
"""
import argparse
import codecs
import functools
import gc
import json
import os
import subprocess
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
SEED = 1234   # faster-whisper's temperature fallback samples; a fixed seed makes the same audio give the same text

CPP_NAMES = {"whispercpp", "whisper.cpp", "whisper-cpp", "cpp"}
FW_NAMES = {"faster-whisper", "fasterwhisper", "faster_whisper", "faster"}
DTW_PRESETS = {"tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en",
               "large.v1", "large.v2", "large.v3", "large.v3.turbo"}


def utf8_stdio():
    """Thai output must not crash a Windows console that defaults to cp1252/cp874."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def notice(msg):
    print(msg, file=sys.stderr, flush=True)


def write_text_atomic(path, text):
    """Write via a temp file so an interrupted run never leaves half a file that later runs would skip."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def write_json_atomic(path, obj, indent=2):
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=indent) + "\n")


# ---------------------------------------------------------------- settings + backend choice

def _whisper_cfg() -> dict:
    return kit.load_config().get("whisper") or {}


def model_name(model=None) -> str:
    """env TAD_CLIP_MODEL beats config.whisper.model."""
    return model or os.environ.get("TAD_CLIP_MODEL") or _whisper_cfg().get("model") or "large-v3"


def _cuda_devices() -> int:
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


_FORCE_CPU = False   # set after a CUDA failure so the rest of the process stays on the CPU


def settings(model=None):
    """(model name, device, compute_type) for faster-whisper."""
    cfg = _whisper_cfg()
    device = cfg.get("device") or "auto"
    compute = cfg.get("compute_type") or "auto"
    if device == "auto":
        device = "cuda" if _cuda_devices() > 0 else "cpu"
    if _FORCE_CPU and device == "cuda":
        device, compute = "cpu", "int8"
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    return model_name(model), device, compute


def cpp_cli():
    """Path of whisper-cli (config.tools, then ~/.tad-clip/bin, then PATH) or None."""
    try:
        return kit.tool("whisper-cli")
    except KitError:
        return None


def ggml_path() -> Path:
    """env TAD_CLIP_GGML (full path) or <models dir>/ggml-<model>.bin."""
    env = os.environ.get("TAD_CLIP_GGML")
    if env:
        return Path(env).expanduser()
    return kit.models_dir() / f"ggml-{model_name()}.bin"


def cpp_ready():
    """(whisper-cli path, ggml path) when both exist, else None."""
    cli, ggml = cpp_cli(), ggml_path()
    return (cli, ggml) if cli and ggml.is_file() else None


def backend_choice():
    """-> (backend name, chosen explicitly?). auto picks whisper.cpp on a Mac that has whisper-cli + the ggml file."""
    raw = str(_whisper_cfg().get("backend") or "auto").strip().lower()
    if raw in CPP_NAMES:
        if cpp_ready() is None:
            raise KitError(f"ตั้ง whisper.backend เป็น whispercpp แต่ไม่พบ "
                           f"{'whisper-cli' if cpp_cli() is None else ggml_path().name} "
                           f"— ติดตั้งให้ครบ หรือเปลี่ยนเป็น \"faster-whisper\" ใน ~/.tad-clip/config.json")
        return "whispercpp", True
    if raw in FW_NAMES:
        return "faster-whisper", True
    if raw != "auto":
        raise KitError(f"whisper.backend \"{raw}\" ไม่รู้จัก — ใช้ auto, whispercpp หรือ faster-whisper")
    return ("whispercpp" if kit.IS_MAC and cpp_ready() else "faster-whisper"), False


def dtw_preset(model):
    """whisper.cpp `-dtw` preset for a model name (large-v3 -> large.v3), None when it has none."""
    name = {"turbo": "large-v3-turbo"}.get(model, model)
    preset = name.replace("-", ".") if name.startswith("large-") else name
    return preset if preset in DTW_PRESETS else None


# ---------------------------------------------------------------- faster-whisper model

@functools.lru_cache(maxsize=None)
def _load_cached(name, device, compute, root):
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from faster_whisper import WhisperModel
    kw = dict(device=device, compute_type=compute, download_root=root)
    try:  # already downloaded -> no network round-trip, works offline
        return WhisperModel(name, local_files_only=True, **kw)
    except FileNotFoundError:   # "not in the local cache" -- the only error that means "go download it"
        pass
    except (RuntimeError, ValueError):   # CUDA / out of memory / unsupported compute type: load_model() decides
        raise
    except Exception as e:
        raise KitError(f"โหลดโมเดล {name} ไม่ได้ ({type(e).__name__}: {e})") from e
    notice(f"กำลังดาวน์โหลดโมเดล {name} ครั้งแรก (ไฟล์ใหญ่ ใช้เวลาสักพัก) ครั้งต่อไปใช้งานออฟไลน์ได้")
    try:
        return WhisperModel(name, local_files_only=False, **kw)
    except (RuntimeError, ValueError):
        raise
    except OSError as e:        # no network, DNS, HTTP errors
        raise KitError(f"โหลดโมเดล {name} ไม่สำเร็จ — ครั้งแรกต้องต่ออินเทอร์เน็ตเพื่อโหลดโมเดล แล้วสั่งใหม่อีกครั้ง\n"
                       f"สาเหตุ: {type(e).__name__}: {e}") from e
    except Exception as e:
        raise KitError(f"โหลดโมเดล {name} ไม่ได้ ({type(e).__name__}: {e})") from e


def _cuda_fallback(why):
    global _FORCE_CPU
    _FORCE_CPU = True
    first = str(why).strip().splitlines()[0] if str(why).strip() else type(why).__name__
    notice(f"ใช้การ์ดจอ (CUDA) ไม่ได้ ({first}) — สลับไปใช้ CPU แทน (ช้ากว่า แต่ทำงานต่อได้)")


def _gpu_trouble(e) -> bool:
    """A failure the CPU can work around: CUDA/cuDNN/out-of-memory (RuntimeError) or a compute type the card does not
    support, e.g. float16 on an old GPU (ValueError). A wrong model name is not one of them."""
    return isinstance(e, RuntimeError) or (isinstance(e, ValueError) and "Invalid model size" not in str(e))


def load_model(model=None):
    """The WhisperModel for this machine, loaded once per process (models live in ~/.tad-clip/models).
    A CUDA failure drops to CPU int8 with a notice instead of stopping."""
    name, device, compute = settings(model)
    root = str(kit.models_dir())
    try:
        return _load_cached(name, device, compute, root)
    except (RuntimeError, ValueError) as e:
        if device != "cuda" or not _gpu_trouble(e):
            raise KitError(f"โหลดโมเดล {name} ไม่ได้ ({type(e).__name__}: {e})") from e
        _cuda_fallback(e)
        return _load_cached(name, "cpu", "int8", root)


def _seed():
    """Fixed seed for ctranslate2's sampler (a no-op on a ctranslate2 that lacks the function)."""
    try:
        import ctranslate2
        set_seed = getattr(ctranslate2, "set_random_seed", None)
    except ImportError:
        return
    if set_seed is not None:
        set_seed(SEED)


def _fresh_model():
    """Forget the cached model so the next load_model() builds a new one.

    ctranslate2 keeps its random generator per worker thread and reads the seed only when that worker first samples:
    on a long-lived model a later set_random_seed() changes nothing and consecutive calls drift apart (measured:
    seeding before each of three calls on one model gave three different texts; a new model per call gave three equal
    ones). Loading costs about 0.1 s for `base` and 4 s for `large-v3` on a CPU, small next to the transcription."""
    _load_cached.cache_clear()
    gc.collect()   # release the old weights before the new ones load (8 GB machines)


def _run_model(fn):
    """fn(model) with a fresh model from load_model(); a CUDA failure during inference reloads on the CPU once.
    fn() calls _seed() right before each model.transcribe()."""
    _fresh_model()
    model = load_model()
    try:
        return fn(model)
    except (RuntimeError, ValueError) as e:
        if settings()[1] == "cuda" and _gpu_trouble(e):
            _cuda_fallback(e)
            return fn(load_model())
        raise KitError(f"ถอดเสียงไม่สำเร็จ ({type(e).__name__}: {e})") from e


# ---------------------------------------------------------------- audio in

def extract_wav(media, wav):
    """Any video/audio file -> 16 kHz mono PCM WAV via ffmpeg."""
    media = Path(media)
    if not media.is_file():
        raise KitError(f"ไม่พบไฟล์ {media}")
    try:
        # first_pts=0: pad the start so sample 0 is container time 0 -- the clock roughcut's phrases and render use
        kit.run([kit.tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-i", media, "-vn",
                 "-af", "aresample=async=1:first_pts=0", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", wav])
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


# ---------------------------------------------------------------- whisper.cpp

def normalize_cpp(raw) -> dict:
    """`whisper-cli -ojf` output (bytes) -> the shape transcribe() returns.

    whisper.cpp writes each token's raw bytes, so a Thai character can be cut across two or three tokens and the
    file is then not valid UTF-8 as a whole. Consecutive tokens are merged until their bytes form whole characters
    (first token's `from`, last token's `to`); special tokens ([_BEG_], [_TT_150]) and the extra fields are dropped;
    stray or dangling bytes are discarded. A character cut across two segments lands in the later one."""
    data = json.loads(raw.decode("utf-8", errors="surrogateescape")) if isinstance(raw, (bytes, bytearray)) else raw
    decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
    start = None   # `from` of the first token of the character being assembled
    segments = []
    for seg in data.get("transcription") or []:
        tokens = []
        for t in seg.get("tokens") or []:
            text = t.get("text", "")
            if text.startswith("[_"):          # [_BEG_]  [_TT_150]  [_EOT_]: whisper.cpp control tokens (same rule as make_subs)
                continue
            f, to = int(t["offsets"]["from"]), int(t["offsets"]["to"])
            if start is None:
                start = f
            out = decoder.decode(text.encode("utf-8", errors="surrogateescape"), final=False)
            buffered = bool(decoder.getstate()[0])
            if out:
                tokens.append({"text": out, "offsets": {"from": start, "to": max(start, to)}})
                start = f if buffered else None
            elif not buffered:
                start = None
        a, b = int(seg["offsets"]["from"]), int(seg["offsets"]["to"])
        if not tokens:
            text = (seg.get("text") or "").encode("utf-8", errors="surrogateescape").decode("utf-8", errors="ignore")
            if not text.strip():
                continue
            tokens = [{"text": text, "offsets": {"from": a, "to": max(a, b)}}]
        segments.append({"offsets": {"from": a, "to": max(a, b)}, "text": "".join(t["text"] for t in tokens),
                         "tokens": tokens})
    return {"transcription": segments}


def _cpp_run(cmd):
    return kit.run(cmd, stdin=subprocess.DEVNULL)


def _transcribe_cpp(wav, prompt, tmp, cli, ggml):
    out = Path(tmp) / "asr"
    cmd = [cli, "-m", ggml, "-f", wav, "-l", "th"]
    preset = dtw_preset(model_name())
    if preset:                                    # -nfa (no flash attention) is required or -dtw is silently disabled
        cmd += ["-nfa", "-dtw", preset]
    cmd += ["-bs", "5", "-sns"]
    if prompt:
        cmd += ["--prompt", prompt]
    cmd += ["-ojf", "-of", out, "-np"]
    _cpp_run(cmd)
    produced = Path(str(out) + ".json")
    if not produced.is_file():
        raise KitError("whisper-cli ทำงานจบแต่ไม่ได้เขียนผลถอดเสียง — ลองสั่งใหม่ หรือตั้ง whisper.backend เป็น faster-whisper")
    return normalize_cpp(produced.read_bytes())


def _transcribe_many_cpp(wavs, prompt, cli, ggml):
    # -mc 0: no text carried from one file to the next (else earlier phrases bleed into later ones)
    cmd = [cli, "-m", ggml, "-l", "th", "-nt", "-otxt", "-np", "-mc", "0"]
    if prompt:
        cmd += ["--prompt", prompt]
    for w in wavs:
        cmd += ["-f", w]
    _cpp_run(cmd)
    out = []
    for w in wavs:
        txt = Path(str(w) + ".txt")
        raw = txt.read_bytes().decode("utf-8", errors="ignore") if txt.is_file() else ""
        out.append(" ".join(line.strip() for line in raw.splitlines() if line.strip()))
    return out


def _cpp_failed(e, explicit):
    if explicit:
        raise e
    notice("whisper.cpp ใช้ไม่ได้ในรอบนี้ — ใช้ faster-whisper แทน (ช้ากว่า)\n" + str(e).strip().splitlines()[0])


# ---------------------------------------------------------------- public API

def transcribe(path, prompt=None) -> dict:
    """Whole clip -> whisper.cpp -ojf-shaped dict (one token per recognised word or character, times in ms)."""
    media = Path(path)
    backend, explicit = backend_choice()
    with tempfile.TemporaryDirectory(prefix="tad-clip-asr-", ignore_cleanup_errors=True) as tmp:
        wav = Path(tmp) / "audio.wav"
        extract_wav(media, wav)
        if backend == "whispercpp":
            cli, ggml = cpp_ready()
            try:
                return _transcribe_cpp(wav, prompt, tmp, cli, ggml)
            except KitError as e:
                _cpp_failed(e, explicit)
        audio = _audio_input(wav)

        def run(model):
            _seed()
            segments, _ = model.transcribe(audio, language="th", word_timestamps=True, vad_filter=True,
                                           vad_parameters={"min_silence_duration_ms": 500}, beam_size=5,
                                           initial_prompt=prompt)
            return {"transcription": [_segment(s) for s in segments]}

        return _run_model(run)


def transcribe_many(wav_paths, prompt=None) -> list:
    """Short WAV clips -> one text each. One model load; no text carried between clips, no VAD."""
    paths = list(wav_paths)
    if not paths:
        return []
    backend, explicit = backend_choice()
    if backend == "whispercpp":
        cli, ggml = cpp_ready()
        try:
            return _transcribe_many_cpp(paths, prompt, cli, ggml)
        except KitError as e:
            _cpp_failed(e, explicit)

    def run(model):
        out = []
        for p in paths:
            _seed()
            segments, _ = model.transcribe(_audio_input(Path(p)), language="th", beam_size=5,
                                           condition_on_previous_text=False, vad_filter=False, initial_prompt=prompt)
            out.append(" ".join(t for t in (s.text.strip() for s in segments) if t))
        return out

    return _run_model(run)


def glossary_prompt():
    """The ASR hint: the shipped Thai hint (engine/subs/glossary-base.json) + config.glossary.prompt_hint, or None."""
    try:
        base = json.loads((kit.KIT_ROOT / "engine" / "subs" / "glossary-base.json").read_text(encoding="utf-8-sig"))
        base_hint = (base.get("prompt_hint") or "").strip()
    except (OSError, ValueError):
        base_hint = ""
    user_hint = ((kit.load_config().get("glossary") or {}).get("prompt_hint") or "").strip()
    return ", ".join(h for h in (base_hint, user_hint) if h) or None


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
        backend, _ = backend_choice()
        if backend == "whispercpp":
            how = f"whisper.cpp · {ggml_path().name}"
        else:
            name, device, compute = settings()
            how = f"faster-whisper · {name} ({device}/{compute})"
        notice(f"ถอดเสียง {Path(args.media).name} · {how} …")
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
    ap = argparse.ArgumentParser(prog="python -m engine.asr", description="ถอดเสียงไทย (whisper.cpp หรือ faster-whisper)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("transcribe", help="ถอดเสียงวิดีโอ/เสียง -> asr.json (รูปแบบเดียวกับ whisper.cpp -ojf)")
    t.add_argument("media")
    t.add_argument("--out", required=True, help="ไฟล์ asr.json ที่จะเขียน")
    t.add_argument("--model", help="ชื่อโมเดล (ค่าเริ่มต้นจาก config.whisper.model)")
    t.add_argument("--prompt", help="คำใบ้ชื่อ/ศัพท์เฉพาะ (ค่าเริ่มต้น = คำใบ้ของ kit + config.glossary.prompt_hint)")
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
