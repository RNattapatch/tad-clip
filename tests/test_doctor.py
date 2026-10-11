"""doctor.py — every check names the missing piece in Thai and gives one command to fix it."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import kit  # noqa: E402
from engine import doctor  # noqa: E402
from engine import install as inst  # noqa: E402


def _real(name):
    try:
        return kit.tool(name)
    except kit.KitError:
        return None


# resolved once with the developer's / CI's real TAD_CLIP_HOME, before each test swaps in a temp home
REAL_FFMPEG = _real("ffmpeg")
REAL_FFPROBE = _real("ffprobe")
HAS_LIBASS = bool(REAL_FFMPEG) and inst.has_libass(REAL_FFMPEG)

FULL_IDS = ["setup", "gitbash", "ffmpeg", "ffprobe", "node", "python", "asr", "model", "chrome", "ytdlp", "cutout", "disk", "ram"]
QUICK_IDS = ["setup", "gitbash", "ffmpeg", "ffprobe", "node", "python", "asr", "model", "chrome"]
QC_IDS = ["duration", "size", "audio", "black", "peak"]
CHECK_KEYS = {"id", "name", "status", "detail", "fix"}


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "บ้าน tad clip"
    monkeypatch.setenv("TAD_CLIP_HOME", str(home))
    return home


def fake_exe(folder: Path, name: str, body: str) -> str:
    """A fake command-line tool: a Python script behind a launcher the OS can run directly."""
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / f"{name}_impl.py"
    script.write_text("import sys, time\nargs = sys.argv[1:]\n" + body, encoding="utf-8")
    if kit.IS_WINDOWS:
        exe = folder / f"{name}.cmd"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = folder / name
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    return str(exe)


FILTERS_NO_ASS = """
if "-filters" in args:
    print(" ... scale             V->V       Scale the input video size and/or convert the image format.")
    print(" ... overlay           VV->V      Overlay a video source on top of the input.")
    print(" ... ebur128           A->N       EBU R128 scanner.")
else:
    print("ffmpeg version 7.1-fake")
