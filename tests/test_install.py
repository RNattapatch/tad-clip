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
    state = {"ffmpeg": None, "ffprobe": None, "node": "/x/node", "uv": None, "yt-dlp": "/x/yt-dlp", "whisper-cli": "/x/whisper-cli"}
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
    assert tools == {"ffmpeg": "/p/ffmpeg", "ffprobe": "/p/ffprobe", "node": "/x/node", "uv": "/p/uv", "yt-dlp": "/x/yt-dlp",
                     "whisper-cli": "/x/whisper-cli"}


def test_mac_skips_brew_when_everything_is_there(monkeypatch):
    full = {"ffmpeg": "/a", "ffprobe": "/b", "node": "/c", "uv": "/d", "yt-dlp": "/e", "whisper-cli": "/f"}
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
    assert found["node"] is None and mac.missing(found) == ["node", "uv", "yt-dlp", "whisper-cpp"]


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
    assert setup.choose_model(None) == "medium"
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


# ---------------------------------------------------------------- browser choice
def _browser_env(monkeypatch, cands, works, mac, download="/home/chrome/155/chrome-headless-shell-mac-arm64/chrome-headless-shell"):
    tried, downloads = [], []
    monkeypatch.setattr(kit, "IS_MAC", mac)
    monkeypatch.setattr(inst, "chrome_candidates", lambda: list(cands))
    monkeypatch.setattr(inst, "chrome_ready", lambda b, t=20: (tried.append(b) or (b in works), "เปิดแล้วไม่ตอบภายใน 20 วิ"))
    monkeypatch.setattr(setup, "_download_shell", lambda node: downloads.append(node) or download)
    return tried, downloads


GOOGLE = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def test_mac_prefers_downloading_the_headless_shell_over_google_chrome(monkeypatch):
    shell = "/home/chrome/155/chrome-headless-shell-mac-arm64/chrome-headless-shell"
    tried, downloads = _browser_env(monkeypatch, [GOOGLE], {GOOGLE, shell}, mac=True)
    assert setup.ensure_chrome("node") == shell
    assert downloads == ["node"] and GOOGLE not in tried


def test_mac_falls_back_to_google_chrome_when_download_fails(monkeypatch):
    tried, downloads = _browser_env(monkeypatch, [GOOGLE], {GOOGLE}, mac=True, download=None)
    assert setup.ensure_chrome("node") == GOOGLE


def test_windows_uses_edge_without_downloading(monkeypatch):
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    tried, downloads = _browser_env(monkeypatch, [edge], {edge}, mac=False)
    assert setup.ensure_chrome("node") == edge and downloads == []


def test_no_working_browser_is_a_thai_error(monkeypatch):
    _browser_env(monkeypatch, [GOOGLE], set(), mac=False, download=None)
    with pytest.raises(kit.KitError, match="เบราว์เซอร์"):
        setup.ensure_chrome("node")


# ---------------------------------------------------------------- whisper on an NVIDIA card (Windows)
def test_cuda_libs_installed_and_verified(monkeypatch, tmp_path):
    site = tmp_path / "site packages"
    site.mkdir()
    pips, runs = [], []
    monkeypatch.setattr(setup, "pip", lambda uv, py, args, what: pips.append(list(map(str, args))))
    monkeypatch.setattr(setup, "_site_packages", lambda py: site)
    monkeypatch.setattr(inst, "_out", lambda cmd, timeout=60: runs.append(cmd) or (0, "cuda ok"))
    assert setup.setup_cuda("uv", Path("py"), "base") == "cuda"
    assert pips and "nvidia-cublas-cu12" in pips[0] and any(a.startswith("nvidia-cudnn-cu12") for a in pips[0])
    pth = (site / "tadclip-cuda.pth").read_text(encoding="utf-8")
    assert pth.startswith("import ") and "add_dll_directory" in pth and "\n" not in pth.strip()
    assert any("cuda" in " ".join(map(str, c)) for c in runs)


