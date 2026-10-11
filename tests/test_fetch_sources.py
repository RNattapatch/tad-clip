"""fetch, dogfood round: Pexels download URLs, licence/author for known hosts, --name, CREDITS.md locking + facts table,
contact sheets sized from clip length, scene cuts. Live Pexels/Commons calls are marked `network`."""
import json
import subprocess
import sys
import threading
import time
import os
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import fetch, kit  # noqa: E402

FFMPEG = kit.tool("ffmpeg")
FFPROBE = kit.tool("ffprobe")
PX = "https://videos.pexels.com/video-files"


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"tools": {"ffmpeg": FFMPEG, "ffprobe": FFPROBE}}), encoding="utf-8")
    monkeypatch.setenv("TAD_CLIP_HOME", str(home))
    return home


def ff(*args):
    subprocess.run([FFMPEG, "-v", "error", "-y", *map(str, args)], check=True)


def probe(path, entries, stream="v:0"):
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", stream, "-show_entries", entries, "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def rows_of(path, head):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    i = lines.index(head)
    out = []
    for ln in lines[i + 2:]:
        if not ln.startswith("|"):
            break
        out.append(ln)
    return out


# ---------- Pexels download URLs ----------

@pytest.mark.parametrize("url,vid", [
    ("https://www.pexels.com/download/video/4629216/", "4629216"),
    ("https://www.pexels.com/download/video/4629216/?h=1080&w=1920", "4629216"),
    ("https://www.pexels.com/video/a-person-grilling-samgyeopsal-4629216/", "4629216"),
    ("https://pexels.com/video/4629216", "4629216"),
    ("https://www.pexels.com/th-th/video/man-shouting-while-talking-on-the-phone-7581268/", "7581268"),
    ("https://videos.pexels.com/video-files/4629216/4629216-hd_1920_1080_24fps.mp4", None),   # already a file: nothing to resolve
    ("https://www.pexels.com/search/videos/bbq/", None),
    ("https://example.com/video/12345/", None),
])
def test_pexels_id_from_page_or_download_url(url, vid):
    assert fetch.pexels_id(url) == vid


def test_pexels_size_ladder_tries_landscape_then_swapped_portrait_for_each_rung():
    assert fetch.pexels_sizes(1080)[:4] == [(1920, 1080), (1080, 1920), (1280, 720), (720, 1280)]   # (w, h)
    assert fetch.pexels_sizes(720)[:2] == [(1280, 720), (720, 1280)]
    assert fetch.pexels_sizes(2160)[0] == (3840, 2160)
    assert all(h <= 1080 and w <= 1920 or w <= 1080 for w, h in fetch.pexels_sizes(1080))   # never asks for more than the cap
    assert fetch.pexels_sizes(100) == []                                                      # nothing that small exists


class Redirects:
    """Stand-in for fetch._redirect_target: replays answers and remembers which URLs were asked."""

    def __init__(self, answers):
        self.answers, self.urls = list(answers), []

    def __call__(self, url):
        self.urls.append(url)
        return self.answers.pop(0) if self.answers else (404, "")


def test_pexels_resolve_landscape_first_with_the_size_params(monkeypatch):
    r = Redirects([(302, f"{PX}/4629216/4629216-hd_1920_1080_24fps.mp4")])
    monkeypatch.setattr(fetch, "_redirect_target", r)
    assert fetch.pexels_resolve("4629216", 1080).endswith("hd_1920_1080_24fps.mp4")
    assert r.urls == ["https://www.pexels.com/download/video/4629216/?h=1080&w=1920"]            # never the size-less URL (that is 4K)


def test_pexels_resolve_swaps_h_and_w_for_portrait_clips(monkeypatch):
    r = Redirects([(404, ""), (302, f"{PX}/7581268/7581268-hd_1080_1920_30fps.mp4")])
    monkeypatch.setattr(fetch, "_redirect_target", r)
    assert "hd_1080_1920" in fetch.pexels_resolve("7581268", 1080)
    assert r.urls[1] == "https://www.pexels.com/download/video/7581268/?h=1920&w=1080"


def test_pexels_resolve_falls_down_the_ladder_when_1080_does_not_exist(monkeypatch):
    r = Redirects([(404, ""), (404, ""), (302, f"{PX}/9/9-hd_1280_720_25fps.mp4")])
    monkeypatch.setattr(fetch, "_redirect_target", r)
    assert "hd_1280_720" in fetch.pexels_resolve("9", 1080)
    assert r.urls[2].endswith("?h=720&w=1280")


