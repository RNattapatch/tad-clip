"""CI must not go green without checking: the smoke's SKIP/QC contract and the owner-string guard."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


smoke = _load(ROOT / "tests" / "smoke" / "smoke.py", "tadclip_smoke")
guard = _load(ROOT / "tests" / "guard_owner_strings.py", "tadclip_guard")


# ---------------------------------------------------------------- smoke
def test_skip_fails_ci_unless_allowed(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "NEEDS", {"engine/not-there-yet.py": "Task 99"})
    assert smoke.main([]) != 0
    assert "SKIP" in capsys.readouterr().out
    assert smoke.main(["--allow-skip"]) == 0


@pytest.mark.parametrize("report, ok", [
    ({"ok": True, "checks": [{"id": "duration", "name": "ความยาว", "status": "ok", "detail": "15 วิ"}]}, True),
    ({"ok": True, "checks": []}, False),                       # nothing was checked
    ({}, False),                                               # doctor printed nothing parseable
    ({"ok": False, "checks": [{"id": "peak", "name": "x", "status": "ok", "detail": ""}]}, False),
    ({"ok": True, "checks": [{"id": "black", "name": "x", "status": "fail", "detail": "จอดำ"}]}, False),
])
def test_qc_report_must_be_non_empty_and_ok(report, ok):
    assert (smoke.qc_problems(report) == []) is ok


def test_qc_report_from_unparseable_text_is_a_problem():
    assert smoke.qc_problems(smoke.parse_report("Traceback (most recent call last):")) != []
    assert smoke.qc_problems(smoke.parse_report(json.dumps({"ok": True, "checks": [{"id": "a", "status": "ok"}]}))) == []


# ---------------------------------------------------------------- owner-string guard (built from pieces so this file passes it)
TH = chr(0x0e1b) + chr(0x0e31) + chr(0x0e19)


@pytest.mark.parametrize("line", [
    "made by " + "P" + "UN", "by " + "p" + "un today", "ส่งให้ " + TH, "send via " + "TELE" + "GRAM",
    "ask " + "co" + "dex", "image from " + "K" + "ie", "~/." + "her" + "mes/.env", "ssh agent" + "macmini@host",
    "/Users/x/Documents/" + "Content/clips", "100.64." + "172.7",
])
def test_guard_catches_owner_strings_in_any_case(line):
    assert guard.scan({"README.md": line})


@pytest.mark.parametrize("line", [
    "kit by @p" + "un_nattapatch", "https://p" + "unnattapatch.com/free/tad-clip", "/plugin install tad-clip@p" + "un-nattapatch",
    'preset "p' + 'un-navy-coral"', "punctuation and cookies are fine", "kitchen sink", "codexes",
])
def test_guard_exemptions_and_innocent_words(line):
    assert guard.scan({"docs/x.md": line}) == []


def test_guard_allows_the_preset_file_and_skips_binaries():
    assert guard.scan({"presets/p" + "un-navy-coral.json": '{"label": "' + TH + '"}'}) == []
    assert guard.scan({"tests/fixtures/clip.mp4": None}) == []



# ---------------------------------------------------------------- workflow triggers + caches (fix round 3)
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_pull_requests_run_only_from_forks():
    assert "pull_request:" in CI
    assert "github.event.pull_request.head.repo.fork" in CI    # same-repo PRs already ran on push


def test_model_cache_key_names_the_model_per_backend():
    assert "faster-whisper-base" in CI and "ggml-base" in CI
