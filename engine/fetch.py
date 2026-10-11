"""fetch — เอาของมาใส่คลิป: คลิป (yt-dlp) · รูป · ค้น Wikimedia Commons · GIF → mp4 · จดลง CREDITS.md

  python -m engine.fetch video <url> --out DIR [--start S --end E] [--max-height 1080] [--no-audio] [--force]
  python -m engine.fetch image <url> --out DIR [--force]
  python -m engine.fetch commons "<คำค้น>" [--limit 8] [--type any|image|video|audio]
  python -m engine.fetch ytsearch "<คำค้น>" [--limit 8]
  python -m engine.fetch gif2clip <in.gif> <out.mp4> [--size 1080x960] [--min-dur 2] [--force]
  python -m engine.fetch credit --file CREDITS.md --item NAME --url URL --license L --ads yes|no [--note N]
  python -m engine.fetch credits-summary --file CREDITS.md [--json]

ไม่ต้องใช้ API key · ทุกคำสั่งรับ path ภาษาไทย/มีช่องว่าง · ไฟล์ปลายทางมีอยู่แล้วจะข้าม (ใส่ --force เพื่อทำใหม่)
"""
import argparse
import html
import json
import math
import os
import re
import shutil
import sys
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


# ---------- image ----------

def image(url: str, out_dir, force: bool = False) -> Path:
    _require_http(url)
    out_dir = Path(out_dir)
    name = _safe_name(urllib.parse.unquote(Path(urllib.parse.urlsplit(url).path).name), "image")
    known = Path(name).suffix.lower() in _KNOWN_IMAGE_SUFFIX
    if known and (out_dir / name).exists() and not force:
        print(f"ข้าม: มี {name} อยู่แล้ว (ใส่ --force เพื่อโหลดใหม่)", file=sys.stderr)
        print(out_dir / name)
        return out_dir / name
    ctype, data = _http_get(url)
    mime = ctype.split(";")[0].strip().lower()
    if not mime.startswith("image/") and not (mime in ("application/octet-stream", "binary/octet-stream", "") and known):
        raise KitError("ลิงก์นี้เป็นหน้าเว็บ ไม่ใช่ไฟล์รูป — เปิดหน้านั้นแล้วหาลิงก์ไฟล์รูปตรงๆ (มักลงท้าย .jpg .png) หรือให้ผู้ใช้กดดาวน์โหลดเอง")
    if not known:
        name += _IMAGE_EXT.get(mime, ".jpg")
    dst = out_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not force:
        print(dst)
        return dst
    tmp = _atomic_name(dst)
    tmp.write_bytes(data)
    tmp.replace(dst)
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


def video(url: str, out_dir, start=None, end=None, max_height: int = 1080, audio: bool = True, force: bool = False) -> Path:
    _require_http(url)
    if start is not None and start < 0:
        raise KitError("--start ต้องไม่ติดลบ")
    if start is not None and end is not None and end <= start:
        raise KitError("--start ต้องน้อยกว่า --end")
    out_dir = Path(out_dir)
    info = _ytdlp_info(url)
    duration = float(info.get("duration") or 0)
    if start is not None and duration and start >= duration:
        raise KitError(f"--start {start:g} เลยความยาวคลิป ({duration:.1f} วินาที)")
    trim = start is not None or end is not None
    stem = _safe_name(str(info.get("id") or "video"), "video")
    if trim:
        stem += f"_{(start or 0):g}-{end:g}" if end is not None else f"_{(start or 0):g}-end"
    dst = out_dir / f"{stem}.mp4"
    if dst.exists() and not force:
        print(f"ข้าม: มี {dst.name} อยู่แล้ว (ใส่ --force เพื่อโหลดใหม่)", file=sys.stderr)
        print(dst)
        return dst

    work = kit.cache_dir() / "fetch" / uuid.uuid4().hex[:12]
    try:
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
    print(f"ที่มา: {info.get('title') or '-'} · ช่อง: {info.get('uploader') or '-'} · ไลเซนส์ที่เว็บระบุ: {info.get('license') or 'ไม่ระบุ'} · {info.get('webpage_url') or url}",
          file=sys.stderr)
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
    cells = _split_row(line)
    return len(cells) >= 4 and cells[0] != "ชิ้น"


