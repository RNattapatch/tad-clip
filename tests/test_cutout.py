"""engine.cutout — speaker cut-out (Apple Vision on macOS · transparent-background anywhere it is installed)."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import cutout, kit  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "คลิป ทดสอบ.mp4"
mac_only = pytest.mark.skipif(not kit.IS_MAC, reason="Apple Vision is macOS only")


# ---------------------------------------------------------------- engine resolution (config.cutout.engine)

def test_auto_is_vision_on_mac(monkeypatch):
    monkeypatch.setattr(cutout, "IS_MAC", True)
    monkeypatch.setattr(cutout, "IS_WINDOWS", False)
    assert cutout.resolve_engine(cfg={"cutout": {"engine": "auto"}}) == "vision"


@pytest.mark.parametrize("installed, expected", [(True, "tb"), (False, "off")])
def test_auto_on_windows_depends_on_transparent_background(monkeypatch, installed, expected):
    monkeypatch.setattr(cutout, "IS_MAC", False)
    monkeypatch.setattr(cutout, "IS_WINDOWS", True)
    monkeypatch.setattr(cutout, "tb_installed", lambda *a: installed)
    assert cutout.resolve_engine(cfg={"cutout": {"engine": "auto"}}) == expected


@pytest.mark.parametrize("value, expected", [("off", "off"), ("none", "off"), ("vision", "vision"), ("tb", "tb"), ("TB", "tb")])
def test_explicit_engine(value, expected):
    assert cutout.resolve_engine(cfg={"cutout": {"engine": value}}) == expected


def test_cli_argument_beats_config():
    assert cutout.resolve_engine("tb", cfg={"cutout": {"engine": "off"}}) == "tb"


def test_unknown_engine_is_a_thai_error():
    with pytest.raises(kit.KitError, match="vision"):
        cutout.resolve_engine("magic", cfg={})


# ---------------------------------------------------------------- output encoders (same args as the original cutout.sh)

def test_mov_is_prores_4444():
    args = cutout.encoder_args(Path("x/ตัวพูด ตัดพื้น.MOV"))
    assert args[args.index("-c:v") + 1] == "prores_ks"
    assert args[args.index("-profile:v") + 1] == "4444"
    assert args[args.index("-pix_fmt") + 1] == "yuva444p10le"


def test_webm_is_vp9_alpha():
    args = cutout.encoder_args(Path("cache.webm"))
    assert args[args.index("-c:v") + 1] == "libvpx-vp9"
    assert args[args.index("-pix_fmt") + 1] == "yuva420p"
    assert args[args.index("-crf") + 1] == "18" and "-row-mt" in args


def test_other_extension_is_refused():
    with pytest.raises(kit.KitError, match=r"\.mov"):
        cutout.encoder_args(Path("out.mp4"))


# ---------------------------------------------------------------- vision binary cache + source normalisation

def test_vision_binary_recompiles_only_when_swift_is_newer(tmp_path):
    src, bin_ = tmp_path / "vision-matte.swift", tmp_path / "vision-matte"
    src.write_text("//")
    assert cutout.needs_compile(bin_, src)            # never built
    bin_.write_text("bin")
    os.utime(src, (time.time() - 100, time.time() - 100))
    assert not cutout.needs_compile(bin_, src)        # binary newer than source
    os.utime(src, (time.time() + 100, time.time() + 100))
    assert cutout.needs_compile(bin_, src)            # source edited after the build


@pytest.mark.parametrize("info, expected", [
    ({"pix_fmt": "yuv420p", "r_frame_rate": "30/1", "avg_frame_rate": "30/1"}, False),
    ({"pix_fmt": "yuv420p10le", "r_frame_rate": "60/1", "avg_frame_rate": "60/1"}, True),           # iPhone HEVC 10-bit
    ({"pix_fmt": "yuv420p", "r_frame_rate": "120/1", "avg_frame_rate": "5994/100"}, True),         # variable frame rate
    ({"pix_fmt": "yuv420p", "r_frame_rate": "30/1", "avg_frame_rate": "30/1", "rotation": -90}, True),
    ({"pix_fmt": "yuv420p", "r_frame_rate": "30/1", "avg_frame_rate": "30/1", "color_transfer": "arib-std-b67"}, True),  # HLG
])
def test_needs_normalize(info, expected):
    assert cutout.needs_normalize(info) is expected


def test_hdr_sources_get_a_tonemap_chain():
    chain = cutout.sdr_chain({"color_transfer": "smpte2084", "color_primaries": "bt2020", "color_space": "bt2020nc"})
    assert "tonemap" in chain and chain.endswith("format=yuv420p")
    assert cutout.sdr_chain({"color_transfer": "bt709"}) == "format=yuv420p"


def test_tb_device_order(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["code"] = cmd[2]
        return subprocess.CompletedProcess(cmd, 0, stdout="mps\n", stderr="")

    monkeypatch.setattr(cutout.kit, "run", fake_run)
    assert cutout.tb_device("python") == "mps"
    code = seen["code"]
    assert code.index("cuda") < code.index("mps") < code.index("cpu")


def test_tb_python_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TAD_CLIP_TB_PYTHON", str(tmp_path / "tb python"))
    assert cutout.tb_python() == str(tmp_path / "tb python")


def test_cli_off_engine_explains_split(tmp_path, monkeypatch):
    monkeypatch.setenv("TAD_CLIP_HOME", str(tmp_path / "home"))
    kit.save_config({"cutout": {"engine": "off"}})
    clip = tmp_path / "คลิป.mp4"
    clip.write_bytes(b"x")
    code = cutout.main([str(clip), str(tmp_path / "out.webm")])
    assert code == 2


# ---------------------------------------------------------------- real cut-outs (fixture: HEVC 10-bit 60 fps, Thai + space in the name)

def _segment(tmp_path, start, dur):
    d = tmp_path / "งาน ตัดพื้น"
    d.mkdir(exist_ok=True)
    seg = d / f"ช่วง {start}.mp4"
    kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-ss", str(start), "-i", str(FIXTURE), "-t", str(dur), "-an", "-c", "copy", str(seg)])
    return seg


def _alpha_range(out: Path, at: float):
    """min / max of the decoded alpha plane at one frame (libvpx decoder keeps VP9 alpha)."""
    pre = ["-c:v", "libvpx-vp9"] if out.suffix == ".webm" else []
    r = kit.run([kit.tool("ffmpeg"), "-v", "error", *pre, "-ss", str(at), "-i", str(out), "-frames:v", "1",
                 "-vf", "alphaextract,signalstats,metadata=mode=print:file=-", "-f", "null", "-"])
    vals = dict(l.split("=", 1) for l in r.stdout.split() if l.startswith("lavfi.signalstats.Y"))
    return float(vals["lavfi.signalstats.YMIN"]), float(vals["lavfi.signalstats.YMAX"])


def _run_cli(*args, env=None):
    return subprocess.run([sys.executable, "-m", "engine.cutout", *map(str, args)], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", env={**os.environ, **(env or {})})


@mac_only
def test_vision_cutout_webm_has_alpha(tmp_path):
    seg = _segment(tmp_path, 5, 1)
    out = seg.parent / "ตัดพื้น ผลลัพธ์.webm"
    r = _run_cli(seg, out, "vision")
    assert r.returncode == 0, r.stderr
    assert "✓" in r.stdout
    st = json.loads(kit.run([kit.tool("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(out)]).stdout)["streams"][0]
    assert st["codec_name"] == "vp9"
    assert st.get("tags", {}).get("alpha_mode") == "1"
    lo, hi = _alpha_range(out, 0.5)
    assert lo < 30 and hi > 220, (lo, hi)   # background cleared, speaker opaque


@mac_only
def test_vision_cutout_mov_is_prores_with_alpha_and_skips_when_done(tmp_path):
    seg = _segment(tmp_path, 12, 0.5)
    out = seg.parent / "ตัวพูด.mov"
    assert _run_cli(seg, out, "vision").returncode == 0
    st = json.loads(kit.run([kit.tool("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(out)]).stdout)["streams"][0]
    assert st["codec_name"] == "prores" and st["pix_fmt"].startswith("yuva444p")
    again = _run_cli(seg, out, "vision")
    assert again.returncode == 0 and "ข้าม" in again.stdout   # existing output is kept unless --force


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("TAD_CLIP_TB_PYTHON") and not cutout.tb_installed(),
                    reason="transparent-background not installed (set TAD_CLIP_TB_PYTHON to test it)")
def test_tb_cutout_has_alpha(tmp_path):
    seg = _segment(tmp_path, 6, 0.4)
    out = seg.parent / "tb ตัดพื้น.webm"
    r = _run_cli(seg, out, "tb")
    assert r.returncode == 0, r.stderr
    lo, hi = _alpha_range(out, 0.2)
    assert lo < 30 and hi > 220, (lo, hi)