def test_pexels_resolve_empty_or_foreign_redirects_are_a_clear_thai_error(monkeypatch):
    # a 302 with an empty Location, and a redirect to some login page, must not be treated as a file
    r = Redirects([(302, ""), (302, "https://www.pexels.com/login"), (302, "")] * 10)
    monkeypatch.setattr(fetch, "_redirect_target", r)
    with pytest.raises(kit.KitError) as e:
        fetch.pexels_resolve("123", 1080)
    msg = str(e.value)
    assert "Pexels" in msg and "123" in msg and "ไม่มีไฟล์" in msg and "http/https" not in msg
    assert "ดาวน์โหลดเอง" in msg                                       # tells the user what to do next


def test_pexels_resolve_for_a_missing_id_names_pexels_in_the_error(monkeypatch):
    monkeypatch.setattr(fetch, "_redirect_target", Redirects([]))
    with pytest.raises(kit.KitError, match="Pexels"):
        fetch.pexels_resolve("5", 720)


def local_clip(tmp_path, secs=4, size="320x180", name="remote.mp4"):
    src = tmp_path / name
    ff("-f", "lavfi", "-i", f"testsrc2=s={size}:r=30:d={secs}", "-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src)
    return src


@pytest.fixture
def stub_pexels(monkeypatch, tmp_path):
    src = local_clip(tmp_path)
    calls = {"resolve": [], "download": []}

    def resolve(vid, max_height):
        calls["resolve"].append((vid, max_height))
        return f"{PX}/{vid}/{vid}-hd_1920_1080_24fps.mp4"

    def download(url, dest):
        calls["download"].append(url)
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / url.rsplit("/", 1)[1]
        target.write_bytes(src.read_bytes())
        return target

    def no_ytdlp(*a, **k):
        raise AssertionError("a Pexels URL must not go through yt-dlp")

    monkeypatch.setattr(fetch, "pexels_resolve", resolve)
    monkeypatch.setattr(fetch, "_download_to", download)
    monkeypatch.setattr(fetch, "_ytdlp_info", no_ytdlp)
    monkeypatch.setattr(fetch, "_ytdlp_download", no_ytdlp)
    return calls


def test_video_from_a_pexels_page_url_resolves_downloads_trims_and_names_it(tmp_path, stub_pexels, capsys):
    p = fetch.video("https://www.pexels.com/video/a-person-grilling-samgyeopsal-4629216/", tmp_path / "ฟุตเทจ ดิบ", start=1, end=3)
    assert p == tmp_path / "ฟุตเทจ ดิบ" / "pexels-4629216_1-3.mp4"
    assert abs(float(probe(p, "format=duration")["format"]["duration"]) - 2.0) < 0.2
    assert stub_pexels["resolve"] == [("4629216", 1080)]
    assert stub_pexels["download"] == [f"{PX}/4629216/4629216-hd_1920_1080_24fps.mp4"]
    out = capsys.readouterr()
    assert out.out.strip().splitlines()[-1] == str(p)
    assert "Pexels License" in out.err and "1920_1080" in out.err                # licence filled in without asking anyone


def test_video_pexels_download_url_and_max_height_are_passed_through(tmp_path, stub_pexels):
    fetch.video("https://www.pexels.com/download/video/4629216/", tmp_path / "o", max_height=720)
    assert stub_pexels["resolve"] == [("4629216", 720)]


def test_video_pexels_credit_row_is_written_with_licence_author_and_range(tmp_path, stub_pexels):
    f = tmp_path / "CREDITS.md"
    p = fetch.video("https://www.pexels.com/video/a-person-grilling-samgyeopsal-4629216/", tmp_path / "o", start=0, end=2,
                    author="Sapol Churanon", credit_file=f, note="ใช้ตอนพูดถึงร้านปิ้งย่าง")
    row = rows_of(f, fetch.CREDIT_HEAD)[0].split("|")
    cells = [c.strip() for c in row[1:-1]]
    assert cells[0] == p.name
    assert cells[1] == "https://www.pexels.com/video/a-person-grilling-samgyeopsal-4629216/"
    assert cells[2] == "Pexels License" and cells[3] == "ใช่"
    assert "Sapol Churanon" in cells[4] and "0-2" in cells[4] and "ใช้ตอนพูดถึงร้านปิ้งย่าง" in cells[4]


def test_video_pexels_credit_without_author_says_so_instead_of_inventing_one(tmp_path, stub_pexels, capsys):
    f = tmp_path / "CREDITS.md"
    fetch.video("https://www.pexels.com/download/video/4629216/", tmp_path / "o", credit_file=f)
    cells = [c.strip() for c in rows_of(f, fetch.CREDIT_HEAD)[0].split("|")[1:-1]]
    assert "ผู้ถ่าย: ยังไม่ระบุ" in cells[4]
    assert cells[1] == "https://www.pexels.com/video/4629216/"                       # canonical page link built from the id
    assert "--author" in capsys.readouterr().err                                      # and the user is told how to add it


def test_video_without_credit_file_prints_the_row_to_return_to_the_main_session(tmp_path, stub_pexels, capsys):
    fetch.video("https://www.pexels.com/download/video/4629216/", tmp_path / "o", author="A B")
    err = capsys.readouterr().err
    assert "จด:" in err and "--license" in err and "Pexels License" in err and "--ads yes" in err


def test_video_name_overrides_the_derived_file_name(tmp_path, stub_pexels):
    p = fetch.video("https://www.pexels.com/download/video/4629216/", tmp_path / "o", start=0, end=1, name="ช็อต ย่าง")
    assert p.name == "ช็อต ย่าง.mp4"


def test_video_pexels_rerun_skips_without_touching_the_network(tmp_path, stub_pexels):
    fetch.video("https://www.pexels.com/download/video/4629216/", tmp_path / "o")
    fetch.video("https://www.pexels.com/download/video/4629216/", tmp_path / "o")
    assert len(stub_pexels["resolve"]) == 1 and len(stub_pexels["download"]) == 1


def test_a_direct_pexels_file_url_downloads_as_is(tmp_path, stub_pexels):
    p = fetch.video(f"{PX}/4629216/4629216-hd_1920_1080_24fps.mp4", tmp_path / "o")
    assert stub_pexels["resolve"] == [] and p.suffix == ".mp4"


def test_download_to_streams_a_file_named_from_the_url(tmp_path, monkeypatch):
    class Resp:
        headers = {"Content-Type": "video/mp4"}
        def __init__(self): self.parts = [b"a" * 10, b"b" * 5, b""]
        def read(self, n): return self.parts.pop(0)
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(urllib_request(), "urlopen", lambda req, timeout=0: Resp())
    p = fetch._download_to(f"{PX}/1/1-hd_1920_1080_24fps.mp4?x=1", tmp_path / "d")
    assert p.name == "1-hd_1920_1080_24fps.mp4" and p.read_bytes() == b"a" * 10 + b"b" * 5


def urllib_request():
    import urllib.request
    return urllib.request


# ---------- licence / author for known hosts ----------

def test_source_of_knows_pexels_and_pixabay_without_any_network_call():
    px = fetch.source_of("https://www.pexels.com/video/a-person-grilling-samgyeopsal-4629216/")
    assert (px["provider"], px["license"], px["ads_ok"]) == ("pexels", "Pexels License", True)
    assert fetch.source_of("https://images.pexels.com/photos/1108099/pexels-photo-1108099.jpeg")["license"] == "Pexels License"
    pb = fetch.source_of("https://cdn.pixabay.com/photo/2015/04/23/22/00/tree-736885_1280.jpg")
    assert (pb["provider"], pb["license"], pb["ads_ok"]) == ("pixabay", "Pixabay Content License", True)
    assert fetch.source_of("https://www.youtube.com/watch?v=abc") == {}
    assert fetch.source_of("https://example.com/a.jpg") == {}


def test_source_of_wikimedia_asks_the_commons_api_for_licence_and_author(monkeypatch):
    seen = []
    monkeypatch.setattr(fetch, "commons_lookup", lambda url: seen.append(url) or
                        {"license": "CC BY-SA 3.0", "author": "User:Diliff", "page_url": "https://commons.wikimedia.org/wiki/File:X.jpg", "ads_ok": False})
    s = fetch.source_of("https://upload.wikimedia.org/wikipedia/commons/f/f6/Bangkok_skytrain_sunset.jpg")
    assert seen and (s["provider"], s["license"], s["author"], s["ads_ok"]) == ("commons", "CC BY-SA 3.0", "User:Diliff", False)


def test_commons_lookup_builds_a_title_query_from_the_file_name(monkeypatch):
    seen = {}
    page = {"query": {"pages": [{"title": "File:Bangkok skytrain sunset.jpg", "imageinfo": [{
        "url": "https://upload.wikimedia.org/x.jpg", "descriptionurl": "https://commons.wikimedia.org/wiki/File:Bangkok_skytrain_sunset.jpg",
        "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"}, "Artist": {"value": "<a>Someone</a>"}}}]}]}}
    monkeypatch.setattr(fetch, "_http_get_json", lambda url: seen.setdefault("url", url) and page)
    got = fetch.commons_lookup("https://upload.wikimedia.org/wikipedia/commons/f/f6/Bangkok_skytrain_sunset.jpg?x=1")
    q = __import__("urllib.parse").parse.parse_qs(__import__("urllib.parse").parse.urlsplit(seen["url"]).query)
    assert q["titles"] == ["File:Bangkok skytrain sunset.jpg"]
    assert (got["license"], got["author"], got["ads_ok"]) == ("CC BY 4.0", "Someone", True)