def test_cuda_that_fails_falls_back_to_cpu(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "pip", lambda *a, **k: None)
    monkeypatch.setattr(setup, "_site_packages", lambda py: tmp_path)
    monkeypatch.setattr(inst, "_out", lambda cmd, timeout=60: (1, "Library cublas64_12.dll is not found"))
    assert setup.setup_cuda("uv", Path("py"), "base") == "cpu"


def test_plan_mentions_cuda_libs_on_windows_with_nvidia():
    lines, mb = setup.plan("windows", "large-v3", with_cutout=False, gpu="NVIDIA GeForce RTX 3060", have={})
    assert any("CUDA" in line for line in lines)
    lines2, mb2 = setup.plan("windows", "large-v3", with_cutout=False, gpu=None, have={})
    assert not any("CUDA" in line for line in lines2) and mb > mb2


# ---------------------------------------------------------------- Windows-only edges seen on CI
def test_windows_fix_command_survives_a_broken_config(monkeypatch):
    kit.home().mkdir(parents=True, exist_ok=True)
    (kit.home() / "config.json").write_text('{"tools": {', encoding="utf-8")
    monkeypatch.setattr(kit, "IS_WINDOWS", True)
    cmd = inst.setup_command("--model base")
    assert "uv.exe" in cmd and cmd.endswith("--yes --model base")


def test_no_yes_on_a_fake_tty_that_hits_eof_exits_2(monkeypatch, capsys):
    class NulStdin:          # Windows: NUL claims to be a tty, then input() raises EOFError
        def isatty(self):
            return True

        def readline(self):
            return ""

    monkeypatch.setattr(inst, "os_name", lambda: "windows")
    monkeypatch.setattr(inst, "nvidia_gpu", lambda: None)
    monkeypatch.setattr(windows, "have", lambda: {})
    monkeypatch.setattr(sys, "stdin", NulStdin())
    assert setup.main(["--model", "base"]) == 2
    assert "--yes" in capsys.readouterr().out


# ---------------------------------------------------------------- whisper.cpp model download (macOS)
class FakeHTTP:
    """Serves `data`, honouring a Range header like Hugging Face's CDN (206 + Content-Range)."""

    def __init__(self, data: bytes, fail_after: int = None, ignore_range=False):
        self.data, self.fail_after, self.ignore_range, self.calls = data, fail_after, ignore_range, []

    def __call__(self, req, timeout=None):
        import urllib.error
        rng = req.get_header("Range")
        start = int(rng.split("=")[1].rstrip("-")) if rng and not self.ignore_range else 0
        self.calls.append(start)
        if rng and not self.ignore_range and start >= len(self.data):
            raise urllib.error.HTTPError(req.full_url, 416, "Range Not Satisfiable", {}, None)
        partial = bool(rng) and not self.ignore_range
        headers = {"Content-Length": str(len(self.data) - start)}
        if partial:
            headers["Content-Range"] = f"bytes {start}-{len(self.data) - 1}/{len(self.data)}"
        return _Resp(self.data[start:], 206 if partial else 200, headers, self._take_fail())

    def _take_fail(self):
        f, self.fail_after = self.fail_after, None   # only the first response breaks
        return f


class _Resp(io.BytesIO):
    def __init__(self, body, status, headers, fail_after):
        super().__init__(body)
        self.status, self.headers, self.fail_after = status, headers, fail_after

    def read(self, n=-1):
        if self.fail_after is not None:
            if self.tell() >= self.fail_after:
                raise OSError("connection reset")
            n = min(n if n and n > 0 else self.fail_after, self.fail_after - self.tell())
        return super().read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_resumable_download_resumes_after_a_drop(monkeypatch, tmp_path):
    data = bytes(range(256)) * 4000            # ~1 MB
    http = FakeHTTP(data, fail_after=300_000)
    monkeypatch.setattr(inst.urllib.request, "urlopen", http)
    monkeypatch.setattr(inst.time, "sleep", lambda s: None)
    out = inst.download_resumable("https://x/ggml-base.bin", tmp_path / "โมเดล ggml-base.bin", "base", expected=len(data))
    assert out.read_bytes() == data
    assert http.calls[0] == 0 and http.calls[1] == 300_000      # second request asked for the rest only
    assert not (tmp_path / "โมเดล ggml-base.bin.part").exists()


