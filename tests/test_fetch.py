"""fetch: Commons parser (canned JSON, no network), credit table, gif2clip, video/image flow with the network stubbed.
Live calls are marked `network` (CI runs -m "not network")."""
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import fetch, kit  # noqa: E402

FFMPEG = kit.tool("ffmpeg")
FFPROBE = kit.tool("ffprobe")


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"tools": {"ffmpeg": FFMPEG, "ffprobe": FFPROBE}}), encoding="utf-8")
    monkeypatch.setenv("TAD_CLIP_HOME", str(home))
    return home


def ff(*args):
    subprocess.run([FFMPEG, "-v", "error", "-y", *map(str, args)], check=True)


def probe(path, entries):
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", entries, "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def page(index, title, license_name, artist, url_name, mime="image/jpeg", restrictions="", size=1000, with_meta=True):
    info = {
        "size": size, "width": 3072, "height": 2048, "mime": mime,
        "url": f"https://upload.wikimedia.org/wikipedia/commons/f/f6/{url_name}?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
        "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{url_name}",
    }
    if with_meta:
        info["extmetadata"] = {
            "LicenseShortName": {"value": license_name}, "Artist": {"value": artist}, "Restrictions": {"value": restrictions},
        }
    return {"pageid": index, "ns": 6, "title": f"File:{title}", "index": index, "imageinfo": [info]}


CANNED = {"batchcomplete": True, "query": {"pages": [
    page(3, "Third.jpg", "CC BY-SA 3.0", "Someone", "Third.jpg"),
    page(1, "First sunset.jpg", "CC BY 4.0", '<a href="//commons.wikimedia.org/wiki/User:Diliff" title="User:Diliff">User:Diliff</a>  &amp; <b>friends</b>', "First_sunset.jpg"),
    page(2, "Second.png", "CC0", "<span>Unknown</span>", "Second.png", mime="image/png", size=2048),
    page(4, "Logo.png", "Public domain", "Gov", "Logo.png", mime="image/png", restrictions="trademarked"),
    page(5, "No meta.jpg", "", "", "No_meta.jpg", with_meta=False),
]}}


def test_parse_commons_orders_by_rank_and_shapes_fields():
    rows = fetch.parse_commons(CANNED)
    assert [r["title"] for r in rows] == ["First sunset.jpg", "Second.png", "Third.jpg", "Logo.png", "No meta.jpg"]
    first = rows[0]
    assert set(["title", "file_url", "page_url", "license", "author"]) <= set(first)
    assert first["license"] == "CC BY 4.0"
    assert first["author"] == "User:Diliff & friends"            # tags stripped, entities decoded, spaces collapsed
    assert first["file_url"] == "https://upload.wikimedia.org/wikipedia/commons/f/f6/First_sunset.jpg"  # utm query dropped
    assert first["page_url"] == "https://commons.wikimedia.org/wiki/File:First_sunset.jpg"
    assert first["mime"] == "image/jpeg" and first["bytes"] == 1000


def test_commons_ads_ok_is_conservative():
    by = {r["title"]: r["ads_ok"] for r in fetch.parse_commons(CANNED)}
    assert by["First sunset.jpg"] is True      # CC BY
    assert by["Second.png"] is True            # CC0
    assert by["Third.jpg"] is False            # CC BY-SA: share-alike -> ask first
    assert by["Logo.png"] is False             # any listed restriction (trademark, personality...) -> no
    assert by["No meta.jpg"] is False          # unknown licence -> no
    assert fetch.parse_commons(CANNED)[4]["license"] == "ไม่ระบุ"


def test_parse_commons_empty_result():
    assert fetch.parse_commons({"batchcomplete": True}) == []


