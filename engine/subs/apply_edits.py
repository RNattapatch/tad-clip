"""The review loop around raw cues: review.md (what Claude reads) and edits.json (what Claude writes back).

review.md lists every cue with its time and any flags. edits.json is validated here, and the code, not the
reviewer, decides what is accepted. A reviewer that sends back a rewrite instead of a correction is refused.
"""
import array
import math
import sys
import wave
from pathlib import Path

from .make_subs import realign_words
from .resplit import resplit_group

STALL_MS = 2500     # one word held longer than this = the ASR lost the speech
GAP_MS = 2000       # silence between cues longer than this is worth a look
SPEECH_DB = -45.0   # RMS above this inside a gap = probably speech that was dropped
GUARD = 0.5         # touching more than this share of the cues = the ASR is wrong, not single words


# ── audio facts used by the flags ──────────────────────────────────────────────────────────

def audio_seconds(wav_path):
    with wave.open(str(wav_path)) as w:
        return w.getnframes() / w.getframerate()


def rms_db(wav_path, from_ms, to_ms):
    """RMS level in dBFS of a 16-bit PCM wav between two times (None when it cannot be measured)."""
    try:
        with wave.open(str(wav_path)) as w:
            if w.getsampwidth() != 2:
                return None
            rate, ch = w.getframerate(), w.getnchannels()
            first = max(0, int(from_ms * rate / 1000))
            n = int((to_ms - from_ms) * rate / 1000)
            if n <= 0 or first >= w.getnframes():
                return None
            w.setpos(first)
            data = array.array("h", w.readframes(n))
    except (OSError, wave.Error, EOFError):
        return None
    if sys.byteorder == "big":
        data.byteswap()
    if ch > 1:
        data = data[::ch]
    if not data:
        return None
    rms = math.sqrt(sum(x * x for x in data) / len(data))
    return -120.0 if rms < 1 else max(-120.0, 20 * math.log10(rms / 32768))


FRAME_MS = 10       # frame size of the level analysis
PAUSE_MS = 150      # shortest silence that counts as a pause (a word break the speaker really made)