def test_commons_lookup_failure_is_none_not_an_exception(monkeypatch):
    def boom(url):
        raise kit.KitError("ต่อเว็บไม่ได้")
    monkeypatch.setattr(fetch, "_http_get_json", boom)
    assert fetch.commons_lookup("https://upload.wikimedia.org/wikipedia/commons/a/ab/X.jpg") is None


# ---------- image: --name + automatic credit ----------

def test_image_name_option_keeps_the_extension_and_default_stays_url_derived(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "_http_get", lambda url: ("image/jpeg", b"\xff\xd8\xff data"))
    url = "https://images.pexels.com/photos/1108099/pexels-photo-1108099.jpeg?auto=compress&w=400"
    assert fetch.image(url, tmp_path / "a").name == "pexels-photo-1108099.jpeg"
    assert fetch.image(url, tmp_path / "b", name="หน้าร้าน กลางคืน").name == "หน้าร้าน กลางคืน.jpeg"
    assert fetch.image(url, tmp_path / "c", name="logo.png").name == "logo.png"
    assert fetch.image("https://e.test/get?id=5", tmp_path / "d", name="ภาพ").name == "ภาพ.jpg"     # no suffix in the URL: from content-type


def test_image_from_pexels_and_commons_is_credited_automatically(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "_http_get", lambda url: ("image/jpeg", b"x"))
    f = tmp_path / "CREDITS.md"
    fetch.image("https://images.pexels.com/photos/1108099/pexels-photo-1108099.jpeg", tmp_path / "o", name="ร้านอาหาร", author="Jane Doe", credit_file=f)
    monkeypatch.setattr(fetch, "commons_lookup", lambda url: {"license": "CC BY 4.0", "author": "Someone Else",
                                                               "page_url": "https://commons.wikimedia.org/wiki/File:Y.jpg", "ads_ok": True})
    fetch.image("https://upload.wikimedia.org/wikipedia/commons/a/ab/Y.jpg", tmp_path / "o", credit_file=f)
    a, b = [[c.strip() for c in r.split("|")[1:-1]] for r in rows_of(f, fetch.CREDIT_HEAD)]
    assert a[0] == "ร้านอาหาร.jpeg" and a[2] == "Pexels License" and a[3] == "ใช่" and "Jane Doe" in a[4]
    assert b[0] == "Y.jpg" and b[1] == "https://commons.wikimedia.org/wiki/File:Y.jpg" and b[2] == "CC BY 4.0" and "Someone Else" in b[4]