"""
FILTERS_WITH_ASS = FILTERS_NO_ASS.replace(
    'print(" ... ebur128',
    'print(" ... ass               V->V       Render ASS subtitles onto input video using the libass library.")\n'
    '    print(" ... subtitles         V->V       Render text subtitles onto input video using the libass library.")\n'
    '    print(" ... ebur128',
)


def by_id(checks):
    return {c["id"]: c for c in checks}


# ---------------------------------------------------------------- ffmpeg
def test_ffmpeg_without_libass_is_red_with_fix(tmp_path):
    kit.save_config({"tools": {"ffmpeg": fake_exe(tmp_path / "bin", "ffmpeg", FILTERS_NO_ASS)}})
    c = doctor.check_ffmpeg()
    assert c["status"] == "fail"
    assert "libass" in c["detail"] and "ass" in c["detail"]
    assert c["fix"] and ("setup.py" in c["fix"] or "ffmpeg-full" in c["fix"])


def test_ffmpeg_with_libass_is_green(tmp_path):
    exe = fake_exe(tmp_path / "bin", "ffmpeg", FILTERS_WITH_ASS)
    kit.save_config({"tools": {"ffmpeg": exe}})
    c = doctor.check_ffmpeg()
    assert c["status"] == "ok", c
    assert exe in c["detail"]


def test_ffmpeg_older_than_5_1_is_red(tmp_path):
    body = FILTERS_WITH_ASS.replace("ffmpeg version 7.1-fake", "ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright (c) 2000-2021")
    kit.save_config({"tools": {"ffmpeg": fake_exe(tmp_path / "bin", "ffmpeg", body)}})
    c = doctor.check_ffmpeg()
    assert c["status"] == "fail"
    assert "5.1" in c["detail"] and "4.4.2" in c["detail"]
    assert c["fix"]


def test_ffmpeg_git_master_build_counts_as_new(tmp_path):
    body = FILTERS_WITH_ASS.replace('print("ffmpeg version 7.1-fake")',
                                    'print("ffmpeg version N-121083-g1a2b3c4d5e-20261010 Copyright (c) 2000-2026")\n'
                                    '    print("libavutil      60. 13.100 / 60. 13.100")')
    kit.save_config({"tools": {"ffmpeg": fake_exe(tmp_path / "bin", "ffmpeg", body)}})
    assert doctor.check_ffmpeg()["status"] == "ok"


def test_missing_ffmpeg_says_not_found_not_traceback(tmp_path, monkeypatch):
    kit.save_config({"tools": {"ffmpeg": str(tmp_path / "gone" / "ffmpeg")}})
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    c = doctor.check_ffmpeg()
    assert c["status"] == "fail"
    assert "ไม่พบ" in c["detail"]
    assert c["fix"]


# ---------------------------------------------------------------- node
def test_node_20_is_red_asks_for_22(tmp_path):
    kit.save_config({"tools": {"node": fake_exe(tmp_path / "bin", "node", 'print("v20.11.1")\n')}})
    c = doctor.check_node()
    assert c["status"] == "fail"
    assert "ต้องการ Node 22" in c["detail"]
    assert "v20.11.1" in c["detail"]
    assert c["fix"]


def test_node_22_is_green(tmp_path):
    kit.save_config({"tools": {"node": fake_exe(tmp_path / "bin", "node", 'print("v22.12.0")\n')}})
    c = doctor.check_node()
    assert c["status"] == "ok"
    assert "v22.12.0" in c["detail"]


# ---------------------------------------------------------------- whisper model
def _fake_model(models: Path, repo="models--Systran--faster-whisper-base", files=("model.bin", "config.json", "tokenizer.json")):
    snap = models / repo / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    for f in files:
        (snap / f).write_bytes(b"x")
    return snap


@pytest.fixture
def fw(monkeypatch):
    """faster-whisper backend regardless of OS (macOS defaults to whisper.cpp)."""
    monkeypatch.setattr(inst, "asr_backend", lambda cfg=None: "faster-whisper")


def test_model_present_is_green(fw):
    kit.save_config({"whisper": {"model": "base"}})
    _fake_model(kit.models_dir())
    c = doctor.check_model()
    assert c["status"] == "ok", c


def test_medium_model_maps_to_its_repo(fw):
    kit.save_config({"whisper": {"model": "medium"}})
    _fake_model(kit.models_dir(), "models--Systran--faster-whisper-medium")
    assert doctor.check_model()["status"] == "ok"


def test_half_downloaded_model_is_red_with_model_in_fix(fw):
    kit.save_config({"whisper": {"model": "base"}})
    _fake_model(kit.models_dir(), files=("config.json",))
    c = doctor.check_model()
    assert c["status"] == "fail"
    assert "base" in c["detail"]
    assert "--model base" in c["fix"]


# ---------------------------------------------------------------- whisper.cpp (macOS backend)
@pytest.fixture
def wcpp(monkeypatch):
    monkeypatch.setattr(inst, "asr_backend", lambda cfg=None: "whispercpp")


def _ggml(model="base", size=None, magic=b"lmgg"):
    f = kit.models_dir() / f"ggml-{model}.bin"
    with open(f, "wb") as h:
        h.write(magic)
        h.truncate(size if size is not None else inst.GGML_BYTES[model])   # sparse: instant, no disk used
    return f


def test_backend_auto_is_whispercpp_on_mac_only(monkeypatch):
    monkeypatch.setattr(kit, "IS_MAC", True)
    assert inst.asr_backend({"whisper": {"backend": "auto"}}) == "whispercpp"
    assert inst.asr_backend({"whisper": {}}) == "whispercpp"
    assert inst.asr_backend({"whisper": {"backend": "faster-whisper"}}) == "faster-whisper"
    monkeypatch.setattr(kit, "IS_MAC", False)
    assert inst.asr_backend({"whisper": {"backend": "auto"}}) == "faster-whisper"


def test_whispercpp_model_complete_is_green(wcpp):
    kit.save_config({"whisper": {"model": "base"}})
    _ggml()
    c = doctor.check_model()
    assert c["status"] == "ok" and "whisper.cpp" in c["name"]


def test_whispercpp_model_cut_short_is_red(wcpp):
    kit.save_config({"whisper": {"model": "large-v3"}})
    _ggml("large-v3", size=1_000_000)
    c = doctor.check_model()
    assert c["status"] == "fail"
    assert "ไม่ครบ" in c["detail"] and "--model large-v3" in c["fix"]


def test_whispercpp_model_missing_is_red(wcpp):
    kit.save_config({"whisper": {"model": "medium"}})
    c = doctor.check_model()
    assert c["status"] == "fail" and "ggml-medium.bin" in c["detail"]


def test_asr_check_runs_whisper_cli(wcpp, tmp_path):
    kit.save_config({"tools": {"whisper-cli": fake_exe(tmp_path / "bin", "whisper-cli", 'sys.exit(0 if "--help" in args else 5)\n')}})
    c = doctor.check_asr()
    assert c["status"] == "ok" and "whisper.cpp" in c["detail"]


def test_asr_check_missing_whisper_cli_is_red_and_setup_installs_it(wcpp, tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_os", lambda: "mac")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    c = doctor.check_asr()
    assert c["status"] == "fail" and "whisper.cpp" in c["detail"]
    assert "setup.py" in c["fix"]          # setup brew-installs whisper-cpp when it is missing


def test_asr_check_faster_whisper_names_the_device(fw):
    kit.save_config({"whisper": {"device": "cpu"}})
    c = doctor.check_asr()
    assert c["status"] == "ok" and "faster-whisper" in c["detail"] and "cpu" in c["detail"]


# ---------------------------------------------------------------- chrome / edge
DUMP_DOM = """
if "--dump-dom" in args and any(a.startswith("--headless") for a in args):
    print("<html><head></head><body></body></html>")
