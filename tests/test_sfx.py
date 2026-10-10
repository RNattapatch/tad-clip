"""sfx_mix: measured gain (-8 dB under the voice by default), video stream untouched, path resolution."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import kit, sfx_mix  # noqa: E402

# Resolve tools under the caller's TAD_CLIP_HOME (dev machine / CI) before each test swaps in a temp home.
FFMPEG = kit.tool("ffmpeg")
FFPROBE = kit.tool("ffprobe")
BEEP = ROOT / "tests" / "fixtures" / "beep.wav"
THAI_FIXTURE = ROOT / "tests" / "fixtures" / "คลิป ทดสอบ.mp4"
SR = sfx_mix.SR


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"tools": {"ffmpeg": FFMPEG, "ffprobe": FFPROBE}}), encoding="utf-8")
    monkeypatch.setenv("TAD_CLIP_HOME", str(home))
    return home


def ff(*args):
    subprocess.run([FFMPEG, "-v", "error", "-y", *map(str, args)], check=True)


def make_wav(path, freq, peak, dur=1.0):
    """Mono sine whose peak amplitude is `peak` (full scale = 1.0)."""
    ff("-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}:sample_rate={SR},volume={peak * 8}", "-ac", "1", "-c:a", "pcm_s16le", path)


def make_video(path, voice_expr="sine=frequency=220:sample_rate=44100:duration=4,volume=2", dur=4):
    """Tiny H.264 clip whose 'voice' is a 220 Hz tone (default peak 0.25 -> RMS about -15 dBFS)."""
    ff("-f", "lavfi", "-i", f"color=c=black:s=160x284:r=30:d={dur}", "-f", "lavfi", "-i", voice_expr,
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", path)


def vmd5(path):
    out = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy", "-f", "md5", "-"],
                         capture_output=True, text=True, check=True).stdout
    return out.strip()


def tone_amp(x, freq):
    """Amplitude of one sine component (Hann-windowed single-bin DFT)."""
    n = np.arange(len(x))
    w = np.hanning(len(x))
    z = np.sum(x * w * np.exp(-2j * np.pi * freq * n / SR))
    return 2 * abs(z) / np.sum(w)


def rel_db(path, t0, t1, freq_sfx, freq_voice=220):
    """Level of the effect tone relative to the voice tone, measured on the mixed output."""
    x = sfx_mix.pcm(str(path))[int(t0 * SR):int(t1 * SR)]
    return 20 * np.log10(tone_amp(x, freq_sfx) / tone_amp(x, freq_voice))


def write_spec(path, items):
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("peak", [0.01, 0.9], ids=["quiet-beep", "loud-tone"])
def test_default_minus_8_db_under_voice(tmp_path, peak):
    """Library files differ by 25 dB+, so the gain is measured, not guessed."""
    video, snd, out = tmp_path / "v.mp4", tmp_path / "s.wav", tmp_path / "out.mp4"
    make_video(video)
    make_wav(snd, 880, peak)
    spec = write_spec(tmp_path / "sfx.json", [{"file": "s.wav", "at": 1.0}])
    sfx_mix.mix(video, spec, out)
    assert abs(rel_db(out, 1.2, 1.8, 880) - (-8.0)) < 1.0


def test_custom_db(tmp_path):
    video, snd, out = tmp_path / "v.mp4", tmp_path / "s.wav", tmp_path / "out.mp4"
    make_video(video)
    make_wav(snd, 880, 0.05)
    sfx_mix.mix(video, write_spec(tmp_path / "sfx.json", [{"file": "s.wav", "at": 1.0, "db": -14}]), out)
    assert abs(rel_db(out, 1.2, 1.8, 880) - (-14.0)) < 1.0


def test_video_stream_is_bit_exact_and_duration_kept(tmp_path):
    video, out = tmp_path / "v.mp4", tmp_path / "out.mp4"
    make_video(video)
    sfx_mix.mix(video, write_spec(tmp_path / "sfx.json", [{"file": str(BEEP), "at": 0.5}, {"file": str(BEEP), "at": 2.5, "dur": 0.4}]), out)
    assert vmd5(out) == vmd5(video)
    dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                               capture_output=True, text=True, check=True).stdout)
    assert abs(dur - 4.0) < 0.15


def test_dur_cuts_the_sound_with_a_fade(tmp_path):
    video, out = tmp_path / "v.mp4", tmp_path / "out.mp4"
    make_video(video, voice_expr="aevalsrc='if(lt(t,3),0,0.25*sin(2*PI*220*t))':s=44100:d=4")  # silent until 3 s
    make_wav(tmp_path / "s.wav", 880, 0.3, dur=1.0)
    sfx_mix.mix(video, write_spec(tmp_path / "sfx.json", [{"file": "s.wav", "at": 0.5, "dur": 0.5}]), out)
    x = sfx_mix.pcm(str(out))
    assert np.abs(x[int(0.55 * SR):int(0.85 * SR)]).max() > 0.02   # audible while it plays
    assert np.abs(x[int(1.05 * SR):int(1.5 * SR)]).max() < 0.003   # cut 0.5 s after the start


def test_silent_voice_at_that_moment_uses_the_clip_level(tmp_path):
    """An effect over a pause is still audible: reference = whole-clip voice level, not -90 dB."""
    video, out = tmp_path / "v.mp4", tmp_path / "out.mp4"
    make_video(video, voice_expr="aevalsrc='if(lt(t,2),0,0.25*sin(2*PI*220*t))':s=44100:d=4")
    make_wav(tmp_path / "s.wav", 880, 0.1)
    sfx_mix.mix(video, write_spec(tmp_path / "sfx.json", [{"file": "s.wav", "at": 0.5}]), out)
    x = sfx_mix.pcm(str(out))
    effect = x[int(0.6 * SR):int(1.4 * SR)]
    voice = x[int(2.5 * SR):int(3.5 * SR)]
    got = 20 * np.log10(tone_amp(effect, 880) / tone_amp(voice, 220))
    assert abs(got - (-8.0)) < 1.0


def test_relative_file_resolves_next_to_json_then_library_dir(tmp_path, tmp_home):
    lib = tmp_path / "คลัง เสียง"
    lib.mkdir()
    shutil.copy(BEEP, lib / "only-in-library.wav")
    shutil.copy(BEEP, tmp_path / "next-to-json.wav")
    cfg = json.loads((tmp_home / "config.json").read_text(encoding="utf-8"))
    cfg["library_dir"] = str(lib)
    (tmp_home / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    spec = write_spec(tmp_path / "sfx.json", [{"file": "next-to-json.wav", "at": 0.2}, {"file": "only-in-library.wav", "at": 1.5}])
    assert sfx_mix.resolve_file("next-to-json.wav", spec.parent) == tmp_path / "next-to-json.wav"
    assert sfx_mix.resolve_file("only-in-library.wav", spec.parent) == lib / "only-in-library.wav"
    video, out = tmp_path / "v.mp4", tmp_path / "out.mp4"
    make_video(video)
    assert len(sfx_mix.mix(video, spec, out)) == 2


def test_missing_sound_is_a_thai_error_naming_both_places(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video)
    spec = write_spec(tmp_path / "sfx.json", [{"file": "nope.mp3", "at": 1.0}])
    with pytest.raises(kit.KitError, match="ไม่พบไฟล์เสียง nope.mp3"):
        sfx_mix.mix(video, spec, tmp_path / "out.mp4")


def test_bad_spec_is_a_thai_error(tmp_path):
    video = tmp_path / "v.mp4"
    make_video(video)
    for bad in ([{"file": str(BEEP)}], [{"file": str(BEEP), "at": -1}], {"file": "x"}, [{"at": 1}]):
        with pytest.raises(kit.KitError):
            sfx_mix.mix(video, write_spec(tmp_path / "sfx.json", bad), tmp_path / "out.mp4")


def test_rerun_skips_unless_inputs_changed_or_force(tmp_path):
    video, out = tmp_path / "v.mp4", tmp_path / "out.mp4"
    make_video(video)
    spec = write_spec(tmp_path / "sfx.json", [{"file": str(BEEP), "at": 0.5}])
    assert sfx_mix.mix(video, spec, out)
    first = out.stat().st_mtime_ns
    assert sfx_mix.mix(video, spec, out) == []          # nothing changed: skipped
    assert out.stat().st_mtime_ns == first
    assert sfx_mix.mix(video, spec, out, force=True)    # --force always re-mixes
    assert out.stat().st_mtime_ns > first
    stamp = out.stat().st_mtime_ns
    spec.write_text(json.dumps([{"file": str(BEEP), "at": 1.5}]), encoding="utf-8")  # sfx.json edited after the output
    assert sfx_mix.mix(video, spec, out)
    assert out.stat().st_mtime_ns > stamp


def test_empty_list_just_remuxes(tmp_path):
    video, out = tmp_path / "v.mp4", tmp_path / "out.mp4"
    make_video(video)
    assert sfx_mix.mix(video, write_spec(tmp_path / "sfx.json", []), out) == []
    assert out.exists() and vmd5(out) == vmd5(video)


def test_cli_with_thai_and_spaces_in_paths(tmp_path):
    if not THAI_FIXTURE.exists():
        pytest.skip("fixture missing")
    work = tmp_path / "งาน ทดสอบ 1"
    work.mkdir()
    video = work / "คลิป ทดสอบ.mp4"
    shutil.copy(THAI_FIXTURE, video)
    shutil.copy(BEEP, work / "เสียง บี๊บ.wav")
    spec = write_spec(work / "sfx ทดสอบ.json", [{"file": "เสียง บี๊บ.wav", "at": 3.0, "why": "ทดสอบ"}])
    out = work / "ผลลัพธ์ final.mp4"
    res = subprocess.run([sys.executable, "-m", "engine.sfx_mix", str(video), str(spec), str(out)],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    assert "1 sound effects" in res.stdout or "1 เสียง" in res.stdout
    assert vmd5(out) == vmd5(video)
    probe = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True, check=True).stdout
    assert "aac" in probe


def test_cli_error_is_thai_and_exit_1(tmp_path):
    res = subprocess.run([sys.executable, "-m", "engine.sfx_mix", str(tmp_path / "ไม่มี.mp4"), str(tmp_path / "x.json"), str(tmp_path / "o.mp4")],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 1
    assert "ไม่พบ" in res.stderr
    assert "Traceback" not in res.stderr
