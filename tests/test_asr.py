"""Tests for engine/asr.py — model loading rules, whisper.cpp -ojf output shape, CLI skip rules.

Fast tests use a fake model / fake whisper-cli. The `slow` tests run the real thing:
  * faster-whisper model named by env TAD_CLIP_TEST_MODEL (default `base`) from the models folder of TAD_CLIP_HOME
  * whisper.cpp (macOS only): needs `whisper-cli` and a ggml file; point env TAD_CLIP_GGML at one, e.g.
    TAD_CLIP_GGML=~/.claude/models/ggml-large-v3.bin (skipped when either is missing)
Run:   TAD_CLIP_HOME=~/.tad-clip-dev ~/.tad-clip-dev/venv/bin/python -m pytest tests/test_asr.py -q
Skip the slow ones with:  -m "not slow"
"""
import json
import re
import shutil
import sys
import wave
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import asr, kit  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "คลิป ทดสอบ.mp4"
SR = 16000
REAL_BACKEND_CHOICE = asr.backend_choice


@pytest.fixture(autouse=True)
def faster_whisper_unless_asked(request, monkeypatch):
    """The fast tests below exercise the faster-whisper path whatever whisper-cli/ggml this machine has."""
    if "real_backend" not in request.fixturenames:
        monkeypatch.setattr(asr, "backend_choice", lambda: ("faster-whisper", False))


# ---------------------------------------------------------------- helpers

def assert_ojf_schema(d):
    """whisper.cpp `-ojf`-compatible, minimal: what engine/subs consumes."""
    assert set(d) == {"transcription"} and isinstance(d["transcription"], list)
    prev_end = 0
    for seg in d["transcription"]:
        assert set(seg) == {"offsets", "text", "tokens"}
        assert set(seg["offsets"]) == {"from", "to"}
        a, b = seg["offsets"]["from"], seg["offsets"]["to"]
        assert type(a) is int and type(b) is int and 0 <= a <= b
        assert a >= prev_end - 1   # ms, segments in order
        prev_end = b
        assert isinstance(seg["text"], str) and isinstance(seg["tokens"], list) and seg["tokens"]
        for tok in seg["tokens"]:
            assert set(tok) == {"text", "offsets"} and isinstance(tok["text"], str)
            ta, tb = tok["offsets"]["from"], tok["offsets"]["to"]
            assert type(ta) is int and type(tb) is int and 0 <= ta <= tb


class FakeModel:
    def __init__(self, segments=None, texts=None):
        self.calls = []
        self.segments = segments if segments is not None else [
            NS(start=0.5, end=2.0, text=" สวัสดีครับ", words=[
                NS(start=0.5, end=1.2, word="สวัสดี", probability=0.9),
                NS(start=1.2, end=2.0004, word="ครับ", probability=0.8)]),
            NS(start=2.4, end=3.0, text="วันนี้", words=[NS(start=2.4, end=3.0, word="วันนี้", probability=0.7)]),
            NS(start=3.5, end=4.0, text="เงียบ", words=None),
        ]
        self.texts = texts

    def transcribe(self, audio, **kw):
        self.calls.append((audio, kw))
        if self.texts is not None:
            return iter([NS(start=0, end=1, text=self.texts[len(self.calls) - 1], words=None)]), NS(language="th")
        return iter(self.segments), NS(language="th")


def make_wav(path, seconds=1.5, freq=220):
    t = np.arange(int(seconds * SR)) / SR
    pcm = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return Path(path)


@pytest.fixture
def media(tmp_path):
    """A small mp4 with Thai + space in the name (ffmpeg is the only decoder involved)."""
    wav = make_wav(tmp_path / "src.wav", 2.0)
    out = tmp_path / "คลิป ทดสอบ 1.mp4"
    kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=2", "-i", wav,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out])
    return out


