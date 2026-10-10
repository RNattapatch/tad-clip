"""Tests for engine/subs — Thai karaoke subtitles (cue builder, review.md, edits, finalize, burn)."""
import copy
import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import kit  # noqa: E402
from engine.subs import __main__ as cli  # noqa: E402
from engine.subs import apply_edits, make_subs, resplit  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
MINI = FIX / "asr-mini.json"
VIDEO = FIX / "คลิป ทดสอบ.mp4"

# Resolve ffmpeg once, with the environment as the test runner set it (before tmp_home hides the dev config).
try:
    FFMPEG = kit.tool("ffmpeg")
except kit.KitError:
    FFMPEG = None


def _has_libass():
    if not FFMPEG:
        return False
    out = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return re.search(r"\bsubtitles\b", out) is not None


HAS_LIBASS = _has_libass()


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TAD_CLIP_HOME", str(tmp_path / "ที่เก็บ tad clip"))
    if FFMPEG:
        kit.save_config({"tools": {"ffmpeg": FFMPEG}})
    return tmp_path


def mini():
    return json.loads(MINI.read_text(encoding="utf-8"))


def make_work(tmp_path, name="demo", asr=None, max_chars=22):
    """A work folder that already holds asr.json + raw.* (no ffmpeg / whisper needed)."""
    d = tmp_path / "งาน ซับ" / "subs"
    d.mkdir(parents=True)
    (d / "asr.json").write_text(json.dumps(asr or mini(), ensure_ascii=False), encoding="utf-8")
    cli.build_raw(d, name=name, max_chars=max_chars)
    return d


def raw_cues(d):
    return json.loads((d / "raw.cues.json").read_text(encoding="utf-8"))["cues"]


def write_edits(d, doc):
    (d / "edits.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


# ── constants kept from the original pipeline ──────────────────────────────────────────────

def test_timing_constants_kept():
    assert (make_subs.MINCH, make_subs.GAP, make_subs.MINDUR) == (8, 300, 550)
    assert (make_subs.MAXDUR, make_subs.LEAD, make_subs.TARGET) == (2400, 160, 18)
    assert {"และ", "แล้ว", "ที่", "ซึ่ง", "จะ", "อีก"} <= make_subs.DANGLE


# ── colour ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hexcol,expected", [
    ("#FF7F50", "&H00507FFF"),
    ("ff7f50", "&H00507FFF"),
    ("#000000", "&H00000000"),
    ("#fff", "&H00FFFFFF"),
    ("#0A141E", "&H001E140A"),
])
def test_ass_colour_conversion(hexcol, expected):
    assert make_subs.ass_color(hexcol) == expected


def test_ass_colour_rejects_garbage():
    with pytest.raises(kit.KitError) as e:
        make_subs.ass_color("coral")
    assert "สี" in str(e.value)


# ── per-character times ────────────────────────────────────────────────────────────────────

def _asr_with_tokens(tokens, frm=0, to=None):
    to = to if to is not None else tokens[-1][2]
    return {"transcription": [{"offsets": {"from": frm, "to": to}, "text": "x",
                               "tokens": [{"text": t, "offsets": {"from": f, "to": e}} for t, f, e in tokens]}]}


def test_whole_word_token_gets_spread_per_character():
    chars = make_subs.load_chars(_asr_with_tokens([("สวัสดี", 1000, 1600)]))
    assert "".join(c[0] for c in chars) == "สวัสดี"
    assert chars[0][1] == 1000 and chars[-1][2] == 1600
    starts = [c[1] for c in chars]
    assert starts == sorted(starts)
    # every character sits inside the word's span and the spread is not one lump
    assert all(1000 <= c[1] <= c[2] <= 1600 for c in chars)
    assert len(set(starts)) > 1


def test_whole_word_token_with_leading_space_keeps_word_span():
    chars = make_subs.load_chars(_asr_with_tokens([("ก", 0, 100), (" ผม", 100, 400)]))
    sp = [c for c in chars if c[0] == " "][0]
    assert sp[1] == sp[2] == 100                  # the space takes no time
    pom = [c for c in chars if c[0] in "ผม"]
    assert pom[0][1] == 100 and pom[-1][2] == 400


def test_single_character_and_special_tokens_unchanged():
    chars = make_subs.load_chars(_asr_with_tokens([("[_BEG_]", 0, 0), ("ก", 100, 180), ("ข", 180, 260)]))
    assert [c[:3] for c in chars] == [["ก", 100, 180], ["ข", 180, 260]]


def test_token_split_mid_character_still_rebuilds(tmp_path):
    # whisper.cpp can split one Thai character (3 UTF-8 bytes) across tokens; the original decoder handled it
    ส, ว = "ส".encode(), "ว".encode()
    p = tmp_path / "asr bytes.json"
    head = b'{"transcription":[{"offsets":{"from":0,"to":400},"text":"x","tokens":['
    tok1 = b'{"text":"' + ส[:2] + b'","offsets":{"from":0,"to":100}},'
    tok2 = b'{"text":"' + ส[2:] + ว + b'","offsets":{"from":100,"to":400}}'
    p.write_bytes(head + tok1 + tok2 + b"]}]}")
    chars = make_subs.load_chars(make_subs.load_asr(p))
    assert "".join(c[0] for c in chars) == "สว"


def test_duplicate_and_zero_length_segments_skipped():
    chars = make_subs.load_chars(mini())
    text = "".join(c[0] for c in chars)
    assert text.count("ขั้นแรก") == 1


# ── cue builder ────────────────────────────────────────────────────────────────────────────