def test_resumable_download_continues_an_old_part_file(monkeypatch, tmp_path):
    data = b"x" * 5000 + b"y" * 5000
    dest = tmp_path / "ggml-small.bin"
    (tmp_path / "ggml-small.bin.part").write_bytes(data[:5000])
    http = FakeHTTP(data)
    monkeypatch.setattr(inst.urllib.request, "urlopen", http)
    inst.download_resumable("https://x/ggml-small.bin", dest, "small", expected=len(data))
    assert http.calls == [5000] and dest.read_bytes() == data


def test_resumable_download_restarts_when_server_ignores_range(monkeypatch, tmp_path):
    data = b"abc" * 1000
    (tmp_path / "f.bin.part").write_bytes(b"zzz")
    monkeypatch.setattr(inst.urllib.request, "urlopen", FakeHTTP(data, ignore_range=True))
    assert inst.download_resumable("https://x/f.bin", tmp_path / "f.bin", "f").read_bytes() == data


def test_resumable_download_rejects_wrong_size(monkeypatch, tmp_path):
    monkeypatch.setattr(inst.urllib.request, "urlopen", FakeHTTP(b"short"))
    monkeypatch.setattr(inst.time, "sleep", lambda s: None)
    with pytest.raises(kit.KitError, match="ขนาดไม่ตรง"):
        inst.download_resumable("https://x/f.bin", tmp_path / "f.bin", "f", expected=999)
    assert not (tmp_path / "f.bin").exists()


def test_setup_downloads_ggml_for_whispercpp(monkeypatch):
    got = []
    monkeypatch.setattr(inst, "asr_backend", lambda cfg=None: "whispercpp")
    def fake(url, dest, label, expected=None):
        got.append((url, Path(dest).name, expected))
        with open(dest, "wb") as h:
            h.truncate(expected)

    monkeypatch.setattr(inst, "download_resumable", fake)
    monkeypatch.setattr(setup, "_call", lambda *a, **k: pytest.fail("must not fetch the faster-whisper model on the whisper.cpp path"))
    setup.download_model(Path("py"), "large-v3")
    assert got == [("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin", "ggml-large-v3.bin", 3095033483)]


def test_setup_skips_complete_ggml(monkeypatch):
    monkeypatch.setattr(inst, "asr_backend", lambda cfg=None: "whispercpp")
    f = kit.models_dir() / "ggml-base.bin"
    with open(f, "wb") as h:
        h.truncate(inst.GGML_BYTES["base"])
    monkeypatch.setattr(inst, "download_resumable", lambda *a, **k: pytest.fail("already complete"))
    setup.download_model(Path("py"), "base")


def test_mac_find_reports_whisper_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "which", lambda name, extra_dirs=(): "/opt/homebrew/bin/whisper-cli" if name == "whisper-cli" else None)
    found = mac.find(tmp_path)
    assert found["whisper-cli"] == "/opt/homebrew/bin/whisper-cli"
    assert "whisper-cpp" not in mac.missing(found)
    monkeypatch.setattr(inst, "which", lambda name, extra_dirs=(): None)
    assert "whisper-cpp" in mac.missing(mac.find(tmp_path))


def test_mac_plan_lists_whispercpp_and_ggml_size(monkeypatch):
    monkeypatch.setattr(inst, "asr_backend", lambda cfg=None: "whispercpp")
    lines, mb = setup.plan("mac", "large-v3", with_cutout=False, gpu=None, have={})
    text = "\n".join(lines)
    assert "whisper.cpp" in text and "ggml-large-v3" in text and "3.1 GB" in text