else:
    sys.exit(3)
"""


def test_chrome_that_renders_is_green(tmp_path):
    kit.save_config({"tools": {"chrome": fake_exe(tmp_path / "bin", "chrome-headless-shell", DUMP_DOM)}})
    c = doctor.check_chrome()
    assert c["status"] == "ok", c


def test_edge_gets_headless_new(tmp_path):
    body = 'print("<html></html>") if "--headless=new" in args else sys.exit(4)\n'
    kit.save_config({"tools": {"chrome": fake_exe(tmp_path / "bin", "msedge", body)}})
    assert doctor.check_chrome()["status"] == "ok"


def test_chrome_that_hangs_is_red_after_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "CHROME_TIMEOUT", 1)
    kit.save_config({"tools": {"chrome": fake_exe(tmp_path / "bin", "chrome-headless-shell", "time.sleep(30)\n")}})
    c = doctor.check_chrome()
    assert c["status"] == "fail"
    assert "1 วิ" in c["detail"]
    assert c["fix"]


def test_no_browser_at_all_is_red(tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "chrome_candidates", lambda: [])
    c = doctor.check_chrome()
    assert c["status"] == "fail"
    assert c["fix"]


# ---------------------------------------------------------------- machine
def test_low_ram_warns_and_suggests_medium(monkeypatch):
    monkeypatch.setattr(inst, "ram_bytes", lambda: 4 * 1024 ** 3)
    c = doctor.check_ram()
    assert c["status"] == "warn"
    assert "--model medium" in c["fix"]
    assert "turbo" not in c["detail"] + c["fix"]


def test_low_disk_warns(monkeypatch):
    monkeypatch.setattr(inst, "free_bytes", lambda p: 5 * 1024 ** 3)
    c = doctor.check_disk()
    assert c["status"] == "warn"
    assert "15" in c["detail"]


def test_mac_without_command_line_tools_is_red_with_xcode_select(monkeypatch):
    monkeypatch.setattr(doctor, "_os", lambda: "mac")
    monkeypatch.setattr(inst, "swiftc", lambda: None)
    c = doctor.check_cutout()
    assert c["status"] == "fail"
    assert "Command Line Tools" in c["detail"]
    assert c["fix"] == "xcode-select --install"


def test_swiftc_ignores_the_usr_bin_shim_without_clt(monkeypatch):
    """/usr/bin/swiftc exists even with no Command Line Tools — only xcode-select/xcrun tell the truth."""
    calls = []

    def fake_out(cmd, timeout=60):
        calls.append([str(c) for c in cmd])
        return (2, "xcode-select: error: unable to get active developer directory") if cmd[0] == "xcode-select" else (0, "")

    monkeypatch.setattr(kit, "IS_MAC", True)
    monkeypatch.setattr(inst, "_out", fake_out)
    assert inst.swiftc() is None
    assert calls == [["xcode-select", "-p"]]          # never runs xcrun (that pops the install dialog)


def test_swiftc_found_through_xcrun(monkeypatch, tmp_path):
    sw = tmp_path / "swiftc"
    sw.write_text("x")
    monkeypatch.setattr(kit, "IS_MAC", True)
    monkeypatch.setattr(inst, "_out", lambda cmd, timeout=60: (0, "/Library/Developer/CommandLineTools\n")
                        if cmd[0] == "xcode-select" else (0, f"{sw}\n"))
    assert inst.swiftc() == str(sw)


def test_windows_without_cutout_addon_is_info_only(monkeypatch):
    monkeypatch.setattr(doctor, "_os", lambda: "windows")
    monkeypatch.setattr(doctor, "_venv_has", lambda mod: False)
    c = doctor.check_cutout()
    assert c["status"] == "info"
    assert "--with-cutout" in c["fix"]


# ---------------------------------------------------------------- CLI
def run_doctor(*args, env_home):
    env = {**os.environ, "TAD_CLIP_HOME": str(env_home), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(ROOT / "engine" / "doctor.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)


def test_json_schema_is_stable(tmp_home):
    res = run_doctor("--json", env_home=tmp_home)
    report = json.loads(res.stdout)
    assert set(report) == {"version", "ok", "mode", "os", "home", "kit", "checks"}
    assert report["version"] == 1 and report["mode"] == "full"
    assert [c["id"] for c in report["checks"]] == FULL_IDS
    for c in report["checks"]:
        assert set(c) == CHECK_KEYS
        assert c["status"] in {"ok", "fail", "warn", "info"}
        if c["status"] == "fail":
            assert c["fix"], c
    # a fresh home has no setup → not ok, exit 1
    assert report["ok"] is False and res.returncode == 1
    assert by_id(report["checks"])["setup"]["status"] == "fail"


def test_corrupt_config_is_a_red_row_not_a_traceback(tmp_home):
    tmp_home.mkdir(parents=True, exist_ok=True)
    (tmp_home / "config.json").write_text('{"tools": {"ffmpeg": ', encoding="utf-8")
    res = run_doctor("--json", env_home=tmp_home)
    report = json.loads(res.stdout)
    assert [c["id"] for c in report["checks"]] == FULL_IDS
    setup = by_id(report["checks"])["setup"]
    assert setup["status"] == "fail" and "อ่านไม่ได้" in setup["detail"]
    assert "Traceback" not in res.stderr


def test_quick_json_ids(tmp_home):
    report = json.loads(run_doctor("--quick", "--json", env_home=tmp_home).stdout)
    assert report["mode"] == "quick"
    assert [c["id"] for c in report["checks"]] == QUICK_IDS


def test_text_report_is_a_thai_table_with_fix_lines(tmp_home):
    out = run_doctor("--quick", env_home=tmp_home).stdout
    assert "| สถานะ | รายการ | ผล |" in out
    assert "❌" in out
    assert "วิธีแก้" in out


# ---------------------------------------------------------------- --qc
needs_ffmpeg = pytest.mark.skipif(not (REAL_FFMPEG and REAL_FFPROBE), reason="needs real ffmpeg/ffprobe")


def _make(path: Path, video: str, audio: str = None, dur=2):
    args = [REAL_FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"{video}:d={dur}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"{audio}:d={dur}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    args += ["-c:a", "aac", "-b:a", "128k", "-shortest"] if audio else ["-an"]
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(args + [str(path)], check=True)
    return path


@pytest.fixture
def qc_tools():
    kit.save_config({"tools": {"ffmpeg": REAL_FFMPEG, "ffprobe": REAL_FFPROBE}})


@needs_ffmpeg
def test_qc_good_video_passes(tmp_path, qc_tools):
    v = _make(tmp_path / "งาน ทดสอบ" / "คลิป ผ่าน.mp4", "testsrc2=s=1080x1920:r=30", "sine=f=440:sample_rate=48000")
    checks = doctor.qc(str(v), expect_duration=2.0)
    assert [c["id"] for c in checks] == QC_IDS
    assert all(c["status"] == "ok" for c in checks), checks


@needs_ffmpeg
def test_qc_catches_black_silent_wrong_size(tmp_path, qc_tools):
    v = _make(tmp_path / "คลิป ดำ.mp4", "color=c=black:s=720x1280:r=30")
    c = by_id(doctor.qc(str(v), expect_duration=5.0))
    assert c["duration"]["status"] == "fail"
    assert c["size"]["status"] == "fail" and "720" in c["size"]["detail"]
    assert c["audio"]["status"] == "fail"
    assert c["black"]["status"] == "fail"


@needs_ffmpeg
def test_qc_catches_hot_audio(tmp_path, qc_tools):
    v = _make(tmp_path / "ดัง.mp4", "testsrc2=s=1440x2560:r=30", "aevalsrc=0.999*sin(2*PI*997*t):s=48000")
    c = by_id(doctor.qc(str(v)))
    assert c["size"]["status"] == "ok"  # 2K preset size is accepted
    assert c["peak"]["status"] == "fail"
    assert "dBTP" in c["peak"]["detail"]
    assert "engine.sfx_mix" in c["peak"]["fix"] and "limiter" in c["peak"]["fix"]


@needs_ffmpeg
def test_qc_cli_json(tmp_path, qc_tools, tmp_home):
    v = _make(tmp_path / "คลิป ผ่าน.mp4", "testsrc2=s=1080x1920:r=30", "sine=f=440:sample_rate=48000")
    res = run_doctor("--qc", str(v), "--expect-duration", "2", "--json", env_home=tmp_home)
    report = json.loads(res.stdout)
    assert report["mode"] == "qc" and report["ok"] is True and res.returncode == 0
    assert [c["id"] for c in report["checks"]] == QC_IDS



# ================================================================ fix round 1
def test_mac_fix_lines_rerun_setup_instead_of_bare_brew(monkeypatch, tmp_path):
    """Homebrew's bin is often not on PATH right after installing it → never print a bare `brew`."""
    monkeypatch.setattr(doctor, "_os", lambda: "mac")
    monkeypatch.setattr(inst, "asr_backend", lambda cfg=None: "whispercpp")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    kit.save_config({"tools": {"ffmpeg": str(tmp_path / "gone")}})
    rows = [doctor.check_ffmpeg(), doctor.check_ffprobe(), doctor.check_node(), doctor.check_ytdlp(), doctor.check_asr()]
    for c in rows:
        assert c["status"] == "fail", c
        assert "setup.py" in c["fix"], c
        assert not c["fix"].startswith("brew ") and " brew install" not in c["fix"], c