def build(max_chars=22, asr=None):
    return make_subs.build_cues(asr or mini(), make_subs.load_glossary({}), max_chars)


def test_cues_respect_max_chars_and_dangle_and_order():
    cues, _ = build(22)
    assert len(cues) >= 6
    for c in cues:
        assert 0 < len(c["t"].strip()) <= 22, c["t"]
        assert c["words"] and make_subs.cue_tokens(c) is not None      # the words can always rebuild the text
        last = [w["t"].strip() for w in c["words"] if w["t"].strip()][-1]
        assert not make_subs.is_dangle(last), c["t"]
    for a, b in zip(cues, cues[1:]):
        assert a["to"] <= b["f"]
        assert a["f"] < a["to"]


LV3 = FIX / "asr-large-v3-clip.json"


def _w(t, f=0, to=100):
    return {"t": t, "f": f, "to": to}


def test_fix_edges_moves_hanging_joiner_to_next_cue():
    groups = [[_w("ตอนนั้น"), _w("ที่"), _w("จะ")], [_w("ทำ"), _w("แคมเปญ")]]
    make_subs._fix_edges(groups, 22)
    assert ["".join(w["t"] for w in g) for g in groups] == ["ตอนนั้น", "ที่จะทำแคมเปญ"]


def test_is_dangle_sees_compound_joiners():
    assert make_subs.is_dangle("จะ") and make_subs.is_dangle("ที่จะ") and make_subs.is_dangle(" และก็ ")
    assert not make_subs.is_dangle("แคมเปญ") and not make_subs.is_dangle("") and not make_subs.is_dangle("ที่คุณ")


def test_fix_edges_leaves_joiner_when_next_cue_is_full():
    groups = [[_w("ตอนนั้น"), _w("จะ")], [_w("ทำแคมเปญกระทะสีดำให้ได้"), _w("เลย")]]
    make_subs._fix_edges(groups, 22)
    assert ["".join(w["t"] for w in g) for g in groups] == ["ตอนนั้นจะ", "ทำแคมเปญกระทะสีดำให้ได้เลย"]


def test_fix_edges_moves_repeat_mark_back():
    groups = [[_w("เร็ว")], [_w("ๆ"), _w("นะ")]]
    make_subs._fix_edges(groups, 22)
    assert ["".join(w["t"] for w in g) for g in groups] == ["เร็วๆ", "นะ"]


def test_real_large_v3_clip_cues():
    asr = json.loads(LV3.read_text(encoding="utf-8"))
    cues, _ = make_subs.build_cues(asr, make_subs.load_glossary({}), 22)
    texts = [c["t"] for c in cues]
    assert 14 <= len(cues) <= 20
    for c in cues:
        assert 0 < len(c["t"].strip()) <= 22
        last = [w["t"].strip() for w in c["words"] if w["t"].strip()][-1]
        assert not make_subs.is_dangle(last), c["t"]
        assert make_subs.cue_tokens(c) is not None
    assert any("ที่จะทำแคมเปญ" in t for t in texts)                  # the joiner moved, not dropped
    assert "".join(texts).replace(" ", "").startswith("เพื่อแก้ไขวิกฤตจากกระทะสีดำ")
    assert [c["f"] for c in cues] == sorted(c["f"] for c in cues)


def test_smaller_max_chars_gives_more_cues():
    assert len(build(14)[0]) > len(build(22)[0])
    assert all(len(c["t"].strip()) <= 14 for c in build(14)[0])


def test_glossary_fix_applied():
    cues, applied = build()
    joined = " ".join(c["t"] for c in cues)
    assert "คอร์ทโค้ด" not in joined and "Claude Code" in joined
    assert ("คอร์ทโค้ด", "Claude Code") in applied


def test_glossary_glue_keeps_phrase_in_one_cue():
    cues, _ = build(22)
    assert any("Claude Code" in c["t"] for c in cues)         # never split across two cues


def test_lead_shift_applied():
    cues, _ = build()
    assert cues[0]["f"] == 400 - make_subs.LEAD


def test_karaoke_ass_has_k_tags_per_word():
    cues, _ = build()
    ass = make_subs.render_ass(cues, make_subs.style_from_config(kit.load_config()), karaoke=True)
    dialogue = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogue) == len(cues)
    assert all(r"{\k" in ln for ln in dialogue)
    # stripped of tags, the text equals the cue text
    for ln, c in zip(dialogue, cues):
        txt = ln.split(",", 9)[9]
        assert re.sub(r"\{[^}]*\}", "", txt) == c["t"]
    # \k durations add up to the cue duration (centiseconds)
    for ln, c in zip(dialogue, cues):
        total = sum(int(x) for x in re.findall(r"\{\\k(\d+)\}", ln))
        assert abs(total - round((c["to"] - c["f"]) / 10)) <= 1


def test_plain_and_karaoke_style_from_config():
    kit.save_config({"brand": {"font": "Mali"}, "subtitle": {"size": 72, "margin_v": 123, "highlight": "#112233"}})
    style = make_subs.style_from_config(kit.load_config())
    plain = make_subs.render_ass([], style, karaoke=False)
    kara = make_subs.render_ass([], style, karaoke=True)
    sline = [ln for ln in kara.splitlines() if ln.startswith("Style:")][0].split(",")
    assert sline[1] == "Mali" and sline[2] == "72" and sline[3] == "&H00332211" and sline[4] == "&H00FFFFFF"
    assert sline[21] == "123"                      # MarginV
    pline = [ln for ln in plain.splitlines() if ln.startswith("Style:")][0].split(",")
    assert pline[3] == "&H00FFFFFF" and pline[1] == "Mali" and pline[21] == "123"


