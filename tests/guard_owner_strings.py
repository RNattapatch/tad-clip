"""CI guard: no owner-specific strings anywhere in the kit (shared context · Global Constraints), any letter case.

  python tests/guard_owner_strings.py      # scans `git ls-files`; exit 1 + one line per hit

Allowed: credit lines / links (@pun_nattapatch, punnattapatch.com), the preset name pun-navy-coral and its file,
and the marketplace id pun-nattapatch.
The patterns are spelled so this file does not match itself.
"""
import re
import subprocess
import sys

BAD = re.compile("|".join([
    "".join(map(chr, (0x0E1B, 0x0E31, 0x0E19))),   # the owner's Thai nickname
    r"\bp[u]n\b", r"agent[m]acmini", r"100\.64\.172\.7", r"documents/[c]ontent",
    r"tele[g]ram", r"\bcode[x]\b", r"\bki[e]\b", r"\.herme[s]\b",
]), re.IGNORECASE)
CREDIT = re.compile(r"@pun_nattapatch|punnattapatch\.com", re.IGNORECASE)
# names locked by the spec: the preset and the plugin marketplace id
NAMES = re.compile(r"pun-navy-coral|pun-nattapatch", re.IGNORECASE)
ALLOWED_FILES = {"presets/pun-navy-coral.json"}


def scan(files: dict) -> list:
    """files = {repo path: text or None (binary)} → ["path:line: text", …]"""
    hits = []
    for name, text in files.items():
        if text is None or name in ALLOWED_FILES:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if CREDIT.search(line):
                continue
            if BAD.search(NAMES.sub("", line)):
                hits.append(f"{name}:{n}: {line.strip()[:140]}")
    return hits


def _read(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (UnicodeDecodeError, OSError):
        return None   # binary (video, font, audio)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")
    names = [f for f in subprocess.run(["git", "ls-files", "-z"], capture_output=True).stdout.decode("utf-8").split("\0") if f]
    hits = scan({f: _read(f) for f in names})
    print("\n".join(hits) if hits else f"ok: {len(names)} files, no owner-specific strings")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