def test_unknown_host_needs_an_explicit_licence_to_be_credited(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "_http_get", lambda url: ("image/jpeg", b"x"))
    f = tmp_path / "CREDITS.md"
    with pytest.raises(kit.KitError, match="--license"):
        fetch.image("https://news.example.com/a.jpg", tmp_path / "o", credit_file=f)
    assert not f.exists()
    fetch.image("https://news.example.com/b.jpg", tmp_path / "o", credit_file=f, license_name="ภาพข่าว ใช้อ้างอิง", ads=False, author="สำนักข่าว X")
    cells = [c.strip() for c in rows_of(f, fetch.CREDIT_HEAD)[0].split("|")[1:-1]]
    assert cells[2] == "ภาพข่าว ใช้อ้างอิง" and cells[3] == "ไม่" and "สำนักข่าว X" in cells[4]


def test_cli_flags_for_name_author_and_credit_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fetch, "_http_get", lambda url: ("image/jpeg", b"x"))
    f = tmp_path / "CREDITS.md"
    rc = fetch.main(["image", "https://images.pexels.com/photos/1/pexels-photo-1.jpeg", "--out", str(tmp_path / "o"), "--name", "ชื่อ",
                     "--author", "A B", "--credit-file", str(f), "--note", "ใช้ตอนเปิด"])
    assert rc == 0 and (tmp_path / "o" / "ชื่อ.jpeg").exists()
    cells = [c.strip() for c in rows_of(f, fetch.CREDIT_HEAD)[0].split("|")[1:-1]]
    assert "A B" in cells[4] and "ใช้ตอนเปิด" in cells[4]


