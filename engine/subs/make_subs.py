"""ASR words (whisper.cpp -ojf shape) -> short, readable Thai cues -> SRT / ASS / LRC / karaoke ASS.

The cue optimiser (constants, DANGLE list, DP cost) is the one that was tuned on real Thai talking-head clips.
Do not change the numbers without re-checking real clips.
"""
import difflib
import json
import re
import unicodedata
from pathlib import Path

from pythainlp.tokenize import word_tokenize

from engine import kit

HERE = Path(__file__).resolve().parent

# Cue timing constants (ms unless noted). Tuned on real Thai clips.
MINCH = 8       # a trailing piece shorter than this counts as a scrap
GAP = 300       # silence that forces a cue break
MINDUR = 550    # shortest cue
MAXDUR = 2400   # longest cue before the cost grows
LEAD = 160      # ASR onsets come later than the real sound, so every cue is pulled earlier
TARGET = 18     # preferred characters per cue

ALGO_VERSION = 3    # bump whenever chunk() would produce different cues; a re-run then rebuilds old raw cues

# Joiners that must not end a cue.
DANGLE = set("และ แล้ว ที่ ซึ่ง จะ กับ ใน ก็ ให้ ของ เป็น ได้ มา ไป ว่า จาก ด้วย เพื่อ แต่ "
             "หรือ คือ ต่อ ทำ ยัง อยาก เอา ไม่ มี ตาม เช่น ถ้า เมื่อ ส่วน โดย พร้อม ทั้ง อีก".split())


# ── glossary ───────────────────────────────────────────────────────────────────────────────

def _pair(p):
    if isinstance(p, dict):
        p = (p.get("from", p.get("old")), p.get("to", p.get("new")))
    if not (isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(x, str) for x in p)):
        raise kit.KitError("glossary.fixes ต้องเป็นคู่ [\"คำที่ถอดผิด\", \"คำที่ถูก\"] — ตรวจ ~/.tad-clip/config.json")
    return p[0], p[1]


def load_glossary(user=None):
    """glossary-base.json merged with config['glossary']. User fixes come first so they win."""
    base = json.loads((HERE / "glossary-base.json").read_text(encoding="utf-8"))
    user = user or {}
    fixes = {}
    for p in [*(user.get("fixes") or []), *base.get("fixes", [])]:
        old, new = _pair(p)
        fixes.setdefault(old, new)

    def union(key):
        return list(dict.fromkeys([*(user.get(key) or []), *base.get(key, [])]))

    hint = ", ".join(h for h in (base.get("prompt_hint", "").strip(), (user.get("prompt_hint") or "").strip()) if h)
    return {"prompt_hint": hint, "fixes": [[o, n] for o, n in fixes.items()],
            "glue": union("glue"), "protect": union("protect")}


# ── ASR -> characters ──────────────────────────────────────────────────────────────────────

def load_asr(path):
    """Read an ASR json. Bytes are decoded with surrogateescape because whisper.cpp can split a UTF-8 character
    across two tokens (the file is then not valid UTF-8 as a whole)."""
    return json.loads(Path(path).read_bytes().decode("utf-8", errors="surrogateescape"))


def _spread(tx, f, to):
    """Per-character (from, to) for a token that holds several whole characters (a word, not a sub-piece).

    Time is shared between base characters. Whitespace and combining marks (Thai vowels/tone marks above and
    below) take no time of their own, so the word keeps its exact span and no space is stretched."""
    weights = [0 if (ch.isspace() or unicodedata.category(ch) == "Mn") else 1 for ch in tx]
    total = sum(weights)
    if not total:
        return [(f, to)] * len(tx)
    out, acc = [], 0
    for w in weights:
        a = f + (to - f) * acc / total
        acc += w
        out.append((int(round(a)), int(round(f + (to - f) * acc / total))))
    return out


def _has_surrogate(s):
    return any("\udc80" <= ch <= "\udcff" for ch in s)