def test_windows_ffmpeg_that_does_not_answer_mentions_antivirus(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "_os", lambda: "windows")
    kit.save_config({"tools": {"ffmpeg": fake_exe(tmp_path / "bin", "ffmpeg", "sys.exit(1)\n")}})
    c = doctor.check_ffmpeg()
    assert c["status"] == "fail" and "กันไวรัส" in c["detail"]


def test_python_row_on_whispercpp_mac_does_not_claim_faster_whisper(monkeypatch):
    monkeypatch.setattr(inst, "asr_backend", lambda cfg=None: "whispercpp")
    assert "faster-whisper" not in doctor.check_python(quick=True)["name"]
    monkeypatch.setattr(inst, "asr_backend", lambda cfg=None: "faster-whisper")
    assert "faster-whisper" in doctor.check_python(quick=True)["name"]


@needs_ffmpeg
def test_qc_takes_the_expected_duration_from_roughcut_mp4_next_to_it(tmp_path, qc_tools):
    w = tmp_path / "tad-clip-คลิป ทดสอบ"
    _make(w / "roughcut.mp4", "testsrc2=s=720x1280:r=30", "sine=f=440:sample_rate=48000", dur=2)
    final = _make(w / "final.mp4", "testsrc2=s=1080x1920:r=30", "sine=f=440:sample_rate=48000", dur=2)
    c = by_id(doctor.qc(str(final)))
    assert c["duration"]["status"] == "ok" and "roughcut.mp4" in c["duration"]["detail"]


