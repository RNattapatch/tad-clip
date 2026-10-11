"""Tests for engine/roughcut.py — phrase detection, plan/split, render, CLI skip rules.

Run: TAD_CLIP_HOME=~/.tad-clip-dev ~/.tad-clip-dev/venv/bin/python -m pytest tests/test_roughcut.py -q
Needs ffmpeg + ffprobe (kit.tool). No Whisper model: ASR calls are replaced by fakes.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import asr, kit, roughcut  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "คลิป ทดสอบ.mp4"
SR = 16000


# ---------------------------------------------------------------- helpers

def write_wav(path, bursts, total, noise=0.001, seed=0):
    """16 kHz mono: sine bursts (start, end) in seconds over faint room noise (-60 dBFS)."""
    t = np.arange(int(total * SR)) / SR
    a = np.random.default_rng(seed).normal(0, noise, t.size)
    for s, e in bursts:
        m = (t >= s) & (t < e)
        a[m] += 0.3 * np.sin(2 * np.pi * 220 * t[m])
    pcm = (np.clip(a, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def make_clip(path, bursts, total, vsrc="color=c=black:s=320x240:r=30", vcodec="libx264", vargs=("-pix_fmt", "yuv420p")):
    wav = Path(path).with_suffix(".src.wav")
    write_wav(wav, bursts, total)
    kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"{vsrc}:d={total}", "-i", wav,
             "-c:v", vcodec, *vargs, "-c:a", "aac", "-b:a", "128k", "-shortest", path])
    wav.unlink()
    return Path(path)


def probe(path):
    res = kit.run([kit.tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", path])
    info = json.loads(res.stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    return v, float(info["format"]["duration"])


def frame_deltas(path):
    res = kit.run([kit.tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
                   "-of", "csv=p=0", path])
    ts = sorted(float(x) for x in res.stdout.split())
    return np.diff(ts)


BURSTS = [(0.5, 1.0), (1.4, 1.9), (2.3, 2.8), (3.2, 3.7)]   # 0.4 s pauses
TOTAL = 4.2


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    d = tmp_path_factory.mktemp("synth")
    return make_clip(d / "bursts.mp4", BURSTS, TOTAL)


# ---------------------------------------------------------------- constants + phrases

def test_algorithm_constants_match_the_original():
    assert (roughcut.SR, roughcut.HOP) == (16000, 160)
    assert (roughcut.DIP, roughcut.MIN_ON, roughcut.PRE, roughcut.POST) == (0.08, 0.06, 0.10, 0.14)


def test_phrases_finds_each_burst_within_30ms(tmp_path):
    wav = tmp_path / "bursts.wav"
    write_wav(wav, BURSTS, TOTAL)
    ph, dur = roughcut.phrases(str(wav))
    assert dur == pytest.approx(TOTAL, abs=0.01)
    assert len(ph) == 4
    for (s, e), (es, ee) in zip(ph, BURSTS):
        assert s == pytest.approx(es, abs=0.03)
        assert e == pytest.approx(ee, abs=0.03)


def test_phrases_merges_dips_shorter_than_80ms_and_drops_blips(tmp_path):
    wav = tmp_path / "dips.wav"
    # 50 ms dip -> one phrase; 40 ms blip (< MIN_ON) -> ignored; real second phrase kept
    write_wav(wav, [(0.5, 1.0), (1.05, 1.55), (1.9, 1.94), (2.4, 2.9)], 3.5)
    ph, _ = roughcut.phrases(str(wav))
    assert len(ph) == 2
    assert ph[0][0] == pytest.approx(0.5, abs=0.03) and ph[0][1] == pytest.approx(1.55, abs=0.03)
    assert ph[1][0] == pytest.approx(2.4, abs=0.03)


def test_phrases_reads_video_with_thai_name_and_spaces(synth, tmp_path):
    p = tmp_path / "คลิป ทดสอบ 1.mp4"
    shutil.copy(synth, p)
    ph, dur = roughcut.phrases(str(p))
    assert len(ph) == 4 and dur == pytest.approx(TOTAL, abs=0.1)
    for (s, e), (es, ee) in zip(ph, BURSTS):  # AAC smears the edges a little more than raw WAV
        assert s == pytest.approx(es, abs=0.05) and e == pytest.approx(ee, abs=0.05)


def test_phrases_without_audio_is_a_thai_error(tmp_path):
    p = tmp_path / "silent.mp4"
    kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=1", "-an", p])
    with pytest.raises(kit.KitError) as e:
        roughcut.phrases(str(p))
    assert "เสียง" in str(e.value)


# ---------------------------------------------------------------- plan / split

PH = [[0.5, 1.0], [1.1, 1.6], [2.5, 3.0]]


def test_plan_joins_phrases_closer_than_gap():
    assert roughcut.plan(PH, 4.0, set(), 0.3) == [pytest.approx([0.4, 1.74]), pytest.approx([2.4, 3.14])]


def test_plan_keeps_phrases_apart_when_gap_is_smaller():
    out = roughcut.plan(PH, 4.0, set(), 0.05)
    assert len(out) == 3
    # padding never reaches into the neighbouring phrase
    assert out[0][1] <= PH[1][0] and out[1][0] >= PH[0][1]


@pytest.mark.parametrize("gap", [0.0, 0.05, 0.15, 0.3])
def test_plan_pieces_never_overlap_even_when_gap_is_below_pre_plus_post(gap):
    ph = [[0.5, 1.0], [1.1, 1.6], [1.75, 2.2], [2.3, 2.9]]       # pauses of 0.10-0.15 s, less than PRE + POST (0.24 s)
    out = roughcut.plan(ph, 4.0, set(), gap)
    for a, b in zip(out, out[1:]):
        assert b[0] >= a[1] - 1e-9, (a, b)             # no audio is played twice
    for s, e in out:
        assert s < e


def test_plan_overlap_is_split_in_the_middle_of_the_shared_air():
    out = roughcut.plan(PH, 4.0, set(), 0.05)
    assert out[0][1] == out[1][0] == pytest.approx(1.05)    # 1.0 (end of phrase 0) .. 1.1 (start of phrase 1)


def test_plan_does_not_pad_into_a_dropped_neighbour():
    out = roughcut.plan(PH, 4.0, {1}, 0.3)
    assert len(out) == 2
    assert out[0][1] == pytest.approx(1.1)      # stops where the dropped retake starts
    assert out[1][0] == pytest.approx(2.4)


def test_split_produces_a_and_b_ids():
    ph, ids = roughcut.split_phrases([[0, 1], [1.5, 3.0], [4, 5]], {"1": 2.0})
    assert ids == ["0", "1a", "1b", "2"]
    assert ph == [[0, 1], [1.5, 2.0], [2.0, 3.0], [4, 5]]


def test_split_handles_several_splits_regardless_of_order():
    ph, ids = roughcut.split_phrases([[0, 1], [1.5, 3.0], [4, 5]], {"0": 0.4, "2": 4.5})
    assert ids == ["0a", "0b", "1", "2a", "2b"]
    assert ph[0] == [0, 0.4] and ph[1] == [0.4, 1] and ph[4] == [4.5, 5]


def test_split_outside_the_phrase_is_a_thai_error():
    with pytest.raises(kit.KitError) as e:
        roughcut.split_phrases([[0, 1], [1.5, 3.0]], {"1": 5.0})
    assert "1" in str(e.value) and "split" in str(e.value)


@pytest.mark.parametrize("key", ["abc", "1.5", "", "-1", "9"])
def test_split_with_a_bad_phrase_number_is_a_thai_error(key):
    with pytest.raises(kit.KitError) as e:
        roughcut.split_phrases([[0, 1], [1.5, 3.0]], {key: 2.0})
    assert "split" in str(e.value) and "Traceback" not in str(e.value)


def test_split_with_a_non_numeric_time_is_a_thai_error():
    with pytest.raises(kit.KitError):
        roughcut.split_phrases([[0, 1], [1.5, 3.0]], {"1": "สอง"})


def test_resolve_keep_drops_the_b_half_of_a_split():
    ph = [[0.5, 1.0], [1.4, 3.0]]
    keep = roughcut.resolve_keep(ph, 4.0, {"drop": ["1b"], "gap": 0.3, "split": {"1": 2.0}})
    assert len(keep) == 2
    assert keep[1][0] == pytest.approx(1.3)
    assert keep[1][1] == pytest.approx(2.0)   # 1a kept, ends where 1b (dropped) starts


WRONG_TYPES = [[1, 2], "drop 1", 5, None, {"split": [1, 2]}, {"split": "25"}, {"split": 3}, {"drop": 3}, {"drop": "12"},
               {"drop": {"1": 2}}, {"drop": [[1]]}, {"drop": [None]}, {"drop": [True]}, {"gap": "wide"}, {"gap": [0.3]}, {"gap": True}]


@pytest.mark.parametrize("spec", WRONG_TYPES)
def test_resolve_keep_with_wrong_json_types_is_a_thai_error(spec):
    with pytest.raises(kit.KitError) as e:
        roughcut.resolve_keep(PH, 4.0, spec)
    assert "drops.json" in str(e.value) or "split" in str(e.value)


@pytest.mark.parametrize("split", [[1, 2], "25", 3, True])
def test_split_phrases_with_a_non_object_split_is_a_thai_error(split):
    with pytest.raises(kit.KitError) as e:
        roughcut.split_phrases(PH, split)
    assert "split" in str(e.value)


@pytest.mark.parametrize("spec", WRONG_TYPES)
def test_render_with_wrong_json_types_in_drops_fails_cleanly(synth, tmp_path, spec, capsys):
    drops, out = tmp_path / "drops.json", tmp_path / "o.mp4"
    drops.write_text(json.dumps(spec), encoding="utf-8")
    assert roughcut.main(["render", str(synth), str(drops), str(out)]) == 1
    err = capsys.readouterr().err
    assert "drops.json" in err or "split" in err
    assert "Traceback" not in err
    assert list(tmp_path.glob("*.mp4")) == [] and list(tmp_path.glob("*.partial*")) == []


def test_resolve_keep_accepts_a_null_gap_and_string_numbers():
    assert roughcut.resolve_keep(PH, 4.0, {"gap": None, "drop": ["1"]}) == roughcut.resolve_keep(PH, 4.0, {"gap": 0.3, "drop": [1]})


def test_resolve_keep_rejects_unknown_drop_ids():
    with pytest.raises(kit.KitError) as e:
        roughcut.resolve_keep(PH, 4.0, {"drop": [7]})
    assert "7" in str(e.value)


def test_resolve_keep_everything_dropped_is_a_thai_error():
    with pytest.raises(kit.KitError):
        roughcut.resolve_keep(PH, 4.0, {"drop": [0, 1, 2]})


# ---------------------------------------------------------------- render on the real fixture

@pytest.fixture(scope="module")
def fixture_render(tmp_path_factory):
    d = tmp_path_factory.mktemp("render") / "โฟลเดอร์ ทดสอบ"
    d.mkdir()
    drops = d / "drops.json"
    drops.write_text(json.dumps({"drop": [], "gap": 0.3}), encoding="utf-8")
    out = d / "ผล roughcut.mp4"
    assert roughcut.main(["render", str(FIXTURE), str(drops), str(out)]) == 0
    return out


def test_render_fixture_gives_a_shorter_playable_sdr_cfr_mp4(fixture_render):
    src_v, src_dur = probe(FIXTURE)
    v, dur = probe(fixture_render)
    assert 0 < dur <= src_dur
    assert (v["codec_name"], v["pix_fmt"]) == ("h264", "yuv420p")
    assert (v["width"], v["height"]) == (src_v["width"], src_v["height"])
    assert (v["color_transfer"], v["color_primaries"], v["color_space"]) == ("bt709",) * 3
    assert v["color_range"] == "tv"
    assert v["r_frame_rate"] == v["avg_frame_rate"] == "60/1"
    kit.run([kit.tool("ffmpeg"), "-v", "error", "-i", fixture_render, "-f", "null", "-"])  # decodes cleanly


def test_render_writes_segments_json_and_cleans_up(fixture_render):
    seg = json.loads(Path(str(fixture_render) + ".segments.json").read_text(encoding="utf-8"))
    assert set(seg) == {"source", "size", "mtime_ns", "drop", "keep"}     # size + mtime: which take this cut came from
    assert seg["drop"] == [] and Path(seg["source"]).name == FIXTURE.name
    assert all(0 <= s < e for s, e in seg["keep"])
    _, dur = probe(fixture_render)
    assert dur == pytest.approx(sum(e - s for s, e in seg["keep"]), abs=0.15)
    assert not list(fixture_render.parent.glob("*.filter"))
    assert not list(fixture_render.parent.glob("*.partial*"))


# ---------------------------------------------------------------- render: skip / force / errors

def _drops(path, **spec):
    path.write_text(json.dumps({"drop": [], "gap": 0.3, **spec}), encoding="utf-8")
    return path


def test_render_skips_when_output_exists_unless_force(synth, tmp_path, capsys):
    drops, out = _drops(tmp_path / "drops.json"), tmp_path / "out.mp4"
    assert roughcut.main(["render", str(synth), str(drops), str(out)]) == 0
    first = out.stat().st_mtime_ns
    capsys.readouterr()
    assert roughcut.main(["render", str(synth), str(drops), str(out)]) == 0
    assert out.stat().st_mtime_ns == first
    assert "ข้าม" in capsys.readouterr().out
    assert roughcut.main(["render", str(synth), str(drops), str(out), "--force"]) == 0
    assert out.stat().st_mtime_ns != first


def test_render_failure_leaves_no_output_or_partial_files(synth, tmp_path, capsys):
    drops = _drops(tmp_path / "drops.json", drop=[0, 1, 2, 3])
    out = tmp_path / "out.mp4"
    assert roughcut.main(["render", str(synth), str(drops), str(out)]) == 1
    assert "ไม่เหลือ" in capsys.readouterr().err
    assert list(tmp_path.glob("*.mp4")) == [] and list(tmp_path.glob("*.partial*")) == []


def test_render_drops_the_retake_and_honours_gap(synth, tmp_path):
    drops, out = _drops(tmp_path / "drops.json", drop=[1], gap=0.3), tmp_path / "out.mp4"
    assert roughcut.main(["render", str(synth), str(drops), str(out)]) == 0
    seg = json.loads(Path(str(out) + ".segments.json").read_text(encoding="utf-8"))
    assert seg["drop"] == ["1"] and len(seg["keep"]) == 3
    # phrase 1 (1.4-1.9) must not leak into the kept ranges
    assert all(not (s < 1.85 and e > 1.45) for s, e in seg["keep"])
    _, dur = probe(out)
    assert dur == pytest.approx(sum(e - s for s, e in seg["keep"]), abs=0.15)


def test_render_thai_and_space_paths_everywhere(synth, tmp_path):
    src = tmp_path / "คลิป ทดสอบ 1.mp4"
    shutil.copy(synth, src)
    drops = _drops(tmp_path / "แก้ไข drops.json")
    out = tmp_path / "ผลลัพธ์ ใหม่" / "ตัดแล้ว.mp4"
    out.parent.mkdir()
    assert roughcut.main(["render", str(src), str(drops), str(out)]) == 0
    assert out.exists() and Path(str(out) + ".segments.json").exists()
    seg = json.loads(Path(str(out) + ".segments.json").read_text(encoding="utf-8"))
    assert Path(seg["source"]).name == "คลิป ทดสอบ 1.mp4"


# ---------------------------------------------------------------- render: HDR + VFR sources

@pytest.mark.parametrize("info,expected", [
    ({"avg_frame_rate": "60/1"}, "60"),
    ({"avg_frame_rate": "30000/1001"}, "30000/1001"),
    ({"avg_frame_rate": "2998/100"}, "30000/1001"),                 # VFR average ~29.98 snaps to 29.97
    ({"avg_frame_rate": "36/1"}, "36"),                              # no standard rate within 1% -> keep the average
    ({"avg_frame_rate": "0/0", "r_frame_rate": "25/1"}, "25"),
    ({"avg_frame_rate": "0/0", "r_frame_rate": "1000/1"}, "30"),     # mkv/webm timebase is not a frame rate
    ({}, "30"),
])
def test_source_fps(info, expected):
    assert roughcut.source_fps(info) == expected


TAGS = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"


def test_video_filter_sdr_converts_matrix_and_range_and_tags_the_result():
    vf = roughcut.video_filter({"color_transfer": "bt709", "color_space": "bt709", "height": 1280}, has_zscale=True)
    assert "in_color_matrix=auto" in vf and "out_color_matrix=bt709" in vf and "out_range=tv" in vf
    assert "zscale" not in vf and vf.endswith("format=yuv420p," + TAGS)


@pytest.mark.parametrize("info,matrix", [
    ({"height": 1080}, "bt709"),                              # untagged HD
    ({"height": 1920, "color_space": "unknown"}, "bt709"),
    ({"height": 720}, "bt709"),
    ({"height": 576}, "bt601"),                               # untagged SD
    ({"height": 480, "color_space": "unknown"}, "bt601"),
    ({"height": 1080, "color_space": "bt709"}, "auto"),       # tagged -> trust the tag
    ({"height": 576, "color_space": "smpte170m"}, "auto"),
    ({"color_space": "bt2020nc", "height": 2160}, "auto"),
    ({}, "bt709"),
])
def test_video_filter_input_matrix_when_the_source_is_untagged(info, matrix):
    vf = roughcut.video_filter({"color_transfer": "bt709", **info}, has_zscale=True)
    assert f"in_color_matrix={matrix}" in vf


def test_probe_reports_the_colour_tags_and_size():
    info = roughcut.probe_video(str(FIXTURE))
    assert (info["color_space"], info["color_transfer"], info["color_primaries"]) == ("bt709",) * 3
    assert (info["width"], info["height"]) == (720, 1280)
    assert roughcut.probe_video(str(FIXTURE))["height"] == 1280


@pytest.mark.parametrize("trc", ["smpte2084", "arib-std-b67"])
def test_video_filter_hdr_uses_the_exact_tonemap_chain(trc):
    vf = roughcut.video_filter({"color_transfer": trc}, has_zscale=True)
    assert vf == ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable,"
                  "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def test_video_filter_hdr_without_zscale_falls_back_with_a_warning(capsys):
    vf = roughcut.video_filter({"color_transfer": "smpte2084"}, has_zscale=False)
    assert vf == "format=yuv420p"
    assert "zscale" in capsys.readouterr().err


def _rgb_frame(path, matrix):
    res = subprocess.run([kit.tool("ffmpeg"), "-v", "error", "-nostdin", "-i", str(path), "-frames:v", "1", "-vf",
                          f"scale=in_color_matrix={matrix}:in_range=tv,format=rgb24", "-f", "rawvideo", "-"],
                         capture_output=True, check=True)
    return np.frombuffer(res.stdout, np.uint8).astype(np.float32)


def test_untagged_hd_source_keeps_its_colours_and_gets_bt709_tags(tmp_path):
    src = make_clip(tmp_path / "untagged.mp4", BURSTS, TOTAL, vsrc="smptebars=s=1280x720:r=30",
                    vargs=("-vf", "format=rgb24,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", "-pix_fmt", "yuv420p",
                           "-x264-params", "colorprim=undef:transfer=undef:colormatrix=undef"))
    sv, _ = probe(src)
    assert sv.get("color_space", "unknown") == "unknown"            # precondition: nothing tells ffmpeg the matrix
    drops, out = _drops(tmp_path / "drops.json"), tmp_path / "out.mp4"
    assert roughcut.main(["render", str(src), str(drops), str(out)]) == 0
    v, _ = probe(out)
    assert (v["color_space"], v["color_transfer"], v["color_primaries"], v["color_range"]) == ("bt709", "bt709", "bt709", "tv")
    err = np.abs(_rgb_frame(src, "bt709") - _rgb_frame(out, "bt709")).mean()
    assert err < 2.0, f"colours shifted: mean error {err:.1f} levels"


def test_untagged_sd_source_is_read_as_bt601_and_comes_out_bt709(tmp_path):
    src = make_clip(tmp_path / "sd.mp4", BURSTS, TOTAL, vsrc="smptebars=s=704x576:r=25",
                    vargs=("-vf", "format=rgb24,scale=out_color_matrix=bt601:out_range=tv,format=yuv420p", "-pix_fmt", "yuv420p",
                           "-x264-params", "colorprim=undef:transfer=undef:colormatrix=undef"))
    assert probe(src)[0].get("color_space", "unknown") == "unknown"
    drops, out = _drops(tmp_path / "drops.json"), tmp_path / "out.mp4"
    assert roughcut.main(["render", str(src), str(drops), str(out)]) == 0
    assert probe(out)[0]["color_space"] == "bt709"
    err = np.abs(_rgb_frame(src, "bt601") - _rgb_frame(out, "bt709")).mean()
    assert err < 2.0, f"colours shifted: mean error {err:.1f} levels"


def test_render_hdr_hevc_source_comes_out_sdr(tmp_path):
    src = make_clip(tmp_path / "hdr.mp4", BURSTS, TOTAL, vsrc="testsrc2=s=320x240:r=30", vcodec="libx265",
                    vargs=("-pix_fmt", "yuv420p10le", "-tag:v", "hvc1", "-x265-params",
                           "log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc"))
    assert probe(src)[0]["color_transfer"] == "smpte2084"
    drops, out = _drops(tmp_path / "drops.json"), tmp_path / "sdr.mp4"
    assert roughcut.main(["render", str(src), str(drops), str(out)]) == 0
    v, _ = probe(out)
    assert (v["pix_fmt"], v["color_transfer"], v["color_primaries"], v["color_space"]) == ("yuv420p", "bt709", "bt709", "bt709")
    assert v["color_range"] == "tv"


def test_render_variable_frame_rate_source_comes_out_constant(tmp_path):
    # keep frames 0-2 of every 5 at 60 fps -> irregular timestamps, avg ~36 fps
    src = make_clip(tmp_path / "vfr.mp4", BURSTS, TOTAL, vsrc="testsrc2=s=320x240:r=60",
                    vargs=("-vf", "select='lt(mod(n,5),3)'", "-fps_mode", "vfr", "-pix_fmt", "yuv420p"))
    assert frame_deltas(src).std() > 0.002, "fixture is not really variable-frame-rate"
    drops, out = _drops(tmp_path / "drops.json"), tmp_path / "cfr.mp4"
    assert roughcut.main(["render", str(src), str(drops), str(out)]) == 0
    v, _ = probe(out)
    assert v["r_frame_rate"] == v["avg_frame_rate"]
    assert frame_deltas(out).std() < 0.0005


def test_render_bakes_in_iphone_style_rotation_metadata(tmp_path):
    land = make_clip(tmp_path / "land.mp4", BURSTS, TOTAL, vsrc="testsrc2=s=320x240:r=30")   # stored 320x240
    rot = tmp_path / "rot.mp4"
    kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-display_rotation", "90", "-i", land, "-c", "copy", rot])
    assert (probe(rot)[0]["width"], probe(rot)[0]["height"]) == (320, 240)
    drops, out = _drops(tmp_path / "drops.json"), tmp_path / "upright.mp4"
    assert roughcut.main(["render", str(rot), str(drops), str(out)]) == 0
    v, _ = probe(out)
    assert (v["width"], v["height"]) == (240, 320) and "side_data_list" not in v


# ---------------------------------------------------------------- robustness (fix round 1)

def _spy_commands(monkeypatch):
    seen, real_run, real_sub = [], kit.run, subprocess.run

    def run(cmd, **kw):
        seen.append([str(c) for c in cmd])
        return real_run(cmd, **kw)

    def sub(cmd, *a, **kw):
        seen.append([str(c) for c in cmd])
        return real_sub(cmd, *a, **kw)

    monkeypatch.setattr(kit, "run", run)
    monkeypatch.setattr(subprocess, "run", sub)
    return seen


def test_every_ffmpeg_call_has_nostdin(synth, tmp_path, monkeypatch):
    seen = _spy_commands(monkeypatch)
    FakeASR(monkeypatch)
    roughcut.main(["phrases", str(synth), "--out", str(tmp_path / "a" / "p.json"), "--whisper"])
    roughcut.main(["render", str(synth), str(_drops(tmp_path / "d.json")), str(tmp_path / "o.mp4")])
    ff = [c for c in seen if Path(c[0]).stem == "ffmpeg"]
    assert len(ff) >= 2 + 4 + 1                              # decode (x2), 4 phrase wavs, render
    assert all("-nostdin" in c for c in ff), [c for c in ff if "-nostdin" not in c]


def test_render_retries_with_the_old_filter_script_option(synth, tmp_path, monkeypatch):
    """Both render attempts are faked: the real ffmpeg here may be a build that has dropped -filter_complex_script
    (Homebrew ffmpeg 8 on the macOS runners) or one that never had -/filter_complex. Only the retry logic is tested."""
    tried, real = [], kit.run

    def run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        if "-/filter_complex" in cmd:
            tried.append("-/filter_complex")
            raise kit.KitError("คำสั่ง ffmpeg ล้มเหลว (exit 1)\nUnrecognized option '/filter_complex'.\nError splitting the argument list: Option not found")
        if "-filter_complex_script" in cmd:
            tried.append("-filter_complex_script")
            script = Path(cmd[cmd.index("-filter_complex_script") + 1])
            assert "concat=n=" in script.read_text(encoding="utf-8")       # the graph file is there while ffmpeg runs
            Path(cmd[-1]).write_bytes(b"rendered")                         # what ffmpeg would write: the .partial file
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real(cmd, **kw)

    monkeypatch.setattr(kit, "run", run)
    monkeypatch.setattr(roughcut, "_FILTER_OPTION", None)
    out = tmp_path / "o.mp4"
    assert roughcut.main(["render", str(synth), str(_drops(tmp_path / "d.json")), str(out)]) == 0
    assert tried == ["-/filter_complex", "-filter_complex_script"] and out.exists()
    # the working spelling is remembered for the next render in this process
    tried.clear()
    assert roughcut.main(["render", str(synth), str(_drops(tmp_path / "d2.json")), str(tmp_path / "o2.mp4")]) == 0
    assert tried == ["-filter_complex_script"]


def test_other_ffmpeg_failures_are_not_retried(synth, tmp_path, monkeypatch, capsys):
    calls = []
    real = kit.run

    def run(cmd, **kw):
        if "-/filter_complex" in [str(c) for c in cmd]:
            calls.append(1)
            raise kit.KitError("คำสั่ง ffmpeg ล้มเหลว (exit 1)\nError initializing filter 'trim'")
        return real(cmd, **kw)

    monkeypatch.setattr(kit, "run", run)
    monkeypatch.setattr(roughcut, "_FILTER_OPTION", None)
    assert roughcut.main(["render", str(synth), str(_drops(tmp_path / "d.json")), str(tmp_path / "o.mp4")]) == 1
    assert calls == [1] and "trim" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.partial*")) and not list(tmp_path.glob("*.filter"))


def test_json_files_saved_with_a_bom_are_read(synth, tmp_path, monkeypatch):
    # Windows Notepad writes UTF-8 with a BOM
    drops = tmp_path / "drops.json"
    drops.write_bytes(b"\xef\xbb\xbf" + json.dumps({"drop": [1], "gap": 0.3}).encode())
    out = tmp_path / "o.mp4"
    assert roughcut.main(["render", str(synth), str(drops), str(out)]) == 0
    assert json.loads(Path(str(out) + ".segments.json").read_text(encoding="utf-8"))["drop"] == ["1"]
    fake = FakeASR(monkeypatch)
    cache = tmp_path / "w" / "asr.json"
    cache.parent.mkdir()
    cache.write_bytes(b"\xef\xbb\xbf" + json.dumps(fake.transcribe("x")).encode())
    fake.whole_calls.clear()
    assert roughcut.main(["phrases", str(synth), "--out", str(tmp_path / "w" / "p.json")]) == 0
    assert fake.whole_calls == [] and json.loads((tmp_path / "w" / "p.json").read_text(encoding="utf-8"))[0]["text"] == "ก"
    phr = tmp_path / "w" / "p.json"
    phr.write_bytes(b"\xef\xbb\xbf" + phr.read_bytes())
    assert roughcut.main(["phrases", str(synth), "--out", str(phr)]) == 0         # the skip path reads it too


def test_remove_survives_a_locked_file(tmp_path, monkeypatch):
    f = tmp_path / "x.filter"
    f.write_text("x")

    def locked(path):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(os, "remove", locked)
    roughcut._remove(f)                                       # an antivirus holding the file must not fail a finished render
    roughcut._remove(tmp_path / "never-existed")


def make_late_clip(tmp_path, offset=0.3):
    """mp4 whose audio starts `offset` s after the video (the bursts are at BURSTS + offset on the container clock)."""
    wav = tmp_path / "src.wav"
    write_wav(wav, BURSTS, TOTAL)
    late = tmp_path / "late.mp4"
    kit.run([kit.tool("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", f"color=c=black:s=320x240:r=30:d={TOTAL + 1}",
             "-itsoffset", str(offset), "-i", wav, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", late])
    res = kit.run([kit.tool("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=start_time", "-of", "csv=p=0", late])
    assert float(res.stdout.strip()) > offset / 2, "fixture is not really late-starting"   # offset minus AAC priming
    return late


def test_phrase_times_follow_the_container_clock_when_audio_starts_late(tmp_path):
    late = make_late_clip(tmp_path)
    ph, dur = roughcut.phrases(str(late))
    for (s, e), (es, ee) in zip(ph, BURSTS):
        assert s == pytest.approx(es + 0.3, abs=0.06) and e == pytest.approx(ee + 0.3, abs=0.06)
    # and a render cuts where the phrases are: with phrase 0 dropped nothing of it may remain
    drops, out = _drops(tmp_path / "d.json", drop=[0]), tmp_path / "o.mp4"
    assert roughcut.main(["render", str(late), str(drops), str(out)]) == 0
    seg = json.loads(Path(str(out) + ".segments.json").read_text(encoding="utf-8"))
    assert seg["keep"][0][0] >= ph[0][1] - 1e-6


def _first_onset(wav_path):
    with wave.open(str(wav_path)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768.0
    return int(np.argmax(np.abs(a) > 0.1)) / SR


def test_asr_audio_and_phrase_audio_put_the_onset_at_the_same_time(tmp_path):
    late = make_late_clip(tmp_path)
    wav = tmp_path / "asr.wav"
    asr.extract_wav(late, wav)
    onset = _first_onset(wav)
    ph, _ = roughcut.phrases(str(late))
    assert onset == pytest.approx(BURSTS[0][0] + 0.3, abs=0.06)       # container clock, not the 0.2 s the raw samples say
    assert onset == pytest.approx(ph[0][0], abs=0.03)                 # the same clock the phrases are measured on


class BurstEars:
    """A 'model' that hears the audio it is given: one 0.1 s word at the start of every loud burst."""
    def transcribe(self, audio, **kw):
        loud = np.flatnonzero(np.abs(audio) > 0.1)
        starts = [loud[0]] + [b for a, b in zip(loud, loud[1:]) if b - a > 0.1 * SR] if len(loud) else []
        segs = [NS(start=s / SR, end=s / SR + 0.4, text=f"w{i}", words=[NS(start=s / SR, end=s / SR + 0.1, word=f"w{i}", probability=1)])
                for i, s in enumerate(starts)]
        return iter(segs), NS(language="th")


def test_default_phrases_path_matches_asr_tokens_to_phrases_on_one_clock(tmp_path, monkeypatch):
    late = make_late_clip(tmp_path)
    monkeypatch.setattr(asr, "backend_choice", lambda: ("faster-whisper", False))
    monkeypatch.setattr(asr, "load_model", lambda *a, **k: BurstEars())
    out = tmp_path / "p" / "phrases.json"
    assert roughcut.main(["phrases", str(late), "--out", str(out)]) == 0
    assert [r["text"] for r in json.loads(out.read_text(encoding="utf-8"))] == ["w0", "w1", "w2", "w3"]


# ---------------------------------------------------------------- phrases CLI

class FakeASR:
    """Replaces asr.transcribe / asr.transcribe_many so no model is needed."""
    def __init__(self, monkeypatch, tokens=None):
        self.whole_calls, self.many_calls, self.many_files, self.many_dirs = [], [], [], []
        self.tokens = tokens
        monkeypatch.setattr(asr, "transcribe", self.transcribe)
        monkeypatch.setattr(asr, "transcribe_many", self.transcribe_many)

    def transcribe(self, path, prompt=None):
        self.whole_calls.append((path, prompt))
        toks = self.tokens or [("ก", 600, 700), ("ข", 1500, 1600), ("ค", 2400, 2500), ("ง", 3300, 3400)]
        return {"transcription": [{"offsets": {"from": toks[0][1], "to": toks[-1][2]}, "text": "".join(t for t, _, _ in toks),
                                   "tokens": [{"text": t, "offsets": {"from": a, "to": b}} for t, a, b in toks]}]}

    def transcribe_many(self, wavs, prompt=None):
        self.many_calls.append(prompt)
        self.many_files.append([(Path(w).name, wave_duration(w)) for w in wavs])
        self.many_dirs.append(Path(wavs[0]).parent)
        return [f"วลี{i}" for i, _ in enumerate(wavs)]


def wave_duration(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def test_phrases_cli_without_whisper_uses_whole_clip_asr_and_caches_it(synth, tmp_path, monkeypatch, capsys):
    fake = FakeASR(monkeypatch)
    out = tmp_path / "งาน ใหม่" / "phrases.json"
    assert roughcut.main(["phrases", str(synth), "--out", str(out)]) == 0
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert [r["text"] for r in rows] == ["ก", "ข", "ค", "ง"]
    assert [r["i"] for r in rows] == [0, 1, 2, 3]
    assert set(rows[0]) == {"i", "s", "e", "text", "gap"}
    assert rows[0]["gap"] == pytest.approx(0.4, abs=0.06) and rows[-1]["gap"] == 0
    assert (out.parent / "asr.json").exists()
    shown = capsys.readouterr().out
    assert "phrases 4" in shown and "⏸" in shown
    assert re.search(r"^  0 +0\.\d\d- +[01]\.\d\d  ก", shown, re.M)
    # a second run reuses phrases.json (skip) and never calls the model
    out.write_text(json.dumps([{"i": 0, "s": 0.5, "e": 1.0, "text": "แก้เอง", "gap": 0.3}]), encoding="utf-8")
    assert roughcut.main(["phrases", str(synth), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))[0]["text"] == "แก้เอง"
    assert len(fake.whole_calls) == 1 and fake.many_calls == []
    assert "แก้เอง" in capsys.readouterr().out


def test_phrases_cli_force_reuses_cached_asr_only_when_not_forced(synth, tmp_path, monkeypatch):
    fake = FakeASR(monkeypatch)
    out = tmp_path / "phrases.json"
    roughcut.main(["phrases", str(synth), "--out", str(out)])
    out.unlink()                                  # phrases gone, asr.json still there -> no new model run
    roughcut.main(["phrases", str(synth), "--out", str(out)])
    assert len(fake.whole_calls) == 1
    roughcut.main(["phrases", str(synth), "--out", str(out), "--force"])
    assert len(fake.whole_calls) == 2


def test_phrases_cli_redoes_everything_for_a_reshot_video_with_the_same_name(tmp_path, monkeypatch, capsys):
    fake = FakeASR(monkeypatch)
    video = make_clip(tmp_path / "คลิป ถ่ายใหม่.mp4", BURSTS, TOTAL)
    out = tmp_path / "งาน" / "phrases.json"
    assert roughcut.main(["phrases", str(video), "--out", str(out)]) == 0
    stamp = json.loads((out.parent / "phrases.source.json").read_text(encoding="utf-8"))
    assert stamp["size"] == video.stat().st_size
    out.write_text(json.dumps([{"i": 0, "s": 0.5, "e": 1.0, "text": "แก้เอง", "gap": 0.3}]), encoding="utf-8")
    capsys.readouterr()

    make_clip(video, [(0.5, 1.5), (2.0, 3.0)], 3.5)                       # re-shot: two phrases now, same file name
    assert roughcut.main(["phrases", str(video), "--out", str(out)]) == 0
    said = capsys.readouterr().out
    assert "เปลี่ยน" in said and "drops.json" in said                       # says why, and that the take picks are stale
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert len(rows) == 2 and len(fake.whole_calls) == 2                    # new phrases, transcribed again (cache ignored)
    old = json.loads((out.parent / "phrases.previous.json").read_text(encoding="utf-8"))
    assert old[0]["text"] == "แก้เอง"                                      # the hand-edited list is kept, not lost

    capsys.readouterr()
    assert roughcut.main(["phrases", str(video), "--out", str(out)]) == 0   # current again: plain skip
    assert len(fake.whole_calls) == 2 and "ข้าม" in capsys.readouterr().out


def test_phrases_cli_does_not_reuse_an_old_asr_cache_after_a_reshoot(tmp_path, monkeypatch):
    fake = FakeASR(monkeypatch)
    video = make_clip(tmp_path / "v.mp4", BURSTS, TOTAL)
    out = tmp_path / "phrases.json"
    roughcut.main(["phrases", str(video), "--out", str(out), "--whisper"])   # --whisper: no asr.json written
    roughcut.main(["phrases", str(video), "--out", str(out), "--force"])     # asr.json of this take
    make_clip(video, [(0.5, 1.5), (2.0, 3.0)], 3.5)
    out.unlink()                                                             # phrases gone, asr.json still there
    roughcut.main(["phrases", str(video), "--out", str(out)])
    assert len(fake.whole_calls) == 2


def test_phrases_from_an_older_kit_without_a_stamp_compare_file_times(tmp_path, monkeypatch):
    fake = FakeASR(monkeypatch)
    video = make_clip(tmp_path / "v.mp4", BURSTS, TOTAL)
    out = tmp_path / "phrases.json"
    roughcut.main(["phrases", str(video), "--out", str(out)])
    (tmp_path / "phrases.source.json").unlink()
    made = out.stat().st_mtime_ns
    os.utime(video, ns=(made - 60_000_000_000, made - 60_000_000_000))        # older than its phrases: still current
    roughcut.main(["phrases", str(video), "--out", str(out)])
    assert len(fake.whole_calls) == 1 and (tmp_path / "phrases.source.json").is_file()
    (tmp_path / "phrases.source.json").unlink()
    os.utime(video, ns=(made + 60_000_000_000, made + 60_000_000_000))        # newer: re-shot after the list was made
    roughcut.main(["phrases", str(video), "--out", str(out)])
    assert len(fake.whole_calls) == 2


def test_render_will_not_skip_onto_a_roughcut_of_another_take(tmp_path, capsys):
    video = make_clip(tmp_path / "v.mp4", BURSTS, TOTAL)
    out = tmp_path / "roughcut.mp4"
    assert roughcut.main(["render", str(video), str(_drops(tmp_path / "d.json")), str(out)]) == 0
    assert roughcut.main(["render", str(video), str(_drops(tmp_path / "d.json")), str(out)]) == 0   # same take: skip
    assert "ข้าม" in capsys.readouterr().out
    make_clip(video, [(0.5, 1.5), (2.0, 3.0), (3.4, 3.9), (4.3, 4.8)], 5.2)  # re-shot under the same name
    before = out.read_bytes()
    assert roughcut.main(["render", str(video), str(_drops(tmp_path / "d.json")), str(out)]) == 1
    err = capsys.readouterr().err
    assert "เปลี่ยน" in err and "--force" in err and "drops.json" in err
    assert out.read_bytes() == before                                        # nothing overwritten without --force
    assert roughcut.main(["render", str(video), str(_drops(tmp_path / "d.json")), str(out), "--force"]) == 0
    seg = json.loads(Path(str(out) + ".segments.json").read_text(encoding="utf-8"))
    assert seg["size"] == video.stat().st_size


def test_phrases_cli_whisper_rehears_every_phrase_alone_with_padding(synth, tmp_path, monkeypatch, capsys):
    fake = FakeASR(monkeypatch)
    out = tmp_path / "phrases.json"
    assert roughcut.main(["phrases", str(synth), "--out", str(out), "--whisper"]) == 0
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert [r["text"] for r in rows] == ["วลี0", "วลี1", "วลี2", "วลี3"]
    assert fake.whole_calls == [] and len(fake.many_calls) == 1
    (files,) = fake.many_files
    assert len(files) == 4
    for (name, dur), r in zip(files, rows):       # phrase + ~0.13 s context + 0.6 s apad silence
        assert dur == pytest.approx((r["e"] - r["s"]) + 0.13 + 0.6, abs=0.1)
    assert "⟨วลี0⟩" in capsys.readouterr().out    # heard-alone text is shown in brackets, as in the original
    assert not fake.many_dirs[0].exists()          # temp wavs are removed after the run


def test_phrases_cli_hint_goes_to_the_whole_clip_run_but_not_to_per_phrase_runs(synth, tmp_path, monkeypatch):
    fake = FakeASR(monkeypatch)
    cfg = kit.load_config()
    monkeypatch.setenv("TAD_CLIP_HOME", str(tmp_path / "home"))
    kit.save_config({"tools": cfg["tools"], "glossary": {"prompt_hint": "ชื่อแบรนด์ Black Pan"}})
    roughcut.main(["phrases", str(synth), "--out", str(tmp_path / "p1.json"), "--whisper"])
    roughcut.main(["phrases", str(synth), "--out", str(tmp_path / "p2.json")])
    base = json.loads((ROOT / "engine" / "subs" / "glossary-base.json").read_text(encoding="utf-8"))["prompt_hint"]
    assert fake.whole_calls[0][1] == base + ", ชื่อแบรนด์ Black Pan"      # shipped Thai hint + the user's own
    # measured on the fixture: with the hint, faster-whisper answers 0.1 s phrases with the hint text itself
    assert fake.many_calls == [None]


def test_cli_errors_are_thai_messages_not_tracebacks(tmp_path, capsys):
    assert roughcut.main(["phrases", str(tmp_path / "ไม่มี ไฟล์.mp4"), "--out", str(tmp_path / "p.json")]) == 1
    err = capsys.readouterr().err
    assert "ไม่พบไฟล์" in err and "Traceback" not in err
