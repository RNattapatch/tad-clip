"""engine.cutout — speaker cut-out (Apple Vision on macOS · transparent-background anywhere it is installed)."""
import json
import os
import subprocess
import sys
import threading
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


def test_vision_off_mac_is_a_thai_error(monkeypatch):
    monkeypatch.setattr(cutout, "IS_MAC", False)
    with pytest.raises(kit.KitError, match="Mac"):
        cutout.vision_bin()


def test_swiftc_shim_without_command_line_tools_is_a_thai_error(monkeypatch, tmp_path):
    """/usr/bin/swiftc exists on every Mac — only `xcrun --find swiftc` says whether the compiler is really there."""
    monkeypatch.setattr(cutout, "IS_MAC", True)
    monkeypatch.setattr(cutout.kit, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(cutout.shutil, "which", lambda name: "/usr/bin/" + name)

    def fake_run(cmd, **kw):
        assert cmd[:3] == ["xcrun", "--find", "swiftc"], cmd
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="xcrun: error: invalid active developer path")

    monkeypatch.setattr(cutout.subprocess, "run", fake_run)
    with pytest.raises(kit.KitError, match="xcode-select --install"):
        cutout.vision_bin()


def test_an_older_vision_build_keeps_cutting_video_when_the_compiler_is_gone(monkeypatch, tmp_path, capsys):
    """After a kit update the swift source is newer than the cached build; a Mac without the compiler keeps the old
    build for video (warning), but a still photo needs the new build (older builds cannot read photos)."""
    monkeypatch.setattr(cutout, "IS_MAC", True)
    monkeypatch.setattr(cutout.kit, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(cutout, "swiftc_path", lambda: None)
    old = tmp_path / "vision-matte"
    old.write_text("old build")
    os.utime(old, (1, 1))
    assert cutout.vision_bin() == old
    assert "xcode-select --install" in capsys.readouterr().err
    with pytest.raises(kit.KitError, match="xcode-select --install"):
        cutout.vision_bin(still=True)


@mac_only
def test_vision_binary_builds_from_scratch_through_xcrun(tmp_path, monkeypatch):
    monkeypatch.setattr(cutout.kit, "cache_dir", lambda: tmp_path)
    b = cutout.vision_bin()
    assert b == tmp_path / "vision-matte" and b.stat().st_size > 10_000 and os.access(b, os.X_OK)


@pytest.mark.skipif(kit.IS_WINDOWS, reason="the fake matte tool is a POSIX script")
def test_chatty_matte_tool_does_not_deadlock(tmp_path, monkeypatch):
    """vision-matte logs to stderr while ffmpeg reads its stdout — >64 KB of log must not block the pipe."""
    clip = tmp_path / "คลิป เล็ก.mp4"
    kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10", "-t", "1",
             "-pix_fmt", "yuv420p", "-c:v", "libx264", str(clip)])
    fake = tmp_path / "fake-matte"
    fake.write_text(f"#!{sys.executable}\nimport sys\nsys.stderr.write('log ' * 100000)\nsys.stderr.flush()\n"
                    "sys.stdout.buffer.write(bytes([255]) * (64 * 64 * 10))\n")
    fake.chmod(0o755)
    monkeypatch.setattr(cutout, "vision_bin", lambda: fake)
    out = tmp_path / "ผล.webm"
    err = []
    t = threading.Thread(target=lambda: err.append(None) if cutout._vision(clip, out, cutout.probe(clip)) is None else None, daemon=True)
    t.start()
    t.join(60)
    assert not t.is_alive(), "deadlock: vision-matte stderr filled its pipe"
    assert out.exists() and out.stat().st_size > 0


