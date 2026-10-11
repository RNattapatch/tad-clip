"""engine/env.sh — the preamble every SKILL bash block starts with, run in bash exactly as the SKILLs write it.

On Windows (Git Bash) python prints CRLF lines and CLAUDE_PLUGIN_ROOT can be a backslash path; both used to leak into
$FF / $NODE ("ffmpeg.exe\\r: command not found"). The real Windows run is a CI step ("Skill preamble from Git Bash");
these tests drive the same code on macOS/Linux with a fake python and OSTYPE=msys (bash keeps an OSTYPE it inherits).
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PREAMBLE = 'K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"'
BASH = shutil.which("bash")

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="needs bash"),
    pytest.mark.skipif(sys.platform == "win32", reason="Windows runs the real preamble in the CI step instead"),
]


def fake_home(tmp_path, *lines):
    """A kit home whose venv python ignores its arguments and prints `lines` with Windows (CRLF) line ends."""
    home = tmp_path / "บ้าน kit"
    py = home / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    body = "".join(line.replace("\\", "\\\\") + "\\r\\n" for line in lines)
    py.write_text(f"#!/bin/sh\nprintf '{body}'\n", encoding="utf-8")
    py.chmod(0o755)
    return home


def preamble(plugin_root, home, ostype=None):
    env = {"PATH": os.environ["PATH"], "HOME": str(home.parent), "TAD_CLIP_HOME": str(home),
           "CLAUDE_PLUGIN_ROOT": str(plugin_root), "LANG": "en_US.UTF-8"}
    if ostype:
        env["OSTYPE"] = ostype
    script = PREAMBLE + ("\nprintf '%s\\n' \"KIT=$KIT\" \"PY=$PY\" \"FF=$FF\" \"NODE=$NODE\" \"PWD=$PWD\""
                         " \"UTF8=${PYTHONUTF8-unset}\"\n")
    res = subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", script], cwd="/", env=env,
                         capture_output=True)
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")
    out = res.stdout.decode("utf-8")
    assert "\r" not in out, repr(out)
    return dict(line.split("=", 1) for line in out.splitlines())


def test_the_skill_files_use_exactly_this_preamble():
    for skill in (ROOT / "skills").glob("*/SKILL.md"):
        assert PREAMBLE in skill.read_text(encoding="utf-8"), skill


def test_crlf_from_python_never_reaches_the_tool_paths(tmp_path):
    home = fake_home(tmp_path, "/opt/kit tools/ffmpeg", "/opt/kit tools/node")
    got = preamble(ROOT, home)
    assert got["FF"] == "/opt/kit tools/ffmpeg" and got["NODE"] == "/opt/kit tools/node"
    assert got["KIT"] == str(ROOT) and got["PWD"] == str(ROOT)            # and it cd's into the kit
    assert got["PY"] == f"{home}/venv/bin/python"


def test_python_runs_with_utf8_mode_so_a_non_ascii_windows_home_prints_correctly(tmp_path):
    """Windows python prints paths in the ANSI code page unless PYTHONUTF8=1 (a Thai user name came out as ?????).
    The fake python echoes the PYTHONUTF8 it was started with in place of the tool paths."""
    home = tmp_path / "บ้าน kit"
    py = home / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\nprintf '/x/ffmpeg-%s\\r\\n/x/node-%s\\r\\n' \"$PYTHONUTF8\" \"$PYTHONUTF8\"\n", encoding="utf-8")
    py.chmod(0o755)
    for ostype in (None, "msys"):
        got = preamble(ROOT, home, ostype=ostype)
        assert got["FF"] == "/x/ffmpeg-1" and got["NODE"] == "/x/node-1", got
        assert got["UTF8"] == "unset"                                      # only that one python call, not the user's shell


def test_git_bash_gets_forward_slash_windows_paths(tmp_path):
    home = fake_home(tmp_path, r"C:\Users\ทดสอบ\.tad-clip\bin\ffmpeg.exe", r"C:\Program Files\nodejs\node.exe")
    got = preamble(ROOT, home, ostype="msys")
    assert got["FF"] == "C:/Users/ทดสอบ/.tad-clip/bin/ffmpeg.exe"
    assert got["NODE"] == "C:/Program Files/nodejs/node.exe"


def test_a_backslash_plugin_root_finds_the_kit_in_git_bash(tmp_path):
    r"""CLAUDE_PLUGIN_ROOT = C:\Users\…\tad-clip in Git Bash. Simulated: a folder literally named `a\b\kit` holding
    engine/env.sh (what `[ -f "$K/engine/env.sh" ]` sees) and the real kit at a/b/kit (where C:/… really points)."""
    literal = tmp_path / "a\\b\\kit"
    (literal / "engine").mkdir(parents=True)
    shutil.copy(ROOT / "engine" / "env.sh", literal / "engine" / "env.sh")
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "kit").symlink_to(ROOT, target_is_directory=True)
    home = fake_home(tmp_path, "/x/ffmpeg", "/x/node")
    got = preamble(literal, home, ostype="msys")
    assert got["KIT"] == f"{tmp_path}/a/b/kit"                             # backslashes read as folder separators
    assert got["FF"] == "/x/ffmpeg"
    # macOS / Linux: a backslash is an ordinary file-name character there, so nothing is rewritten
    assert preamble(literal, home)["KIT"] == str(literal)


def test_no_python_yet_falls_back_to_bare_names_with_a_thai_hint(tmp_path):
    home = tmp_path / "ว่าง"
    home.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "TAD_CLIP_HOME": str(home), "CLAUDE_PLUGIN_ROOT": str(ROOT)}
    res = subprocess.run([BASH, "--noprofile", "--norc", "-c", PREAMBLE + '\nprintf "%s|%s" "$FF" "$NODE"'], cwd="/",
                         env=env, capture_output=True)
    assert res.stdout.decode() == "ffmpeg|node"
    assert re.search("ยังไม่ได้ติดตั้ง", res.stderr.decode("utf-8"))
