"""End-to-end smoke test: the Thai-named fixture clip → final.mp4, through every engine CLI exactly as a skill runs them.

  python tests/smoke/smoke.py [--work DIR] [--artifacts DIR] [--keep]

fixture → roughcut phrases + render (drops chosen by time from tests/fixtures/README.md) → subs + finalize (no edits)
→ thv.mjs check/all/motion/compose/subs (title card, hook pill, and on macOS a real cut-out) → sfx_mix with beep.wav
→ doctor --qc final.mp4 + a burned-subtitle check. The work folder name has Thai characters and spaces on purpose.

Exit 0 = pass (or SKIP when another task's engine files are not in this checkout yet), 1 = fail.
Uses whatever TAD_CLIP_HOME points at (CI: a fresh `setup.py --yes --model base`).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from engine import kit  # noqa: E402

HERE = Path(__file__).resolve().parent
FIXTURE = ROOT / "tests" / "fixtures" / "คลิป ทดสอบ.mp4"
BEEP = ROOT / "tests" / "fixtures" / "beep.wav"
NEEDS = {  # engine piece → owning task, for the SKIP message
    "engine/roughcut.py": "Task 1", "engine/asr.py": "Task 1", "engine/subs/__main__.py": "Task 2",
    "engine/visual/thv.mjs": "Task 3", "engine/cutout/__main__.py": "Task 3", "engine/sfx_mix.py": "Task 4",
    "engine/doctor.py": "Task 5",
}


class SmokeFail(Exception):
    pass


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(title: str) -> None:
    say(f"\n=== {title} ===")


def run(cmd: list, cwd: Path = ROOT, capture: bool = False, check: bool = True) -> str:
    cmd = [str(c) for c in cmd]
    say("$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    t = time.time()
    res = subprocess.run(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL,
                         **({"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"} if capture else {}))
    if capture and res.stdout:
        say(res.stdout.rstrip())
    if capture and res.stderr:
        say(res.stderr.rstrip())
    if check and res.returncode != 0:
        raise SmokeFail(f"exit {res.returncode}: {' '.join(cmd[:4])} …")
    say(f"  ({time.time() - t:.1f} s)")
    return res.stdout if capture else ""


def probe_duration(video: Path) -> float:
    out = kit.run([kit.tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video]).stdout
    return float(out.strip())


def choose_drops(phrases: list, ranges: list) -> list:
    """Phrase numbers whose midpoint lies inside one of the README's drop ranges."""
    out = []
    for p in phrases:
        mid = (float(p["s"]) + float(p["e"])) / 2
        if any(a <= mid <= b for a, b in ranges):
            out.append(p["i"])
    return out


def build_plan(template: dict, end: float, cutout: bool) -> dict:
    plan = json.loads(json.dumps(template))
    shots = []
    for s in plan["shots"]:
        if s.pop("mac_only", False) and not cutout:   # no cut-out engine here (Windows default, or no Vision) → plain speaker shot
            s = {"id": s["id"], "t": s["t"], "layout": "full-speaker", "say": s["say"], "overlays": s.get("overlays", [])}
        shots.append(s)
    if end < shots[-1]["t"][0] + 0.5:
        raise SmokeFail(f"rough cut is only {end:.2f} s — too short for the plan (last shot starts at {shots[-1]['t'][0]} s)")
    shots[-1]["t"][1] = end
    plan["shots"] = shots
    return plan


def first_cue_mid(srt: Path, after: float) -> float:
    """Midpoint (s) of the first subtitle cue that starts after `after` s."""
    ts = re.findall(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", srt.read_text(encoding="utf-8"))
    for g in ts:
        a = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000
        b = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7]) / 1000
        if a >= after:
            return (a + b) / 2
    raise SmokeFail(f"no subtitle cue after {after} s in {srt.name}")


