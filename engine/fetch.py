"""fetch — เอาของมาใส่คลิป: คลิป (yt-dlp) · รูป · ค้น Wikimedia Commons · GIF → mp4 · จดลง CREDITS.md

  python -m engine.fetch video <url> --out DIR [--start S --end E] [--max-height 1080] [--no-audio] [--name N]
                             [--author A] [--credit-file CREDITS.md] [--license L --ads yes|no] [--note N] [--force]
  python -m engine.fetch image <url> --out DIR [--name N] [--author A] [--credit-file CREDITS.md] [--license L --ads yes|no] [--note N] [--force]
  python -m engine.fetch commons "<คำค้น>" [--limit 8] [--type any|image|video|audio]
  python -m engine.fetch ytsearch "<คำค้น>" [--limit 8]
  python -m engine.fetch gif2clip <in.gif> <out.mp4> [--size 1080x960] [--min-dur 2] [--force]
  python -m engine.fetch credit --file CREDITS.md --item NAME --url URL --license L --ads yes|no [--note N]
  python -m engine.fetch fact --file CREDITS.md --text T --source S --year Y --url URL [--note N]
  python -m engine.fetch credits-summary --file CREDITS.md [--json]
  python -m engine.fetch sheet <clip> [--out FILE] [--interval 0.5] [--force]     (contact sheet sized from the clip length)
  python -m engine.fetch scenes <clip> [--threshold 0.25]                           (scene cuts of a source clip)

Pexels: a page URL (…/video/<slug>-<id>/) or download URL (…/download/video/<id>/) works; the size parameters Pexels needs
(landscape h/w, swapped for portrait, never the 4K default) are added here. Pexels / Pixabay / Wikimedia licences are filled in.

ไม่ต้องใช้ API key · ทุกคำสั่งรับ path ภาษาไทย/มีช่องว่าง · ไฟล์ปลายทางมีอยู่แล้วจะข้าม (ใส่ --force เพื่อทำใหม่)
"""
import argparse
import contextlib
import html
import json
import math
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from engine import kit
from engine.kit import KitError

USER_AGENT = "tad-clip/1.0 (+https://github.com/RNattapatch/tad-clip; free Claude Code plugin)"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
MAX_DOWNLOAD = 200 * 1024 * 1024
CREDIT_TITLE = "# CREDITS"
CREDIT_INTRO = ("ของทุกชิ้นที่แทรกในคลิปนี้ · \"ใช้ยิงแอดได้\" = ใช่ ต่อเมื่อไลเซนส์อนุญาตเชิงพาณิชย์ชัดเจน "
                "ไม่แน่ใจให้ถือว่า \"ไม่\"")
CREDIT_HEAD = "| ชิ้น | ที่มา | ไลเซนส์ | ใช้ยิงแอดได้ | หมายเหตุ |"
CREDIT_SEP = "|---|---|---|---|---|"
FACTS_TITLE = "## ตรวจข้อเท็จจริง"
FACTS_HEAD = "| ข้อความบนจอ | แหล่ง | ปี | ลิงก์ | หมายเหตุ |"
FACTS_SEP = "|---|---|---|---|---|"
LOCK_TIMEOUT = 15.0     # seconds to wait for another writer of the same CREDITS.md
LOCK_STALE = 60.0       # a lock file older than this belongs to a crashed process
SHEET_TILE = 240
SHEET_MARGIN = SHEET_PAD = 4
_PEXELS_RUNGS = (2160, 1440, 1080, 720, 540, 360)   # short sides Pexels offers
YES, NO = "ใช่", "ไม่"
_IMAGE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
              "image/svg+xml": ".svg", "image/tiff": ".tif", "image/avif": ".avif", "image/bmp": ".bmp"}
_KNOWN_IMAGE_SUFFIX = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".tif", ".tiff", ".avif", ".bmp"}
_COMMONS_TYPE = {"image": "filetype:bitmap", "video": "filetype:video", "audio": "filetype:audio", "any": ""}
_YTDLP_HINT = "\nถ้าเป็นเพราะ yt-dlp เก่าไป ลองอัปเดตก่อน: yt-dlp -U (macOS ที่ลงด้วย brew: brew upgrade yt-dlp)"
_RATE_HINT = "\nเว็บปลายทางจำกัดความถี่ (429) — รอสัก 1-2 นาทีแล้วลองใหม่ หรือเลือกไฟล์อื่น"


# ---------- helpers ----------

def _require_http(url: str) -> None:
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise KitError(f"รับเฉพาะลิงก์ http/https เท่านั้น: {url}")


_WINDOWS_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])")