@needs_ffmpeg
def test_qc_finds_roughcut_in_the_parent_work_folder_and_catches_a_short_render(tmp_path, qc_tools):
    w = tmp_path / "tad-clip-x"
    _make(w / "roughcut.mp4", "testsrc2=s=720x1280:r=30", "sine=f=440:sample_rate=48000", dur=3)
    short = _make(w / "out" / "preview-composite-subs.mp4", "testsrc2=s=1080x1920:r=30", "sine=f=440:sample_rate=48000", dur=2)
    c = by_id(doctor.qc(str(short)))
    assert c["duration"]["status"] == "fail" and "roughcut.mp4" in c["duration"]["detail"]


@needs_ffmpeg
def test_qc_without_roughcut_or_expectation_only_reports(tmp_path, qc_tools):
    v = _make(tmp_path / "เดี่ยว" / "clip.mp4", "testsrc2=s=1080x1920:r=30", "sine=f=440:sample_rate=48000", dur=2)
    assert by_id(doctor.qc(str(v)))["duration"]["status"] == "info"


# ---------------------------------------------------------------- --probe (orchestrator asks before anything else)
FIXTURE = ROOT / "tests" / "fixtures" / "คลิป ทดสอบ.mp4"
PROBE_KEYS = {"file", "duration", "width", "height", "fps_avg", "fps_variable", "codec", "hdr", "size_bytes",
              "free_video_bytes", "free_home_bytes", "need_bytes", "space_ok"}