def test_commons_query_builds_request_without_key(monkeypatch):
    seen = {}

    def fake_get_json(url):
        seen["url"] = url
        return CANNED

    monkeypatch.setattr(fetch, "_http_get_json", fake_get_json)
    rows = fetch.commons("bangkok street", limit=2, kind="image")
    assert len(rows) == 2                                   # limit applied after ranking
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(seen["url"]).query)
    assert q["gsrsearch"] == ["filetype:bitmap bangkok street"] and q["gsrlimit"] == ["2"] and q["gsrnamespace"] == ["6"]
    assert q["formatversion"] == ["2"] and not {"key", "apikey", "api_key", "token"} & set(q)   # no API key anywhere


def test_commons_cli_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(fetch, "_http_get_json", lambda url: CANNED)
    assert fetch.main(["commons", "ท้องฟ้า กรุงเทพ", "--limit", "3"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 3 and rows[0]["title"] == "First sunset.jpg"


def test_commons_network_error_is_thai(monkeypatch, capsys):
    def boom(url):
        raise fetch.KitError("ต่อ Wikimedia Commons ไม่ได้")
    monkeypatch.setattr(fetch, "_http_get_json", boom)
    assert fetch.main(["commons", "x"]) == 1
    assert "ต่อ Wikimedia Commons ไม่ได้" in capsys.readouterr().err


# ---------- credit ----------

def test_credit_creates_header_once_and_appends_rows(tmp_path):
    f = tmp_path / "งาน ทดสอบ" / "CREDITS.md"
    fetch.credit(f, "ภาพสนามบิน", "https://example.com/a", "CC BY 4.0", True, "ผู้ถ่าย: A")
    fetch.credit(f, "คลิปทีวี", "https://example.com/b", "ลิขสิทธิ์เจ้าของ", False)
    fetch.credit(f, "ภาพของเรา", "-", "ถ่ายเอง", True)
    text = f.read_text(encoding="utf-8")
    assert text.count("| ชิ้น | ที่มา | ไลเซนส์ | ใช้ยิงแอดได้ | หมายเหตุ |") == 1
    rows = [ln for ln in text.splitlines() if ln.startswith("| ") and "ชิ้น" not in ln.split("|")[1]]
    assert len(rows) == 3
    assert rows[0] == "| ภาพสนามบิน | https://example.com/a | CC BY 4.0 | ใช่ | ผู้ถ่าย: A |"
    assert rows[1].split("|")[4].strip() == "ไม่"
    assert text.startswith("# CREDITS")


def test_credit_escapes_pipes_and_newlines_and_updates_same_item(tmp_path):
    f = tmp_path / "CREDITS.md"
    fetch.credit(f, "a | b", "https://x.test/1", "CC0", True, "line1\nline2")
    fetch.credit(f, "a | b", "https://x.test/1", "CC0", False, "แก้เป็นไม่")      # same item+url -> row replaced, not duplicated
    lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.startswith("| a")]
    assert lines == ["| a \\| b | https://x.test/1 | CC0 | ไม่ | แก้เป็นไม่ |"]


def test_credit_appends_header_to_a_file_that_has_none(tmp_path):
    f = tmp_path / "CREDITS.md"
    f.write_text("# my notes\n\nsome text\n", encoding="utf-8")
    fetch.credit(f, "x", "u", "L", True)
    text = f.read_text(encoding="utf-8")
    assert text.startswith("# my notes") and text.count("| ชิ้น |") == 1 and text.rstrip().endswith("| x | u | L | ใช่ |  |")


def test_credit_cli_and_summary(tmp_path, capsys):
    f = tmp_path / "CREDITS.md"
    assert fetch.main(["credit", "--file", str(f), "--item", "ภาพ 1", "--url", "https://e.test/1", "--license", "CC0", "--ads", "yes"]) == 0
    assert fetch.main(["credit", "--file", str(f), "--item", "คลิป 2", "--url", "https://e.test/2", "--license", "ลิขสิทธิ์", "--ads", "no", "--note", "ท่อนสั้น"]) == 0
    capsys.readouterr()
    assert fetch.main(["credits-summary", "--file", str(f), "--json"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s == {"total": 2, "ads_ok": 1, "ads_not_ok": 1, "not_ok_items": ["คลิป 2"]}
    assert fetch.main(["credits-summary", "--file", str(f)]) == 0
    out = capsys.readouterr().out
    assert "ทั้งหมด 2 ชิ้น" in out and "ห้ามเอาไปยิงแอด" in out and "คลิป 2" in out


def test_credit_cli_rejects_bad_ads_value(tmp_path):
    with pytest.raises(SystemExit):
        fetch.main(["credit", "--file", str(tmp_path / "c.md"), "--item", "x", "--url", "u", "--license", "L", "--ads", "maybe"])


# ---------- gif2clip ----------

def test_gif2clip_fits_on_blurred_copy_1080x960(tmp_path):
    gif = tmp_path / "มีม ทดสอบ.gif"
    ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=0.5", gif)          # 16:9, 0.5 s
    out = tmp_path / "ผลลัพธ์ มีม.mp4"
    fetch.gif2clip(gif, out)
    info = probe(out, "stream=width,height,pix_fmt,r_frame_rate,duration")["streams"][0]
    assert (info["width"], info["height"], info["pix_fmt"]) == (1080, 960, "yuv420p")
    assert info["r_frame_rate"] == "30/1"
    assert float(info["duration"]) >= 1.9                                   # looped whole cycles up to >= 2 s
    # 16:9 fitted in 1080x960 leaves bars at the top; they must hold the blurred copy, not black
    gray = subprocess.run([FFMPEG, "-v", "error", "-i", str(out), "-frames:v", "1", "-vf", "format=gray", "-f", "rawvideo", "-"],
                          capture_output=True, check=True).stdout
    frame = np.frombuffer(gray, np.uint8).reshape(960, 1080)
    assert frame[:40].mean() > 25


def test_gif2clip_skips_existing_unless_force(tmp_path, capsys):
    gif, out = tmp_path / "a.gif", tmp_path / "a.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=64x64:r=10:d=0.3", gif)
    fetch.gif2clip(gif, out)
    stamp = out.stat().st_mtime_ns
    fetch.gif2clip(gif, out)
    skipped = capsys.readouterr()
    assert out.stat().st_mtime_ns == stamp and "ข้าม" in skipped.err and skipped.out.strip().endswith("a.mp4")
    fetch.gif2clip(gif, out, force=True)
    assert out.stat().st_mtime_ns > stamp


def test_gif2clip_custom_size(tmp_path):
    gif, out = tmp_path / "a.gif", tmp_path / "a.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=64x128:r=10:d=0.3", gif)
    fetch.gif2clip(gif, out, size=(1080, 1920), min_dur=1)
    info = probe(out, "stream=width,height")["streams"][0]
    assert (info["width"], info["height"]) == (1080, 1920)


# ---------- video (yt-dlp stubbed) ----------

@pytest.fixture
def stub_ytdlp(monkeypatch, tmp_path):
    calls = {"info": 0, "download": []}
    src = tmp_path / "remote.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=6", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src)

    def info(url):
        calls["info"] += 1
        return {"id": "abc123", "title": "ชื่อ คลิป", "uploader": "ช่อง ทดสอบ", "license": "Standard YouTube License",
                "webpage_url": url, "duration": 6.0}

    def download(url, dest, max_height, audio):
        calls["download"].append((url, max_height, audio))
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / "abc123.mp4"
        target.write_bytes(src.read_bytes())
        return target

    monkeypatch.setattr(fetch, "_ytdlp_info", info)
    monkeypatch.setattr(fetch, "_ytdlp_download", download)
    return calls


