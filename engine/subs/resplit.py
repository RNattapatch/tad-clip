"""Move a line break between cues (or split one cue) without transcribing again.

Word times are borrowed from the old characters by make_subs._realign(). Used by apply_edits for the
`resplit` entries of edits.json.
"""
from .make_subs import LEAD, _realign


def resplit_group(group, new):
    """group = consecutive cue dicts, new = replacement lines -> list of new cue dicts.

    The first new cue starts where the group started, the last one ends where it ended, and the breaks in
    between follow the first word of the next line (minus LEAD). Raises ValueError when the new lines cannot
    be timed (the caller turns that into a rejection)."""
    raw = []
    for c in group:
        for w in c["words"]:
            if w["t"].strip():
                raw.append([w["t"].strip(), w["f"], w["to"], w["t"][:1].isspace() and bool(raw)])
    if not raw:
        raise ValueError("cue เดิมไม่มีเวลาของคำ")
    out = []
    for t, toks in zip(new, _realign(raw, "".join(new), pieces=new)):
        ws = [{"t": (" " if tk[3] else "") + tk[0], "f": tk[1], "to": tk[2]} for tk in toks]
        if not ws or "".join(w["t"] for w in ws) != t:
            raise ValueError(f"ประกอบกลับไม่ตรง: {t!r}")
        out.append({"t": t, "words": ws})
    out[0]["f"], out[-1]["to"] = group[0]["f"], group[-1]["to"]
    for a, b in zip(out, out[1:]):
        b["f"] = max(a["f"] + 200, b["words"][0]["f"] - LEAD)
        a["to"] = b["f"]
    return out
