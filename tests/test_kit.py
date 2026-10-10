import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import kit  # noqa: E402


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TAD_CLIP_HOME", str(tmp_path / "ที่เก็บ tad clip"))
    return tmp_path


def test_home_honours_env(tmp_path):
    assert kit.home() == tmp_path / "ที่เก็บ tad clip"
    assert kit.home().is_dir()


def test_defaults_when_no_config():
    cfg = kit.load_config()
    assert cfg["whisper"]["model"] == "large-v3"
    assert cfg["brand"]["preset"] == "neutral"
    assert cfg["brand"]["colors"]["accent"] == "#f97316"


def test_preset_then_user_colours_win():
    kit.save_config({"brand": {"preset": "pun-navy-coral", "colors": {"accent": "#123456"}}})
    cfg = kit.load_config()
    assert cfg["brand"]["colors"]["primary"] == "#072b4e"
    assert cfg["brand"]["colors"]["accent"] == "#123456"
    assert cfg["subtitle"]["max_chars"] == 22  # untouched defaults survive the merge


def test_unknown_preset_falls_back_to_neutral():
    kit.save_config({"brand": {"preset": "nope"}})
    assert kit.load_config()["brand"]["colors"]["accent"] == "#f97316"


def test_tool_prefers_configured_path(tmp_path):
    fake = tmp_path / "my ffmpeg"
    fake.write_text("x")
    kit.save_config({"tools": {"ffmpeg": str(fake)}})
    assert kit.tool("ffmpeg") == str(fake)


def test_tool_ignores_configured_path_that_vanished(tmp_path, monkeypatch):
    kit.save_config({"tools": {"ffmpeg": str(tmp_path / "gone")}})
    monkeypatch.setenv("PATH", "")
    with pytest.raises(kit.KitError) as e:
        kit.tool("ffmpeg")
    assert "ไม่พบโปรแกรม ffmpeg" in str(e.value)


def test_tool_finds_kit_bin(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    b = kit.home() / "bin"
    b.mkdir()
    exe = b / ("yt-dlp.exe" if kit.IS_WINDOWS else "yt-dlp")
    exe.write_text("x")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    assert kit.tool("yt-dlp") == str(exe)


def test_run_raises_thai_error_with_stderr_tail():
    with pytest.raises(kit.KitError) as e:
        kit.run([sys.executable, "-c", "import sys; sys.stderr.write('boom ผิดพลาด'); sys.exit(3)"])
    assert "exit 3" in str(e.value) and "boom ผิดพลาด" in str(e.value)
