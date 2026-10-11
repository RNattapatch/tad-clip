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

FULL_IDS = ["setup", "ffmpeg", "ffprobe", "node", "python", "asr", "model", "chrome", "ytdlp", "cutout", "disk", "ram"]
QUICK_IDS = ["setup", "ffmpeg", "ffprobe", "node", "python", "asr", "model", "chrome"]
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


def test_turbo_model_maps_to_its_repo(fw):
    kit.save_config({"whisper": {"model": "large-v3-turbo"}})
    _fake_model(kit.models_dir(), "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo")
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


def _ggml(model="base", size=None):
    f = kit.models_dir() / f"ggml-{model}.bin"
    with open(f, "wb") as h:
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


def test_asr_check_missing_whisper_cli_is_red_with_brew_fix(wcpp, tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_os", lambda: "mac")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    c = doctor.check_asr()
    assert c["status"] == "fail"
    assert "brew install whisper-cpp" in c["fix"]


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