@needs_ffmpeg
def test_probe_on_the_thai_fixture(qc_tools):
    p = doctor.probe(str(FIXTURE))
    assert set(p) == PROBE_KEYS
    assert abs(p["duration"] - 21.6) < 0.1
    assert (p["width"], p["height"]) == (720, 1280)
    assert round(p["fps_avg"]) == 60 and p["codec"] == "hevc" and p["hdr"] is False
    assert p["size_bytes"] == FIXTURE.stat().st_size
    assert p["need_bytes"] == 3 * p["size_bytes"] + 5 * 1024 ** 3


@needs_ffmpeg
def test_probe_cli_text_and_json(tmp_home, qc_tools):
    res = run_doctor("--probe", str(FIXTURE), env_home=tmp_home)
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert "21.6" in out and "720×1280" in out and "HEVC" in out and "HDR" in out and "60" in out
    js = json.loads(run_doctor("--probe", str(FIXTURE), "--json", env_home=tmp_home).stdout)
    assert js["mode"] == "probe" and set(js["probe"]) == PROBE_KEYS


@needs_ffmpeg
def test_probe_warns_when_disk_is_tight(qc_tools, monkeypatch, capsys):
    monkeypatch.setattr(inst, "free_bytes", lambda p: 2 * 1024 ** 3)
    p = doctor.probe(str(FIXTURE))
    assert p["space_ok"] is False
    print(doctor.render_probe(p))
    assert "⚠️" in capsys.readouterr().out