def test_lrc_artist_tag_from_speaker():
    cues, _ = build()
    assert make_subs.render_lrc(cues, "คุณสมชาย").startswith("[ar:คุณสมชาย]\n")
    assert not make_subs.render_lrc(cues, "").startswith("[ar:")
    assert re.match(r"\[00:00\.\d\d\]", make_subs.render_lrc(cues, "").splitlines()[0])


def test_finalize_writes_speaker_name_into_lrc(tmp_path):
    kit.save_config({"speaker": {"name": "ครูสมศรี"}})
    d = make_work(tmp_path)
    assert cli.main(["finalize", str(d)]) == 0
    assert (d / "demo.lrc").read_text(encoding="utf-8").startswith("[ar:ครูสมศรี]\n")


def test_asr_json_of_wrong_shape_is_a_clear_error(tmp_path, capsys):
    d = tmp_path / "x"
    d.mkdir()
    (d / "asr.json").write_text('{"nope": 1}', encoding="utf-8")
    with pytest.raises(kit.KitError) as e:
        cli.build_raw(d, name="x")
    assert "asr.json" in str(e.value)


def test_lrc_never_prints_sixty_seconds():
    assert make_subs.lrc_stamp(59996) == "[01:00.00]"
    assert make_subs.lrc_stamp(61230) == "[01:01.23]"


# ── glossary ───────────────────────────────────────────────────────────────────────────────

def test_glossary_merge_user_wins_and_hint_joins():
    base = make_subs.load_glossary({})
    g = make_subs.load_glossary({"fixes": [["คอร์ทโค้ด", "Claude-Code"], ["กาแฟร้อน", "กาแฟเย็น"]],
                                 "glue": ["Mali Co"], "protect": ["Mali Co"], "prompt_hint": "Mali Co, Acme"})
    fixes = dict(map(tuple, g["fixes"]))
    assert fixes["คอร์ทโค้ด"] == "Claude-Code"            # user override of a base entry
    assert fixes["กาแฟร้อน"] == "กาแฟเย็น"
    assert sum(1 for f in g["fixes"] if f[0] == "คอร์ทโค้ด") == 1
    assert "Mali Co" in g["glue"] and "Claude Code" in g["glue"]
    assert g["prompt_hint"].startswith(base["prompt_hint"]) and g["prompt_hint"].endswith("Mali Co, Acme")
    assert make_subs.load_glossary({"prompt_hint": ""})["prompt_hint"] == base["prompt_hint"]


def test_base_glossary_has_no_owner_entries():
    text = (ROOT / "engine" / "subs" / "glossary-base.json").read_text(encoding="utf-8")
    for bad in ("ปัน", "ณัฐพัชร์", "Federer", "Jordan", "Nissin", "Dyson", "Patagonia", "Codex", "Mac mini",
                "FutureSkill", "Supabase", "Cloudflare", "agentmacmini"):
        assert bad not in text, bad
    g = json.loads(text)
    assert g["prompt_hint"] and g["fixes"] and all(len(f) == 2 for f in g["fixes"])


def test_no_owner_strings_in_task_files():
    files = [ROOT / "engine" / "subs" / n for n in
             ("__init__.py", "__main__.py", "make_subs.py", "resplit.py", "apply_edits.py",
              "glossary-base.json", "edits.schema.json")]
    files += [ROOT / "skills" / "tad-clip-subs" / "SKILL.md", FIX / "asr-mini.json"]
    forbidden = ["ปัน", "Pun", "agentmacmini", "100.64.172.7", "Documents/content", "Telegram", "Codex", ".hermes"]
    for f in files:
        text = f.read_text(encoding="utf-8")
        for bad in forbidden:
            assert bad not in text, f"{bad!r} in {f.name}"
        assert not re.search(r"\bkie\b", text, re.I), f.name


# ── review.md ──────────────────────────────────────────────────────────────────────────────

def test_review_md_lists_numbered_cues_with_times(tmp_path):
    d = make_work(tmp_path)
    md = (d / "review.md").read_text(encoding="utf-8")
    cues = raw_cues(d)
    for i, c in enumerate(cues, 1):
        assert re.search(rf"^\s*{i}\s+\d\d:\d\d\.\d\d-\d\d:\d\d\.\d\d\s+{re.escape(c['t'])}", md, re.M), i
    assert "Claude Code" in md


def test_review_md_documents_edits_schema_with_example(tmp_path):
    d = make_work(tmp_path)
    md = (d / "review.md").read_text(encoding="utf-8")
    for key in ("corrections", "resplit", "drop", "flags", "notes", "edits.json"):
        assert key in md
    m = re.search(r"```json\n(.*?)\n```", md, re.S)
    assert m, "review.md must carry one json example"
    example = json.loads(m.group(1))
    assert apply_edits.validate_shape(example) == []
    schema = json.loads((ROOT / "engine" / "subs" / "edits.schema.json").read_text(encoding="utf-8"))
    assert set(example) <= set(schema["properties"])
    assert set(schema["properties"]) == {"corrections", "resplit", "drop", "flags", "notes"}


def _stalled_asr():
    a = mini()
    seg = a["transcription"][1]
    toks = [t for t in seg["tokens"] if not t["text"].startswith("[_")]
    toks[3]["offsets"]["to"] = toks[3]["offsets"]["from"] + 3100         # one word held for 3.1 s
    return a