def load_chars(asr):
    """-> list of [char, from_ms, to_ms, segment_index].

    Sub-character tokens (whisper.cpp) keep the token's span for every byte, exactly as before.
    Whole-word tokens (faster-whisper) get their span spread across the characters."""
    out, seen, si = [], set(), -1
    for seg in asr["transcription"]:
        o = seg["offsets"]
        if o["to"] <= o["from"]:            # whisper duplicate / zero-length artefact
            continue
        key = (o["from"], o["to"])
        if key in seen:
            continue
        seen.add(key)
        si += 1
        buf, times = bytearray(), []
        for t in seg.get("tokens", []):
            tx = t["text"]
            if tx.startswith("[_"):         # [_BEG_], [_TT_123]
                continue
            f, to = t["offsets"]["from"], t["offsets"]["to"]
            if len(tx) > 1 and not _has_surrogate(tx):
                for ch, span in zip(tx, _spread(tx, f, to)):
                    b = ch.encode("utf-8")
                    buf += b
                    times += [span] * len(b)
                continue
            b = tx.encode("utf-8", errors="surrogateescape")
            buf += b
            times += [(f, to)] * len(b)
        if not times:                       # no tokens, so nothing to time
            continue
        s = buf.decode("utf-8", errors="replace")
        bi = 0
        for ch in s:
            n = len(ch.encode("utf-8", errors="replace"))
            f, to = times[bi] if bi < len(times) else times[-1]
            out.append([ch, f, to, si])
            bi += n
    return out