def test_probe_detects_hdr_and_variable_fps_from_stream_info():
    s = {"codec_name": "hevc", "width": 1080, "height": 1920, "avg_frame_rate": "30000/1001", "r_frame_rate": "60/1",
         "color_transfer": "arib-std-b67"}
    assert doctor.stream_traits(s) == {"fps_avg": pytest.approx(29.97, abs=0.01), "fps_variable": True, "hdr": True}
    s.update(color_transfer="bt709", r_frame_rate="30000/1001")
    assert doctor.stream_traits(s) == {"fps_avg": pytest.approx(29.97, abs=0.01), "fps_variable": False, "hdr": False}


def test_probe_missing_file_is_a_thai_error_not_a_traceback(tmp_home):
    res = run_doctor("--probe", str(tmp_home / "ไม่มี ไฟล์.mp4"), env_home=tmp_home)
    assert res.returncode == 1 and "ไม่พบไฟล์" in res.stdout and "Traceback" not in res.stderr



# ================================================================ fix round 2: Git Bash on Windows
@pytest.fixture
def win_paths(tmp_path, monkeypatch):
    """A fake Windows: Program Files, LOCALAPPDATA, SystemRoot and PATH all under tmp, nothing installed."""
    monkeypatch.setattr(doctor, "_os", lambda: "windows")
    root = tmp_path / "C drive"
    env = {"ProgramFiles": root / "Program Files", "ProgramW6432": root / "Program Files",
           "ProgramFiles(x86)": root / "Program Files (x86)", "LOCALAPPDATA": root / "Users" / "u" / "AppData" / "Local",
           "SystemRoot": root / "Windows"}
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.setenv("PATH", str(root / "empty"))
    monkeypatch.delenv("CLAUDE_CODE_GIT_BASH_PATH", raising=False)
    monkeypatch.setattr(inst, "GIT_BASH_DEFAULT", str(root / "Program Files" / "Git" / "bin" / "bash.exe"))
    return env


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")
    return path


def test_git_bash_in_program_files_is_green(win_paths):
    exe = _touch(win_paths["ProgramFiles"] / "Git" / "bin" / "bash.exe")
    c = doctor.check_gitbash()
    assert c["status"] == "ok" and str(exe) in c["detail"]


def test_git_bash_per_user_install_is_green(win_paths):
    _touch(win_paths["LOCALAPPDATA"] / "Programs" / "Git" / "bin" / "bash.exe")
    assert doctor.check_gitbash()["status"] == "ok"


def test_git_bash_on_path_is_green(win_paths, monkeypatch, tmp_path):
    d = _touch(tmp_path / "PortableGit" / "usr" / "bin" / "bash.exe").parent
    monkeypatch.setenv("PATH", str(d))
    assert doctor.check_gitbash()["status"] == "ok"


def test_claude_code_git_bash_path_env_is_honoured(win_paths, monkeypatch, tmp_path):
    exe = _touch(tmp_path / "custom git" / "bash.exe")
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", str(exe))
    assert str(exe) in doctor.check_gitbash()["detail"]


def test_wsl_bash_in_system32_is_not_git_bash(win_paths, monkeypatch):
    sys32 = _touch(win_paths["SystemRoot"] / "System32" / "bash.exe").parent
    apps = _touch(win_paths["LOCALAPPDATA"] / "Microsoft" / "WindowsApps" / "bash.exe").parent
    monkeypatch.setenv("PATH", os.pathsep.join([str(sys32), str(apps)]))
    assert doctor.check_gitbash()["status"] == "fail"


def test_no_git_bash_is_red_with_the_install_line(win_paths):
    c = doctor.check_gitbash()
    assert c["status"] == "fail"
    assert "Git for Windows" in c["detail"]
    assert "https://git-scm.com/download/win" in c["fix"] and "Claude" in c["fix"]


def test_git_bash_row_is_info_on_mac(monkeypatch):
    monkeypatch.setattr(doctor, "_os", lambda: "mac")
    c = doctor.check_gitbash()
    assert c["id"] == "gitbash" and c["status"] == "info"