def test_video_trims_with_ffmpeg_and_names_by_id_and_range(tmp_path, stub_ytdlp, capsys):
    out_dir = tmp_path / "assets" / "ฟุตเทจ ดิบ"
    p = fetch.video("https://youtu.be/abc123", out_dir, start=1.5, end=3.5)
    assert p == out_dir / "abc123_1.5-3.5.mp4"
    dur = float(probe(p, "format=duration")["format"]["duration"])
    assert abs(dur - 2.0) < 0.2
    assert stub_ytdlp["download"] == [("https://youtu.be/abc123", 1080, True)]
    printed = capsys.readouterr()
    assert str(p) in printed.out.strip().splitlines()[-1]            # saved path is the last stdout line
    assert "ช่อง ทดสอบ" in printed.err and "Standard YouTube License" in printed.err   # credit info goes to stderr
    cache = kit.cache_dir() / "fetch"
    assert not cache.exists() or not any(cache.iterdir())            # temp download cleaned up


def test_video_without_trim_keeps_the_download_and_skips_on_rerun(tmp_path, stub_ytdlp):
    p = fetch.video("https://youtu.be/abc123", tmp_path / "o", max_height=720)
    assert p.name == "abc123.mp4" and stub_ytdlp["download"][0][1] == 720
    again = fetch.video("https://youtu.be/abc123", tmp_path / "o", max_height=720)
    assert again == p and len(stub_ytdlp["download"]) == 1          # no second download
    fetch.video("https://youtu.be/abc123", tmp_path / "o", force=True)
    assert len(stub_ytdlp["download"]) == 2