def apply_fixes(chars, fixes):
    """Replace known mishearings, sharing the old span's time across the new characters."""
    applied = []
    for old, new in fixes:
        if old == new or not old:
            continue
        start = 0
        while True:
            txt = "".join(c[0] for c in chars)
            i = txt.find(old, start)
            if i < 0:
                break
            span = chars[i:i + len(old)]
            f, to = span[0][1], span[-1][2]
            step = max(1, (to - f) // max(1, len(new)))
            sid = span[0][3]
            rep = [[ch, f + step * k, f + step * (k + 1), sid] for k, ch in enumerate(new)]
            if rep:
                rep[-1][2] = to
            chars[i:i + len(old)] = rep
            applied.append((old, new))
            start = i + len(rep)            # never rescan the replacement (a fix may contain its own key)
    return chars, applied


def words(chars):
    """Split Thai into words per ASR segment -> list of word blocks (one block per segment)."""
    by_seg = {}
    for c in chars:
        by_seg.setdefault(c[3], []).append(c)
    blocks = []
    for si in sorted(by_seg):
        sub = by_seg[si]
        txt = "".join(c[0] for c in sub)
        ws, i = [], 0
        for w in word_tokenize(txt, engine="newmm", keep_whitespace=True):
            seg = sub[i:i + len(w)]
            i += len(w)
            if not seg:
                continue
            ws.append({"t": w, "f": min(c[1] for c in seg), "to": max(c[2] for c in seg)})
        if ws:
            blocks.append(ws)
    return blocks


def glue_words(ws, glue_list):
    """Merge phrases that must never be split across cues (names, tool names) into one block."""
    for ph in glue_list:
        i = 0
        while i < len(ws):
            acc = ""
            for j in range(i, min(i + 8, len(ws))):
                acc += ws[j]["t"]
                if " ".join(acc.split()) == ph:
                    ws[i:j + 1] = [{"t": acc, "f": ws[i]["f"], "to": ws[j]["to"]}]
                    break
                if len(acc.strip()) > len(ph):
                    break
            i += 1
    return ws


# ── cue optimiser (DP) ─────────────────────────────────────────────────────────────────────

def _vis(ws, i, j):
    return len("".join(w["t"] for w in ws[i:j]).strip())


def _cost(ws, i, j, last, maxch):
    L = _vis(ws, i, j)
    if L == 0 or L > maxch:
        return None
    dur = ws[j - 1]["to"] - ws[i]["f"]
    c = (L - TARGET) ** 2 * 1.0
    if dur < MINDUR:
        c += (MINDUR - dur) * 0.08
    if dur > MAXDUR:
        c += (dur - MAXDUR) * 0.08
    if L < 10 and not last:
        c += (10 - L) * 12
    tail = ws[j - 1]["t"].strip()
    if tail in DANGLE:
        c += 70
    if not last:
        pause = ws[j]["f"] - ws[j - 1]["to"]
        c -= min(pause, 500) * 0.22
        space = ws[j - 1]["t"].endswith(" ") or ws[j]["t"].strip() == "" or ws[j]["t"].startswith(" ")
        if space:
            c -= 30
        elif pause < 90:
            c += 22          # do not cut mid-phrase where the speaker did not breathe
    return c


def _dp(ws, maxch):
    """Cut a sentence block into cues with the lowest total cost."""
    n = len(ws)
    INF = float("inf")
    best = [INF] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        for i in range(max(0, j - 40), j):
            if best[i] == INF:
                continue
            c = _cost(ws, i, j, j == n, maxch)
            if c is None:
                continue
            v = best[i] + c + 25          # fixed cost per cue so it does not over-split
            if v < best[j]:
                best[j] = v
                back[j] = i
    if best[n] == INF:                    # fallback: greedy
        out, cur = [], []
        for w in ws:
            if cur and _vis(cur + [w], 0, len(cur) + 1) > maxch:
                out.append(cur)
                cur = []
            cur.append(w)
        if cur:
            out.append(cur)
        return out
    cuts, j = [], n
    while j > 0:
        cuts.append((back[j], j))
        j = back[j]
    return [ws[i:j] for i, j in reversed(cuts)]


def _spaced(t):
    t = re.sub(r"(?<=[\u0E00-\u0E7F])(?=[A-Za-z0-9])", " ", t)
    return re.sub(r"(?<=[A-Za-z0-9])(?=[\u0E00-\u0E7F])", " ", t)


def is_dangle(t):
    """True when the word is exactly one of the joiners in DANGLE. Compounds such as "ไปแล้ว", "ทำได้", "ไม่มี",
    "ไม่ได้" or "ที่จะ" are not joiners: they end sentences, so they are never matched."""
    return t.strip() in DANGLE


def _fix_edges(groups, maxch):
    """Clean up the cue edges the DP can leave behind, on words (so text and word times stay in step).

    A leading "ๆ" / "ฯ" goes back to the previous cue. A joiner from DANGLE hanging at the end of a cue moves to
    the front of the next one while that cue still fits in maxch, but only when the speaker did not stop after it:
    a silence of GAP ms or more means the word ends its sentence, so it stays where it is. The DP only
    discourages hanging joiners (a long breath after one can outweigh the penalty). The original did this on the
    cue text, which never fired for Thai because Thai text has no spaces to split on."""
    for gi in range(1, len(groups)):
        while len(groups[gi]) > 1 and groups[gi][0]["t"].strip() in ("ๆ", "ฯ"):
            groups[gi - 1].append(groups[gi].pop(0))
    for gi in range(len(groups) - 1):
        while True:
            g, nxt = groups[gi], groups[gi + 1]
            k = max((i for i, w in enumerate(g) if w["t"].strip()), default=0)
            first = next((w for w in nxt if w["t"].strip()), None)
            if k == 0 or first is None or not is_dangle(g[k]["t"]):
                break
            if first["f"] - g[k]["to"] >= GAP:        # a real stop: the word ends its sentence
                break
            moved = g[k:]
            if _vis(moved + nxt, 0, len(moved) + len(nxt)) > maxch:
                break
            groups[gi + 1] = moved + nxt
            del g[k:]


def chunk(blocks, glue_list, maxch):
    cues = []
    for ws in blocks:
        if not ws:
            continue
        groups = _dp(glue_words(ws, glue_list), maxch)
        _fix_edges(groups, maxch)
        for grp in groups:
            t = _spaced(" ".join("".join(w["t"] for w in grp).split()))
            if t:
                cues.append({
                    "t": t,
                    "f": grp[0]["f"],
                    "to": grp[-1]["to"],
                    "words": [{"t": w["t"], "f": w["f"], "to": w["to"]} for w in grp],
                })
    cues = [c for c in cues if c["t"].strip()]
    cues.sort(key=lambda c: c["f"])
    for c in cues:
        c["f"] = max(0, c["f"] - LEAD)
        c["to"] = max(c["f"] + 200, c["to"] - LEAD)
    for i, c in enumerate(cues):
        if c["to"] - c["f"] < MINDUR:
            c["to"] = c["f"] + MINDUR
        if i + 1 < len(cues) and c["to"] > cues[i + 1]["f"]:
            c["to"] = max(c["f"] + 200, cues[i + 1]["f"] - 20)
    for a, b in zip(cues, cues[1:]):             # the 200 ms floors above must never run a cue into the next one
        if a["to"] > b["f"]:
            a["to"] = b["f"]
    return cues


def build_cues(asr, glossary, max_chars):
    """-> (cues, applied_fixes). cue = {"t": text, "f": ms, "to": ms, "words": [{"t","f","to"}]}"""
    chars, applied = apply_fixes(load_chars(asr), [tuple(x) for x in glossary["fixes"]])
    return chunk(words(chars), glossary["glue"], max_chars), applied


# ── karaoke words ──────────────────────────────────────────────────────────────────────────

def _is_thai(ch):
    return "\u0E00" <= ch <= "\u0E7F"


def _realign(toks, text, pieces=None):
    """Words no longer rebuild the cue text (a word was corrected, or a break moved).

    Spread each old word's time over its characters, diff against the new text at character level: matching
    characters borrow their old time, replaced ones share the time of the span they replace. Then cut the new
    text into words with newmm. toks = [[text, from, to, space_before], ...].
    pieces = the new text split into lines (joined = text); returns one token list per line (used by resplit)."""
    R, rt = "", []
    for t, f, to, sp in toks:
        if sp:
            R += " "
            rt.append((f, f))
        n = len(t)
        for k in range(n):
            R += t[k]
            rt.append((f + (to - f) * k / n, f + (to - f) * (k + 1) / n))
    tt = [None] * len(text)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, R, text, autojunk=False).get_opcodes():
        if op == "equal":
            tt[j1:j2] = rt[i1:i2]
        elif op in ("replace", "insert") and j2 > j1:
            a = rt[i1][0] if i1 < i2 else (rt[i1 - 1][1] if i1 else rt[0][0])
            b = rt[i2 - 1][1] if i1 < i2 else a
            n = j2 - j1
            tt[j1:j2] = [(a + (b - a) * k / n, a + (b - a) * (k + 1) / n) for k in range(n)]
    groups, pos = [], 0
    for piece in pieces or [text]:              # one line at a time so newmm cannot join words across a break
        out, pending = [], False
        for w in word_tokenize(piece, engine="newmm", keep_whitespace=True):
            span = tt[pos:pos + len(w)]
            pos += len(w)
            if not w.strip():
                pending = True
                continue
            out.append([w, int(min(s[0] for s in span)), int(max(s[1] for s in span)), pending and bool(out)])
            pending = False
        groups.append(out)
    return groups if pieces else groups[0]