def _safe_name(name: str, fallback: str = "file") -> str:
    """File name that is legal on Windows and macOS (reserved device names get a leading underscore)."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:120] or fallback
    if _WINDOWS_RESERVED.fullmatch(name.split(".")[0].strip()):
        name = "_" + name
    return name


def _http_get(url: str) -> tuple:
    """(content_type, bytes). Wikimedia requires a descriptive User-Agent."""
    _require_http(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ctype = r.headers.get("Content-Type", "")
            data, total = [], 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise KitError(f"ไฟล์ใหญ่เกิน {MAX_DOWNLOAD // (1024 * 1024)} MB — เลือกไฟล์ที่เล็กกว่านี้")
                data.append(chunk)
    except urllib.error.HTTPError as e:
        raise KitError(f"โหลดไม่ได้ (เว็บตอบ {e.code}): {url}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise KitError(f"ต่อเว็บไม่ได้ — เช็คอินเทอร์เน็ตแล้วลองใหม่ ({e})") from None
    return ctype, b"".join(data)


def _http_get_json(url: str):
    _, data = _http_get(url)
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:
        raise KitError("Wikimedia Commons ตอบกลับมาไม่ใช่ JSON — ลองใหม่อีกครั้ง") from None


def _atomic_name(dst: Path) -> Path:
    return dst.with_name(dst.stem + ".tmp" + dst.suffix)


# ---------- Wikimedia Commons ----------

def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def _ads_ok(license_name: str, restrictions: str) -> bool:
    """Conservative: only CC0 / public domain / plain CC BY with no listed restriction. CC BY-SA, GFDL, unknown -> False."""
    lic = (license_name or "").strip().lower()
    if not lic or restrictions.strip():
        return False
    if re.match(r"^(cc0|public domain|pd\b|pdm)", lic):
        return True
    return bool(re.match(r"^cc[- ]by(?![- ](sa|nc|nd))[- ]?\d", lic))


def parse_commons(data: dict) -> list:
    """Wikimedia API (generator=search, formatversion=2) -> [{title, file_url, page_url, license, author, ...}] best match first."""
    ranked = []
    for p in (data.get("query") or {}).get("pages", []):
        info = (p.get("imageinfo") or [{}])[0]
        if not info.get("url"):
            continue
        md = info.get("extmetadata") or {}

        def field(key):
            v = md.get(key)
            return _strip_html(v.get("value", "")) if isinstance(v, dict) and isinstance(v.get("value", ""), str) else ""

        lic = field("LicenseShortName") or field("License")
        restrictions = field("Restrictions")
        parts = urllib.parse.urlsplit(info["url"])
        file_url = urllib.parse.urlunsplit(parts._replace(query="")) if parts.netloc.endswith("wikimedia.org") else info["url"]
        ranked.append((p.get("index", 0), {
            "title": re.sub(r"^File:", "", p.get("title", "")),
            "file_url": file_url,
            "page_url": info.get("descriptionurl", ""),
            "license": lic or "ไม่ระบุ",
            "author": field("Artist") or "ไม่ระบุ",
            "mime": info.get("mime", ""),
            "width": info.get("width", 0),
            "height": info.get("height", 0),
            "bytes": info.get("size", 0),
            "restrictions": restrictions,
            "ads_ok": _ads_ok(lic, restrictions),
        }))
    return [row for _, row in sorted(ranked, key=lambda t: t[0])]


def commons(query: str, limit: int = 8, kind: str = "any") -> list:
    q = " ".join(x for x in (_COMMONS_TYPE.get(kind, ""), query) if x)
    params = {"action": "query", "generator": "search", "gsrsearch": q, "gsrnamespace": "6", "gsrlimit": str(limit),
              "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size", "format": "json", "formatversion": "2", "origin": "*"}
    return parse_commons(_http_get_json(f"{COMMONS_API}?{urllib.parse.urlencode(params)}"))[:limit]


# ---------- known hosts: Pexels · Pixabay · Wikimedia ----------

def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def _is_host(url: str, *names: str) -> bool:
    h = _host(url)
    return any(h == n or h.endswith("." + n) for n in names)


def pexels_id(url: str):
    """Video id of a Pexels page (…/video/<slug>-<id>/) or download URL (…/download/video/<id>/); None for anything else."""
    if not _is_host(url, "pexels.com") or _host(url).startswith(("videos.", "images.")):
        return None
    path = urllib.parse.urlsplit(url).path
    m = re.search(r"/download/video/(\d+)", path) or re.search(r"/video/(?:[^/]*-)?(\d+)/?$", path)
    return m.group(1) if m else None


def pexels_sizes(max_height: int = 1080) -> list:
    """(w, h) to ask Pexels for, best first: for each short side <= max_height the landscape size, then the portrait one."""
    out = []
    for short in (r for r in _PEXELS_RUNGS if r <= int(max_height)):
        long_ = round(short * 16 / 9 / 2) * 2
        out += [(long_, short), (short, long_)]
    return out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _redirect_target(url: str) -> tuple:
    """(status, Location) of one request, without following the redirect."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=30) as r:
            return r.status, r.headers.get("Location", "") or ""
    except urllib.error.HTTPError as e:
        loc = (e.headers.get("Location", "") if e.headers else "") or ""
        e.close()
        return e.code, loc
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise KitError(f"ต่อ Pexels ไม่ได้ — เช็คอินเทอร์เน็ตแล้วลองใหม่ ({e})") from None


def pexels_resolve(vid: str, max_height: int = 1080) -> str:
    """Direct file URL on videos.pexels.com for a video id at the largest size <= max_height.
    The bare download URL returns UHD 4K, and portrait clips only answer when h/w are swapped (else 404)."""
    tried = []
    for w, h in pexels_sizes(max_height):
        status, loc = _redirect_target(f"https://www.pexels.com/download/video/{vid}/?h={h}&w={w}")
        if 300 <= status < 400 and loc.startswith("https://videos.pexels.com/"):
            return loc
        tried.append(f"{w}x{h}: {status}{'' if status != 302 else ' (ไม่มีลิงก์ไฟล์)'}")
    raise KitError(f"Pexels ไม่มีไฟล์ของวิดีโอ {vid} ที่ขนาดไม่เกิน {max_height}p (ลองแล้ว {' · '.join(tried[:4])}) — เลขวิดีโอผิด หรือวิดีโอถูกลบ · "
                   f"เปิดหน้า https://www.pexels.com/video/{vid}/ ในเบราว์เซอร์ แล้วให้ผู้ใช้กดดาวน์โหลดเอง จากนั้นบอก path ไฟล์")


