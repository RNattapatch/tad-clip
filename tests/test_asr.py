"""Tests for engine/asr.py — model loading rules, whisper.cpp -ojf output shape, CLI skip rules.

Fast tests use a fake model. The one `slow` test runs the real faster-whisper model named by env
TAD_CLIP_TEST_MODEL (default `base`) from the models folder of TAD_CLIP_HOME, so run it like this:
  TAD_CLIP_HOME=~/.tad-clip-dev ~/.tad-clip-dev/venv/bin/python -m pytest tests/test_asr.py -q
Skip it with:  -m "not slow"
"""
import json
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
        raise OSError("no network")

    monkeypatch.setattr(faster_whisper, "WhisperModel", boom)
    with pytest.raises(kit.KitError) as e:
        asr.load_model()
    assert seen == [True, False]
    assert "large-v3" in str(e.value) and "อินเทอร์เน็ต" in str(e.value)


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
    assert [kw["initial_prompt"] for _, kw in m.calls] == ["จากคำสั่ง", "จากคอนฟิก"]


def test_cli_missing_file_is_a_thai_message(tmp_path, capsys):
    assert asr.main(["transcribe", str(tmp_path / "ไม่มี.mp4"), "--out", str(tmp_path / "a.json")]) == 1
    err = capsys.readouterr().err
    assert "ไม่พบไฟล์" in err and "Traceback" not in err


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
