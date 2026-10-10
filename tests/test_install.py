"""Installer logic: macOS (Homebrew) and Windows (direct downloads), tested with fakes so both run on any OS."""
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import kit  # noqa: E402
from engine import install as inst  # noqa: E402
from engine.install import mac, setup, windows  # noqa: E402


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "บ้าน tad clip"
    monkeypatch.setenv("TAD_CLIP_HOME", str(home))
    return home


# ---------------------------------------------------------------- Windows
NODE_INDEX = [
    {"version": "v24.1.0", "files": ["win-x64-zip", "win-x64-msi"]},
    {"version": "v23.11.0", "files": ["win-x64-zip"]},
    {"version": "v22.23.4", "files": ["win-x64-msi"]},          # no zip → skip
    {"version": "v22.23.3", "files": ["win-x64-zip", "osx-arm64-tar"]},
    {"version": "v22.23.2", "files": ["win-x64-zip"]},
    {"version": "v20.19.0", "files": ["win-x64-zip"]},
]


def test_pick_node_takes_newest_22_with_a_zip():
    v, url = windows.pick_node(NODE_INDEX)
    assert v == "v22.23.3"
    assert url == "https://nodejs.org/dist/v22.23.3/node-v22.23.3-win-x64.zip"


def test_pick_node_without_22_is_a_thai_error():
    with pytest.raises(kit.KitError, match="Node 22"):
        windows.pick_node([{"version": "v20.1.0", "files": ["win-x64-zip"]}])


def test_urls_are_the_documented_ones():
    assert windows.FFMPEG_URL == "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    assert windows.NODE_INDEX_URL == "https://nodejs.org/dist/index.json"
    assert windows.UV_URL == "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
    assert windows.YTDLP_URL == "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"


def test_torch_index_cpu_without_nvidia_cuda_with_it():
    assert windows.torch_index(None) == "https://download.pytorch.org/whl/cpu"
    assert windows.torch_index("NVIDIA GeForce RTX 3060").startswith("https://download.pytorch.org/whl/cu")


def _zip(path: Path, names):
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, f"content of {n}")
    return path


def test_extract_takes_only_wanted_files_by_name(tmp_path):
    z = _zip(tmp_path / "ffmpeg.zip", ["ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe",
                                       "ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe",
                                       "ffmpeg-master-latest-win64-gpl/bin/ffplay.exe",
                                       "ffmpeg-master-latest-win64-gpl/doc/ffmpeg.html"])
    out = tmp_path / "ที่ ลง"
    out.mkdir()
    windows.extract(z, {"ffmpeg.exe": out / "ffmpeg.exe", "ffprobe.exe": out / "ffprobe.exe"})
    assert sorted(p.name for p in out.iterdir()) == ["ffmpeg.exe", "ffprobe.exe"]
    assert (out / "ffmpeg.exe").read_text() == "content of ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe"


def test_extract_missing_member_is_a_thai_error(tmp_path):
    z = _zip(tmp_path / "x.zip", ["a/readme.txt"])
    with pytest.raises(kit.KitError, match="node.exe"):
        windows.extract(z, {"node.exe": tmp_path / "node.exe"})


def test_ensure_ffmpeg_downloads_extracts_and_cleans_zip(monkeypatch, tmp_path):
    calls = []

    def fake_download(url, dest, label=""):
        calls.append(url)
        return _zip(Path(dest), ["x/bin/ffmpeg.exe", "x/bin/ffprobe.exe"])

    monkeypatch.setattr(windows, "download", fake_download)
    monkeypatch.setattr(inst, "has_libass", lambda ff: True)
    ff, fp = windows.ensure_ffmpeg()
    assert calls == [windows.FFMPEG_URL]
    assert Path(ff) == kit.home() / "bin" / "ffmpeg.exe" and Path(fp).exists()
    assert not list((kit.home() / "bin").glob("*.zip"))
    # second run: already there → no download
    assert windows.ensure_ffmpeg() == (ff, fp) and len(calls) == 1