def _rebuild(toks):
    return "".join((" " if t[3] else "") + t[0] for t in toks)


def cue_tokens(c):
    """[[text, from, to, space_before], ...] that rebuild c['t'] exactly, or None when that is impossible.

    Thai has no spaces between words, so a wrong space would change how the subtitle looks; when the words
    cannot be matched to the text we return None and the caller shows the plain line."""
    raw = c.get("words") or []
    if not raw:
        return None
    toks, pending_space = [], False
    for w in raw:
        t = w["t"]
        if not t.strip():                       # whitespace-only token: its time goes to the previous word
            if toks:
                toks[-1][2] = w["to"]
            pending_space = True
            continue
        space_before = pending_space or t[:1].isspace()
        pending_space = t[-1:].isspace()
        toks.append([re.sub(r"\s+", " ", t).strip(), w["f"], w["to"], space_before and bool(toks)])
    if not toks:
        return None
    for i in range(1, len(toks)):               # the space _spaced() put between Thai and Latin text
        prev, cur = toks[i - 1][0][-1:], toks[i][0][:1]
        if prev and cur and (_is_thai(prev) != _is_thai(cur)) and (prev.isalnum() or _is_thai(prev)) and (cur.isalnum() or _is_thai(cur)):
            toks[i][3] = True
    if _rebuild(toks) != c["t"]:                # text was corrected after timing: borrow time from matching characters
        try:
            toks = _realign(toks, c["t"])
        except (ValueError, IndexError):
            return None
        if not toks or _rebuild(toks) != c["t"]:
            return None
    return toks