@pytest.fixture
def fake_model(monkeypatch):
    m = FakeModel()
    monkeypatch.setattr(asr, "load_model", lambda *a, **k: m)
    return m


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Fresh TAD_CLIP_HOME that still finds the real ffmpeg; clears the per-process model cache."""
    tools = kit.load_config()["tools"]
    monkeypatch.setenv("TAD_CLIP_HOME", str(tmp_path / "ที่เก็บ home"))
    monkeypatch.delenv("TAD_CLIP_MODEL", raising=False)
    monkeypatch.setattr(asr, "_FORCE_CPU", False)
    kit.save_config({"tools": tools})
    asr._load_cached.cache_clear()
    yield tmp_path / "ที่เก็บ home"
    asr._load_cached.cache_clear()


# ---------------------------------------------------------------- model loading

class RecorderModel:
    instances = []

    def __init__(self, name, **kw):
        self.name, self.kw = name, kw
        RecorderModel.instances.append(self)


@pytest.fixture
def recorder(monkeypatch, isolated_home):
    import faster_whisper
    RecorderModel.instances = []
    monkeypatch.setattr(faster_whisper, "WhisperModel", RecorderModel)
    return RecorderModel


def _cuda(monkeypatch, n):
    import ctranslate2
    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: n)


def test_cpu_defaults_are_int8_and_download_root_is_models_dir(recorder, monkeypatch, isolated_home):
    _cuda(monkeypatch, 0)
    m = asr.load_model()
    assert m.name == "large-v3"
    assert m.kw["device"] == "cpu" and m.kw["compute_type"] == "int8"
    assert m.kw["download_root"] == str(isolated_home / "models")


def test_cuda_picks_float16(recorder, monkeypatch):
    _cuda(monkeypatch, 1)
    m = asr.load_model()
    assert (m.kw["device"], m.kw["compute_type"]) == ("cuda", "float16")


def test_config_values_are_honoured(recorder, monkeypatch):
    _cuda(monkeypatch, 1)
    cfg = kit.load_config()
    kit.save_config({"tools": cfg["tools"], "whisper": {"model": "small", "device": "cpu", "compute_type": "int8_float32"}})
    m = asr.load_model()
    assert (m.name, m.kw["device"], m.kw["compute_type"]) == ("small", "cpu", "int8_float32")


def test_env_overrides_the_model_name(recorder, monkeypatch):
    _cuda(monkeypatch, 0)
    monkeypatch.setenv("TAD_CLIP_MODEL", "base")
    assert asr.load_model().name == "base"


def test_model_is_loaded_once_per_process(recorder, monkeypatch):
    _cuda(monkeypatch, 0)
    assert asr.load_model() is asr.load_model()
    assert len(recorder.instances) == 1


def test_loading_prefers_local_files_so_it_works_offline(recorder, monkeypatch):
    _cuda(monkeypatch, 0)
    assert asr.load_model().kw.get("local_files_only") is True


def test_missing_model_falls_back_to_download_then_gives_a_thai_error(monkeypatch, isolated_home):
    import faster_whisper
    _cuda(monkeypatch, 0)
    seen = []

    def boom(name, **kw):
        seen.append(kw.get("local_files_only"))
        raise FileNotFoundError("not cached") if kw.get("local_files_only") else OSError("no network")

    monkeypatch.setattr(faster_whisper, "WhisperModel", boom)
    with pytest.raises(kit.KitError) as e:
        asr.load_model()
    assert seen == [True, False]
    assert "large-v3" in str(e.value) and "อินเทอร์เน็ต" in str(e.value)


def test_only_the_not_cached_error_triggers_a_download(monkeypatch, isolated_home):
    """An out-of-memory / bad compute type at load time must not be reported as 'needs internet'."""
    import faster_whisper
    _cuda(monkeypatch, 0)
    seen = []

    def boom(name, **kw):
        seen.append(kw.get("local_files_only"))
        raise RuntimeError("out of memory")

    monkeypatch.setattr(faster_whisper, "WhisperModel", boom)
    with pytest.raises(kit.KitError) as e:
        asr.load_model()
    assert seen == [True]                                   # never went looking on the network
    assert "อินเทอร์เน็ต" not in str(e.value) and "out of memory" in str(e.value)


def test_cuda_failure_at_load_falls_back_to_cpu_int8_with_a_thai_notice(monkeypatch, isolated_home, capsys):
    import faster_whisper
    _cuda(monkeypatch, 1)
    built = []

    def maybe(name, **kw):
        built.append((kw["device"], kw["compute_type"]))
        if kw["device"] == "cuda":
            raise RuntimeError("CUDA failed with error out of memory")
        return RecorderModel(name, **kw)

    monkeypatch.setattr(faster_whisper, "WhisperModel", maybe)
    m = asr.load_model()
    assert (m.kw["device"], m.kw["compute_type"]) == ("cpu", "int8")
    assert built == [("cuda", "float16"), ("cpu", "int8")]
    err = capsys.readouterr().err
    assert "CUDA" in err and "CPU" in err
    assert asr.load_model() is m                           # and it stays on cpu for the rest of the process
    assert asr.settings()[1:] == ("cpu", "int8")


def test_cuda_failure_on_first_inference_reloads_on_cpu(media, monkeypatch, isolated_home, capsys):
    import faster_whisper
    _cuda(monkeypatch, 1)
    RecorderModel.instances = []
    good = FakeModel()

    class Flaky(RecorderModel):
        def transcribe(self, audio, **kw):
            if self.kw["device"] == "cuda":
                def gen():
                    raise RuntimeError("Library libcudnn_ops.so.9 is not found or cannot be loaded")
                    yield
                return gen(), NS(language="th")
            return good.transcribe(audio, **kw)

    monkeypatch.setattr(faster_whisper, "WhisperModel", Flaky)
    d = asr.transcribe(media)
    assert_ojf_schema(d)
    assert [i.kw["device"] for i in RecorderModel.instances] == ["cuda", "cpu"]
    assert "CPU" in capsys.readouterr().err


GPU_VALUE_ERROR = "Requested float16 compute type, but the target device or backend do not support efficient float16 computation."


def test_value_error_at_load_on_cuda_falls_back_to_cpu_int8(monkeypatch, isolated_home, capsys):
    import faster_whisper
    _cuda(monkeypatch, 1)
    built = []

    def maybe(name, **kw):
        built.append((kw["device"], kw["compute_type"], kw["local_files_only"]))
        if kw["device"] == "cuda":
            raise ValueError(GPU_VALUE_ERROR)
        return RecorderModel(name, **kw)

    monkeypatch.setattr(faster_whisper, "WhisperModel", maybe)
    m = asr.load_model()
    assert (m.kw["device"], m.kw["compute_type"]) == ("cpu", "int8")
    assert built == [("cuda", "float16", True), ("cpu", "int8", True)]     # no pointless download attempt in between
    assert "CUDA" in capsys.readouterr().err


def test_value_error_on_first_inference_on_cuda_reloads_on_cpu(media, monkeypatch, isolated_home, capsys):
    import faster_whisper
    _cuda(monkeypatch, 1)
    RecorderModel.instances = []
    good = FakeModel()

    class Old(RecorderModel):
        def transcribe(self, audio, **kw):
            if self.kw["device"] == "cuda":
                raise ValueError(GPU_VALUE_ERROR)
            return good.transcribe(audio, **kw)

    monkeypatch.setattr(faster_whisper, "WhisperModel", Old)
    assert_ojf_schema(asr.transcribe(media))
    assert [i.kw["device"] for i in RecorderModel.instances] == ["cuda", "cpu"]
    assert "CPU" in capsys.readouterr().err


def test_invalid_model_name_is_not_blamed_on_the_gpu(monkeypatch, isolated_home, capsys):
    import faster_whisper
    _cuda(monkeypatch, 1)
    seen = []

    def bad(name, **kw):
        seen.append(kw["device"])
        raise ValueError("Invalid model size 'nonsense', expected one of: tiny, base")

    monkeypatch.setattr(faster_whisper, "WhisperModel", bad)
    with pytest.raises(kit.KitError) as e:
        asr.load_model("nonsense")
    assert seen == ["cuda"] and "nonsense" in str(e.value)               # no cpu retry, no CUDA notice
    assert "CUDA" not in capsys.readouterr().err


def test_value_error_on_cpu_is_a_thai_error(media, monkeypatch, isolated_home):
    import faster_whisper
    _cuda(monkeypatch, 0)

    class Odd(RecorderModel):
        def transcribe(self, audio, **kw):
            raise ValueError("bad audio shape")

    monkeypatch.setattr(faster_whisper, "WhisperModel", Odd)
    with pytest.raises(kit.KitError) as e:
        asr.transcribe(media)
    assert "bad audio shape" in str(e.value)


def test_runtime_error_on_cpu_is_a_thai_error_not_a_traceback(media, monkeypatch, isolated_home):
    import faster_whisper
    _cuda(monkeypatch, 0)

    class Broken(RecorderModel):
        def transcribe(self, audio, **kw):
            raise RuntimeError("something native broke")

    monkeypatch.setattr(faster_whisper, "WhisperModel", Broken)
    with pytest.raises(kit.KitError) as e:
        asr.transcribe(media)
    assert "something native broke" in str(e.value)


# ---------------------------------------------------------------- transcribe

def test_transcribe_returns_whisper_cpp_shape(media, fake_model):
    d = asr.transcribe(media)
    assert_ojf_schema(d)
    seg0 = d["transcription"][0]
    assert seg0["offsets"] == {"from": 500, "to": 2000}
    assert seg0["text"] == " สวัสดีครับ"
    assert [t["text"] for t in seg0["tokens"]] == ["สวัสดี", "ครับ"]
    assert seg0["tokens"][1]["offsets"] == {"from": 1200, "to": 2000}
    # a segment without word timings still carries its text as one token
    assert d["transcription"][2]["tokens"] == [{"text": "เงียบ", "offsets": {"from": 3500, "to": 4000}}]


def test_transcribe_passes_the_exact_whisper_settings(media, fake_model):
    asr.transcribe(media, prompt="ชื่อ Black Pan")
    audio, kw = fake_model.calls[0]
    assert kw == dict(language="th", word_timestamps=True, vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
                      beam_size=5, initial_prompt="ชื่อ Black Pan")
    # ffmpeg extracted 16 kHz mono; the array (not a path) is handed to the model
    assert isinstance(audio, np.ndarray) and audio.dtype == np.float32
    assert len(audio) / SR == pytest.approx(2.0, abs=0.15)


def test_transcribe_prompt_defaults_to_none(media, fake_model):
    asr.transcribe(media)
    assert fake_model.calls[0][1]["initial_prompt"] is None


def test_transcribe_cleans_up_its_temp_wav(media, fake_model, monkeypatch, tmp_path):
    import tempfile
    made = []
    real = tempfile.TemporaryDirectory

    def spy(*a, **k):
        d = real(*a, **k)
        made.append(Path(d.name))
        return d

    monkeypatch.setattr(asr.tempfile, "TemporaryDirectory", spy)
    asr.transcribe(media)
    assert made and not made[0].exists()


def test_transcribe_accepts_audio_only_files(tmp_path, fake_model):
    wav = make_wav(tmp_path / "เสียง ทดสอบ.wav", 1.0)
    assert_ojf_schema(asr.transcribe(wav))


def test_transcribe_with_no_speech_gives_empty_transcription(media, monkeypatch):
    monkeypatch.setattr(asr, "load_model", lambda *a, **k: FakeModel(segments=[]))
    assert asr.transcribe(media) == {"transcription": []}


def test_transcribe_unreadable_media_is_a_thai_error(tmp_path, fake_model):
    bad = tmp_path / "พัง.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(kit.KitError) as e:
        asr.transcribe(bad)
    assert "พัง.mp4" in str(e.value)


# ---------------------------------------------------------------- transcribe_many

def test_transcribe_many_loads_once_and_uses_clean_per_clip_settings(tmp_path, monkeypatch):
    loads = []
    m = FakeModel(texts=[" อัน หนึ่ง ", "สอง", ""])
    monkeypatch.setattr(asr, "load_model", lambda *a, **k: loads.append(1) or m)
    wavs = [make_wav(tmp_path / f"p{i}.wav", 0.8) for i in range(3)]
    out = asr.transcribe_many(wavs)
    assert out == ["อัน หนึ่ง", "สอง", ""]
    assert len(loads) == 1 and len(m.calls) == 3
    for audio, kw in m.calls:
        assert isinstance(audio, np.ndarray)
        assert kw["condition_on_previous_text"] is False and kw["vad_filter"] is False
        assert kw["language"] == "th" and kw["beam_size"] == 5 and kw["initial_prompt"] is None


def test_transcribe_many_accepts_a_prompt_and_str_paths(tmp_path, monkeypatch):
    m = FakeModel(texts=["x"])
    monkeypatch.setattr(asr, "load_model", lambda *a, **k: m)
    asr.transcribe_many([str(make_wav(tmp_path / "ก.wav"))], prompt="คำใบ้")
    assert m.calls[0][1]["initial_prompt"] == "คำใบ้"


def test_transcribe_many_hands_other_wav_formats_to_the_model_by_path(tmp_path, monkeypatch):
    m = FakeModel(texts=["x"])
    monkeypatch.setattr(asr, "load_model", lambda *a, **k: m)
    p = tmp_path / "44k.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\0\0" * 2 * 4410)
    asr.transcribe_many([p])
    assert m.calls[0][0] == str(p)


def test_transcribe_many_of_nothing_does_not_load_the_model(monkeypatch):
    monkeypatch.setattr(asr, "load_model", lambda *a, **k: pytest.fail("model loaded"))
    assert asr.transcribe_many([]) == []


# ---------------------------------------------------------------- CLI

def test_cli_writes_asr_json_and_skips_unless_force(media, fake_model, tmp_path, capsys):
    out = tmp_path / "ผลลัพธ์ asr" / "asr.json"
    assert asr.main(["transcribe", str(media), "--out", str(out)]) == 0
    assert_ojf_schema(json.loads(out.read_text(encoding="utf-8")))
    assert len(fake_model.calls) == 1
    out.write_text('{"transcription": []}', encoding="utf-8")        # the user's edit
    capsys.readouterr()
    assert asr.main(["transcribe", str(media), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == '{"transcription": []}' and len(fake_model.calls) == 1
    assert "ข้าม" in capsys.readouterr().out
    assert asr.main(["transcribe", str(media), "--out", str(out), "--force"]) == 0
    assert len(fake_model.calls) == 2 and json.loads(out.read_text(encoding="utf-8"))["transcription"]


def test_cli_model_flag_overrides_for_this_run_only(media, tmp_path, monkeypatch, isolated_home):
    import os
    seen = []
    monkeypatch.setattr(asr, "load_model", lambda *a, **k: seen.append(os.environ.get("TAD_CLIP_MODEL")) or FakeModel())
    asr.main(["transcribe", str(media), "--out", str(tmp_path / "a.json"), "--model", "large-v3-turbo"])
    assert seen == ["large-v3-turbo"] and "TAD_CLIP_MODEL" not in os.environ


def test_cli_prompt_flag_wins_and_config_hint_is_the_default(media, tmp_path, monkeypatch, isolated_home):
    m = FakeModel()
    monkeypatch.setattr(asr, "load_model", lambda *a, **k: m)
    asr.main(["transcribe", str(media), "--out", str(tmp_path / "a.json"), "--prompt", "จากคำสั่ง"])
    cfg = kit.load_config()
    kit.save_config({"tools": cfg["tools"], "glossary": {"prompt_hint": "จากคอนฟิก"}})
    asr.main(["transcribe", str(media), "--out", str(tmp_path / "b.json")])
    first, second = [kw["initial_prompt"] for _, kw in m.calls]
    assert first == "จากคำสั่ง"
    assert second.endswith(", จากคอนฟิก") and second.startswith(base_hint())   # shipped Thai hint + the user's own


def base_hint():
    return json.loads((ROOT / "engine" / "subs" / "glossary-base.json").read_text(encoding="utf-8"))["prompt_hint"]


def test_glossary_prompt_merges_the_shipped_hint_with_the_users(isolated_home):
    assert asr.glossary_prompt() == base_hint()
    cfg = kit.load_config()
    kit.save_config({"tools": cfg["tools"], "glossary": {"prompt_hint": "ชื่อแบรนด์ Black Pan"}})
    assert asr.glossary_prompt() == base_hint() + ", ชื่อแบรนด์ Black Pan"


def test_cli_missing_file_is_a_thai_message(tmp_path, capsys):
    assert asr.main(["transcribe", str(tmp_path / "ไม่มี.mp4"), "--out", str(tmp_path / "a.json")]) == 1
    err = capsys.readouterr().err
    assert "ไม่พบไฟล์" in err and "Traceback" not in err


# ---------------------------------------------------------------- whisper.cpp: token normaliser

def cpp_json(segments):
    """Bytes shaped like `whisper-cli -ojf`: token texts are written raw, so a Thai character split across two
    tokens makes the file invalid UTF-8 as a whole (exactly what the real tool does)."""
    segs = []
    for (a, b), toks in segments:
        parts = []
        for tx, f, to in toks:
            raw = tx if isinstance(tx, bytes) else tx.encode("utf-8")
            parts.append(b'{"text":"' + raw + b'","timestamps":{"from":"00:00:00,000","to":"00:00:00,000"},'
                         b'"offsets":{"from":%d,"to":%d},"id":1,"p":0.9,"t_dtw":-1}' % (f, to))
        text = b"".join((tx if isinstance(tx, bytes) else tx.encode()) for tx, _, _ in toks if not (isinstance(tx, str) and tx.startswith("[_")))
        segs.append(b'{"timestamps":{"from":"x","to":"y"},"offsets":{"from":%d,"to":%d},"text":"' % (a, b) + text
                    + b'","tokens":[' + b",".join(parts) + b"]}")
    return b'{"systeminfo":"x","model":{"type":"large"},"result":{"language":"th"},"transcription":[' + b",".join(segs) + b"]}"


def test_normalizer_merges_a_character_split_across_tokens():
    fru = "ฤ".encode()                                    # e0 b8 a4
    raw = cpp_json([((0, 2000), [("[_BEG_]", 0, 0), ("วิก", 100, 400), (fru[:2], 400, 400), (fru[2:], 400, 480),
                                 ("ต", 480, 600), ("[_TT_150]", 600, 600)])])
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")                              # the fixture really is invalid UTF-8
    d = asr.normalize_cpp(raw)
    assert_ojf_schema(d)
    (seg,) = d["transcription"]
    assert [t["text"] for t in seg["tokens"]] == ["วิก", "ฤ", "ต"]
    assert seg["tokens"][1]["offsets"] == {"from": 400, "to": 480}   # first token's from, last token's to
    assert seg["text"] == "วิกฤต" and seg["offsets"] == {"from": 0, "to": 2000}


def test_normalizer_merges_one_byte_tokens_and_keeps_whole_character_tokens():
    b = "ก".encode()
    raw = cpp_json([((0, 900), [(bytes([b[0]]), 10, 20), (bytes([b[1]]), 20, 30), (bytes([b[2]]), 30, 50),
                                ("เพ", 50, 150), ("ื่", 150, 200), ("อ", 200, 300), (" Black", 300, 500)])])
    (seg,) = asr.normalize_cpp(raw)["transcription"]
    assert [(t["text"], t["offsets"]["from"], t["offsets"]["to"]) for t in seg["tokens"]] == [
        ("ก", 10, 50), ("เพ", 50, 150), ("ื่", 150, 200), ("อ", 200, 300), (" Black", 300, 500)]
    assert seg["text"] == "กเพื่อ Black"


def test_normalizer_drops_special_tokens_and_whisper_extras():
    raw = cpp_json([((0, 500), [("[_BEG_]", 0, 0), ("สวัสดี", 0, 300), ("[_TT_25]", 500, 500), ("[_EOT_]", 500, 500)])])
    d = asr.normalize_cpp(raw)
    assert d == {"transcription": [{"offsets": {"from": 0, "to": 500}, "text": "สวัสดี",
                                    "tokens": [{"text": "สวัสดี", "offsets": {"from": 0, "to": 300}}]}]}


@pytest.mark.parametrize("special", ["[_BEG_]", "[_TT_150", "[_", "[_weird thing]", "[_SOT_]x", "[_not_closed"])
def test_normalizer_drops_every_token_that_starts_with_bracket_underscore(special):
    raw = cpp_json([((0, 500), [(special, 0, 0), ("สวัสดี", 0, 300), (special, 300, 300)])])
    (seg,) = asr.normalize_cpp(raw)["transcription"]
    assert [t["text"] for t in seg["tokens"]] == ["สวัสดี"] and seg["text"] == "สวัสดี"


def test_normalizer_carries_a_split_character_into_the_next_segment():
    fru = "ฤ".encode()
    raw = cpp_json([((0, 1000), [("วิก", 0, 400), (fru[:2], 400, 450)]), ((1000, 2000), [(fru[2:], 1000, 1100), ("ต", 1100, 1300)])])
    a, b = asr.normalize_cpp(raw)["transcription"]
    assert [t["text"] for t in a["tokens"]] == ["วิก"] and a["text"] == "วิก"
    assert [t["text"] for t in b["tokens"]] == ["ฤ", "ต"] and b["tokens"][0]["offsets"] == {"from": 400, "to": 1100}


def test_normalizer_throws_away_stray_and_dangling_bytes():
    raw = cpp_json([((0, 800), [(b"\xa4", 0, 10), ("ก", 10, 100), (b"\xe0\xb8", 100, 120), (b"abc", 120, 200), ("ข", 200, 300),
                                (b"\xe0\xb8", 300, 320)])])
    (seg,) = asr.normalize_cpp(raw)["transcription"]
    assert "".join(t["text"] for t in seg["tokens"]) == "กabcข"
    assert "�" not in seg["text"]


def test_normalizer_output_is_clean_json_and_utf8():
    fru = "ฤ".encode()
    d = asr.normalize_cpp(cpp_json([((0, 500), [(fru[:1], 0, 10), (fru[1:], 10, 20), ("ก", 20, 90)])]))
    blob = json.dumps(d, ensure_ascii=False).encode("utf-8")           # would raise on a lone surrogate
    assert json.loads(blob.decode("utf-8")) == d


def test_normalizer_keeps_a_token_less_segment_as_one_token():
    raw = cpp_json([((0, 500), [])]).replace(b'"text":""', "\"text\":\"ว่างเปล่า\"".encode())
    (seg,) = asr.normalize_cpp(raw)["transcription"]
    assert seg["tokens"] == [{"text": "ว่างเปล่า", "offsets": {"from": 0, "to": 500}}]


def test_normalised_output_feeds_make_subs_unchanged():
    ms = pytest.importorskip("engine.subs.make_subs")
    fru = "ฤ".encode()
    raw = cpp_json([((0, 3000), [("[_BEG_]", 0, 0), ("เพื่อ", 0, 400), ("แก้", 400, 700), ("ไข", 700, 900), ("วิก", 900, 1200),
                                 (fru[:2], 1200, 1200), (fru[2:], 1200, 1400), ("ต", 1400, 1500), ("[_TT_150]", 1500, 1500)])])
    cues, _ = ms.build_cues(asr.normalize_cpp(raw), ms.load_glossary(None), 22)
    assert "วิกฤต" in "".join(c["t"] for c in cues)


# ---------------------------------------------------------------- whisper.cpp: backend choice

@pytest.fixture
def real_backend(isolated_home, monkeypatch):
    monkeypatch.setattr(asr, "backend_choice", REAL_BACKEND_CHOICE)
    monkeypatch.delenv("TAD_CLIP_GGML", raising=False)
    return isolated_home


def _tool(monkeypatch, cli="/fake/bin/whisper-cli"):
    real = kit.tool

    def fake(name):
        if name == "whisper-cli":
            if cli is None:
                raise kit.KitError("ไม่พบโปรแกรม whisper-cli")
            return cli
        return real(name)

    monkeypatch.setattr(kit, "tool", fake)


def _ggml(home, name="large-v3"):
    f = kit.models_dir() / f"ggml-{name}.bin"
    f.write_bytes(b"x")
    return f


def _config(**whisper):
    kit.save_config({"tools": kit.load_config()["tools"], "whisper": whisper})


def test_auto_picks_whispercpp_on_a_mac_with_cli_and_model(real_backend, monkeypatch):
    _tool(monkeypatch)
    _ggml(real_backend)
    monkeypatch.setattr(kit, "IS_MAC", True)
    assert asr.backend_choice() == ("whispercpp", False)


@pytest.mark.parametrize("why", ["no-cli", "no-ggml", "not-mac"])
def test_auto_falls_back_to_faster_whisper(real_backend, monkeypatch, why):
    _tool(monkeypatch, None if why == "no-cli" else "/fake/bin/whisper-cli")
    if why != "no-ggml":
        _ggml(real_backend)
    monkeypatch.setattr(kit, "IS_MAC", why != "not-mac")
    assert asr.backend_choice() == ("faster-whisper", False)


def test_config_can_force_either_backend(real_backend, monkeypatch):
    _tool(monkeypatch)
    _ggml(real_backend)
    monkeypatch.setattr(kit, "IS_MAC", True)
    _config(backend="faster-whisper")
    assert asr.backend_choice() == ("faster-whisper", True)
    _config(backend="whispercpp")
    assert asr.backend_choice() == ("whispercpp", True)
    monkeypatch.setattr(kit, "IS_MAC", False)                     # explicit choice works on any OS
    assert asr.backend_choice() == ("whispercpp", True)


def test_forcing_whispercpp_without_its_parts_is_a_thai_error(real_backend, monkeypatch):
    _tool(monkeypatch, None)
    _config(backend="whispercpp")
    with pytest.raises(kit.KitError) as e:
        asr.backend_choice()
    assert "whisper-cli" in str(e.value) and "faster-whisper" in str(e.value)
    _tool(monkeypatch)                                            # cli found, ggml missing
    with pytest.raises(kit.KitError) as e:
        asr.backend_choice()
    assert "ggml-large-v3.bin" in str(e.value)


def test_unknown_backend_name_is_a_thai_error(real_backend):
    _config(backend="nonsense")
    with pytest.raises(kit.KitError) as e:
        asr.backend_choice()
    assert "nonsense" in str(e.value)


def test_ggml_path_follows_the_model_name_and_env(real_backend, monkeypatch, tmp_path):
    assert asr.ggml_path() == kit.models_dir() / "ggml-large-v3.bin"
    monkeypatch.setenv("TAD_CLIP_MODEL", "large-v3-turbo")
    assert asr.ggml_path() == kit.models_dir() / "ggml-large-v3-turbo.bin"
    monkeypatch.setenv("TAD_CLIP_GGML", str(tmp_path / "ที่อื่น" / "my.bin"))
    assert asr.ggml_path() == tmp_path / "ที่อื่น" / "my.bin"


@pytest.mark.parametrize("model,preset", [("large-v3", "large.v3"), ("large-v3-turbo", "large.v3.turbo"), ("turbo", "large.v3.turbo"),
                                          ("large-v2", "large.v2"), ("medium", "medium"), ("small", "small"), ("base", "base"),
                                          ("tiny", "tiny"), ("small.en", "small.en"), ("distil-large-v3", None)])
def test_dtw_preset(model, preset):
    assert asr.dtw_preset(model) == preset


# ---------------------------------------------------------------- whisper.cpp: commands

class FakeCli:
    """Stands in for kit.run when the command is whisper-cli: records it and writes the files the real tool would."""
    def __init__(self, monkeypatch, asr_json=None, texts=None, fail=None):
        self.cmds, self.asr_json, self.texts, self.fail = [], asr_json, texts or {}, fail
        real = kit.run

        def run(cmd, **kw):
            cmd = [str(c) for c in cmd]
            if Path(cmd[0]).name != "whisper-cli":
                return real(cmd, **kw)
            self.cmds.append((cmd, kw))
            if self.fail:
                raise kit.KitError(self.fail)
            if "-ojf" in cmd:
                Path(cmd[cmd.index("-of") + 1] + ".json").write_bytes(self.asr_json)
            for i, a in enumerate(cmd):
                if a == "-f":
                    wav = Path(cmd[i + 1])
                    (wav.parent / (wav.name + ".txt")).write_bytes(self.texts.get(wav.name, b""))
            return NS(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(kit, "run", run)


@pytest.fixture
def cpp(real_backend, monkeypatch, tmp_path):
    _tool(monkeypatch)
    monkeypatch.setattr(kit, "IS_MAC", True)
    return _ggml(real_backend)


def test_whole_clip_command_is_exactly_the_agreed_one(cpp, media, monkeypatch):
    fake = FakeCli(monkeypatch, asr_json=cpp_json([((0, 1000), [("[_BEG_]", 0, 0), ("สวัสดี", 0, 500)])]))
    d = asr.transcribe(media, prompt="คำใบ้ Black Pan")
    assert_ojf_schema(d)
    ((cmd, kw),) = fake.cmds
    out = cmd[cmd.index("-of") + 1]
    wav = cmd[cmd.index("-f") + 1]
    assert wav.endswith("audio.wav") and Path(out).name == "asr" and Path(out).parent == Path(wav).parent
    assert cmd == ["/fake/bin/whisper-cli", "-m", str(cpp), "-f", wav, "-l", "th", "-nfa", "-dtw", "large.v3", "-bs", "5", "-sns",
                   "--prompt", "คำใบ้ Black Pan", "-ojf", "-of", out, "-np"]
    assert kw.get("stdin") is not None                             # never waits on the terminal
    assert d["transcription"][0]["tokens"] == [{"text": "สวัสดี", "offsets": {"from": 0, "to": 500}}]


def test_whole_clip_without_prompt_or_dtw_preset_omits_those_flags(cpp, media, monkeypatch):
    monkeypatch.setenv("TAD_CLIP_MODEL", "distil-large-v3")
    monkeypatch.setenv("TAD_CLIP_GGML", str(cpp))
    fake = FakeCli(monkeypatch, asr_json=cpp_json([((0, 1000), [("ก", 0, 500)])]))
    asr.transcribe(media)
    (cmd, _), = fake.cmds
    assert "--prompt" not in cmd and "-dtw" not in cmd and "-nfa" not in cmd
    assert cmd[1:3] == ["-m", str(cpp)]


def test_whole_clip_uses_the_env_ggml_override(cpp, media, monkeypatch, tmp_path):
    other = tmp_path / "ที่เก็บ อื่น" / "ggml-large-v3.bin"
    other.parent.mkdir()
    other.write_bytes(b"x")
    monkeypatch.setenv("TAD_CLIP_GGML", str(other))
    fake = FakeCli(monkeypatch, asr_json=cpp_json([((0, 1000), [("ก", 0, 500)])]))
    asr.transcribe(media)
    assert fake.cmds[0][0][2] == str(other)


def test_per_phrase_command_is_one_run_for_all_files(cpp, tmp_path, monkeypatch):
    wavs = [make_wav(tmp_path / f"p{i:03d}.wav", 0.5) for i in range(3)]
    fake = FakeCli(monkeypatch, texts={"p000.wav": "ข้อ หนึ่ง\n".encode(), "p001.wav": b"", "p002.wav": " สาม \nบรรทัดสอง\n".encode()})
    out = asr.transcribe_many(wavs)
    assert out == ["ข้อ หนึ่ง", "", "สาม บรรทัดสอง"]
    ((cmd, _),) = fake.cmds
    assert cmd[:10] == ["/fake/bin/whisper-cli", "-m", str(cpp), "-l", "th", "-nt", "-otxt", "-np", "-mc", "0"]
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-f"] == [str(w) for w in wavs]


def test_per_phrase_text_with_a_split_character_is_decoded_tolerantly(cpp, tmp_path, monkeypatch):
    wav = make_wav(tmp_path / "p000.wav", 0.5)
    FakeCli(monkeypatch, texts={"p000.wav": "ก".encode() + "ฤ".encode()[:2] + b"\n"})
    assert asr.transcribe_many([wav]) == ["ก"]


def test_auto_mode_falls_back_to_faster_whisper_when_whisper_cli_fails(cpp, media, monkeypatch, capsys):
    FakeCli(monkeypatch, fail="คำสั่ง whisper-cli ล้มเหลว (exit 1)")
    m = FakeModel()
    monkeypatch.setattr(asr, "load_model", lambda *a, **k: m)
    assert_ojf_schema(asr.transcribe(media))
    assert len(m.calls) == 1 and "faster-whisper" in capsys.readouterr().err


def test_explicit_whispercpp_does_not_fall_back(cpp, media, monkeypatch):
    _config(backend="whispercpp")
    FakeCli(monkeypatch, fail="คำสั่ง whisper-cli ล้มเหลว (exit 1)")
    monkeypatch.setattr(asr, "load_model", lambda *a, **k: pytest.fail("fell back"))
    with pytest.raises(kit.KitError):
        asr.transcribe(media)


def test_cli_banner_names_the_backend(cpp, media, tmp_path, monkeypatch, capsys):
    FakeCli(monkeypatch, asr_json=cpp_json([((0, 1000), [("สวัสดี", 0, 500)])]))
    assert asr.main(["transcribe", str(media), "--out", str(tmp_path / "a.json")]) == 0
    assert "whisper.cpp" in capsys.readouterr().err
    assert_ojf_schema(json.loads((tmp_path / "a.json").read_text(encoding="utf-8")))


# ---------------------------------------------------------------- ffmpeg hygiene

def test_every_ffmpeg_call_has_nostdin(media, fake_model, monkeypatch):
    seen = []
    real = kit.run

    def spy(cmd, **kw):
        seen.append([str(c) for c in cmd])
        return real(cmd, **kw)

    monkeypatch.setattr(kit, "run", spy)
    asr.transcribe(media)
    ff = [c for c in seen if Path(c[0]).stem == "ffmpeg"]
    assert ff and all("-nostdin" in c for c in ff)


# ---------------------------------------------------------------- real model (slow)

@pytest.mark.slow
def test_real_model_output_passes_the_schema(monkeypatch):
    import os
    model = os.environ.get("TAD_CLIP_TEST_MODEL", "base")
    monkeypatch.setenv("TAD_CLIP_MODEL", model)
    asr._load_cached.cache_clear()
    d = asr.transcribe(FIXTURE)
    assert_ojf_schema(d)   # also proves every faster-whisper keyword we pass exists in the installed version
    if model not in {"tiny", "base", "small"}:  # the small models often hear nothing at all in this Thai clip
        text = "".join(s["text"] for s in d["transcription"])
        assert any("\u0e00" <= c <= "\u0e7f" for c in text), f"no Thai text recognised: {text!r}"


def _expect_words():
    """Exact Thai words only hold for the big models; CI runs `base`, which only has to produce Thai script."""
    return any(k in asr.model_name() for k in ("large", "medium"))


def _assert_thai(text, word):
    if _expect_words():
        assert word in text, text
    else:
        assert sum("\u0e00" <= c <= "\u0e7f" for c in text) >= 3, f"no Thai text recognised: {text!r}"


def _need_cpp():
    if not kit.IS_MAC:
        pytest.skip("whisper.cpp backend is macOS-only")
    if asr.cpp_ready() is None:
        pytest.skip("needs whisper-cli and a ggml model (set TAD_CLIP_GGML=/path/ggml-large-v3.bin)")


@pytest.mark.slow
def test_real_whispercpp_backend_on_the_fixture(monkeypatch):
    _need_cpp()
    monkeypatch.setattr(asr, "backend_choice", lambda: ("whispercpp", True))
    d = asr.transcribe(FIXTURE, prompt=asr.glossary_prompt())
    assert_ojf_schema(d)
    text = "".join(s["text"] for s in d["transcription"])
    _assert_thai(text, "ผลจาก")
    assert "�" not in text
    json.dumps(d, ensure_ascii=False).encode("utf-8")                 # clean UTF-8, no lone surrogates
    ms = pytest.importorskip("engine.subs.make_subs")
    cues, _ = ms.build_cues(d, ms.load_glossary(None), 22)
    assert cues
    _assert_thai("".join(c["t"] for c in cues), "ผลจาก")


@pytest.mark.slow
def test_real_whispercpp_per_phrase(monkeypatch, tmp_path):
    _need_cpp()
    monkeypatch.setattr(asr, "backend_choice", lambda: ("whispercpp", True))
    wavs = []
    for i, (a, b) in enumerate([(17.39, 18.98), (19.56, 21.34)]):      # the two "ผลจากกระทะ…" takes of the fixture
        w = tmp_path / f"p{i:03d}.wav"
        kit.run([kit.tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-ss", str(a), "-t", str(b - a), "-i", FIXTURE, "-vn",
                 "-ac", "1", "-ar", "16000", "-af", "apad=pad_dur=0.6", w])
        wavs.append(w)
    texts = asr.transcribe_many(wavs)
    assert len(texts) == 2, texts
    for t in texts:
        _assert_thai(t, "ผลจาก")