def test_tb_runs_with_the_kit_model_caches(tmp_path, monkeypatch):
    """setup pre-downloads transparent-background weights + torch/HF caches into the kit home — the tb run must use them,
    not download a second copy outside ~/.tad-clip."""
    from engine.install import setup
    monkeypatch.setenv("TAD_CLIP_HOME", str(tmp_path / "บ้าน kit"))
    monkeypatch.delenv("TAD_CLIP_TB_PYTHON", raising=False)
    monkeypatch.setattr(cutout, "tb_python", lambda: "tb-python")
    monkeypatch.setattr(cutout, "tb_installed", lambda *a: True)
    monkeypatch.setattr(cutout, "tb_device", lambda *a, **k: "cpu")
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        if cutout.TB_MAIN in cmd:
            (Path(kw["cwd"]) / "src_map.mp4").write_bytes(b"map")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(cutout.kit, "run", fake_run)
    monkeypatch.setattr(cutout.kit, "tool", lambda name: name)   # the fresh home has no tools; PATH may have no ffmpeg (CI)
    work = tmp_path / "งาน"
    work.mkdir()
    (work / "src.mp4").write_bytes(b"clip")
    cutout._tb(work / "src.mp4", tmp_path / "out.webm", {"avg_frame_rate": "30/1"}, work)

    env = next(kw["env"] for cmd, kw in calls if cutout.TB_MAIN in cmd)
    want = setup.kit_dirs()
    home = kit.home()
    for key in ("TRANSPARENT_BACKGROUND_FILE_PATH", "TORCH_HOME", "HF_HOME"):
        assert env[key] == want[key], key                       # exactly the paths setup downloaded into
        assert Path(env[key]).is_dir(), key                     # created if missing
        assert Path(env[key]).is_relative_to(home), key         # nothing outside the kit home
    assert env["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"


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
    r = subprocess.run([sys.executable, "-m", "engine.cutout", *map(str, args)], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", env={**os.environ, **(env or {})})
    if "Could not create inference context" in (r.stderr or ""):
        pytest.skip("Apple Vision has no GPU/Neural Engine on this machine (CI virtual machine)")
    return r


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


# ---------------------------------------------------------------- still photos (mode stage: visual.pose)

def _photo(path, size=(120, 200), exif_rotate=False):
    """Grey photo with a darker 'person' block in the middle; exif_rotate stores it sideways + an EXIF 'rotate' tag."""
    from PIL import Image
    im = Image.new("RGB", size, (200, 200, 200))
    im.paste((40, 60, 90), (40, 50, 80, 190))
    if exif_rotate:   # stored rotated 90° CW · orientation 6 = viewers rotate it back 90° CW → upright
        exif = Image.Exif()
        exif[0x0112] = 6
        im.transpose(Image.Transpose.ROTATE_90).save(path, exif=exif)
    else:
        im.save(path)
    return path


def _fake_vision(tmp_path, monkeypatch, box=(40, 50, 80, 190)):
    """vision-matte stand-in: reads the PNG it is given and prints a W×H matte that is white inside `box`."""
    fake = tmp_path / "fake-matte"
    fake.write_text(f"#!{sys.executable}\nimport sys\nfrom PIL import Image\nw, h = Image.open(sys.argv[1]).size\n"
                    f"x0, y0, x1, y1 = {box}\n"
                    "sys.stdout.buffer.write(bytes(255 if x0 <= x < x1 and y0 <= y < y1 else 0 for y in range(h) for x in range(w)))\n")
    fake.chmod(0o755)
    monkeypatch.setattr(cutout, "vision_bin", lambda **kw: fake)


def test_png_output_is_for_stills_only():
    with pytest.raises(kit.KitError, match=r"\.mov"):
        cutout.encoder_args(Path("คลิป.png"))
    assert cutout.is_still("รูป ท่า 1.JPG") and cutout.is_still("x.webp") and not cutout.is_still("x.mp4")


def test_still_needs_png_output(tmp_path):
    src = _photo(tmp_path / "รูป.jpg")
    with pytest.raises(kit.KitError, match=r"\.png"):
        cutout.cutout(src, tmp_path / "out.webm", "vision")


@pytest.mark.skipif(kit.IS_WINDOWS, reason="the fake matte tool is a POSIX script")
def test_still_vision_gives_a_transparent_png_cropped_to_the_person(tmp_path, monkeypatch):
    from PIL import Image
    _fake_vision(tmp_path, monkeypatch)
    out = tmp_path / "ผล ท่า.png"
    info = cutout.cutout(_photo(tmp_path / "รูป ท่า 1.jpg"), out, "vision")
    with Image.open(out) as im:
        assert im.mode == "RGBA"
        assert im.size == (info["width"], info["height"])
        assert 40 <= im.width <= 44 and 140 <= im.height <= 146, im.size     # 40×140 person + 2 % margin
        a = im.getchannel("A")
        assert a.getpixel((im.width // 2, im.height // 2)) == 255             # the person is opaque
        assert a.getpixel((0, 0)) == 0                                         # margin is transparent


@pytest.mark.skipif(kit.IS_WINDOWS, reason="the fake matte tool is a POSIX script")
def test_still_applies_the_exif_rotation_before_the_matte(tmp_path, monkeypatch):
    from PIL import Image
    _fake_vision(tmp_path, monkeypatch)
    out = tmp_path / "upright.png"
    cutout.cutout(_photo(tmp_path / "sideways.jpg", exif_rotate=True), out, "vision")
    with Image.open(out) as im:
        assert im.height > im.width, im.size                                   # tall person, not lying on its side


@pytest.mark.skipif(kit.IS_WINDOWS, reason="the fake matte tool is a POSIX script")
def test_still_without_a_person_is_a_thai_error(tmp_path, monkeypatch):
    _fake_vision(tmp_path, monkeypatch, box=(0, 0, 0, 0))
    with pytest.raises(kit.KitError, match="ไม่เจอคน"):
        cutout.cutout(_photo(tmp_path / "ว่าง.png"), tmp_path / "out.png", "vision")


def test_still_tb_runs_on_the_photo_and_merges_its_map(tmp_path, monkeypatch):
    """Windows path: transparent-background writes <name>_map.png next to the photo; the kit merges it into the alpha."""
    from PIL import Image
    monkeypatch.setenv("TAD_CLIP_HOME", str(tmp_path / "บ้าน kit"))
    monkeypatch.setattr(cutout, "tb_python", lambda: "tb-python")
    monkeypatch.setattr(cutout, "tb_installed", lambda *a: True)
    monkeypatch.setattr(cutout, "tb_device", lambda *a, **k: "cpu")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["cwd"] = cmd, kw["cwd"]
        w, h = Image.open(Path(kw["cwd"]) / cmd[cmd.index("--source") + 1]).size
        m = Image.new("L", (w // 2, h // 2), 0)                               # tb maps can come back at another size
        m.paste(255, (20, 25, 40, 95))
        m.save(Path(kw["cwd"]) / "src_map.png")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(cutout.kit, "run", fake_run)
    out = tmp_path / "ผล tb.png"
    cutout.cutout(_photo(tmp_path / "รูป.webp"), out, "tb")
    assert seen["cmd"][seen["cmd"].index("--source") + 1] == "src.png"      # plain ASCII name inside the work dir
    with Image.open(out) as im:
        assert im.mode == "RGBA" and 40 <= im.width <= 48 and 140 <= im.height <= 150, im.size   # map upscaled ×2 (soft edge)


@mac_only
def test_vision_cuts_a_real_photo(tmp_path):
    """A frame of the fixture (a real person) → transparent PNG through the CLI, like a user making a pose image."""
    photo = tmp_path / "ท่า จากคลิป.jpg"
    kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-ss", "5", "-i", str(FIXTURE), "-frames:v", "1", "-q:v", "3", str(photo)])
    out = tmp_path / "ท่า พื้นใส.png"
    r = _run_cli(photo, out, "vision")
    assert r.returncode == 0, r.stderr
    from PIL import Image
    with Image.open(out) as im:
        lo, hi = im.getchannel("A").getextrema()
        assert im.mode == "RGBA" and lo < 30 and hi > 220, (im.mode, lo, hi)


@mac_only
def test_vision_video_matte_has_no_empty_first_frame(tmp_path):
    """Vision used to return an empty matte for frame 0 → every cut-out shot opened with one frame without the speaker."""
    seg = _segment(tmp_path, 5, 0.5)
    out = seg.parent / "เฟรมแรก.webm"
    r = _run_cli(seg, out, "vision")
    assert r.returncode == 0, r.stderr
    lo, hi = _alpha_range(out, 0)
    assert hi > 220, (lo, hi)


@mac_only
def test_vision_cuts_an_iphone_heic_photo(tmp_path):
    """iPhone photos are .HEIC by default; Pillow cannot read them, sips converts (EXIF rotation kept)."""
    jpg = tmp_path / "รูป.jpg"
    kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-ss", "5", "-i", str(FIXTURE), "-frames:v", "1", "-q:v", "3", str(jpg)])
    heic = tmp_path / "ท่า iPhone.HEIC"
    kit.run(["sips", "-s", "format", "heic", str(jpg), "--out", str(heic)])
    out = tmp_path / "ท่า.png"
    r = _run_cli(heic, out, "vision")
    assert r.returncode == 0, r.stderr
    from PIL import Image
    with Image.open(out) as im:
        assert im.mode == "RGBA" and im.height > im.width, (im.mode, im.size)


def test_heic_off_mac_is_a_thai_error(tmp_path, monkeypatch):
    monkeypatch.setattr(cutout, "IS_MAC", False)
    heic = tmp_path / "ท่า.heic"
    heic.write_bytes(b"not really a heic")
    with pytest.raises(kit.KitError, match="HEIC"):
        cutout.cutout(heic, tmp_path / "out.png", "tb")