def text_pixels(before: Path, after: Path, t: float) -> float:
    """% of pixels in the lower half that changed strongly between two renders at time t (subtitles burned = text appeared)."""
    ff = kit.tool("ffmpeg")
    fc = ("[0:v][1:v]blend=all_mode=difference,crop=iw:ih/2:0:ih/2,format=gray,"
          "lutyuv=y='if(gt(val,60),255,0)',signalstats,metadata=print:key=lavfi.signalstats.YAVG")
    log = kit.run([ff, "-hide_banner", "-ss", f"{t:.3f}", "-i", before, "-ss", f"{t:.3f}", "-i", after,
                   "-filter_complex", fc, "-frames:v", "1", "-f", "null", "-"]).stderr
    m = re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", log)
    if not m:
        raise SmokeFail("could not measure the subtitle difference")
    return float(m[-1]) / 255 * 100


def frames(video: Path, times: dict, dest: Path) -> list:
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for name, t in times.items():
        png = dest / f"frame-{name}.png"
        kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", png])
        out.append(png)
    return out


def vision_runs(py: Path, src: Path, work: Path) -> bool:
    """Apple Vision needs a GPU / Neural Engine; GitHub's macOS VMs have neither ("Could not create inference
    context"). Any other cut-out failure is a real bug and fails the smoke."""
    seg, out = work / "vision-probe.mp4", work / "vision-probe.webm"
    kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-i", src, "-t", "0.5", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", seg])
    res = subprocess.run([str(py), "-m", "engine.cutout", str(seg), str(out), "vision"], cwd=str(ROOT), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    for f in (seg, out):
        if f.exists():
            f.unlink()
    if res.returncode == 0:
        return True
    log = (res.stdout or "") + (res.stderr or "")
    if "Could not create inference context" in log:
        return False
    raise SmokeFail(f"Apple Vision cut-out probe failed: {log.strip()[-400:]}")


def missing_pieces() -> list:
    return [f"{p} ({task})" for p, task in NEEDS.items() if not (ROOT / p).exists()]


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", help="folder to work in (default: a new temp folder)")
    ap.add_argument("--artifacts", help="copy final.mp4 + frames + storyboard here")
    ap.add_argument("--keep", action="store_true", help="keep the work folder")
    a = ap.parse_args(argv)

    gone = missing_pieces()
    if gone:
        say("SKIP smoke: this checkout does not have every engine piece yet — " + " · ".join(gone))
        say("(the smoke runs in full once all tasks are merged)")
        return 0

    mac = kit.IS_MAC
    py = kit.venv_python()
    if not py.exists():
        say(f"FAIL: no venv at {py} — run engine/install/setup.py first")
        return 1
    node = kit.tool("node")
    thv = ROOT / "engine" / "visual" / "thv.mjs"

    base = Path(a.work) if a.work else Path(tempfile.mkdtemp(prefix="tadclip-smoke-"))
    root = base / "งาน ทดสอบ"
    clipdir = root / "tad-clip-คลิป ทดสอบ"
    t0 = time.time()
    try:
        if root.exists():
            shutil.rmtree(root)
        clipdir.mkdir(parents=True)
        video = root / FIXTURE.name
        shutil.copy2(FIXTURE, video)
        say(f"smoke · {'macOS' if mac else 'Windows' if kit.IS_WINDOWS else 'other'} · home {kit.home()} · work {clipdir}")

        step("1. rough cut")
        run([py, "-m", "engine.roughcut", "phrases", video, "--out", clipdir / "phrases.json"])
        phrases = json.loads((clipdir / "phrases.json").read_text(encoding="utf-8"))
        spec = json.loads((HERE / "drops.json").read_text(encoding="utf-8"))
        drop = choose_drops(phrases, spec["drop_times"])
        if sorted(drop) != sorted(spec["drop"]):
            say(f"note: phrases here give drop {drop} (macOS reference {spec['drop']}) — using {drop}")
        if not drop:
            raise SmokeFail("no phrase falls inside the README's drop times — phrase detection changed?")
        (clipdir / "drops.json").write_text(json.dumps({"drop": drop, "gap": spec["gap"]}), encoding="utf-8")
        rc = clipdir / "roughcut.mp4"
        run([py, "-m", "engine.roughcut", "render", video, clipdir / "drops.json", rc])
        raw_dur, rc_dur = probe_duration(video), probe_duration(rc)
        say(f"rough cut {rc_dur:.2f} s from {raw_dur:.2f} s (dropped phrases {drop})")
        if not rc_dur < raw_dur - 2:
            raise SmokeFail(f"rough cut {rc_dur:.2f} s did not remove the ~4 s of retakes from {raw_dur:.2f} s")

        step("2. subtitles")
        run([py, "-m", "engine.subs", rc, "-o", clipdir / "subs"])
        run([py, "-m", "engine.subs", "finalize", clipdir / "subs"])
        kass = [p for p in (clipdir / "subs").glob("*.karaoke.ass") if p.name != "raw.karaoke.ass"]
        srt = [p for p in (clipdir / "subs").glob("*.srt") if p.name != "raw.srt"]
        if not kass or not srt:
            raise SmokeFail("finalize did not write <name>.karaoke.ass + <name>.srt")

        step("3. visuals")
        cutout = mac and vision_runs(py, rc, clipdir)
        if mac and not cutout:
            say("NOTE: Apple Vision cannot run on this machine (no GPU / Neural Engine — e.g. a CI virtual machine) "
                "→ the cut-out shot is rendered as a plain speaker shot")
        end = math.floor(rc_dur * 30) / 30
        plan = build_plan(json.loads((HERE / "plan.json").read_text(encoding="utf-8")), end, cutout)
        plan["subs"] = kass[0].relative_to(clipdir).as_posix()
        (clipdir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (clipdir / "assets" / "ai").mkdir(parents=True)
        kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                 "color=c=0x334155:s=1024x1536,drawgrid=w=128:h=128:t=6:c=0xf97316", "-frames:v", "1",
                 clipdir / "assets" / "ai" / "bg.png"])
        pj = clipdir / "plan.json"
        for cmd in ("check", "all", "motion", "compose", "subs"):
            run([node, thv, cmd, pj])
        out = clipdir / "out"
        comp, comp_subs = out / "preview-composite.mp4", out / "preview-composite-subs.mp4"

        step("4. sound effects")
        shutil.copy2(HERE / "sfx.json", clipdir / "sfx.json")
        if BEEP.exists():
            shutil.copy2(BEEP, clipdir / "beep.wav")
        else:   # Task 4 owns the fixture; make an identical one if this checkout lacks it
            kit.run([kit.tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
                     clipdir / "beep.wav"])
        final = clipdir / "final.mp4"
        run([py, "-m", "engine.sfx_mix", comp_subs, clipdir / "sfx.json", final])

        step("5. QC")
        report = json.loads(run([py, ROOT / "engine" / "doctor.py", "--qc", final, "--expect-duration", f"{end:.3f}", "--json"],
                                capture=True, check=False) or "{}")
        problems = []
        for c in report.get("checks", []):
            say(f"  {c['status']:4}  {c['name']}: {c['detail']}")
            if c["status"] == "fail":
                problems.append(f"QC {c['id']}: {c['detail']}")
        cue = first_cue_mid(srt[0], 0.5)
        pct = text_pixels(comp, comp_subs, cue)
        say(f"  subtitle pixels changed at {cue:.2f} s: {pct:.2f}% of the lower half")
        if pct < 0.3:
            problems.append(f"subtitles do not look burned in ({pct:.2f}% changed at {cue:.2f} s)")
        shots = {s["id"]: s["t"] for s in plan["shots"]}
        grabs = frames(final, {f"{i}-{plan['shots'][n]['layout']}": (shots[i][0] + shots[i][1]) / 2
                               for n, i in enumerate(["01", "02", "03"])}, clipdir / "frames")
        if a.artifacts:
            art = Path(a.artifacts)
            art.mkdir(parents=True, exist_ok=True)
            for f in [final, out / "storyboard.png", *grabs]:
                if f.exists():
                    shutil.copy2(f, art / f.name)
        if problems:
            raise SmokeFail("; ".join(problems))
        say(f"\nPASS smoke in {time.time() - t0:.0f} s · {final}")
        return 0
    except (SmokeFail, kit.KitError) as e:
        say(f"\nFAIL smoke: {e}")
        return 1
    finally:
        if not a.keep and not a.work:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