def test_ensure_ffmpeg_replaces_a_build_without_libass(monkeypatch):
    b = kit.home() / "bin"
    b.mkdir(parents=True)
    (b / "ffmpeg.exe").write_text("old")
    (b / "ffprobe.exe").write_text("old")
    calls = []
    monkeypatch.setattr(windows, "download", lambda url, dest, label="": calls.append(url) or _zip(Path(dest), ["ffmpeg.exe", "ffprobe.exe"]))
    monkeypatch.setattr(inst, "has_libass", lambda ff: Path(ff).read_text() != "old")
    windows.ensure_ffmpeg()
    assert calls == [windows.FFMPEG_URL]


def test_ensure_node_downloads_22_zip(monkeypatch):
    calls = []
    monkeypatch.setattr(windows, "_fetch_json", lambda url: NODE_INDEX)
    monkeypatch.setattr(windows, "download", lambda url, dest, label="": calls.append(url) or _zip(Path(dest), ["node-v22.23.3-win-x64/node.exe", "node-v22.23.3-win-x64/npm"]))
    node = windows.ensure_node()
    assert calls == ["https://nodejs.org/dist/v22.23.3/node-v22.23.3-win-x64.zip"]
    assert Path(node) == kit.home() / "bin" / "node.exe"


def test_ensure_ytdlp_and_uv_skip_when_present(monkeypatch):
    b = kit.home() / "bin"
    b.mkdir(parents=True)
    (b / "yt-dlp.exe").write_text("x")
    (b / "uv.exe").write_text("x")
    monkeypatch.setattr(windows, "download", lambda *a, **k: pytest.fail("must not download"))
    assert windows.ensure_ytdlp() == str(b / "yt-dlp.exe")
    assert windows.ensure_uv() == str(b / "uv.exe")


def test_download_retries_then_gives_thai_error(monkeypatch, tmp_path):
    import urllib.error
    tries = []

    def boom(*a, **k):
        tries.append(1)
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(windows.urllib.request, "urlopen", boom)
    monkeypatch.setattr(windows.time, "sleep", lambda s: None)
    with pytest.raises(kit.KitError, match="โหลด"):
        windows.download("https://example.invalid/x.zip", tmp_path / "x.zip", "ทดสอบ")
    assert len(tries) == 3
    assert not (tmp_path / "x.zip").exists() and not (tmp_path / "x.zip.part").exists()