def test_review_flags_stall_gap_and_glossary(tmp_path):
    d = make_work(tmp_path, asr=_stalled_asr())
    md = (d / "review.md").read_text(encoding="utf-8")
    assert "ASR STALL" in md
    assert re.search(r"GAP .*[23]\.\d", md)
    assert "GLOSSARY" in md and "Claude Code" in md
    # the flag sits on the cue line itself so Claude sees it in context
    claude_line = [ln for ln in md.splitlines() if "Claude Code" in ln and re.match(r"\s*\d+\s+\d\d:", ln)]
    assert claude_line and "GLOSSARY" in claude_line[0]


def test_review_without_problems_says_so(tmp_path):
    a = mini()
    a["transcription"] = [a["transcription"][0]]          # first sentence only: no gap, no stall, no glossary term
    d = make_work(tmp_path, asr=a)
    md = (d / "review.md").read_text(encoding="utf-8")
    assert "ไม่มีธง" in md
    assert "ASR STALL" not in md and "GAP " not in md and "GLOSSARY" not in md


def test_gap_flag_reports_audio_level_when_wav_present(tmp_path):
    d = tmp_path / "w"
    d.mkdir()
    # 1 s silence + 1 s loud tone + 1 s silence, 16 kHz mono
    import math
    rate = 16000
    frames = [0] * rate + [int(12000 * math.sin(2 * math.pi * 300 * k / rate)) for k in range(rate)] + [0] * rate
    with wave.open(str(d / "audio.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(int(x).to_bytes(2, "little", signed=True) for x in frames))
    assert apply_edits.rms_db(d / "audio.wav", 0, 900) < -80
    assert -30 < apply_edits.rms_db(d / "audio.wav", 1100, 1900) < -10
    assert apply_edits.audio_seconds(d / "audio.wav") == pytest.approx(3.0)


# ── edits: validate / apply ────────────────────────────────────────────────────────────────

def _pick(cues, needle):
    for i, c in enumerate(cues, 1):
        if needle in c["t"]:
            return i, c
    raise AssertionError(needle)


def test_validate_shape_flags_bad_documents():
    assert apply_edits.validate_shape({"corrections": [{"i": 1, "from": "a", "to": "b"}]}) == []
    assert apply_edits.validate_shape({"corrections": [{"i": "x"}]})
    assert apply_edits.validate_shape({"nope": 1})
    assert apply_edits.validate_shape([])


def test_finalize_changes_one_word_and_keeps_timing(tmp_path):
    d = make_work(tmp_path, name="คลิป ตัดแล้ว")
    raw = raw_cues(d)
    i, cue = _pick(raw, "ซับ")
    new = cue["t"].replace("ซับ", "ซับไตเติล", 1)
    write_edits(d, {"corrections": [{"i": i, "from": cue["t"], "to": new, "why": "ทดสอบ"}]})
    assert cli.main(["finalize", str(d)]) == 0

    final = json.loads((d / "cues.json").read_text(encoding="utf-8"))["cues"]
    assert len(final) == len(raw)
    assert final[i - 1]["t"] == new
    assert (final[i - 1]["f"], final[i - 1]["to"]) == (cue["f"], cue["to"])         # timing preserved
    assert [c["t"] for k, c in enumerate(final) if k != i - 1] == [c["t"] for k, c in enumerate(raw) if k != i - 1]
    assert "".join(w["t"] for w in final[i - 1]["words"]) == new                    # words realigned to new text
    ws = final[i - 1]["words"]
    assert ws[0]["f"] >= cue["words"][0]["f"] - 1 and ws[-1]["to"] <= cue["words"][-1]["to"] + 1
    assert all(a["f"] <= b["f"] for a, b in zip(ws, ws[1:]))

    for ext in (".srt", ".ass", ".lrc", ".karaoke.ass"):
        assert (d / f"คลิป ตัดแล้ว{ext}").exists(), ext
    assert new in (d / "คลิป ตัดแล้ว.srt").read_text(encoding="utf-8")
    assert new in (d / "คลิป ตัดแล้ว.lrc").read_text(encoding="utf-8")
    kara = (d / "คลิป ตัดแล้ว.karaoke.ass").read_text(encoding="utf-8")
    assert r"{\k" in kara and "ไตเติล" in re.sub(r"\{[^}]*\}", "", kara)
    assert (d / "edits-report.md").exists()
    # raw stays untouched
    assert raw_cues(d) == raw


def test_finalize_without_edits_writes_raw_as_final(tmp_path):
    d = make_work(tmp_path)
    assert cli.main(["finalize", str(d)]) == 0
    final = json.loads((d / "cues.json").read_text(encoding="utf-8"))["cues"]
    assert [c["t"] for c in final] == [c["t"] for c in raw_cues(d)]
    assert (d / "demo.srt").exists() and (d / "demo.karaoke.ass").exists()


def test_finalize_explicit_edits_path(tmp_path):
    d = make_work(tmp_path)
    i, cue = _pick(raw_cues(d), "ซับ")
    other = tmp_path / "ที่อื่น" / "แก้ไข.json"
    other.parent.mkdir()
    other.write_text(json.dumps({"corrections": [{"i": i, "from": cue["t"], "to": cue["t"].replace("ซับ", "ซัป")}]},
                                ensure_ascii=False), encoding="utf-8")
    assert cli.main(["finalize", str(d), "--edits", str(other)]) == 0
    assert "ซัป" in (d / "demo.srt").read_text(encoding="utf-8")


def test_finalize_refuses_more_than_half_changed(tmp_path, capsys):
    d = make_work(tmp_path)
    raw = raw_cues(d)
    n = len(raw) // 2 + 1
    write_edits(d, {"corrections": [{"i": i, "from": c["t"], "to": c["t"] + "ๆ"} for i, c in enumerate(raw[:n], 1)]})
    assert cli.main(["finalize", str(d)]) == 3
    assert not (d / "demo.srt").exists() and not (d / "cues.json").exists()
    rep = (d / "edits-report.md").read_text(encoding="utf-8")
    assert "เกินครึ่ง" in rep and "## แก้แล้ว" not in rep      # nothing was applied, so the report must not claim it


def test_apply_edits_guard_boundary():
    cues = [{"t": f"คำ{i}", "f": i * 1000, "to": i * 1000 + 800, "words": [{"t": f"คำ{i}", "f": i * 1000, "to": i * 1000 + 800}]}
            for i in range(10)]
    half = {"corrections": [{"i": i, "from": cues[i - 1]["t"], "to": cues[i - 1]["t"] + "ๆ"} for i in range(1, 6)]}
    over = {"corrections": [{"i": i, "from": cues[i - 1]["t"], "to": cues[i - 1]["t"] + "ๆ"} for i in range(1, 7)]}
    assert not apply_edits.apply_edits(cues, half, 22).too_many
    assert apply_edits.apply_edits(cues, over, 22).too_many


def test_bad_corrections_are_rejected_not_applied(tmp_path):
    d = make_work(tmp_path)
    raw = raw_cues(d)
    good_i, good = _pick(raw, "ซับ")
    other_i = 1 if good_i != 1 else 2
    edits = {"corrections": [
        {"i": good_i, "from": good["t"], "to": good["t"].replace("ซับ", "ซัป")},
        {"i": other_i, "from": "ข้อความที่ไม่มีอยู่จริง", "to": "อะไรก็ได้"},            # from does not match
        {"i": 999, "from": "x", "to": "y"},                                             # no such cue
        {"i": 3, "from": raw[2]["t"], "to": raw[2]["t"]},                                # unchanged
        {"i": 4, "from": raw[3]["t"], "to": "   "},                                      # empty
        {"i": 5, "from": raw[4]["t"], "to": raw[4]["t"] * 3},                            # rewritten, not corrected
    ]}
    write_edits(d, edits)
    assert cli.main(["finalize", str(d)]) == 0
    rep = (d / "edits-report.md").read_text(encoding="utf-8")
    assert "ตีกลับ" in rep and "ไม่มีอยู่จริง" in rep
    final = json.loads((d / "cues.json").read_text(encoding="utf-8"))["cues"]
    assert final[good_i - 1]["t"] == good["t"].replace("ซับ", "ซัป")
    assert [c["t"] for k, c in enumerate(final) if k != good_i - 1] == [c["t"] for k, c in enumerate(raw) if k != good_i - 1]


def test_over_max_chars_correction_rejected():
    cues = [{"t": "สวัสดี", "f": 0, "to": 900, "words": [{"t": "สวัสดี", "f": 0, "to": 900}]}] * 4
    long_to = "สวัสดีครับ" * 3
    res = apply_edits.apply_edits(cues, {"corrections": [{"i": 1, "from": "สวัสดี", "to": long_to}]}, 22)
    assert not res.applied and res.rejected and "ยาวเกิน" in res.rejected[0]["reason"]


def test_flags_and_notes_pass_through_to_report(tmp_path):
    d = make_work(tmp_path)
    write_edits(d, {"flags": [{"i": 2, "note": "ไม่แน่ใจว่าพูดว่าอะไร"}], "notes": "สรุปสั้นๆ"})
    assert cli.main(["finalize", str(d)]) == 0
    rep = (d / "edits-report.md").read_text(encoding="utf-8")
    assert "ไม่แน่ใจว่าพูดว่าอะไร" in rep and "สรุปสั้นๆ" in rep


def test_malformed_edits_json_is_a_clear_error(tmp_path, capsys):
    d = make_work(tmp_path)
    (d / "edits.json").write_text("{not json", encoding="utf-8")
    assert cli.main(["finalize", str(d)]) == 2
    assert "edits.json" in capsys.readouterr().err


def test_finalize_before_run_says_what_to_do(tmp_path, capsys):
    (tmp_path / "ว่าง").mkdir()
    assert cli.main(["finalize", str(tmp_path / "ว่าง")]) == 2
    assert "raw.cues.json" in capsys.readouterr().err


# ── resplit + drop through edits ──────────────────────────────────────────────────────────

def test_resplit_moves_a_break_and_keeps_spans(tmp_path):
    d = make_work(tmp_path)
    raw = raw_cues(d)
    a, b = raw[1], raw[2]                                    # two adjacent cues
    joined = (a["t"] + b["t"]).replace(" ", "")
    cut = len(a["t"].replace(" ", "")) - 2                   # move the break two characters earlier
    new = [joined[:cut], joined[cut:]]
    write_edits(d, {"resplit": [{"i": [2, 3], "to": new, "why": "ย้ายรอยตัด"}]})
    assert cli.main(["finalize", str(d)]) == 0
    final = json.loads((d / "cues.json").read_text(encoding="utf-8"))["cues"]
    assert len(final) == len(raw)
    assert [final[1]["t"].replace(" ", ""), final[2]["t"].replace(" ", "")] == new
    assert final[1]["f"] == a["f"] and final[2]["to"] == b["to"]
    assert final[1]["to"] <= final[2]["f"]
    for c in final[1:3]:
        assert "".join(w["t"] for w in c["words"]) == c["t"]
    assert r"{\k" in (d / "demo.karaoke.ass").read_text(encoding="utf-8")


def test_resplit_rejects_changed_wording():
    cues = [{"t": "สวัสดีครับ", "f": 0, "to": 900, "words": [{"t": "สวัสดี", "f": 0, "to": 600}, {"t": "ครับ", "f": 600, "to": 900}]},
            {"t": "ยินดีที่ได้รู้จัก", "f": 900, "to": 2000, "words": [{"t": "ยินดี", "f": 900, "to": 1300}, {"t": "ที่ได้รู้จัก", "f": 1300, "to": 2000}]}]
    res = apply_edits.apply_edits(cues, {"resplit": [{"i": [1, 2], "to": ["สวัสดีค่ะ", "ยินดีที่ได้รู้จัก"]}]}, 22)
    assert not res.applied and "ไม่ตรงคำพูดเดิม" in res.rejected[0]["reason"]


def test_resplit_requires_adjacent_cues():
    cues = [{"t": f"คำ{k}", "f": k * 1000, "to": k * 1000 + 800, "words": [{"t": f"คำ{k}", "f": k * 1000, "to": k * 1000 + 800}]} for k in range(4)]
    res = apply_edits.apply_edits(cues, {"resplit": [{"i": [1, 3], "to": ["คำ0คำ2"]}]}, 22)
    assert not res.applied and "ติดกัน" in res.rejected[0]["reason"]


def test_drop_removes_cue_and_conflicts_are_rejected(tmp_path):
    d = make_work(tmp_path)
    raw = raw_cues(d)
    write_edits(d, {"drop": [{"i": 1, "from": raw[0]["t"], "why": "ซ้ำกับตัวอักษรบนจอ"},
                             {"i": 2, "from": raw[1]["t"], "why": "ลบซ้อนกับการแก้"}],
                    "corrections": [{"i": 2, "from": raw[1]["t"], "to": raw[1]["t"] + "ๆ"}]})
    assert cli.main(["finalize", str(d)]) == 0
    final = json.loads((d / "cues.json").read_text(encoding="utf-8"))["cues"]
    assert len(final) == len(raw) - 1                          # cue 1 dropped; cue 2 corrected, its drop refused
    assert final[0]["t"] == raw[1]["t"] + "ๆ"
    rep = (d / "edits-report.md").read_text(encoding="utf-8")
    assert "ซ้ำกับตัวอักษรบนจอ" in rep and "แตะไปแล้ว" in rep


# ── realign (kept from the original) ──────────────────────────────────────────────────────

def test_realign_keeps_matching_character_times():
    toks = [["ดิว", 1000, 1300, False], ["กับ", 1300, 1600, False]]
    out = make_subs._realign(toks, "ดีลกับ")
    assert "".join(t[0] for t in out) == "ดีลกับ"
    assert out[0][1] >= 1000 and out[-1][2] <= 1600


# ── run pipeline (fake ASR, real ffmpeg) ──────────────────────────────────────────────────

@pytest.fixture
def fake_asr(monkeypatch):
    calls = []

    def fake(wav, prompt=None):
        calls.append({"wav": str(wav), "prompt": prompt})
        return mini()

    monkeypatch.setattr(cli, "_transcribe", fake)
    return calls


needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")


@needs_ffmpeg
def test_run_on_thai_path_with_spaces(tmp_path, fake_asr, capsys):
    src_dir = tmp_path / "โฟลเดอร์ คลิป"
    src_dir.mkdir()
    video = src_dir / "คลิป ทดสอบ 1.mp4"
    shutil.copy(VIDEO, video)
    out = src_dir / "tad-clip-ทดสอบ" / "subs"
    kit.save_config({"tools": {"ffmpeg": FFMPEG}, "glossary": {"prompt_hint": "คำเฉพาะของฉัน"}})

    assert cli.main([str(video), "-o", str(out), "--karaoke"]) == 0
    for name in ("audio.wav", "asr.json", "raw.cues.json", "raw.srt", "review.md", "raw.karaoke.ass"):
        assert (out / name).exists(), name
    with wave.open(str(out / "audio.wav")) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 16000, 2)
    assert len(fake_asr) == 1
    assert fake_asr[0]["prompt"].endswith("คำเฉพาะของฉัน") and "Claude Code" in fake_asr[0]["prompt"]
    assert json.loads((out / "raw.cues.json").read_text(encoding="utf-8"))["meta"]["name"] == "คลิป ทดสอบ 1"
    assert "คลิป ทดสอบ 1" in (out / "review.md").read_text(encoding="utf-8")
    # finalize names its files after the video
    assert cli.main(["finalize", str(out)]) == 0
    assert (out / "คลิป ทดสอบ 1.karaoke.ass").exists()


@needs_ffmpeg
def test_rerun_skips_done_steps_and_never_touches_edits(tmp_path, fake_asr):
    video = tmp_path / "คลิป.mp4"
    shutil.copy(VIDEO, video)
    out = tmp_path / "out"
    assert cli.main([str(video), "-o", str(out)]) == 0
    write_edits(out, {"notes": "งานของฉัน อย่าลบ"})
    before = (out / "edits.json").read_bytes()
    stamp = (out / "asr.json").stat().st_mtime_ns

    assert cli.main([str(video), "-o", str(out)]) == 0
    assert len(fake_asr) == 1 and (out / "asr.json").stat().st_mtime_ns == stamp
    assert (out / "edits.json").read_bytes() == before

    assert cli.main([str(video), "-o", str(out), "--force"]) == 0
    assert len(fake_asr) == 2
    assert (out / "edits.json").read_bytes() == before


@needs_ffmpeg
def test_rerun_with_new_max_chars_rebuilds_cues_without_new_asr(tmp_path, fake_asr):
    video = tmp_path / "v.mp4"
    shutil.copy(VIDEO, video)
    out = tmp_path / "out"
    cli.main([str(video), "-o", str(out), "--max-chars", "22"])
    n22 = len(raw_cues(out))
    cli.main([str(video), "-o", str(out), "--max-chars", "12"])
    assert len(fake_asr) == 1
    assert len(raw_cues(out)) > n22


def test_run_missing_video_is_a_thai_error(tmp_path, capsys):
    assert cli.main([str(tmp_path / "ไม่มี.mp4"), "-o", str(tmp_path / "o")]) == 2
    assert "ไม่พบไฟล์" in capsys.readouterr().err


def test_missing_asr_module_message(monkeypatch):
    import builtins
    real = builtins.__import__

    def deny(name, *a, **k):
        if name == "engine" and a and a[2] and "asr" in a[2]:
            raise ImportError("no asr")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", deny)
    with pytest.raises(kit.KitError) as e:
        cli._transcribe(Path("x.wav"), None)
    assert "ถอดเสียง" in str(e.value)


def test_transcribe_wraps_asr_failures_in_thai(monkeypatch):
    from engine import asr as real_asr

    def boom(path, prompt=None):
        raise ImportError("No module named 'faster_whisper'")

    monkeypatch.setattr(real_asr, "transcribe", boom)
    with pytest.raises(kit.KitError) as e:
        cli._transcribe(Path("x.wav"), None)
    assert "ตรวจเครื่อง tad-clip" in str(e.value) and "faster_whisper" in str(e.value)

    monkeypatch.setattr(real_asr, "transcribe", lambda path, prompt=None: (_ for _ in ()).throw(kit.KitError("โมเดลหาย")))
    with pytest.raises(kit.KitError) as e2:
        cli._transcribe(Path("x.wav"), None)
    assert str(e2.value) == "โมเดลหาย"                 # the ASR's own Thai message passes through untouched


# ── patch (re-transcribe a stretch and splice) ────────────────────────────────────────────

_NEW = {"transcription": [{"offsets": {"from": 0, "to": 2000}, "text": "ใหม่",
                           "tokens": [{"text": "ใหม่", "offsets": {"from": 100, "to": 1900}}]}]}


def test_splice_asr_replaces_only_the_window():
    old = mini()          # segments: 400-6790, 7090-12635, (dup), (empty), 15335-20750, 21150-26550
    spliced = cli.splice_asr(old, _NEW, start_ms=13000, end_ms=21000)
    segs = spliced["transcription"]
    starts = [s["offsets"]["from"] for s in segs]
    assert starts == sorted(starts)
    patched = [s for s in segs if s["text"] == "ใหม่"][0]
    assert patched["offsets"] == {"from": 13000, "to": 15000}
    assert patched["tokens"][0]["offsets"] == {"from": 13100, "to": 14900}
    for keep in (old["transcription"][0], old["transcription"][1], old["transcription"][-1]):
        assert keep in segs
    assert not any(s["offsets"]["from"] == 15335 for s in segs)            # the dropped sentence is gone


def test_splice_asr_trims_a_segment_that_straddles_the_window():
    old = mini()
    seg = old["transcription"][1]                 # 7090-12635
    cut = 9000
    spliced = cli.splice_asr(old, _NEW, start_ms=cut, end_ms=None)
    left = [s for s in spliced["transcription"] if s["offsets"]["from"] == seg["offsets"]["from"]][0]
    real = [t for t in left["tokens"] if not t["text"].startswith("[_")]
    assert real and all(t["offsets"]["to"] <= cut for t in real)
    assert left["offsets"]["to"] <= cut
    assert not any(s["offsets"]["from"] > cut and s["text"] != "ใหม่" for s in spliced["transcription"])
    # the straddler's tail words are not lost into the wrong place: text order stays chronological
    text = "".join(c[0] for c in make_subs.load_chars(spliced))
    assert text.index("ขั้นแรก") < text.index("ใหม่")


def test_splice_asr_keeps_words_after_the_window_even_inside_a_straddler():
    old = mini()
    spliced = cli.splice_asr(old, _NEW, start_ms=8000, end_ms=10000)       # window sits inside segment 2
    text = "".join(c[0] for c in make_subs.load_chars(spliced))
    assert text.index("ขั้นแรก") < text.index("ใหม่") < text.index("ออก")


@needs_ffmpeg
def test_patch_command_splices_and_rebuilds(tmp_path, monkeypatch):
    d = make_work(tmp_path)
    # a real audio.wav of the right length so ffmpeg can cut from it
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=30",
                    "-ac", "1", "-ar", "16000", str(d / "audio.wav")], check=True)
    seen = {}

    def fake(wav, prompt=None):
        with wave.open(str(wav)) as w:
            seen["secs"] = w.getnframes() / w.getframerate()
        return {"transcription": [{"offsets": {"from": 0, "to": 1500}, "text": "ท่อนใหม่",
                                   "tokens": [{"text": "ท่อน", "offsets": {"from": 0, "to": 700}},
                                              {"text": "ใหม่", "offsets": {"from": 700, "to": 1500}}]}]}

    monkeypatch.setattr(cli, "_transcribe", fake)
    before = (d / "asr.json").read_bytes()
    assert cli.main(["patch", str(d), "--from", "13", "--to", "20"]) == 0
    assert seen["secs"] == pytest.approx(7.0, abs=0.1)
    assert (d / "asr.before-patch.json").read_bytes() == before
    assert any("ท่อนใหม่" in c["t"] for c in raw_cues(d))
    assert "ท่อนใหม่" in (d / "review.md").read_text(encoding="utf-8")