def test_video_no_audio_strips_audio_stream(tmp_path, stub_ytdlp):
    p = fetch.video("https://youtu.be/abc123", tmp_path / "o", start=0, end=2, audio=False)
    assert stub_ytdlp["download"][0][2] is False
    a = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True, check=True).stdout.strip()
    assert a == ""


def test_video_rejects_bad_range(tmp_path, stub_ytdlp):
    with pytest.raises(kit.KitError, match="start"):
        fetch.video("https://youtu.be/abc123", tmp_path / "o", start=5, end=2)
    with pytest.raises(kit.KitError, match="เลย"):
        fetch.video("https://youtu.be/abc123", tmp_path / "o", start=10, end=12)  # beyond the 6 s source


def test_video_only_http_urls(tmp_path):
    with pytest.raises(kit.KitError, match="http"):
        fetch.video("file:///etc/passwd", tmp_path / "o")


def test_ytdlp_command_prefers_h264_and_caps_height(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(kit, "run", lambda cmd, **kw: seen.append([str(c) for c in cmd]) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(kit, "tool", lambda name: {"yt-dlp": "YTDLP", "ffmpeg": "FFMPEG"}[name])
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x.mp4").write_bytes(b"1")
    fetch._ytdlp_download("https://e.test/v", tmp_path / "d", 1080, True)
    cmd = seen[0]
    assert cmd[0] == "YTDLP" and "--no-playlist" in cmd
    fmt = cmd[cmd.index("-f") + 1]
    assert "height<=?1080" in fmt and "+ba" in fmt
    assert cmd[cmd.index("--ffmpeg-location") + 1] == "FFMPEG"
    seen.clear()
    fetch._ytdlp_download("https://e.test/v", tmp_path / "d", 720, False)
    fmt = seen[0][seen[0].index("-f") + 1]
    assert "height<=?720" in fmt and "+ba" not in fmt


def test_ytsearch_parses_flat_playlist_output(monkeypatch, capsys):
    seen = []
    out = "abc123\t0:15\tช่อง ทดสอบ\thttps://www.youtube.com/watch?v=abc123\tชื่อ คลิป\tมีแท็บ\nNA\tNA\tNA\tNA\tbroken\nxyz\tNA\tNA\tNA\tไม่มีลิงก์\n"
    monkeypatch.setattr(kit, "run", lambda cmd, **kw: seen.append([str(c) for c in cmd]) or subprocess.CompletedProcess(cmd, 0, out, ""))
    monkeypatch.setattr(kit, "tool", lambda name: "YTDLP")
    rows = fetch.ytsearch("นิสชิน 1971 CM", limit=3)
    assert seen[0][0] == "YTDLP" and seen[0][-1] == "ytsearch3:นิสชิน 1971 CM" and "--flat-playlist" in seen[0]
    assert rows[0] == {"id": "abc123", "title": "ชื่อ คลิป\tมีแท็บ", "channel": "ช่อง ทดสอบ", "duration": "0:15", "url": "https://www.youtube.com/watch?v=abc123"}
    assert rows[1]["id"] == "xyz" and rows[1]["url"] == "https://www.youtube.com/watch?v=xyz" and rows[1]["channel"] == ""
    assert len(rows) == 2                                   # the all-NA line has no id and is dropped
    assert fetch.main(["ytsearch", "x"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == "abc123"


# ---------- image (http stubbed) ----------

def test_image_downloads_with_name_from_url(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "_http_get", lambda url: ("image/jpeg", b"\xff\xd8\xff data"))
    p = fetch.image("https://upload.wikimedia.org/wikipedia/commons/f/f6/Bangkok%20skytrain%20sunset.jpg?x=1", tmp_path / "รูป")
    assert p == tmp_path / "รูป" / "Bangkok skytrain sunset.jpg"
    assert p.read_bytes().startswith(b"\xff\xd8")


def test_image_name_gets_extension_from_content_type_and_is_windows_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "_http_get", lambda url: ("image/png", b"\x89PNG"))
    p = fetch.image("https://e.test/get?id=5", tmp_path)
    assert p.suffix == ".png"
    p2 = fetch.image('https://e.test/a/b:c*d?.png', tmp_path)
    assert not set('<>:"/\\|?*') & set(p2.name)


def test_image_rejects_html_pages_and_non_http(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "_http_get", lambda url: ("text/html; charset=utf-8", b"<html>"))
    with pytest.raises(kit.KitError, match="หน้าเว็บ"):
        fetch.image("https://e.test/page", tmp_path)
    with pytest.raises(kit.KitError, match="http"):
        fetch.image("ftp://e.test/a.jpg", tmp_path)


def test_image_skips_existing_unless_force(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fetch, "_http_get", lambda url: calls.append(url) or ("image/jpeg", b"x"))
    fetch.image("https://e.test/a.jpg", tmp_path)
    fetch.image("https://e.test/a.jpg", tmp_path)
    assert len(calls) == 1
    fetch.image("https://e.test/a.jpg", tmp_path, force=True)
    assert len(calls) == 2


# ---------- live (network) ----------

@pytest.mark.network
def test_live_commons_search():
    rows = fetch.commons("Bangkok skytrain", limit=3, kind="image")
    assert rows and all(r["file_url"].startswith("https://upload.wikimedia.org/") for r in rows)
    assert all(r["page_url"].startswith("https://commons.wikimedia.org/wiki/File:") for r in rows)
    assert all(r["license"] for r in rows)


@pytest.mark.network
def test_live_image_and_video_from_commons(tmp_path):
    rows = [r for r in fetch.commons("Bangkok skytrain", limit=8, kind="image") if r["bytes"] < 3_000_000]
    assert rows, "no small image found"
    img = fetch.image(rows[0]["file_url"], tmp_path / "img")
    assert img.stat().st_size == rows[0]["bytes"]
    vids = [r for r in fetch.commons("Bangkok", limit=20, kind="video") if r["mime"] in ("video/webm", "application/ogg") and r["bytes"] < 8_000_000]
    if not vids:
        pytest.skip("no small Commons video found")
    try:
        clip = fetch.video(vids[0]["file_url"], tmp_path / "vid", start=0, end=1.5)
    except kit.KitError as e:
        if "429" in str(e):
            pytest.skip("Wikimedia rate limit (429) — rerun later")
        raise
    assert clip.exists() and clip.suffix == ".mp4"