def test_download_writes_file(monkeypatch, tmp_path):
    class Resp(io.BytesIO):
        headers = {"Content-Length": "6"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(windows.urllib.request, "urlopen", lambda *a, **k: Resp(b"abcdef"))
    out = windows.download("https://example.invalid/f.bin", tmp_path / "ไฟล์ ใหม่.bin", "f")
    assert out.read_bytes() == b"abcdef"


# ---------------------------------------------------------------- macOS
def test_no_homebrew_stops_with_the_official_one_liner(monkeypatch):
    monkeypatch.setattr(mac, "brew", lambda: None)
    with pytest.raises(kit.KitError) as e:
        mac.install_tools()
    msg = str(e.value)
    assert '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"' in msg
    assert "Terminal" in msg and "รหัสผ่าน" in msg


def test_mac_installs_only_what_is_missing(monkeypatch, tmp_path):
    prefix = tmp_path / "brew prefix"
    state = {"ffmpeg": None, "ffprobe": None, "node": "/x/node", "uv": None, "yt-dlp": "/x/yt-dlp"}
    installed = []
    monkeypatch.setattr(mac, "brew", lambda: "/fake/brew")
    monkeypatch.setattr(mac, "brew_prefix", lambda b: prefix)
    monkeypatch.setattr(mac, "find", lambda p: dict(state))

    def fake_install(b, formulas):
        installed.extend(formulas)
        state.update({"ffmpeg": "/p/ffmpeg", "ffprobe": "/p/ffprobe", "uv": "/p/uv"})

    monkeypatch.setattr(mac, "brew_install", fake_install)
    tools = mac.install_tools()
    assert installed == ["ffmpeg-full", "uv"]
    assert tools == {"ffmpeg": "/p/ffmpeg", "ffprobe": "/p/ffprobe", "node": "/x/node", "uv": "/p/uv", "yt-dlp": "/x/yt-dlp"}


def test_mac_skips_brew_when_everything_is_there(monkeypatch):
    full = {"ffmpeg": "/a", "ffprobe": "/b", "node": "/c", "uv": "/d", "yt-dlp": "/e"}
    monkeypatch.setattr(mac, "brew", lambda: "/fake/brew")
    monkeypatch.setattr(mac, "brew_prefix", lambda b: Path("/opt/homebrew"))
    monkeypatch.setattr(mac, "find", lambda p: dict(full))
    monkeypatch.setattr(mac, "brew_install", lambda b, f: pytest.fail("must not call brew install"))
    assert mac.install_tools() == full


def test_mac_find_uses_keg_only_ffmpeg_full(tmp_path, monkeypatch):
    prefix = tmp_path / "homebrew"
    keg = prefix / "opt" / "ffmpeg-full" / "bin"
    keg.mkdir(parents=True)
    for n in ("ffmpeg", "ffprobe"):
        (keg / n).write_text("x")
    monkeypatch.setattr(inst, "which", lambda name, extra_dirs=(): None)
    found = mac.find(prefix)
    assert found["ffmpeg"] == str(keg / "ffmpeg") and found["ffprobe"] == str(keg / "ffprobe")
    assert found["node"] is None and mac.missing(found) == ["node", "uv", "yt-dlp"]


def test_mac_old_node_on_path_counts_as_missing(tmp_path, monkeypatch):
    old = tmp_path / "nvm" / "node"
    old.parent.mkdir()
    old.write_text("x")
    monkeypatch.setattr(inst, "which", lambda name, extra_dirs=(): str(old) if name == "node" else None)
    monkeypatch.setattr(inst, "node_major", lambda p: 20)
    found = mac.find(tmp_path)
    assert found["node"] is None and "node" in mac.missing(found)
    monkeypatch.setattr(inst, "node_major", lambda p: 22)
    assert mac.find(tmp_path)["node"] == str(old)


# ---------------------------------------------------------------- setup.py
def test_config_update_keeps_preset_switchable():
    kit.save_config({"speaker": {"name": "ทดสอบ"}, "brand": {"preset": "paper"}})
    setup.update_config({"ffmpeg": "/x/ffmpeg"}, "base")
    raw = json.loads((kit.home() / "config.json").read_text(encoding="utf-8"))
    assert raw["tools"] == {"ffmpeg": "/x/ffmpeg"} and raw["whisper"]["model"] == "base"
    assert raw["speaker"]["name"] == "ทดสอบ"
    assert "colors" not in raw["brand"]  # merged preset colours must never be frozen into the file
    raw["brand"]["preset"] = "neutral"
    kit.save_config(raw)
    assert kit.load_config()["brand"]["colors"]["accent"] == "#f97316"


def test_default_model_respects_config_then_ram(monkeypatch):
    monkeypatch.setattr(inst, "ram_bytes", lambda: 16 * 1024 ** 3)
    assert setup.choose_model(None) == "large-v3"
    monkeypatch.setattr(inst, "ram_bytes", lambda: 4 * 1024 ** 3)
    assert setup.choose_model(None) == "large-v3-turbo"
    kit.save_config({"whisper": {"model": "medium"}})
    assert setup.choose_model(None) == "medium"
    assert setup.choose_model("base") == "base"


def test_plan_lists_sizes_and_total(monkeypatch):
    lines, mb = setup.plan("windows", "large-v3", with_cutout=True, gpu=None, have={})
    text = "\n".join(lines)
    assert "ffmpeg" in text and "Node.js" in text and "large-v3" in text and "transparent-background" in text
    assert mb > 3000


@pytest.mark.skipif(kit.IS_MAC and not mac.brew(), reason="the plan needs Homebrew on macOS")
def test_setup_without_yes_and_no_tty_shows_plan_and_exits_2(tmp_home):
    import os
    env = {**os.environ, "TAD_CLIP_HOME": str(tmp_home)}
    res = subprocess.run([sys.executable, str(ROOT / "engine" / "install" / "setup.py"), "--model", "base"],
                         capture_output=True, text=True, encoding="utf-8", env=env, stdin=subprocess.DEVNULL, timeout=120)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "--yes" in res.stdout and "base" in res.stdout
    assert not (tmp_home / "venv").exists()  # nothing was installed