# ── burn ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,expected", [
    ("/tmp/a b/คลิป.ass", r"'/tmp/a b/คลิป.ass'"),
    ("C:\\Users\\สม ชาย\\x.ass", r"'C\:/Users/สม ชาย/x.ass'"),
    ("/tmp/it's/x.ass", r"'/tmp/it\'\''s/x.ass'"),
])
def test_filter_path_escaping(path, expected):
    assert cli.filter_path(path) == expected


def test_burn_filter_names_both_ass_and_fontsdir():
    flt = cli.burn_filter("/x/y.ass", "/x/fonts")
    assert flt == "subtitles='/x/y.ass':fontsdir='/x/fonts'"


@pytest.mark.skipif(not HAS_LIBASS, reason="ffmpeg without libass")
def test_burn_real_frame_with_awkward_paths(tmp_path):
    work = tmp_path / ("งาน's ซับ, a" if sys.platform == "win32" else "งาน's ซับ, a:b")   # Thai, space, apostrophe, comma, colon
    work.mkdir()
    clip = work / "คลิป สั้น.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x223344:s=360x640:d=3:r=25",
                    "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)], check=True)
    d = work / "subs"
    d.mkdir()
    (d / "asr.json").write_text(json.dumps(mini(), ensure_ascii=False), encoding="utf-8")
    cli.build_raw(d, name="คลิป สั้น")
    assert cli.main(["finalize", str(d)]) == 0
    out = work / "ผลลัพธ์ ซับ.mp4"
    assert cli.main(["burn", str(clip), str(d / "คลิป สั้น.karaoke.ass"), "-o", str(out)]) == 0
    assert out.exists() and out.stat().st_size > 0
    probe = subprocess.run([FFMPEG, "-hide_banner", "-i", str(out)], capture_output=True, text=True).stderr
    assert "yuv420p" in probe and "h264" in probe