# ---------- CREDITS.md: concurrent writers + facts table ----------

def test_credit_is_safe_against_concurrent_writers(tmp_path, monkeypatch):
    real = Path.read_text

    def slow_read(self, *a, **k):        # widens the read-modify-write window so an unlocked implementation loses rows
        out = real(self, *a, **k)
        time.sleep(0.03)
        return out
    monkeypatch.setattr(Path, "read_text", slow_read)
    d = tmp_path / "งาน"
    d.mkdir()
    f = d / "CREDITS.md"
    barrier = threading.Barrier(8)
    errors = []

    def work(n):
        barrier.wait()
        try:
            for k in range(3):
                fetch.credit(f, f"ชิ้น {n}-{k}", f"https://e.test/{n}/{k}", "CC0", True)
        except Exception as e:           # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    rows = rows_of(f, fetch.CREDIT_HEAD)
    assert len(rows) == 24 and len({r.split("|")[1] for r in rows}) == 24
    assert f.read_text(encoding="utf-8").count(fetch.CREDIT_HEAD) == 1
    assert sorted(p.name for p in d.iterdir()) == ["CREDITS.md"]                 # no lock or temp file left behind


def test_credit_is_safe_across_processes(tmp_path):
    d = tmp_path / "งาน"
    d.mkdir()
    f = d / "CREDITS.md"
    procs = [subprocess.Popen([sys.executable, "-m", "engine.fetch", "credit", "--file", str(f), "--item", f"ไฟล์ {n}", "--url", f"https://e.test/{n}",
                               "--license", "CC0", "--ads", "yes"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for n in range(10)]
    codes = [p.wait(timeout=60) for p in procs]
    assert codes == [0] * 10
    assert len(rows_of(f, fetch.CREDIT_HEAD)) == 10
    assert sorted(p.name for p in d.iterdir()) == ["CREDITS.md"]


def test_a_stale_lock_is_cleared_and_a_live_one_times_out_with_a_thai_message(tmp_path, monkeypatch):
    f = tmp_path / "CREDITS.md"
    lock = tmp_path / "CREDITS.md.lock"
    lock.write_text("999999")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    fetch.credit(f, "a", "u", "L", True)                                 # stale (an hour old): removed, row written
    assert not lock.exists() and len(rows_of(f, fetch.CREDIT_HEAD)) == 1
    lock.write_text("1")                                                  # fresh lock held by someone else
    monkeypatch.setattr(fetch, "LOCK_TIMEOUT", 0.3)
    with pytest.raises(kit.KitError, match="CREDITS.md") as e:
        fetch.credit(f, "b", "u2", "L", True)
    assert "รอ" in str(e.value) and ".lock" in str(e.value)
    assert len(rows_of(f, fetch.CREDIT_HEAD)) == 1                       # nothing half-written


def test_facts_table_header_is_defined_and_lives_next_to_the_credits_table(tmp_path):
    assert fetch.FACTS_HEAD == "| ข้อความบนจอ | แหล่ง | ปี | ลิงก์ | หมายเหตุ |"
    f = tmp_path / "CREDITS.md"
    fetch.credit(f, "ภาพ 1", "https://e.test/1", "CC0", True)
    fetch.fact(f, "ปิด 26 สาขา 2 วัน", "MGR Online", "2559", "https://e.test/f1", "ตรวจแล้ว: ถูก")
    fetch.fact(f, "เปิดตัวกระทะ 22 ก.ย.", "Brand Buffet", "2559", "https://e.test/f2")
    text = f.read_text(encoding="utf-8")
    assert text.count(fetch.FACTS_HEAD) == 1 and text.count(fetch.CREDIT_HEAD) == 1
    assert rows_of(f, fetch.FACTS_HEAD)[0] == "| ปิด 26 สาขา 2 วัน | MGR Online | 2559 | https://e.test/f1 | ตรวจแล้ว: ถูก |"
    assert len(rows_of(f, fetch.FACTS_HEAD)) == 2
    assert fetch.credits_summary(f) == {"total": 1, "ads_ok": 1, "ads_not_ok": 0, "not_ok_items": []}   # facts are never counted as credits


def test_credit_after_fact_still_lands_in_the_credits_table_and_same_fact_text_is_updated(tmp_path):
    f = tmp_path / "CREDITS.md"
    fetch.credit(f, "ภาพ 1", "u1", "CC0", True)
    fetch.fact(f, "ข้อความ ก", "แหล่ง 1", "2559", "-", "")
    fetch.credit(f, "ภาพ 2", "u2", "CC0", False)
    fetch.fact(f, "ข้อความ ก", "แหล่ง 1", "2559", "https://e.test/ก", "แก้ลิงก์")
    assert [r.split("|")[1].strip() for r in rows_of(f, fetch.CREDIT_HEAD)] == ["ภาพ 1", "ภาพ 2"]
    assert rows_of(f, fetch.FACTS_HEAD) == ["| ข้อความ ก | แหล่ง 1 | 2559 | https://e.test/ก | แก้ลิงก์ |"]
    assert fetch.credits_summary(f)["total"] == 2


def test_fact_into_a_file_without_a_credits_table_and_cli(tmp_path, capsys):
    f = tmp_path / "งาน" / "CREDITS.md"
    assert fetch.main(["fact", "--file", str(f), "--text", "ข้อความบนจอ", "--source", "รายงานประจำปี", "--year", "2566", "--url", "-"]) == 0
    assert rows_of(f, fetch.FACTS_HEAD) == ["| ข้อความบนจอ | รายงานประจำปี | 2566 | - |  |"]
    capsys.readouterr()
    assert fetch.main(["credits-summary", "--file", str(f), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 0


# ---------- contact sheets + scene cuts ----------

@pytest.mark.parametrize("dur,expected", [
    (2.0, (4, 4, 1)),          # 0.5 s per tile -> 4 tiles, one row
    (6.0, (12, 6, 2)),         # the dogfood case: 12 frames used to leave half of a 6x4 sheet black
    (3.2, (7, 4, 2)),          # 7 tiles: 4 columns leaves one empty cell, 6 columns would leave five
    (12.0, (24, 6, 4)),
    (107.0, (24, 6, 4)),       # long clips are capped at 24 tiles and the interval stretches
    (0.2, (1, 1, 1)),
])
def test_sheet_layout_is_sized_from_the_clip_length(dur, expected):
    assert fetch.sheet_layout(dur) == expected


def make_clip(path, secs):
    ff("-f", "lavfi", "-i", f"testsrc2=s=320x180:r=30:d={secs}", "-c:v", "libx264", "-pix_fmt", "yuv420p", path)


def test_sheet_has_a_unique_default_name_per_clip_and_no_black_rows(tmp_path, capsys):
    a, b = tmp_path / "คลิป หนึ่ง.mp4", tmp_path / "คลิป สอง.mp4"
    make_clip(a, 6)
    make_clip(b, 2)
    sa, sb = fetch.sheet(a), fetch.sheet(b)
    assert sa == tmp_path / "คลิป หนึ่ง-sheet.jpg" and sb == tmp_path / "คลิป สอง-sheet.jpg" and sa.exists() and sb.exists() and sa != sb
    T = fetch.SHEET_TILE
    size = probe(sa, "stream=width,height")["streams"][0]
    assert (size["width"], size["height"]) == (6 * T + 5 * 4 + 8, 2 * T + 1 * 4 + 8)           # 6 x 2: exactly the 12 tiles of a 6 s clip
    size = probe(sb, "stream=width,height")["streams"][0]
    assert (size["width"], size["height"]) == (4 * T + 3 * 4 + 8, 1 * T + 8)                    # 2 s clip: one row of 4
    gray = subprocess.run([FFMPEG, "-v", "error", "-i", str(sa), "-vf", "format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    img = np.frombuffer(gray, np.uint8).reshape(2 * T + 12, 6 * T + 28)
    assert img[T + 12:, :].mean() > 20, "the second row must not be black"
    capsys.readouterr()
    assert fetch.main(["sheet", str(a), "--out", str(tmp_path / "ที่อื่น" / "x.jpg")]) == 0
    out = capsys.readouterr().out
    assert str(tmp_path / "ที่อื่น" / "x.jpg") in out and "ช่องละ" in out and "0.5" in out
    assert (tmp_path / "ที่อื่น" / "x.jpg").exists()


def test_sheet_rerun_is_idempotent_unless_force(tmp_path, capsys):
    a = tmp_path / "a.mp4"
    make_clip(a, 2)
    first = fetch.sheet(a)
    stamp = first.stat().st_mtime_ns
    fetch.sheet(a)
    assert first.stat().st_mtime_ns == stamp and "ข้าม" in capsys.readouterr().err
    fetch.sheet(a, force=True)
    assert first.stat().st_mtime_ns > stamp


def test_scenes_lists_cuts_and_says_so_explicitly_when_there_are_none(tmp_path, capsys):
    cut = tmp_path / "มี จุดตัด.mp4"
    ff("-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=2", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=2",
       "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-pix_fmt", "yuv420p", cut)
    flat = tmp_path / "ไม่มี จุดตัด.mp4"
    ff("-f", "lavfi", "-i", "color=c=green:s=320x180:r=30:d=3", "-pix_fmt", "yuv420p", flat)
    cuts = fetch.scenes(cut)
    assert len(cuts) == 1 and abs(cuts[0] - 2.0) < 0.1
    assert fetch.scenes(flat) == []
    assert fetch.main(["scenes", str(cut)]) == 0
    out = capsys.readouterr().out
    assert "2.0" in out and "ไม่พบจุดตัดฉาก" not in out
    assert fetch.main(["scenes", str(flat)]) == 0
    assert "ไม่พบจุดตัดฉาก" in capsys.readouterr().out
    assert fetch.main(["scenes", str(tmp_path / "ไม่มีไฟล์นี้.mp4")]) == 1


# ---------- live (network) ----------

@pytest.mark.network
def test_live_pexels_landscape_portrait_and_missing_ids():
    assert "hd_1920_1080" in fetch.pexels_resolve("4629216", 1080)               # landscape
    assert "hd_1080_1920" in fetch.pexels_resolve("7581268", 1080)               # portrait: needs the swapped h/w
    assert "hd_1280_720" in fetch.pexels_resolve("4629216", 720)
    with pytest.raises(kit.KitError, match="Pexels"):
        fetch.pexels_resolve("1", 1080)                                          # an id that does not exist: clear error, not "http/https"


@pytest.mark.network
def test_live_pexels_video_trim_credit_and_size_cap(tmp_path):
    f = tmp_path / "CREDITS.md"
    p = fetch.video("https://www.pexels.com/download/video/1509518/", tmp_path / "v", start=0, end=1, author="Jason H Austin", credit_file=f)
    assert p.name == "pexels-1509518_0-1.mp4" and abs(float(probe(p, "format=duration")["format"]["duration"]) - 1.0) < 0.3
    s = probe(p, "stream=width,height")["streams"][0]
    assert min(s["width"], s["height"]) <= 1080                                  # not the UHD file the bare download URL returns
    assert "Pexels License" in f.read_text(encoding="utf-8") and "Jason H Austin" in f.read_text(encoding="utf-8")


@pytest.mark.network
def test_live_pexels_image_and_commons_lookup(tmp_path):
    img = fetch.image("https://images.pexels.com/photos/1108099/pexels-photo-1108099.jpeg?auto=compress&cs=tinysrgb&w=400", tmp_path, name="ทดสอบ")
    assert img.name == "ทดสอบ.jpeg" and img.stat().st_size > 1000
    got = fetch.commons_lookup("https://upload.wikimedia.org/wikipedia/commons/f/f6/Bangkok_skytrain_sunset.jpg")
    assert got and got["license"].startswith("CC BY-SA") and "Diliff" in got["author"] and got["ads_ok"] is False
