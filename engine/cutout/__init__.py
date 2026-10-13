"""Cut the speaker out of a clip → transparent video for compose, CapCut or DaVinci.

    python -m engine.cutout <clip> <out.mov|out.webm> [vision|tb] [--force]
    python -m engine.cutout <photo.png|jpg|webp|heic> <out.png> [vision|tb] [--force]   (still photo → PNG, cropped to the person)

Engines (config.cutout.engine — "auto" = vision on macOS, tb elsewhere when installed, else off):
  vision  Apple Vision person segmentation, accurate quality · ~2 s per 1 s of 720p · no install beyond
          Xcode Command Line Tools (swiftc). Good enough for reels; the default on a Mac.
  tb      transparent-background (InSPyReNet) from the kit venv · ~30 s per 1 s on Apple MPS, faster on
          an NVIDIA GPU, much slower on CPU · use when vision breaks (fingers turn see-through, edges
          fall apart, a mic or prop in the hand disappears) or for production work.
          Do not use its "fast" mode — quality drops visibly.
          Dev/testing only: env TAD_CLIP_TB_PYTHON points tb at another python (a separate venv that already has
          transparent-background + torch) instead of the kit venv. Not user-facing.
Output: .mov = ProRes 4444 (yuva444p10le) for editors · .webm = VP9 with alpha (~1/30 the size; compose cache).
Still photo: .png with real transparency, EXIF rotation applied, cropped to the person (+ a small margin) — the pose image
for mode stage (thv visual.pose), so its height in the plan is the person's height.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

from engine import kit
from engine.kit import IS_MAC, IS_WINDOWS, KitError

HERE = Path(__file__).resolve().parent
SWIFT_SRC = HERE / "vision-matte.swift"
ENGINES = ("vision", "tb")
STILL_EXT = (".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif")   # .heic = iPhone camera default (Mac only, via sips)
STILL_MARGIN = 0.02   # transparent margin kept around the person in a still cut-out (fraction of the cropped size)
HDR_TRANSFERS = ("smpte2084", "arib-std-b67")   # PQ · HLG (iPhone HDR video)
# output is limited-range BT.709 and tagged so (editors and thv compose then read the colours the same way)
TO709 = "scale=out_color_matrix=bt709:out_range=tv"
TAG709 = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]

# transparent-background's own console entry, run through the venv python (no reliance on Scripts/ or bin/ layout)
TB_MAIN = "import sys; from transparent_background import console; sys.exit(console())"
TB_DEVICE = ("import sys, torch; "
             "print('cuda' if torch.cuda.is_available() else "
             "'mps' if sys.platform == 'darwin' and torch.backends.mps.is_available() else 'cpu')")


# ---------------------------------------------------------------- which engine

def tb_python() -> str:
    """Python that runs transparent-background: TAD_CLIP_TB_PYTHON, else the kit venv python."""
    return os.environ.get("TAD_CLIP_TB_PYTHON") or kit.tool("python")


def tb_installed(py: str | None = None) -> bool:
    try:
        py = py or tb_python()
    except KitError:
        return False
    probe = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('transparent_background') else 1)"
    try:
        return subprocess.run([py, "-c", probe], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def resolve_engine(requested: str | None = None, cfg: dict | None = None) -> str:
    """vision | tb | off. A CLI argument beats config.cutout.engine; "none" means off."""
    if requested is None:
        cfg = kit.load_config() if cfg is None else cfg
        requested = (cfg.get("cutout") or {}).get("engine")
    eng = str(requested or "auto").strip().lower()
    if eng in ("off", "none"):
        return "off"
    if eng == "auto":
        if IS_MAC:
            return "vision"
        return "tb" if tb_installed() else "off"
    if eng not in ENGINES:
        raise KitError(f'ไม่รู้จักตัวตัดพื้นหลัง "{requested}" — ใช้ได้: vision (Mac) · tb (transparent-background) · off')
    return eng


# ---------------------------------------------------------------- helpers

def is_still(p) -> bool:
    return Path(p).suffix.lower() in STILL_EXT


def encoder_args(out: Path) -> list:
    ext = Path(out).suffix.lower()
    if ext == ".png":
        raise KitError("ไฟล์ปลายทาง .png ใช้กับรูปนิ่งเท่านั้น — คลิปวิดีโอต้องลงท้าย .mov หรือ .webm")
    if ext == ".mov":    # ProRes 4444 for CapCut / DaVinci
        return ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-an"]
    if ext == ".webm":   # small VP9 alpha (thv compose cache)
        return ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-crf", "18", "-b:v", "0", "-row-mt", "1", "-cpu-used", "4", "-an"]
    raise KitError(f"ไฟล์ปลายทางต้องลงท้าย .mov (ProRes 4444 พื้นใส) หรือ .webm (VP9 พื้นใส) — ได้ {Path(out).name}")


def needs_compile(bin_: Path, src: Path) -> bool:
    return not bin_.exists() or src.stat().st_mtime > bin_.stat().st_mtime


def probe(src: Path) -> dict:
    out = kit.run([kit.tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", str(src)]).stdout
    streams = json.loads(out).get("streams") or []
    if not streams:
        raise KitError(f"ไม่พบภาพวิดีโอในไฟล์ {Path(src).name}")
    st = streams[0]
    rot = 0
    for sd in st.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = rot or int(float((st.get("tags") or {}).get("rotate", 0) or 0))
    st["rotation"] = rot
    return st


def _rate(info: dict) -> str:
    for key in ("avg_frame_rate", "r_frame_rate"):
        r = info.get(key) or "0/0"
        try:
            if Fraction(r) > 0:
                return r
        except (ValueError, ZeroDivisionError):
            pass
    return "30/1"


def needs_normalize(info: dict) -> bool:
    """Rotated, variable-frame-rate, not 8-bit 4:2:0 or HDR → re-encode first, so the matte lines up frame by frame."""
    try:
        vfr = Fraction(info.get("r_frame_rate") or "0/1") != Fraction(info.get("avg_frame_rate") or "0/1")
    except (ValueError, ZeroDivisionError):
        vfr = True
    return bool(info.get("rotation")) or vfr or info.get("pix_fmt") not in ("yuv420p", "yuvj420p") \
        or info.get("color_transfer") in HDR_TRANSFERS


def sdr_chain(info: dict) -> str:
    """ffmpeg filter chain ending in 8-bit SDR yuv420p (tone-maps PQ / HLG with zscale)."""
    trc = info.get("color_transfer")
    if trc in HDR_TRANSFERS:
        pin, mat = info.get("color_primaries") or "bt2020", info.get("color_space") or "bt2020nc"
        return (f"zscale=tin={trc}:pin={pin}:min={mat}:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    return "format=yuv420p"


def normalize(src: Path, dst: Path, info: dict) -> Path:
    """Constant frame rate, upright, 8-bit SDR H.264 copy (ffmpeg applies the rotation tag while decoding)."""
    kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-an", "-vf", f"fps={_rate(info)},{sdr_chain(info)},{TO709}",
             "-c:v", "libx264", "-crf", "12", "-preset", "veryfast", *TAG709, str(dst)])
    return dst


def _alphamerge(color: Path, matte_input: list, rate: str, out: Path, **popen_kw) -> list:
    """ffmpeg command: input 0 = matte (gray), input 1 = colour. Frames pair by index (setpts=N) — robust to edit lists."""
    pts = f"setpts=N/({rate})/TB"
    return [kit.tool("ffmpeg"), "-v", "error", "-y", *matte_input, "-i", str(color),
            "-filter_complex", f"[0:v]{pts},format=gray[m];[1:v]{pts},format=rgba[c];[c][m]alphamerge,{TO709}",
            *encoder_args(out), *TAG709, str(out)]


# ---------------------------------------------------------------- vision (macOS)

def swiftc_path() -> str | None:
    """The real Swift compiler, or None. /usr/bin/swiftc is a shim present on every Mac even without the
    Xcode Command Line Tools, so `shutil.which` proves nothing — `xcrun --find swiftc` fails when they are missing."""
    try:
        r = subprocess.run(["xcrun", "--find", "swiftc"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = (r.stdout or "").strip().splitlines()
    return found[-1] if r.returncode == 0 and found else None


def vision_bin(still: bool = False) -> Path:
    """The compiled helper. still=True needs a build of the current source (older builds cannot read photos);
    for video an older build still works, so a Mac that lost its compiler (a macOS upgrade often does) keeps cutting."""
    if not IS_MAC:
        raise KitError("ตัวตัดพื้นหลัง vision ใช้ได้เฉพาะ Mac — บน Windows ใช้ tb (ติดตั้งเพิ่มด้วย setup --with-cutout) หรือใช้ layout split แทน")
    bin_ = kit.cache_dir() / "vision-matte"
    if needs_compile(bin_, SWIFT_SRC):
        swiftc = swiftc_path()
        if not swiftc and bin_.exists() and not still:
            print("⚠️  คอมไพล์ตัวตัดพื้น Apple Vision รุ่นใหม่ไม่ได้ (ไม่มี Xcode Command Line Tools) — ใช้รุ่นเดิมไปก่อน "
                  "(เฟรมแรกของแต่ละช็อตอาจไม่มีคน) · แก้: xcode-select --install", file=sys.stderr)
            return bin_
        if not swiftc:
            raise KitError("ตัดพื้นหลังด้วย Apple Vision ต้องมี Xcode Command Line Tools — เปิด Terminal แล้วพิมพ์: xcode-select --install "
                           "(กดติดตั้งในหน้าต่างที่เด้งขึ้น แล้วรันใหม่) · หรือใช้ layout split แทน")
        tmp = bin_.with_name(f"vision-matte.build-{os.getpid()}")
        # compile through xcrun: it sets SDKROOT — the compiler run by its absolute path cannot find the Swift stdlib
        kit.run(["xcrun", "swiftc", "-O", str(SWIFT_SRC), "-o", str(tmp)])
        os.replace(tmp, bin_)   # atomic: two renders compiling at once never see a half-written binary
    return bin_


def _vision(src: Path, out: Path, info: dict) -> None:
    w, h, rate = info["width"], info["height"], _rate(info)
    # both stderr streams go to temp files, never to pipes read after the fact: a tool that logs more than the
    # ~64 KB pipe buffer would block on stderr while ffmpeg waits for its stdout → deadlock
    with tempfile.TemporaryFile() as v_log, tempfile.TemporaryFile() as ff_log:
        matte = subprocess.Popen([str(vision_bin()), str(src), "accurate"], stdout=subprocess.PIPE, stderr=v_log)
        cmd = _alphamerge(src, ["-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", rate, "-i", "-"], rate, out)
        ff = subprocess.Popen(cmd, stdin=matte.stdout, stderr=ff_log)
        matte.stdout.close()   # ffmpeg owns the pipe now; vision gets SIGPIPE if ffmpeg dies
        ff.wait()
        matte.wait()
        tail = lambda f: (f.seek(0), f.read()[-2000:].decode("utf-8", "replace"))[1]
        if matte.returncode != 0:
            raise KitError("Apple Vision ตัดพื้นหลังไม่สำเร็จ\n" + tail(v_log))
        if ff.returncode != 0:
            raise KitError("ffmpeg รวมพื้นใสไม่สำเร็จ\n" + tail(ff_log))


# ---------------------------------------------------------------- tb (transparent-background)

TB_CACHE_KEYS = ("TRANSPARENT_BACKGROUND_FILE_PATH", "TORCH_HOME", "HF_HOME")


def tb_env() -> dict:
    """Environment for transparent-background: the same model/cache folders `setup --with-cutout` downloaded into
    (engine.install.setup.kit_dirs — weights in <home>/.transparent-background, torch + Hugging Face under <home>/cache),
    so a run never fetches a second copy outside the kit home. Folders are created if missing."""
    from engine.install.setup import kit_dirs   # one source of truth for those paths; imported late (setup is big)
    dirs = {k: v for k, v in kit_dirs().items() if k in TB_CACHE_KEYS}
    for d in dirs.values():
        Path(d).mkdir(parents=True, exist_ok=True)
    return {**os.environ, **dirs, "PYTORCH_ENABLE_MPS_FALLBACK": "1", "PYTHONUTF8": "1"}


def tb_device(py: str) -> str:
    """cuda if available, else mps on a Mac, else cpu."""
    return kit.run([py, "-c", TB_DEVICE]).stdout.strip().splitlines()[-1]


def _tb(src: Path, out: Path, info: dict, work: Path) -> None:
    py = tb_python()
    if not tb_installed(py):
        raise KitError("ยังไม่ได้ติดตั้ง transparent-background — รัน setup ใหม่พร้อม --with-cutout หรือใช้ engine vision (Mac) / layout split")
    dev = tb_device(py)
    # OpenCV on Windows cannot open non-ASCII paths → run inside the work dir with a plain relative name
    plain = work / "src.mp4"
    if src != plain:
        shutil.copyfile(src, plain)
    kit.run([py, "-c", TB_MAIN, "--source", "src.mp4", "--dest", ".", "--type", "map", "--device", dev], cwd=str(work), env=tb_env())
    maps = sorted(work.glob("src_map.*"))
    if not maps:
        raise KitError("transparent-background ไม่ได้สร้างไฟล์ matte (src_map.*)")
    rate = _rate(info)
    kit.run(_alphamerge(plain, ["-i", str(maps[0])], rate, out))


# ---------------------------------------------------------------- still photo → transparent PNG

def _still_matte_vision(src: Path, w: int, h: int):
    from PIL import Image
    r = subprocess.run([str(vision_bin(still=True)), str(src), "accurate"], capture_output=True)
    if r.returncode != 0 or len(r.stdout) != w * h:
        raise KitError("Apple Vision ตัดพื้นหลังรูปไม่สำเร็จ\n" + r.stderr.decode("utf-8", "replace")[-2000:])
    return Image.frombytes("L", (w, h), r.stdout)


def _still_matte_tb(src: Path, w: int, h: int, work: Path):
    from PIL import Image
    py = tb_python()
    if not tb_installed(py):
        raise KitError("ยังไม่ได้ติดตั้ง transparent-background — รัน setup ใหม่พร้อม --with-cutout หรือใช้ engine vision (Mac)")
    # plain relative name inside the work dir (OpenCV on Windows cannot open non-ASCII paths)
    kit.run([py, "-c", TB_MAIN, "--source", src.name, "--dest", ".", "--type", "map", "--device", tb_device(py)], cwd=str(work), env=tb_env())
    maps = sorted(work.glob(f"{src.stem}_map.*"))
    if not maps:
        raise KitError("transparent-background ไม่ได้สร้างไฟล์ matte (*_map.*)")
    with Image.open(maps[0]) as m:
        return m.convert("L").resize((w, h))


def cutout_still(src, out, engine: str) -> dict:
    """Photo of a person → `out` .png: the person on a transparent background, cropped to them. Returns {width, height}."""
    from PIL import Image, ImageOps
    src, out = Path(src), Path(out)
    if out.suffix.lower() != ".png":
        raise KitError(f"รูปนิ่งต้องออกเป็น .png (พื้นใส) — ได้ {out.name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tadclip-cutout-") as td:
        work = Path(td)
        if src.suffix.lower() in (".heic", ".heif"):   # Pillow cannot read HEIC · macOS sips can (keeps the EXIF rotation tag)
            if not IS_MAC:
                raise KitError(f"อ่านรูป HEIC ({src.name}) ได้เฉพาะบน Mac — แปลงเป็น .jpg ก่อน (เปิดรูปแล้วบันทึกเป็น JPG) แล้วรันใหม่")
            heic = work / "heic.png"
            kit.run(["sips", "-s", "format", "png", str(src), "--out", str(heic)])
            src_img = heic
        else:
            src_img = src
        with Image.open(src_img) as im:
            rgb = ImageOps.exif_transpose(im).convert("RGB")   # phone photos are stored sideways + an EXIF rotation tag
        plain = work / "src.png"
        rgb.save(plain)
        w, h = rgb.size
        if engine == "vision":
            matte = _still_matte_vision(plain, w, h)
        elif engine == "tb":
            matte = _still_matte_tb(plain, w, h, work)
        else:
            raise KitError("เครื่องนี้ยังไม่มีตัวตัดพื้นหลัง (cutout.engine = off) — Mac ใช้ vision ได้เลย · Windows รัน setup พร้อม --with-cutout")
        box = matte.point(lambda v: 255 if v > 24 else 0).getbbox()
        if not box:
            raise KitError(f"ไม่เจอคนในรูป {src.name} — ใช้รูปที่เห็นคนชัดๆ (ครึ่งตัวหรือเต็มตัว พื้นหลังไม่รก)")
        rgba = rgb.copy()
        rgba.putalpha(matte)
        mx, my = round((box[2] - box[0]) * STILL_MARGIN), round((box[3] - box[1]) * STILL_MARGIN)
        rgba = rgba.crop((max(0, box[0] - mx), max(0, box[1] - my), min(w, box[2] + mx), min(h, box[3] + my)))
        tmp_out = work / "out.png"
        rgba.save(tmp_out)
        shutil.move(str(tmp_out), out)
    return {"width": rgba.width, "height": rgba.height}


# ---------------------------------------------------------------- entry

def cutout(src, out, engine: str) -> dict:
    """Cut the speaker out of `src` into `out` (.mov / .webm) with `engine` (vision | tb). Returns the probe info.
    A still photo (.png/.jpg/.webp) goes to cutout_still → .png."""
    src, out = Path(src), Path(out)
    if not src.exists():
        raise KitError(f"ไม่เจอไฟล์: {src}")
    if is_still(src):
        return cutout_still(src, out, engine)
    encoder_args(out)   # fail fast on a wrong extension
    out.parent.mkdir(parents=True, exist_ok=True)
    info = probe(src)
    with tempfile.TemporaryDirectory(prefix="tadclip-cutout-") as td:
        work = Path(td)
        if needs_normalize(info) or engine == "tb":
            src = normalize(src, work / "src.mp4", info)
            info = probe(src)
        tmp_out = work / f"out{out.suffix.lower()}"
        if engine == "vision":
            _vision(src, tmp_out, info)
        elif engine == "tb":
            _tb(src, tmp_out, info, work)
        else:
            raise KitError("เครื่องนี้ยังไม่มีตัวตัดพื้นหลัง (cutout.engine = off) — ใช้ layout split แทน (ภาพครึ่งบน ผู้พูดครึ่งล่าง)")
        shutil.move(str(tmp_out), out)   # only a finished file ever appears at `out`
    return info


def main(argv=None) -> int:
    import argparse
    for stream in (sys.stdout, sys.stderr):   # Thai on a Windows console
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m engine.cutout",
                                 description="ตัดพื้นหลังคนในคลิป → .mov (ProRes 4444) หรือ .webm (VP9) พื้นใส · รูปนิ่ง → .png พื้นใส")
    ap.add_argument("clip", help="คลิป หรือรูปนิ่ง (.png .jpg .webp · .heic บน Mac)")
    ap.add_argument("out", help=".mov หรือ .webm (คลิป) · .png (รูปนิ่ง)")
    ap.add_argument("engine", nargs="?", default=None, help="vision | tb (ไม่ใส่ = ตาม config.cutout.engine)")
    ap.add_argument("--force", action="store_true", help="ทำใหม่แม้มีไฟล์ปลายทางแล้ว")
    a = ap.parse_args(argv)
    clip, out = Path(a.clip), Path(a.out)
    try:
        if out.exists() and not a.force and clip.exists() and out.stat().st_mtime >= clip.stat().st_mtime:
            print(f"ข้าม — มี {out.name} แล้ว (ใส่ --force เพื่อทำใหม่)")
            return 0
        engine = resolve_engine(a.engine)
        if engine == "off":
            raise KitError("เครื่องนี้ยังไม่มีตัวตัดพื้นหลัง (cutout.engine = off) — ใช้ layout split แทน (ภาพครึ่งบน ผู้พูดครึ่งล่าง) "
                           "หรือติดตั้งเพิ่ม: Mac ใช้ vision ได้เลย · Windows รัน setup พร้อม --with-cutout")
        info = cutout(clip, out, engine)
        print(f"✓ {out} ({engine} · {info['width']}x{info['height']})")
        return 0
    except KitError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2