def test_burn_without_libass_names_the_fix(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_has_libass", lambda ffmpeg: False)
    v, a = tmp_path / "v.mp4", tmp_path / "a.ass"
    v.write_bytes(b"x"); a.write_text("x", encoding="utf-8")
    kit.save_config({"tools": {"ffmpeg": FFMPEG or sys.executable}})
    assert cli.main(["burn", str(v), str(a), "-o", str(tmp_path / "o.mp4")]) == 2
    err = capsys.readouterr().err
    assert "libass" in err and "ตรวจเครื่อง tad-clip" in err


# ── real sample (added by the ASR task) ───────────────────────────────────────────────────

@pytest.mark.skipif(not (FIX / "asr-sample.json").exists(), reason="asr-sample.json not generated yet")
def test_real_asr_sample_properties():
    """Whatever the ASR heard (even noisy text), the cue rules hold."""
    asr = json.loads((FIX / "asr-sample.json").read_text(encoding="utf-8"))
    cues, _ = make_subs.build_cues(asr, make_subs.load_glossary({}), 22)
    assert cues
    for k, c in enumerate(cues):
        assert 0 < len(c["t"].strip()) <= 22
        last = [w["t"].strip() for w in c["words"] if w["t"].strip()][-1]
        if make_subs.is_dangle(last) and k + 1 < len(cues):
            nxt = cues[k + 1]
            no_room = len(nxt["t"].replace(" ", "")) + len(last) > 22
            real_break = nxt["f"] - c["to"] >= make_subs.GAP
            assert no_room or real_break, c["t"]          # a hanging joiner is only left when it cannot move
    for a, b in zip(cues, cues[1:]):
        assert a["to"] <= b["f"]
    ass = make_subs.render_ass(cues, make_subs.style_from_config(kit.load_config()), karaoke=True)
    assert r"{\k" in ass


def test_skill_frontmatter_and_required_lines():
    text = (ROOT / "skills" / "tad-clip-subs" / "SKILL.md").read_text(encoding="utf-8")
    head = text.split("---")[1]
    assert re.search(r"^name: tad-clip-subs$", head, re.M)
    desc = re.search(r"^description: (.+)$", head, re.M).group(1)
    for trigger in ("ทำซับ", "ใส่ซับให้คลิปนี้", "karaoke subtitles"):
        assert trigger in desc
    assert "ถ้า `${CLAUDE_PLUGIN_ROOT}` ไม่ถูกแทนค่า ให้ใช้ `~/.tad-clip/kit`" in text
    assert "${CLAUDE_PLUGIN_ROOT}/engine/" in text
    assert "~/.tad-clip/venv/bin/python" in text and r"%USERPROFILE%\.tad-clip\venv\Scripts\python.exe" in text
    for cmd in ("-m engine.subs", "finalize", "patch", "burn", "edits.schema.json"):
        assert cmd in text


def test_schema_file_is_valid_json_and_matches_shape():
    s = json.loads((ROOT / "engine" / "subs" / "edits.schema.json").read_text(encoding="utf-8"))
    assert s["type"] == "object" and s["additionalProperties"] is False
    assert set(s["properties"]["corrections"]["items"]["required"]) == {"i", "from", "to"}
    assert copy.deepcopy(s)