def _download_to(url: str, dest: Path) -> Path:
    """Stream a direct file URL into dest/<name from the URL> (follows redirects, 200 MB cap)."""
    _require_http(url)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / _safe_name(urllib.parse.unquote(Path(urllib.parse.urlsplit(url).path).name), "video.mp4")
    tmp = target.with_name(target.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as out:
            total = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise KitError(f"ไฟล์ใหญ่เกิน {MAX_DOWNLOAD // (1024 * 1024)} MB — เลือกขนาดที่เล็กกว่านี้")
                out.write(chunk)
        tmp.replace(target)
    except urllib.error.HTTPError as e:
        raise KitError(f"โหลดไม่ได้ (เว็บตอบ {e.code}): {url}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise KitError(f"ต่อเว็บไม่ได้ — เช็คอินเทอร์เน็ตแล้วลองใหม่ ({e})") from None
    finally:
        tmp.unlink(missing_ok=True)
    return target


def commons_lookup(url: str):
    """Licence/author of a Wikimedia file URL from the Commons API (no key). None when it cannot be determined."""
    parts = [p for p in urllib.parse.urlsplit(url).path.split("/") if p]
    if "thumb" in parts and len(parts) >= 2:          # …/thumb/a/ab/Name.jpg/320px-Name.jpg
        name = parts[-2]
    elif parts:
        name = parts[-1]
    else:
        return None
    title = "File:" + urllib.parse.unquote(name).replace("_", " ")
    params = {"action": "query", "titles": title, "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size", "format": "json", "formatversion": "2"}
    try:
        rows = parse_commons(_http_get_json(f"{COMMONS_API}?{urllib.parse.urlencode(params)}"))
    except KitError:
        return None
    if not rows:
        return None
    r = rows[0]
    return {"license": "" if r["license"] == "ไม่ระบุ" else r["license"], "author": "" if r["author"] == "ไม่ระบุ" else r["author"],
            "page_url": r["page_url"], "ads_ok": r["ads_ok"]}


def source_of(url: str) -> dict:
    """Terms of hosts whose licence is known from the URL alone ({} otherwise): provider, license, ads_ok, author, page_url."""
    if _is_host(url, "pexels.com"):
        vid = pexels_id(url)
        page = f"https://www.pexels.com/video/{vid}/" if vid and "/download/" in url else url
        return {"provider": "pexels", "license": "Pexels License", "ads_ok": True, "author": "", "page_url": page}
    if _is_host(url, "pixabay.com"):
        return {"provider": "pixabay", "license": "Pixabay Content License", "ads_ok": True, "author": "", "page_url": url}
    if _is_host(url, "wikimedia.org"):
        got = commons_lookup(url) or {}
        return {"provider": "commons", "license": got.get("license", ""), "author": got.get("author", ""),
                "page_url": got.get("page_url") or url, "ads_ok": bool(got.get("ads_ok"))}
    return {}


def _credit_plan(meta: dict, url: str, *, author, license_name, ads, credit_file) -> dict:
    """What would be written to CREDITS.md. Raises (before anything is downloaded) when a row was asked for but the licence is unknown."""
    lic = license_name or meta.get("license") or ""
    if credit_file and not lic:
        raise KitError("ไม่รู้ไลเซนส์ของเว็บนี้ — ใส่ --license \"…\" และ --ads yes|no ด้วย (ไม่แน่ใจให้ใส่ no) หรือจดทีหลังด้วยคำสั่ง credit")
    return {"license": lic, "ads_ok": bool(meta.get("ads_ok")) if ads is None else bool(ads),
            "author": author or meta.get("author") or "", "page_url": meta.get("page_url") or url, "provider": meta.get("provider", "")}


def _finish_credit(plan: dict, saved: Path, *, credit_file, note: str, rng: str = "") -> None:
    """Write the row (when --credit-file) or print the row to hand back to the session that owns CREDITS.md."""
    who = f"ผู้ถ่าย/เจ้าของ: {plan['author']}" if plan["author"] else "ผู้ถ่าย: ยังไม่ระบุ (ชื่ออยู่ในหน้าวิดีโอ/รูป — เปิดด้วย WebFetch แล้วจดทีหลังหรือรันใหม่ใส่ --author)"
    parts = [who] + ([rng] if rng else []) + ([note] if note else [])
    full_note = " · ".join(parts)
    if plan["provider"] == "pexels" and not plan["author"]:
        print("หมายเหตุ: ชื่อผู้ถ่ายของ Pexels อ่านได้จากหน้าวิดีโอ/รูปเท่านั้น (Cloudflare กันสคริปต์) — เปิดหน้าด้วย WebFetch แล้วใส่ --author", file=sys.stderr)
    if credit_file:
        credit(credit_file, saved.name, plan["page_url"], plan["license"], plan["ads_ok"], full_note)
        print(f"จดแล้ว: {saved.name} · {plan['license']} · ใช้ยิงแอดได้: {YES if plan['ads_ok'] else NO} → {credit_file}", file=sys.stderr)
    elif plan["license"]:
        print(f"จด: credit --file CREDITS.md --item \"{saved.name}\" --url \"{plan['page_url']}\" --license \"{plan['license']}\" "
              f"--ads {'yes' if plan['ads_ok'] else 'no'} --note \"{full_note}\"", file=sys.stderr)
    else:
        print(f"จด: ยังไม่รู้ไลเซนส์ของเว็บนี้ — ตรวจเอง แล้วจดด้วย credit --item \"{saved.name}\" (ไม่แน่ใจให้ใส่ --ads no)", file=sys.stderr)


# ---------- image ----------

def image(url: str, out_dir, force: bool = False, *, name=None, author=None, credit_file=None, license_name=None, ads=None, note: str = "") -> Path:
    _require_http(url)
    out_dir = Path(out_dir)
    url_name = _safe_name(urllib.parse.unquote(Path(urllib.parse.urlsplit(url).path).name), "image")
    url_suffix = Path(url_name).suffix.lower()
    meta = source_of(url)
    plan = _credit_plan(meta, url, author=author, license_name=license_name, ads=ads, credit_file=credit_file)
    if name:
        name = _safe_name(name, "image")
        if Path(name).suffix.lower() not in _KNOWN_IMAGE_SUFFIX and url_suffix in _KNOWN_IMAGE_SUFFIX:
            name += url_suffix
        want = name
    else:
        want = url_name
    known = Path(want).suffix.lower() in _KNOWN_IMAGE_SUFFIX
    if known and (out_dir / want).exists() and not force:
        print(f"ข้าม: มี {want} อยู่แล้ว (ใส่ --force เพื่อโหลดใหม่)", file=sys.stderr)
        _finish_credit(plan, out_dir / want, credit_file=credit_file, note=note)
        print(out_dir / want)
        return out_dir / want
    ctype, data = _http_get(url)
    mime = ctype.split(";")[0].strip().lower()
    if not mime.startswith("image/") and not (mime in ("application/octet-stream", "binary/octet-stream", "") and url_suffix in _KNOWN_IMAGE_SUFFIX):
        raise KitError("ลิงก์นี้เป็นหน้าเว็บ ไม่ใช่ไฟล์รูป — เปิดหน้านั้นแล้วหาลิงก์ไฟล์รูปตรงๆ (มักลงท้าย .jpg .png) หรือให้ผู้ใช้กดดาวน์โหลดเอง")
    if not known:
        want += _IMAGE_EXT.get(mime, ".jpg")
    dst = out_dir / want
    out_dir.mkdir(parents=True, exist_ok=True)
    if not (dst.exists() and not force):
        tmp = _atomic_name(dst)
        tmp.write_bytes(data)
        tmp.replace(dst)
    if plan["license"]:
        print(f"ที่มา: {plan['page_url']} · ไลเซนส์ที่ระบุ: {plan['license']}" + (f" · {plan['author']}" if plan["author"] else ""), file=sys.stderr)
    _finish_credit(plan, dst, credit_file=credit_file, note=note)
    print(dst)
    return dst


# ---------- video (yt-dlp) ----------

def _ytdlp(args: list):
    """Run yt-dlp telling it (flag + env) to speak UTF-8: a legacy Windows console code page would turn Thai titles into '?'."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    return kit.run([kit.tool("yt-dlp"), "--encoding", "utf-8", *args], env=env)


def _hint(err: Exception) -> str:
    return _RATE_HINT if "429" in str(err) else _YTDLP_HINT


def _ytdlp_info(url: str) -> dict:
    try:
        res = _ytdlp(["--no-playlist", "--no-warnings", "--skip-download", "-J", url])
    except KitError as e:
        raise KitError(f"{e}{_hint(e)}") from None
    data = json.loads(res.stdout)
    if data.get("_type") == "playlist":
        entries = [e for e in data.get("entries") or [] if e]
        if not entries:
            raise KitError("ลิงก์นี้เป็นเพลย์ลิสต์ที่ไม่มีคลิป — ส่งลิงก์คลิปเดี่ยว")
        data = entries[0]
    return {k: data.get(k) for k in ("id", "title", "uploader", "license", "webpage_url", "duration")} | {
        "uploader": data.get("uploader") or data.get("channel")}


def _ytdlp_download(url: str, dest: Path, max_height: int, audio: bool) -> Path:
    h = int(max_height)
    # "<=?" keeps formats whose height is unknown (direct file links) instead of dropping them
    fmt = f"bv*[height<=?{h}]+ba/b[height<=?{h}]" if audio else f"bv*[height<=?{h}]/b[height<=?{h}]"
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["--no-playlist", "--no-warnings", "--no-progress", "-f", fmt,
           "-S", "res,fps,vcodec:h264,acodec:aac", "--merge-output-format", "mp4",
           "--ffmpeg-location", kit.tool("ffmpeg"), "-o", str(dest / "%(id)s.%(ext)s"), url]
    try:
        _ytdlp(cmd)
    except KitError as e:
        raise KitError(f"{e}{_hint(e)}") from None
    files = [p for p in dest.iterdir() if p.is_file() and p.suffix.lower() not in (".part", ".ytdl", ".temp")]
    if not files:
        raise KitError("yt-dlp ทำงานจบแต่ไม่มีไฟล์ออกมา — ลองลิงก์อื่น")
    return max(files, key=lambda p: p.stat().st_size)


def ytsearch(query: str, limit: int = 8) -> list:
    """Search YouTube through yt-dlp (no key, nothing downloaded) -> [{id, title, channel, duration, url}]."""
    try:
        res = _ytdlp(["--no-warnings", "--flat-playlist", "--print",
                      "%(id)s\t%(duration_string)s\t%(channel)s\t%(url)s\t%(title)s", f"ytsearch{int(limit)}:{query}"])
    except KitError as e:
        raise KitError(f"{e}{_hint(e)}") from None
    out = []
    for line in res.stdout.splitlines():
        parts = line.split("\t", 4)
        if len(parts) < 5:
            continue
        vid, dur, channel, url, title = (None if x == "NA" else x for x in parts)
        if not vid:
            continue
        out.append({"id": vid, "title": title or "", "channel": channel or "", "duration": dur or "",
                    "url": url or f"https://www.youtube.com/watch?v={vid}"})
    return out


def video(url: str, out_dir, start=None, end=None, max_height: int = 1080, audio: bool = True, force: bool = False, *,
          name=None, author=None, credit_file=None, license_name=None, ads=None, note: str = "") -> Path:
    _require_http(url)
    if start is not None and start < 0:
        raise KitError("--start ต้องไม่ติดลบ")
    if start is not None and end is not None and end <= start:
        raise KitError("--start ต้องน้อยกว่า --end")
    out_dir = Path(out_dir)
    pid = pexels_id(url)
    direct = _is_host(url, "videos.pexels.com")            # already a file on the Pexels CDN
    own_download = bool(pid or direct)                      # Pexels is fetched here, never through yt-dlp
    if own_download:
        stem0 = f"pexels-{pid}" if pid else Path(urllib.parse.unquote(Path(urllib.parse.urlsplit(url).path).name)).stem
        info = {"id": stem0, "title": (re.sub(r"-?\d+/?$", "", urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]).replace("-", " ")
                                          if pid and "/video/" in url else ""), "uploader": "", "license": "", "webpage_url": "", "duration": 0}
        meta = source_of(url)
    else:
        info = _ytdlp_info(url)
        meta = source_of(url) or {"provider": "web", "license": info.get("license") or "", "ads_ok": False, "page_url": info.get("webpage_url") or url}
        meta.setdefault("author", "")
        meta["author"] = meta["author"] or info.get("uploader") or ""
    plan = _credit_plan(meta, url, author=author, license_name=license_name, ads=ads, credit_file=credit_file)
    duration = float(info.get("duration") or 0)
    if start is not None and duration and start >= duration:
        raise KitError(f"--start {start:g} เลยความยาวคลิป ({duration:.1f} วินาที)")
    trim = start is not None or end is not None
    rng = f"ช่วง {(start or 0):g}-{end:g} วินาทีของต้นฉบับ" if trim and end is not None else (f"ช่วง {(start or 0):g}-จบ วินาทีของต้นฉบับ" if trim else "")
    if name:
        dst = out_dir / (_safe_name(name, "video") if name.lower().endswith(".mp4") else _safe_name(name, "video") + ".mp4")
    else:
        stem = _safe_name(str(info.get("id") or "video"), "video")
        if trim:
            stem += f"_{(start or 0):g}-{end:g}" if end is not None else f"_{(start or 0):g}-end"
        dst = out_dir / f"{stem}.mp4"
    if dst.exists() and not force:
        print(f"ข้าม: มี {dst.name} อยู่แล้ว (ใส่ --force เพื่อโหลดใหม่)", file=sys.stderr)
        _finish_credit(plan, dst, credit_file=credit_file, note=note, rng=rng)
        print(dst)
        return dst

    work = kit.cache_dir() / "fetch" / uuid.uuid4().hex[:12]
    fetched = ""
    try:
        if own_download:
            file_url = url if direct else pexels_resolve(pid, max_height)
            fetched = Path(urllib.parse.urlsplit(file_url).path).name
            src = _download_to(file_url, work)
        else:
            src = _ytdlp_download(url, work, max_height, audio)
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = _atomic_name(dst)
        if trim or not audio:
            cmd = [kit.tool("ffmpeg"), "-v", "error", "-y"]
            if start:
                cmd += ["-ss", f"{start:g}"]
            cmd += ["-i", str(src)]
            if end is not None:
                cmd += ["-t", f"{end - (start or 0):g}"]
            if trim:  # ตัดแม่นยำต้องเข้ารหัสใหม่ (x264 crf 18 · ภาพไม่แตกต่างจากต้นฉบับ)
                cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
            else:
                cmd += ["-c:v", "copy"]
            cmd += ["-c:a", "aac", "-b:a", "192k"] if audio else ["-an"]
            kit.run(cmd + ["-movflags", "+faststart", str(tmp)])
        else:
            shutil.copyfile(src, tmp)
        tmp.replace(dst)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    who = plan["author"] or "-"
    print(f"ที่มา: {info.get('title') or '-'}{' · ไฟล์ ' + fetched if fetched else ''} · ช่อง/ผู้ถ่าย: {who} · "
          f"ไลเซนส์ที่ระบุ: {plan['license'] or 'ไม่ระบุ'} · {plan['page_url']}", file=sys.stderr)
    _finish_credit(plan, dst, credit_file=credit_file, note=note, rng=rng)
    print(dst)
    return dst


# ---------- gif -> clip ----------

def _duration(path: Path) -> float:
    try:
        res = kit.run([kit.tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
        return float(res.stdout.strip())
    except (KitError, ValueError):
        return 0.0


def gif2clip(src, out, size=(1080, 960), min_dur: float = 2.0, fps: int = 30, force: bool = False) -> Path:
    """GIF -> mp4 ขนาด size: ภาพเต็มสัดส่วนวางกลางบนพื้นเบลอของตัวเอง · วนทั้งรอบจนยาว >= min_dur · ไม่มีเสียง"""
    src, out = Path(src), Path(out)
    if not src.is_file():
        raise KitError(f"ไม่พบไฟล์ {src}")
    if out.exists() and not force:
        print(f"ข้าม: มี {out.name} อยู่แล้ว (ใส่ --force เพื่อทำใหม่)", file=sys.stderr)
        print(out)
        return out
    w, h = size
    dur = _duration(src) or 1.0
    loops = min(200, max(0, math.ceil(min_dur / dur - 1e-6) - 1))
    fc = (f"fps={fps},split=2[bg][fg];"
          f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=40:5[b];"
          f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2[f];"
          f"[b][f]overlay=(W-w)/2:(H-h)/2,format=yuv420p,setsar=1")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = _atomic_name(out)
    try:
        kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-stream_loop", str(loops), "-i", src, "-filter_complex", fc,
                 "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-movflags", "+faststart", tmp])
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    print(out)
    return out


# ---------- footage prep: contact sheet + scene cuts ----------

def sheet_layout(duration: float, interval: float = 0.5, max_tiles: int = 24) -> tuple:
    """(tiles, cols, rows): one tile per `interval` seconds (stretched past max_tiles), columns chosen to leave the fewest empty cells."""
    n = max(1, min(int(max_tiles), math.ceil(duration / interval - 1e-9)))
    best = None
    for cols in range(min(n, 6), min(n, 3) - 1, -1):
        rows = math.ceil(n / cols)
        empty = rows * cols - n
        if best is None or empty < best[0]:
            best = (empty, cols, rows)
    return n, best[1], best[2]


def sheet(clip, out=None, interval: float = 0.5, force: bool = False, max_tiles: int = 24) -> Path:
    """Contact sheet of a clip with the time of every tile drawn in the corner. Default file: <clip name>-sheet.jpg next to the clip."""
    clip = Path(clip)
    if not clip.is_file():
        raise KitError(f"ไม่พบไฟล์ {clip}")
    out = Path(out) if out else clip.with_name(clip.stem + "-sheet.jpg")
    if out.exists() and not force and out.stat().st_mtime_ns >= clip.stat().st_mtime_ns:
        print(f"ข้าม: มี {out.name} อยู่แล้ว (ใส่ --force เพื่อทำใหม่)", file=sys.stderr)
        print(out)
        return out
    dur = _duration(clip)
    if dur <= 0:
        raise KitError(f"อ่านความยาวของ {clip.name} ไม่ได้ — ไฟล์นี้เป็นวิดีโอที่เปิดได้หรือเปล่า")
    n, cols, rows = sheet_layout(dur, interval, max_tiles)
    step = dur / n
    ffmpeg, T = kit.tool("ffmpeg"), SHEET_TILE
    # fontfile is relative to cwd = the fonts folder, so no Windows drive-letter escaping inside the filter string
    draw = lambda label: (f"drawtext=fontfile=Prompt-Bold.ttf:text='{label}':fontsize=26:fontcolor=white:box=1:boxcolor=0xDD4155@0.9:"  # noqa: E731
                          "boxborderw=5:x=6:y=6")
    try:
        kit.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64", "-frames:v", "1", "-vf", draw("0.0s"), "-f", "null", "-"], cwd=kit.fonts_dir())
        labels = True
    except KitError:
        labels = False
    work = kit.cache_dir() / "fetch" / ("sheet-" + uuid.uuid4().hex[:10])
    work.mkdir(parents=True, exist_ok=True)
    times = [step * k for k in range(n)]               # start of each interval: clean labels (0.0s, 0.5s, 1.0s …)
    try:
        for k, t in enumerate(times, 1):
            vf = ",".join([f"scale={T}:{T}:force_original_aspect_ratio=decrease", f"pad={T}:{T}:(ow-iw)/2:(oh-ih)/2:black"]
                          + ([draw(f"{t:.1f}s")] if labels else []) + ["format=yuvj420p"])
            kit.run([ffmpeg, "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(clip.resolve()), "-frames:v", "1", "-vf", vf, "-q:v", "3", str(work / f"t{k:03d}.jpg")],
                    cwd=kit.fonts_dir())
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = _atomic_name(out)
        kit.run([ffmpeg, "-v", "error", "-y", "-framerate", "1", "-i", str(work / "t%03d.jpg"), "-vf",
                 f"tile={cols}x{rows}:margin={SHEET_MARGIN}:padding={SHEET_PAD}:color=black", "-frames:v", "1", "-q:v", "3", str(tmp)])
        tmp.replace(out)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(out)
    print(f"ช่องละ ~{step:.1f} วินาที · {n} ช่อง ({cols} ช่องต่อแถว · เรียงซ้าย→ขวา บน→ล่าง)"
          + (" · เวลาของแต่ละช่องอยู่มุมซ้ายบน" if labels else " · ffmpeg ตัวนี้เขียนเวลาลงภาพไม่ได้ — เวลาของช่องที่ k = " + f"(k-1)×{step:.1f} วินาที"))
    return out


def scenes(clip, threshold: float = 0.25) -> list:
    """Times (seconds) where the picture of the source cuts to another scene."""
    clip = Path(clip)
    if not clip.is_file():
        raise KitError(f"ไม่พบไฟล์ {clip}")
    res = kit.run([kit.tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(clip), "-vf", f"select='gt(scene,{threshold:g})',showinfo",
                   "-an", "-f", "null", "-"])
    return [float(t) for t in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", res.stderr or "")]


# ---------- CREDITS.md ----------

def _cell(s: str) -> str:
    return re.sub(r"\s*[\r\n]+\s*", " ", str(s)).strip().replace("|", "\\|")


def _split_row(line: str) -> list:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", body)]


def _is_data_row(line: str) -> bool:
    line = line.strip()
    if not line.startswith("|") or re.match(r"^\|\s*-{2,}", line):
        return False
    return len(_split_row(line)) >= 4


def _table_block(lines: list, head: str):
    """(first_row, end) of the table body under `head` — other tables in the same file are not part of it."""
    for i, ln in enumerate(lines):
        if ln.strip() == head:
            j = i + 1
            if j < len(lines) and re.match(r"^\|\s*-{2,}", lines[j].strip()):
                j += 1
            start = j
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            return start, j
    return None


@contextlib.contextmanager
def _locked(path: Path):
    """Exclusive lock for a read-modify-write of one file (O_EXCL lock file, stale locks cleared, waits up to LOCK_TIMEOUT)."""
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_TIMEOUT
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise KitError(f"{path.name} กำลังถูกเขียนโดยอีกคำสั่งหนึ่งนานเกิน {LOCK_TIMEOUT:g} วินาที — รอแล้วลองใหม่ "
                               f"(ถ้าไม่มีคำสั่งอื่นทำงานอยู่ ลบไฟล์ {lock.name} ในโฟลเดอร์เดียวกัน)") from None
            time.sleep(0.03 + (os.getpid() % 7) * 0.005)
            continue
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        break
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        for attempt in range(6):          # Windows: the target can be briefly held open by a reader or a virus scanner
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.1)
    finally:
        tmp.unlink(missing_ok=True)


def _upsert_row(file, head: str, sep: str, row: str, key: tuple, prelude_new: list, prelude_append: list) -> Path:
    """Insert `row` at the end of the table under `head` (creating the table if missing); a row with the same key is replaced.
    Locked + atomic, so concurrent writers cannot drop each other's rows."""
    f = Path(file)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        with _locked(f):
            lines = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
            if _table_block(lines, head) is None:
                if not lines:
                    lines += prelude_new
                else:
                    if lines[-1].strip():
                        lines.append("")
                    lines += prelude_append
                lines += [head, sep]
            start, end = _table_block(lines, head)
            for i in range(start, end):
                if _is_data_row(lines[i]) and tuple(_split_row(lines[i])[:len(key)]) == key:
                    lines[i] = row
                    break
            else:
                lines.insert(end, row)
            _write_atomic(f, "\n".join(lines) + "\n")
    except OSError as e:
        raise KitError(f"เขียน {f.name} ไม่ได้ ({e.strerror or e}) — เช็คว่าโฟลเดอร์ {f.parent} มีอยู่ ไฟล์ไม่ได้เปิดค้างในโปรแกรมอื่น และมีสิทธิ์เขียน") from None
    return f


def credit(file, item: str, url: str, license_name: str, ads_ok: bool, note: str = "") -> Path:
    """Add one row to the credits table of CREDITS.md (header written once). Same item+url again -> that row is replaced."""
    row = f"| {_cell(item)} | {_cell(url)} | {_cell(license_name)} | {YES if ads_ok else NO} | {_cell(note)} |"
    return _upsert_row(file, CREDIT_HEAD, CREDIT_SEP, row, (item.strip(), url.strip()),
                       [CREDIT_TITLE, "", CREDIT_INTRO, ""], [])


def fact(file, text: str, source: str, year: str, url: str, note: str = "") -> Path:
    """Add one row to the fact-check table (หัวข้อ "ตรวจข้อเท็จจริง", under the credits table). Same text again -> that row is replaced."""
    row = f"| {_cell(text)} | {_cell(source)} | {_cell(year)} | {_cell(url)} | {_cell(note)} |"
    return _upsert_row(file, FACTS_HEAD, FACTS_SEP, row, (text.strip(),),
                       [CREDIT_TITLE, "", FACTS_TITLE, ""], [FACTS_TITLE, ""])


def credits_summary(file) -> dict:
    f = Path(file)
    if not f.exists():
        raise KitError(f"ไม่พบ {f}")
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise KitError(f"อ่าน {f.name} ไม่ได้ ({e.strerror or e})") from None
    block = _table_block(lines, CREDIT_HEAD)
    rows = [_split_row(ln) for ln in lines[block[0]:block[1]] if _is_data_row(ln)] if block else []
    bad = [r[0] for r in rows if r[3] != YES]
    return {"total": len(rows), "ads_ok": len(rows) - len(bad), "ads_not_ok": len(bad), "not_ok_items": bad}


# ---------- CLI ----------

def _size(s: str) -> tuple:
    m = re.fullmatch(r"(\d+)[xX](\d+)", s)
    if not m:
        raise argparse.ArgumentTypeError("ขนาดต้องเป็นรูปแบบ 1080x960")
    return int(m.group(1)), int(m.group(2))


def _source_flags(p, name_help: str) -> None:
    p.add_argument("--name", help=name_help)
    p.add_argument("--author", help="ชื่อผู้ถ่าย/เจ้าของ (Pexels/Pixabay อ่านจากหน้าเว็บด้วยสคริปต์ไม่ได้ ต้องส่งมา)")
    p.add_argument("--credit-file", help="ถ้าใส่ จดแถว CREDITS.md ให้เลย (เฉพาะ session หลักเท่านั้น · subagent อย่าใส่)")
    p.add_argument("--license", dest="license_name", help="ไลเซนส์ (ต้องใส่ถ้าเว็บนี้ไม่ใช่ Pexels/Pixabay/Wikimedia และใช้ --credit-file)")
    p.add_argument("--ads", choices=["yes", "no"], help="ใช้ยิงแอดได้ไหม (ไม่ใส่ = ตามเว็บ · ไม่รู้ = no)")
    p.add_argument("--note", default="", help="หมายเหตุลง CREDITS.md เช่น ใช้ตรงไหนของคลิป")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m engine.fetch", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("video", help="โหลดคลิปด้วย yt-dlp (ไม่เกิน --max-height) แล้วตัดช่วงด้วย ffmpeg")
    v.add_argument("url")
    v.add_argument("--out", required=True)
    v.add_argument("--start", type=float)
    v.add_argument("--end", type=float)
    v.add_argument("--max-height", type=int, default=1080)
    v.add_argument("--no-audio", action="store_true")
    v.add_argument("--force", action="store_true")
    _source_flags(v, "ชื่อไฟล์ผลลัพธ์ (ค่าเริ่ม = เลขคลิป + ช่วงเวลา)")
    i = sub.add_parser("image", help="โหลดรูปจากลิงก์ไฟล์ตรงๆ")
    i.add_argument("url")
    i.add_argument("--out", required=True)
    i.add_argument("--force", action="store_true")
    _source_flags(i, "ชื่อไฟล์ (ค่าเริ่ม = ชื่อจากลิงก์) นามสกุลเติมให้เอง")
    c = sub.add_parser("commons", help="ค้นไฟล์ใน Wikimedia Commons (ไม่ต้องใช้ key) -> JSON")
    c.add_argument("query")
    c.add_argument("--limit", type=int, default=8)
    c.add_argument("--type", choices=sorted(_COMMONS_TYPE), default="any")
    y = sub.add_parser("ytsearch", help="ค้น YouTube ด้วย yt-dlp (ยังไม่โหลดอะไร) -> JSON")
    y.add_argument("query")
    y.add_argument("--limit", type=int, default=8)
    g = sub.add_parser("gif2clip", help="GIF -> mp4 วางบนพื้นเบลอของตัวเอง")
    g.add_argument("src")
    g.add_argument("out")
    g.add_argument("--size", type=_size, default=(1080, 960))
    g.add_argument("--min-dur", type=float, default=2.0)
    g.add_argument("--force", action="store_true")
    cr = sub.add_parser("credit", help="จดของที่ใช้ลง CREDITS.md")
    cr.add_argument("--file", required=True)
    cr.add_argument("--item", required=True)
    cr.add_argument("--url", required=True, help='ลิงก์ที่มา · ของที่ผู้ใช้ถ่าย/ทำเองใส่ "-"')
    cr.add_argument("--license", required=True)
    cr.add_argument("--ads", required=True, choices=["yes", "no"], help="ใช้ยิงแอดได้ไหม · ไม่แน่ใจ = no")
    cr.add_argument("--note", default="")
    ft = sub.add_parser("fact", help="จดข้อเท็จจริงที่ตรวจแล้วลงตาราง ตรวจข้อเท็จจริง ใน CREDITS.md")
    ft.add_argument("--file", required=True)
    ft.add_argument("--text", required=True, help="ข้อความ/ตัวเลขที่ขึ้นบนจอ")
    ft.add_argument("--source", required=True, help="ชื่อแหล่ง")
    ft.add_argument("--year", required=True, help="ปีของแหล่ง")
    ft.add_argument("--url", required=True, help='ลิงก์แหล่ง · ไม่มีใส่ "-"')
    ft.add_argument("--note", default="")
    sh = sub.add_parser("sheet", help="ภาพรวมของคลิป (contact sheet) ขนาดตามความยาวคลิป มีเวลากำกับ")
    sh.add_argument("clip")
    sh.add_argument("--out")
    sh.add_argument("--interval", type=float, default=0.5, help="วินาทีต่อช่อง (ค่าเริ่ม 0.5 · คลิปยาวถูกยืดให้ไม่เกิน 24 ช่อง)")
    sh.add_argument("--force", action="store_true")
    sc = sub.add_parser("scenes", help="หาจุดตัดฉากของคลิปต้นทาง")
    sc.add_argument("clip")
    sc.add_argument("--threshold", type=float, default=0.25)
    cs = sub.add_parser("credits-summary", help="สรุป CREDITS.md: กี่ชิ้นยิงแอดได้ / ห้าม")
    cs.add_argument("--file", required=True)
    cs.add_argument("--json", action="store_true")
    return ap


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    a = _parser().parse_args(argv)
    try:
        if a.cmd == "video":
            video(a.url, a.out, a.start, a.end, a.max_height, not a.no_audio, a.force, name=a.name, author=a.author, credit_file=a.credit_file,
                  license_name=a.license_name, ads=None if a.ads is None else a.ads == "yes", note=a.note)
        elif a.cmd == "image":
            image(a.url, a.out, a.force, name=a.name, author=a.author, credit_file=a.credit_file, license_name=a.license_name,
                  ads=None if a.ads is None else a.ads == "yes", note=a.note)
        elif a.cmd == "commons":
            print(json.dumps(commons(a.query, a.limit, a.type), ensure_ascii=False, indent=2))
        elif a.cmd == "ytsearch":
            print(json.dumps(ytsearch(a.query, a.limit), ensure_ascii=False, indent=2))
        elif a.cmd == "gif2clip":
            gif2clip(a.src, a.out, a.size, a.min_dur, force=a.force)
        elif a.cmd == "credit":
            f = credit(a.file, a.item, a.url, a.license, a.ads == "yes", a.note)
            print(f"จดแล้ว: {a.item} · ใช้ยิงแอดได้: {YES if a.ads == 'yes' else NO} → {f}")
        elif a.cmd == "fact":
            f = fact(a.file, a.text, a.source, a.year, a.url, a.note)
            print(f"จดข้อเท็จจริงแล้ว: {a.text} ← {a.source} {a.year} → {f}")
        elif a.cmd == "sheet":
            sheet(a.clip, a.out, a.interval, a.force)
        elif a.cmd == "scenes":
            cuts = scenes(a.clip, a.threshold)
            if cuts:
                print("จุดตัดฉากของต้นทาง (วินาที): " + " · ".join(f"{t:.2f}" for t in cuts))
                print("เลือก --start ของช็อตให้อยู่หลังจุดตัดเหล่านี้ ไม่งั้นจะติดภาพของฉากก่อนหน้า")
            else:
                print(f"ไม่พบจุดตัดฉาก (ภาพไม่เปลี่ยนฉากเกินเกณฑ์ {a.threshold:g} ทั้งคลิป) — เริ่มช็อตที่วินาทีไหนก็ได้")
        elif a.cmd == "credits-summary":
            s = credits_summary(a.file)
            if a.json:
                print(json.dumps(s, ensure_ascii=False))
            else:
                print(f"ทั้งหมด {s['total']} ชิ้น · ใช้ยิงแอดได้ {s['ads_ok']} · ห้ามเอาไปยิงแอด {s['ads_not_ok']}")
                if s["not_ok_items"]:
                    print("ห้ามเอาไปยิงแอด (ต้องเปลี่ยนก่อนถ้าจะโปรโมต): " + " · ".join(s["not_ok_items"]))
    except KitError as e:
        print(f"ผิดพลาด: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"ผิดพลาด: อ่านหรือเขียนไฟล์ไม่ได้ ({e.filename or ''}: {e.strerror or e}) — เช็คว่าโฟลเดอร์มีอยู่ ไฟล์ไม่ได้เปิดค้าง และมีสิทธิ์เขียน", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
