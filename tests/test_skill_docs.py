"""What the orchestrator skill and the user docs promise, pinned (final fix wave): QC for clips the user cut, the
re-cut order, the Auto permission hint, Git Bash wording, install time, every bash block's first line."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREAMBLE = 'K="${CLAUDE_PLUGIN_ROOT}"; [ -f "$K/engine/env.sh" ] || K="$HOME/.tad-clip/kit"; . "$K/engine/env.sh"'
SKILL = ROOT / "skills" / "tad-clip" / "SKILL.md"
QC = ROOT / "skills" / "tad-clip" / "references" / "qc.md"
SETUP = ROOT / "skills" / "tad-clip-setup" / "SKILL.md"


def read(p):
    return Path(p).read_text(encoding="utf-8")


def section(text, start, end):
    return text[text.index(start):text.index(end) if end else None]


def test_every_bash_block_of_the_orchestrator_starts_with_the_preamble():
    """tad-clip + its references (the other skills pin their own blocks; tad-clip-setup has pre-install lines)."""
    blocks = 0
    for f in [SKILL, *(ROOT / "skills" / "tad-clip" / "references").glob("*.md")]:
        for indent, body in re.findall(r"^([ \t]*)```bash\n(.*?)^\1```", read(f), re.S | re.M):
            blocks += 1
            assert body.splitlines()[0].strip() == PREAMBLE, f"{f.relative_to(ROOT)}: {body.splitlines()[0][:80]}"
    assert blocks >= 12


def test_qc_of_a_clip_the_user_cut_points_doctor_at_that_file():
    for f in (SKILL, QC, SETUP):
        text = read(f)
        assert "--expect-duration" not in text, f
        assert '--source "<ไฟล์ที่ผู้ใช้ตัดมา>"' in text, f
    assert "--source" in read(ROOT / "engine" / "doctor.py")                     # the flag the guidance names exists


def test_unattended_mode_tells_the_user_once_to_switch_to_auto():
    step1 = section(read(SKILL), "## 1.", "## 2.")
    first_clip = read(ROOT / "docs" / "03-first-clip.md")
    for words in ("ตั้งโหมดสิทธิ์ของแท็บ Code เป็น Auto ก่อน", "ไม่งั้นงานจะค้างรอคุณกดอนุญาตคำสั่งถัดไป"):
        assert words in step1 and words in first_clip                            # same wording as the user guide
    assert "ปุ่มข้างปุ่มส่ง" in step1 and "ปุ่มข้างปุ่มส่ง" in read(ROOT / "docs" / "06-troubleshooting.md")
    assert "ครั้งเดียว" in step1


def test_recut_lists_every_step_to_rerun_in_order():
    recut = section(read(SKILL), "**ตัดใหม่**", None)
    steps = [ln for ln in recut.splitlines() if re.match(r"\d+\. ", ln)]
    want = [("engine.roughcut render", "--force"), ("ทำซับแบบข้อ 4", "review.md", "edits.json"), ("engine.subs finalize",),
            ("plan.json", "sfx.json", "thv.mjs check"), ("thv.mjs motion", "compose", "`subs`"), ("engine.sfx_mix",),
            ("QC ข้อ 9",)]
    assert len(steps) == len(want), steps
    for n, (line, keys) in enumerate(zip(steps, want), 1):
        assert line.startswith(f"{n}. ") and all(k in line for k in keys), (n, line)


def test_git_bash_comes_from_git_for_windows_not_with_claude_code():
    for f in [ROOT / "README.md", *(ROOT / "docs").glob("*.md"), *(ROOT / "skills").glob("**/*.md")]:
        assert not re.search(r"Git Bash (ของ|ที่มากับ) Claude", read(f)), f
    assert "Git Bash จาก Git for Windows" in read(ROOT / "skills" / "tad-clip-subs" / "SKILL.md")


def test_install_time_is_the_same_everywhere():
    for f in ("README.md", "docs/01-install-mac.md", "docs/02-install-windows.md", "skills/tad-clip-setup/SKILL.md"):
        text = read(ROOT / f)
        assert "10–30 นาที" in text, f
        assert not re.search(r"10[–-]20 นาที", text), f


def test_copyright_page_warns_about_people_and_brands_in_stock_footage_for_ads():
    text = read(ROOT / "docs" / "07-copyright.md")
    line = next(ln for ln in text.splitlines() if "หน้าคน" in ln)
    assert "Pexels" in line and "Pixabay" in line and "โลโก้" in line and "แอด" in line