def _credit_block(lines: list):
    """(first_row, end) of the credits table body — other tables in the same file (the fact-check table) are not part of it."""
    for i, ln in enumerate(lines):
        if ln.strip() == CREDIT_HEAD:
            j = i + 1
            if j < len(lines) and re.match(r"^\|\s*-{2,}", lines[j].strip()):
                j += 1
            start = j
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            return start, j
    return None


def credit(file, item: str, url: str, license_name: str, ads_ok: bool, note: str = "") -> Path:
    """Add one row to the credits table of CREDITS.md (header written once). Same item+url again -> that row is replaced."""
    f = Path(file)
    row = f"| {_cell(item)} | {_cell(url)} | {_cell(license_name)} | {YES if ads_ok else NO} | {_cell(note)} |"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        lines = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
        if _credit_block(lines) is None:
            if lines and lines[-1].strip():
                lines.append("")
            elif not lines:
                lines += [CREDIT_TITLE, "", CREDIT_INTRO, ""]
            lines += [CREDIT_HEAD, CREDIT_SEP]
        start, end = _credit_block(lines)
        key = (item.strip(), url.strip())
        for i in range(start, end):
            if _is_data_row(lines[i]) and tuple(_split_row(lines[i])[:2]) == key:
                lines[i] = row
                break
        else:
            lines.insert(end, row)
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        raise KitError(f"เขียน {f.name} ไม่ได้ ({e.strerror or e}) — เช็คว่าโฟลเดอร์ {f.parent} มีอยู่ ไฟล์ไม่ได้เปิดค้างในโปรแกรมอื่น และมีสิทธิ์เขียน") from None
    return f


def credits_summary(file) -> dict:
    f = Path(file)
    if not f.exists():
        raise KitError(f"ไม่พบ {f}")
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise KitError(f"อ่าน {f.name} ไม่ได้ ({e.strerror or e})") from None
    block = _credit_block(lines)
    rows = [_split_row(ln) for ln in lines[block[0]:block[1]] if _is_data_row(ln)] if block else []
    bad = [r[0] for r in rows if r[3] != YES]
    return {"total": len(rows), "ads_ok": len(rows) - len(bad), "ads_not_ok": len(bad), "not_ok_items": bad}


# ---------- CLI ----------

def _size(s: str) -> tuple:
    m = re.fullmatch(r"(\d+)[xX](\d+)", s)
    if not m:
        raise argparse.ArgumentTypeError("ขนาดต้องเป็นรูปแบบ 1080x960")
    return int(m.group(1)), int(m.group(2))


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
    i = sub.add_parser("image", help="โหลดรูปจากลิงก์ไฟล์ตรงๆ")
    i.add_argument("url")
    i.add_argument("--out", required=True)
    i.add_argument("--force", action="store_true")
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
            video(a.url, a.out, a.start, a.end, a.max_height, not a.no_audio, a.force)
        elif a.cmd == "image":
            image(a.url, a.out, a.force)
        elif a.cmd == "commons":
            print(json.dumps(commons(a.query, a.limit, a.type), ensure_ascii=False, indent=2))
        elif a.cmd == "ytsearch":
            print(json.dumps(ytsearch(a.query, a.limit), ensure_ascii=False, indent=2))
        elif a.cmd == "gif2clip":
            gif2clip(a.src, a.out, a.size, a.min_dur, force=a.force)
        elif a.cmd == "credit":
            f = credit(a.file, a.item, a.url, a.license, a.ads == "yes", a.note)
            print(f"จดแล้ว: {a.item} · ใช้ยิงแอดได้: {YES if a.ads == 'yes' else NO} → {f}")
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