def _frame_db(wav_path):
    """Level in dBFS of every 10 ms frame of a 16-bit PCM wav ([] when it cannot be read)."""
    try:
        with wave.open(str(wav_path)) as w:
            if w.getsampwidth() != 2:
                return []
            rate, ch = w.getframerate(), w.getnchannels()
            data = array.array("h", w.readframes(w.getnframes()))
    except (OSError, wave.Error, EOFError):
        return []
    if sys.byteorder == "big":
        data.byteswap()
    if ch > 1:
        data = data[::ch]
    n = max(1, rate * FRAME_MS // 1000)
    out = []
    for i in range(0, len(data) - n + 1, n):
        rms = math.sqrt(sum(x * x for x in data[i:i + n]) / n)
        out.append(-120.0 if rms < 1 else max(-120.0, 20 * math.log10(rms / 32768)))
    return out


def pause_threshold(levels):
    """Silence threshold that follows the recording's own noise floor: the 10th-percentile level + 12 dB,
    kept between -48 and -34 dBFS."""
    floor = sorted(levels)[len(levels) // 10]
    return min(-34.0, max(-48.0, floor + 12))


def find_pauses(wav_path, min_ms=PAUSE_MS, noise_db=None):
    """Silences of at least min_ms in audio.wav -> [(start_ms, end_ms), ...] (empty when the audio is unreadable)."""
    levels = _frame_db(wav_path)
    if not levels:
        return []
    thr = pause_threshold(levels) if noise_db is None else noise_db
    out, start = [], None
    for k, db in enumerate(levels + [0.0]):                       # the sentinel closes a silence that runs to the end
        if db < thr:
            start = k if start is None else start
        elif start is not None:
            if (k - start) * FRAME_MS >= min_ms:
                out.append((start * FRAME_MS, k * FRAME_MS))
            start = None
    return out


def snap_window(pauses, start_s, end_s=None, reach_s=3.0):
    """Move a patch window so it begins and ends in silence, never making it narrower.

    start goes back to the middle of the nearest earlier pause, end goes forward to the middle of the nearest later
    pause (each within reach_s). A boundary already inside a pause is kept. -> (start_s, end_s, notes)"""
    notes = []

    def pick(sec, back, flag):
        ms = sec * 1000
        if any(a <= ms <= b for a, b in pauses):
            return sec
        mids = [(a + b) / 2 for a, b in pauses if ((a + b) / 2 <= ms if back else (a + b) / 2 >= ms)
                and abs((a + b) / 2 - ms) <= reach_s * 1000]
        if not mids:
            notes.append(f"{flag} {sec:g} ไม่พบช่วงเงียบ{'ก่อนหน้า' if back else 'ถัดไป'}ภายใน {reach_s:g} วินาที — ใช้ค่าเดิม (อาจตัดกลางคำ)")
            return sec
        mid = max(mids) if back else min(mids)
        a, b = next((a, b) for a, b in pauses if (a + b) / 2 == mid)
        notes.append(f"{flag} {sec:g} -> {mid / 1000:g} (กลางช่วงเงียบ {a / 1000:.2f}-{b / 1000:.2f})")
        return mid / 1000

    start = pick(start_s, True, "--from")
    end = None if end_s is None else pick(end_s, False, "--to")
    return start, end, notes


def fmt_t(ms):
    cs = int(round(ms / 10))
    m, cs = divmod(cs, 6000)
    return f"{m:02d}:{cs / 100:05.2f}"


# ── flags ──────────────────────────────────────────────────────────────────────────────────

def _gap_text(secs, a_ms, b_ms, wav):
    s = f"GAP {secs:.1f} วิ ({fmt_t(a_ms)}-{fmt_t(b_ms)})"
    lvl = rms_db(wav, a_ms, b_ms) if wav else None
    if lvl is None:
        return s
    verdict = "มีเสียงอยู่ น่าจะมีคำพูดหาย" if lvl > SPEECH_DB else "เงียบ น่าจะไม่มีคำพูด"
    return f"{s} · ระดับเสียงช่วงนั้น {lvl:.0f} dB: {verdict}"


def find_flags(cues, applied_fixes, glossary, wav=None):
    """-> list of {"cue": 1-based number or None, "kind": "ASR STALL"|"GAP"|"GLOSSARY", "text": str}"""
    out = []
    protect = glossary.get("protect", [])
    fixes = list(dict.fromkeys(tuple(x) for x in applied_fixes))
    for n, c in enumerate(cues, 1):
        for w in c.get("words", []):
            if w["t"].strip() and w["to"] - w["f"] > STALL_MS:
                out.append({"cue": n, "kind": "ASR STALL",
                            "text": f"ASR STALL \"{w['t'].strip()}\" ค้าง {(w['to'] - w['f']) / 1000:.1f} วิ "
                                    f"({fmt_t(w['f'])}-{fmt_t(w['to'])}) — คำพูดช่วงนี้น่าจะหาย"})
        prev_end = cues[n - 2]["to"] if n > 1 else 0
        if c["f"] - prev_end > GAP_MS:
            where = "ก่อน cue นี้" if n > 1 else "ก่อน cue แรก"
            out.append({"cue": n, "kind": "GAP", "text": _gap_text((c["f"] - prev_end) / 1000, prev_end, c["f"], wav) + f" {where}"})
        terms = [p for p in protect if p in c["t"]]
        fixed = [old for old, new in fixes if new in c["t"]]
        if terms or fixed:
            bits = [" ".join(terms)] if terms else []
            if fixed:
                bits.append("แก้อัตโนมัติจาก " + ", ".join(f"\"{f}\"" for f in fixed))
            out.append({"cue": n, "kind": "GLOSSARY", "text": "GLOSSARY " + " · ".join(bits) + " — เช็คการสะกด"})
    if wav and cues:
        try:
            end_ms = audio_seconds(wav) * 1000
        except (OSError, wave.Error, EOFError):
            end_ms = 0
        if end_ms - cues[-1]["to"] > GAP_MS:
            out.append({"cue": None, "kind": "GAP", "text": _gap_text((end_ms - cues[-1]["to"]) / 1000, cues[-1]["to"], end_ms, wav) + " หลัง cue สุดท้ายถึงท้ายเสียง"})
    return out


# ── review.md ──────────────────────────────────────────────────────────────────────────────

_EXAMPLE = """{
  "corrections": [
    {"i": 12, "from": "เปิดคอร์ทโค้ดแล้ว", "to": "เปิด Claude Code แล้ว", "why": "ศัพท์เครื่องมือถอดเพี้ยน"},
    {"i": 21, "from": "เสียยอกขายกว่า 2 วัน", "to": "เสียยอดขายกว่า 2 วัน", "why": "สะกดผิด"}
  ],
  "resplit": [
    {"i": [20, 21], "to": ["ละเอียดและแก้ไข", "ยอมเสียยอดขายกว่า 2 วัน"], "why": "รอยตัดอยู่กลางคำ (cue 21 ถูกแก้คำด้านบนด้วย บรรทัดใหม่จึงใช้คำที่แก้แล้ว)"}
  ],
  "drop": [
    {"i": 3, "from": "ข้อความทั้งบรรทัดที่ซ้ำ", "why": "ซ้ำกับตัวอักษรที่ฝังบนจอแล้ว"}
  ],
  "flags": [
    {"i": 31, "note": "ไม่แน่ใจว่าพูดว่า ดีล หรือ ดิว"}
  ],
  "notes": "แก้ 2 จุด ย้ายรอยตัด 1 จุด ลบ 1 บรรทัด"
}"""


def review_markdown(name, cues, flags, max_chars, work_dir, wav=None):
    by_cue = {}
    for f in flags:
        by_cue.setdefault(f["cue"], []).append(f)
    kinds = {}
    for f in flags:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    total = fmt_t(cues[-1]["to"]) if cues else "00:00.00"
    summary = " · ".join(f"{k} {v}" for k, v in kinds.items()) or "ไม่มีธง"
    d = str(work_dir)
    lines = [
        f"# ตรวจซับ — {name}",
        "",
        "อ่านไฟล์นี้ให้จบ แล้วเขียน `edits.json` เฉพาะจุดที่ **ฟังผิดชัดเจน** ไม่ต้องเปิดไฟล์อื่น",
        "",
        f"- cue ทั้งหมด **{len(cues)}** · เพดาน {max_chars} ตัวอักษรต่อบรรทัด · ซับยาวถึง {total}",
        f"- ธง: {summary}",
        "",
        "## วิธีเขียน edits.json",
        "",
        f"บันทึกที่ `{Path(d) / 'edits.json'}` แล้วรัน `python -m engine.subs finalize \"{d}\"` (ใช้ python ของชุดเครื่องมือ รันจากโฟลเดอร์ชุดเครื่องมือ)",
        "",
        "กติกา:",
        "",
        "1. แก้เฉพาะคำที่ฟังผิดชัดๆ: ชื่อเฉพาะหรือศัพท์เครื่องมือที่ถอดเพี้ยน, คำไทยสะกดผิดจนอ่านสะดุด, ภาษาอังกฤษที่ถอดเป็นคำอ่านไทย",
        "2. ห้ามเกลาสำนวน ห้ามตัดคำพูดติดปาก (ครับ นะ เนี่ย) ห้ามเติมคำที่ไม่ได้พูด — ซับคือคำพูดจริง",
        "3. ไม่มั่นใจ = ใส่ใน `flags` อย่าแก้ · ไม่มีจุดที่ต้องแก้ = ไม่ต้องเขียนไฟล์ แล้วรัน finalize ได้เลย",
        "4. `from` ต้องตรงกับบรรทัดเดิมทุกตัวอักษร · เลข `i` คือเลข cue ในรายการด้านล่าง (เลขเดิม ไม่ต้องเลื่อนเมื่อมีหลายรายการ)",
        "5. cue หนึ่งมี `corrections` ได้รายการเดียว และอยู่ใน `resplit` ได้รายการเดียว (`drop` ใช้ได้เมื่อไม่มีรายการอื่นแตะ cue นั้น)",
        "6. **แก้คำและย้ายรอยตัดใน cue เดียวกัน**: ใส่การแก้คำใน `corrections` ตามปกติ แล้วใน `resplit` เขียนบรรทัดใหม่ด้วย **คำที่แก้แล้ว** (ระบบแก้คำก่อน แล้วค่อยย้ายรอยตัด) ดู cue 21 ในตัวอย่าง · `resplit` ย้ายรอยตัดได้อย่างเดียว ถ้าเปลี่ยนคำในบรรทัดใหม่โดยไม่ผ่าน `corrections` จะถูกตีกลับ",
        "7. แตะเกินครึ่งของ cue = เครื่องไม่รับ (exit 3) แปลว่าถอดเสียงผิดทั้งคลิป ให้ถอดใหม่ ไม่ใช่แก้ทีละคำ",
        "",
        "| key | ใช้ทำอะไร | รูปแบบของแต่ละรายการ |",
        "|---|---|---|",
        "| `corrections` | แก้คำใน 1 cue (เวลาเดิม) | `{\"i\": เลข, \"from\": บรรทัดเดิมทั้งบรรทัด, \"to\": บรรทัดที่แก้แล้ว, \"why\": เหตุผลสั้นๆ}` |",
        "| `resplit` | ย้ายรอยตัดระหว่าง cue ที่ติดกัน (คำพูดต้องเหมือนเดิม หลังแก้คำแล้ว) | `{\"i\": [เลขที่ติดกัน], \"to\": [บรรทัดใหม่...], \"why\": ...}` |",
        "| `drop` | ลบ cue (เช่น ซ้ำกับตัวอักษรบนจอ) | `{\"i\": เลข, \"from\": บรรทัดเดิม, \"why\": ...}` |",
        "| `flags` | สงสัยแต่ไม่แก้ | `{\"i\": เลข, \"note\": ...}` |",
        "| `notes` | สรุปสั้นๆ | ข้อความ |",
        "",
        "ทุก key เป็นตัวเลือก ใส่เฉพาะที่ใช้ ตัวอย่าง:",
        "",
        "```json",
        _EXAMPLE,
        "```",
        "",
        "## Flags",
        "",
    ]
    if flags:
        for f in flags:
            lines.append(f"- cue {f['cue']}: {f['text']}" if f["cue"] else f"- {f['text']}")
        lines.append("")
        lines.append("ถ้าเสียงช่วง GAP / ASR STALL มีคำพูดหายจริง ให้ถอดช่วงนั้นใหม่ด้วย `python -m engine.subs patch` (ดู SKILL) แล้วอ่านไฟล์นี้ใหม่ก่อนเขียน edits.json")
    else:
        lines.append("ไม่มีธง")
    lines += ["", "## Cues", ""]
    width = len(str(len(cues)))
    for n, c in enumerate(cues, 1):
        tags = "".join(f"  [{f['text']}]" for f in by_cue.get(n, []))
        lines.append(f"{n:>{width}}  {fmt_t(c['f'])}-{fmt_t(c['to'])}  {c['t']}{tags}")
    return "\n".join(lines) + "\n"


# ── edits.json: validate + apply ───────────────────────────────────────────────────────────

_INT, _STR = "จำนวนเต็ม", "ข้อความ"
_SPEC = {
    "corrections": {"i": _INT, "from": _STR, "to": _STR},
    "resplit": {"i": [_INT], "to": [_STR]},
    "drop": {"i": _INT, "from": _STR},
    "flags": {"i": _INT, "note": _STR},
}
_OPTIONAL = {"why": _STR}


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _type_ok(v, spec):
    if isinstance(spec, list):
        return isinstance(v, list) and bool(v) and all(_type_ok(x, spec[0]) for x in v)
    return _is_int(v) if spec == _INT else isinstance(v, str)


def validate_shape(doc):
    """Structural problems of an edits.json document, in Thai (empty list = well formed)."""
    if not isinstance(doc, dict):
        return ["edits.json ต้องเป็น object ที่ขึ้นต้นด้วย { }"]
    errs = []
    allowed = set(_SPEC) | {"notes"}
    for k in sorted(set(doc) - allowed):
        errs.append(f"key \"{k}\" ไม่รู้จัก — ใช้ได้เฉพาะ {', '.join(sorted(allowed))}")
    if "notes" in doc and not isinstance(doc["notes"], str):
        errs.append("notes ต้องเป็นข้อความ")
    for key, spec in _SPEC.items():
        if key not in doc:
            continue
        if not isinstance(doc[key], list):
            errs.append(f"{key} ต้องเป็น array")
            continue
        for n, e in enumerate(doc[key], 1):
            if not isinstance(e, dict):
                errs.append(f"{key}[{n}] ต้องเป็น object")
                continue
            for f in sorted(set(e) - set(spec) - set(_OPTIONAL)):
                errs.append(f"{key}[{n}] มี key \"{f}\" ที่ไม่รู้จัก")
            for f, t in spec.items():
                if f not in e:
                    errs.append(f"{key}[{n}] ขาด \"{f}\"")
                elif not _type_ok(e[f], t):
                    errs.append(f"{key}[{n}].{f} ชนิดไม่ถูก (ต้องเป็น{'รายการของ' + t[0] if isinstance(t, list) else t})")
            if "why" in e and not isinstance(e["why"], str):
                errs.append(f"{key}[{n}].why ต้องเป็นข้อความ")
    return errs


def norm(s):
    return " ".join(str(s).split())


def _squash(s):
    return "".join(str(s).split())


class Result:
    def __init__(self, cues, total):
        self.cues = cues
        self.total = total
        self.applied, self.rejected, self.flags = [], [], []
        self.notes = ""
        self.changed = 0
        self.too_many = False


def apply_edits(cues, doc, max_chars):
    """Validate every entry against the raw cues and apply the ones that pass.

    Order: corrections, then resplit (on the corrected words), then drop. So one cue can be corrected *and* be part
    of a resplit: the resplit's `to` lines must use the corrected words. Rejected (kept out, listed in the report
    with the real cue text): `from` that does not match the line, empty or unchanged text, a line over
    max_chars + 6, a length change beyond 2x or below 0.5x, a resplit that changes the wording or is not adjacent,
    a cue corrected twice, in two resplits or dropped after another entry took it. If the accepted entries touch
    more than half of the cues nothing is applied (too_many)."""
    n = len(cues)
    res = Result(cues, n)
    res.notes = doc.get("notes", "") or ""
    res.flags = list(doc.get("flags", []))
    limit = max_chars + 6
    corrected, taken, repl, skip = set(), set(), {}, set()     # taken = in a resplit or dropped

    def cur(k):
        return repl[k][0] if k in corrected else cues[k]

    def reject(kind, e, why, actual=None):
        res.rejected.append({"kind": kind, "i": e["i"], "to": e.get("to"), "actual": actual, "reason": why})

    for e in doc.get("corrections", []):
        i = e["i"]
        if not 1 <= i <= n:
            reject("correction", e, "เลข cue ไม่มีอยู่จริง")
            continue
        cue = cues[i - 1]
        old, to = norm(cue["t"]), norm(e["to"])
        if norm(e["from"]) != old:
            reject("correction", e, f"ข้อความเดิมไม่ตรงกับ cue จริง (น่าจะเดาเอง) — ที่ส่งมา: {norm(e['from'])}", old)
        elif not to:
            reject("correction", e, "แก้เป็นบรรทัดว่าง", old)
        elif to == old:
            reject("correction", e, "เหมือนเดิม", old)
        elif len(to) > limit:
            reject("correction", e, f"ยาวเกินเพดาน ({len(to)} > {limit})", old)
        elif not 0.5 <= len(to) / max(1, len(old)) <= 2:
            reject("correction", e, f"ความยาวเปลี่ยนเกินเท่าตัว ({len(to) / max(1, len(old)):.2f}x) — น่าจะเขียนใหม่ ไม่ใช่แก้คำ", old)
        elif i - 1 in corrected:
            reject("correction", e, "cue นี้ถูกแก้คำไปแล้วโดยรายการอื่น", old)
        else:
            new = dict(cue)
            new["t"], new["words"] = to, realign_words(cue, to)
            corrected.add(i - 1)
            repl[i - 1] = [new]
            res.applied.append({"kind": "correction", "i": i, "from": cue["t"], "to": to, "why": e.get("why", "")})

    for e in doc.get("resplit", []):
        idxs = [k - 1 for k in e["i"]]
        pieces = [norm(p) for p in e["to"]]
        if not all(0 <= k < n for k in idxs):
            reject("resplit", e, "เลข cue ไม่มีอยู่จริง")
            continue
        actual = [norm(cur(k)["t"]) for k in idxs]                 # the text as it stands after the word corrections
        if idxs != list(range(idxs[0], idxs[0] + len(idxs))):
            reject("resplit", e, "cue ต้องเรียงติดกัน", actual)
        elif not all(pieces):
            reject("resplit", e, "มีบรรทัดใหม่ที่ว่าง", actual)
        elif _squash("".join(pieces)) != _squash("".join(actual)):
            reject("resplit", e, "ข้อความใหม่ต่อกันแล้วไม่ตรงคำพูดเดิม — ย้ายรอยตัดได้อย่างเดียว ถ้าจะแก้คำด้วยให้ใส่ใน `corrections` "
                                 "(cue เดียวกันได้) แล้วเขียนบรรทัดใหม่ด้วยคำที่แก้แล้ว (ข้อความข้างหน้าคือคำที่แก้แล้วของตอนนี้)", actual)
        elif any(len(p) > limit for p in pieces):
            reject("resplit", e, f"บรรทัดใหม่ยาวเกินเพดาน ({max(map(len, pieces))} > {limit})", actual)
        elif any(k in taken for k in idxs):
            reject("resplit", e, "cue นี้อยู่ใน resplit อื่นไปแล้ว", actual)
        else:
            try:
                out = resplit_group([cur(k) for k in idxs], pieces)
            except (ValueError, IndexError) as ex:
                reject("resplit", e, f"ใส่เวลาให้บรรทัดใหม่ไม่ได้ ({ex})", actual)
                continue
            taken.update(idxs)
            repl[idxs[0]] = out
            skip.update(idxs[1:])
            res.applied.append({"kind": "resplit", "i": e["i"], "from": actual, "to": pieces, "why": e.get("why", "")})

    for e in doc.get("drop", []):
        i = e["i"]
        if not 1 <= i <= n:
            reject("drop", e, "เลข cue ไม่มีอยู่จริง")
        elif norm(e["from"]) != norm(cues[i - 1]["t"]):
            reject("drop", e, f"ข้อความเดิมไม่ตรงกับ cue จริง (น่าจะเดาเอง) — ที่ส่งมา: {norm(e['from'])}", norm(cues[i - 1]["t"]))
        elif i - 1 in corrected or i - 1 in taken:
            reject("drop", e, "cue นี้ถูกรายการอื่นแตะไปแล้ว", norm(cur(i - 1)["t"]))
        else:
            taken.add(i - 1)
            repl[i - 1] = []
            res.applied.append({"kind": "drop", "i": i, "from": cues[i - 1]["t"], "to": None, "why": e.get("why", "")})

    res.changed = len(corrected | taken)
    res.too_many = res.changed > n * GUARD
    if not res.too_many and (corrected or taken):
        out = []
        for idx, c in enumerate(cues):
            if idx in skip:
                continue
            out.extend(repl[idx]) if idx in repl else out.append(c)
        res.cues = out
    return res


def _join(x):
    return " | ".join(x) if isinstance(x, list) else (x or "")


def _rejected_line(r):
    """cue 27: <real text now> → <what was proposed> — <why>. The real text is always printed."""
    cue = r["i"] if not isinstance(r["i"], list) else ", ".join(map(str, r["i"]))
    actual = _join(r.get("actual")) or "(ไม่มีข้อความ)"
    proposed = _join(r.get("to")) or "(ไม่มี)"
    return f"- cue {cue}: {actual} → {proposed} — {r['reason']}"


def report_markdown(name, res, edits_name="edits.json"):
    proposed = len(res.applied) + len(res.rejected)
    status = (f"ไม่ใช้ edits ทั้งชุด — แตะเกินครึ่งของ cue ({res.changed} จาก {res.total}) "
              "แปลว่าถอดเสียงผิดทั้งคลิป ถอดใหม่ (`--force` หรือ `patch`) ก่อน" if res.too_many
              else f"ใช้แล้ว — แตะ {res.changed} จาก {res.total} cue")
    lines = [f"# ผลตรวจซับ — {name}", "",
             f"- ไฟล์ edits: `{edits_name}`",
             f"- เสนอมา {proposed} รายการ · ผ่านด่าน {len(res.applied)} · ตีกลับ {len(res.rejected)}",
             f"- สถานะ: {status}", "", "## เสนอแต่ยังไม่ได้ใช้ (แตะเกินครึ่ง)" if res.too_many else "## แก้แล้ว", ""]
    if res.applied:
        for a in res.applied:
            why = f"  _({a['why']})_" if a.get("why") else ""
            if a["kind"] == "correction":
                lines.append(f"- แก้ cue {a['i']}: {a['from']} → **{a['to']}**{why}")
            elif a["kind"] == "resplit":
                lines.append(f"- ย้ายรอยตัด cue {a['i']}: {' | '.join(a['from'])} → **{' | '.join(a['to'])}**{why}")
            else:
                lines.append(f"- ลบ cue {a['i']}: {a['from']}{why}")
    else:
        lines.append("ไม่มี")
    lines += ["", "## สงสัย (ไม่ได้แก้ ให้คนตัดสิน)", ""]
    lines += [f"- cue {f.get('i')}: {f.get('note', '')}" for f in res.flags] or ["ไม่มี"]
    lines += ["", "## ตีกลับ (โค้ดไม่ยอมให้แก้)", ""]
    lines += [_rejected_line(r) for r in res.rejected] or ["ไม่มี"]
    if res.notes:
        lines += ["", "## บันทึก", "", res.notes]
    return "\n".join(lines) + "\n"