# ================================================================ fix round 3
def test_ggml_with_the_right_size_but_wrong_header_is_red(wcpp):
    kit.save_config({"whisper": {"model": "base"}})
    _ggml(magic=b"<htm")
    c = doctor.check_model()
    assert c["status"] == "fail" and "ไม่ใช่ไฟล์โมเดล" in c["detail"]


def test_model_fix_line_drops_an_unsupported_model(fw):
    kit.save_config({"whisper": {"model": "large-v3-turbo"}})
    c = doctor.check_model()
    assert c["status"] == "fail" and "--model" not in c["fix"] and "setup.py" in c["fix"]
    assert "ไม่อยู่ในชุดที่รองรับ" in c["detail"]


@pytest.mark.parametrize("text", ["[]", '"just a string"', "[1, 2]", "42"])
def test_valid_json_that_is_not_an_object_is_a_red_row(tmp_home, text):
    tmp_home.mkdir(parents=True, exist_ok=True)
    (tmp_home / "config.json").write_text(text, encoding="utf-8")
    res = run_doctor("--json", env_home=tmp_home)
    assert "Traceback" not in res.stderr, res.stderr
    report = json.loads(res.stdout)
    setup = by_id(report["checks"])["setup"]
    assert setup["status"] == "fail" and "ไฟล์ตั้งค่าเสีย" in setup["detail"]


def test_display_size_follows_rotation():
    assert doctor.display_size({"width": 1920, "height": 1080}) == (1920, 1080)
    assert doctor.display_size({"width": 1920, "height": 1080, "side_data_list": [{"rotation": -90}]}) == (1080, 1920)
    assert doctor.display_size({"width": 1920, "height": 1080, "tags": {"rotate": "270"}}) == (1080, 1920)
    assert doctor.display_size({"width": 1920, "height": 1080, "side_data_list": [{"rotation": 180}]}) == (1920, 1080)


@needs_ffmpeg
def test_subs_only_output_keeps_the_source_size_without_a_plan(tmp_path, qc_tools):
    w = tmp_path / "tad-clip-แนวนอน"
    _make(w / "roughcut.mp4", "testsrc2=s=640x360:r=30", "sine=f=440:sample_rate=48000", dur=2)
    final = _make(w / "final.mp4", "testsrc2=s=640x360:r=30", "sine=f=440:sample_rate=48000", dur=2)
    c = by_id(doctor.qc(str(final)))
    assert c["size"]["status"] == "ok", c["size"]
    assert "640×360" in c["size"]["detail"] and "ต้นทาง" in c["size"]["detail"]


@needs_ffmpeg
def test_with_a_plan_the_preset_sizes_still_rule(tmp_path, qc_tools):
    w = tmp_path / "tad-clip-มีแผน"
    _make(w / "roughcut.mp4", "testsrc2=s=640x360:r=30", "sine=f=440:sample_rate=48000", dur=2)
    (w / "plan.json").write_text("{}", encoding="utf-8")
    final = _make(w / "final.mp4", "testsrc2=s=640x360:r=30", "sine=f=440:sample_rate=48000", dur=2)
    c = by_id(doctor.qc(str(final)))
    assert c["size"]["status"] == "fail" and "plan.json" in c["size"]["detail"]


@needs_ffmpeg
def test_qc_source_flag_gives_size_and_duration(tmp_path, qc_tools, tmp_home):
    src = _make(tmp_path / "คลิปที่ตัดเอง" / "my cut.mp4", "testsrc2=s=640x360:r=30", "sine=f=440:sample_rate=48000", dur=2)
    out = _make(tmp_path / "งานซับ" / "subbed.mp4", "testsrc2=s=640x360:r=30", "sine=f=440:sample_rate=48000", dur=2)
    c = by_id(doctor.qc(str(out), source=str(src)))
    assert c["size"]["status"] == "ok" and c["duration"]["status"] == "ok" and "my cut.mp4" in c["duration"]["detail"]
    report = json.loads(run_doctor("--qc", str(out), "--source", str(src), "--json", env_home=tmp_home).stdout)
    assert report["ok"] is True