def realign_words(cue, text):
    """New `words` list for `cue` after its text became `text` ([] when the words cannot be matched)."""
    toks = cue_tokens({**cue, "t": text})
    if not toks:
        return []
    return [{"t": (" " if t[3] else "") + t[0], "f": t[1], "to": t[2]} for t in toks]


def _ass_esc(s):
    return s.replace("{", "(").replace("}", ")")


def karaoke_line(c):
    """cue -> ASS text with one {\\kNN} per word (centiseconds, as the ASS spec wants)."""
    toks = cue_tokens(c)
    if toks is None:
        return _ass_esc(c["t"])
    dur_cs = max(1, int(round((c["to"] - c["f"]) / 10)))
    raw_k = [max(1, int(round((t[2] - t[1]) / 10))) for t in toks]
    scale = dur_cs / max(1, sum(raw_k))
    out, spent = [], 0
    for i, t in enumerate(toks):
        if i == len(toks) - 1:
            k = max(1, dur_cs - spent)
        else:
            k = max(1, int(round(raw_k[i] * scale)))
            k = min(k, max(1, dur_cs - spent - (len(toks) - i - 1)))
        spent += k
        out.append(f"{{\\k{k}}}" + ((" " if t[3] else "") + _ass_esc(t[0])))
    return "".join(out)


# ── writers ────────────────────────────────────────────────────────────────────────────────

def ass_color(hexstr):
    """'#RRGGBB' -> ASS '&H00BBGGRR'."""
    h = str(hexstr).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise kit.KitError(f"สีไฮไลต์ซับ \"{hexstr}\" ใช้ไม่ได้ — ใส่เป็น #RRGGBB เช่น #FF7F50")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def style_from_config(cfg):
    sub = cfg["subtitle"]
    return {"font": cfg["brand"]["font"], "size": int(sub["size"]), "margin_v": int(sub["margin_v"]),
            "highlight": sub["highlight"]}


def ts(ms, sep=","):
    h, ms = divmod(int(ms), 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def ass_ts(ms):
    h, ms = divmod(int(ms), 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:d}:{m:02d}:{s:02d}.{ms // 10:02d}"


def lrc_stamp(ms):
    cs = int(round(ms / 10))
    m, cs = divmod(cs, 6000)
    return f"[{m:02d}:{cs / 100:05.2f}]"


_ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary},{secondary},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_header(style, karaoke):
    """Plain: white text. Karaoke: PrimaryColour is the highlight (words already sung), SecondaryColour is white
    (words still to come). PlayRes is 720x1280, libass scales it to any 9:16 video."""
    hi = ass_color(style["highlight"])
    primary, secondary = (hi, "&H00FFFFFF") if karaoke else ("&H00FFFFFF", "&H000000FF")
    return _ASS_HEAD.format(font=str(style["font"]).replace(",", " "), size=int(style["size"]), primary=primary,
                            secondary=secondary, margin_v=int(style["margin_v"]))


def render_srt(cues):
    return "".join(f"{i}\n{ts(c['f'])} --> {ts(c['to'])}\n{c['t']}\n\n" for i, c in enumerate(cues, 1))


def render_ass(cues, style, karaoke=False):
    lines = [f"Dialogue: 0,{ass_ts(c['f'])},{ass_ts(c['to'])},Default,,0,0,0,,"
             f"{karaoke_line(c) if karaoke else _ass_esc(c['t'])}\n" for c in cues]
    return ass_header(style, karaoke) + "".join(lines)


def render_lrc(cues, artist=""):
    head = f"[ar:{artist}]\n" if artist else ""
    return head + "".join(f"{lrc_stamp(c['f'])}{c['t']}\n" for c in cues)


def write_all(cues, base, style, artist=""):
    """base = path without extension. Writes .srt .ass .karaoke.ass .lrc next to it."""
    base = Path(base)

    def out(ext, text):
        (base.parent / (base.name + ext)).write_text(text, encoding="utf-8")

    out(".srt", render_srt(cues))
    out(".ass", render_ass(cues, style))
    out(".karaoke.ass", render_ass(cues, style, karaoke=True))
    out(".lrc", render_lrc(cues, artist))
