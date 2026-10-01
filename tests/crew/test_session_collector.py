"""Session collector on the miner's fake transcripts: one signal per pain, scope, no text, novelty."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from adapters import sessions  # noqa: E402

FIXTURE = ROOT / "tests" / "exams" / "fixtures" / "transcripts.json"
NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)
ISSUE = "https://github.com/example/repo/issues/1"


@pytest.fixture()
def home(tmp_path):
    root = tmp_path / "home" / "sessions"
    root.mkdir(parents=True)
    for name, rows in json.loads(FIXTURE.read_text(encoding="utf-8")).items():
        (root / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp_path / "home"


def _cache(tmp_path, pains):
    d = tmp_path / "cache"
    d.mkdir()
    issues = {str(n): {"html_url": f"https://github.com/example/repo/issues/{n}", "pain": p}
              for n, p in enumerate(pains, 1)}
    (d / "example__repo.json").write_text(json.dumps({"version": 1, "issues": issues}), encoding="utf-8")
    return d


def _pain(row):
    return next(p for p, text in sessions.DESC.items() if text in row["pain"])


def test_each_pain_type_yields_a_valid_signal(home, tmp_path):
    rows = sessions.collect(home, 0, NOW, _cache(tmp_path, ["Dark mode toggle missing"]))
    assert sorted(_pain(r) for r in rows) == sorted(sessions.miner.PAINS)
    v = Draft202012Validator(json.loads((ROOT / "schemas" / "signal.schema.json").read_text(encoding="utf-8")))
    for r in rows:
        assert [e.message for e in v.iter_errors(r)] == []
        assert r["source"] == "session:owner" and r["layer"] == "real" and r["mentions"]["people"] == 1


def test_excluded_session_kinds_are_skipped(home, tmp_path):
    rows = sessions.collect(home, 0, NOW, _cache(tmp_path, ["unrelated"]))
    assert sum(r["mentions"]["count"] for r in rows) == len(sessions.miner.PAINS)  # owner session only
    for name in ("incognito", "temporary", "app"):
        (home / "sessions" / f"dashboard_{name}.jsonl").rename(home / f"{name}.jsonl")
    assert sessions.collect(home, 0, NOW, tmp_path / "cache") == rows  # removing them changes nothing


def test_no_text_or_session_key_leaks(home, tmp_path):
    out = json.dumps(sessions.collect(home, 0, NOW, _cache(tmp_path, ["unrelated"])))
    for fx in json.loads(FIXTURE.read_text(encoding="utf-8")).values():
        for r in fx[1:]:
            text = str(r.get("content") or "").strip()
            if len(text) > 3:
                assert text not in out
    for leaked in ("dashboard_", "login", "logout", "config key", "wrong file", str(tmp_path)):
        assert leaked not in out


def test_novelty_new_when_no_issue_matches(home, tmp_path):
    rows = sessions.collect(home, 0, NOW, _cache(tmp_path, ["Dark mode toggle missing"]))
    assert all(r["pain"].startswith("NEW") and r["links"] == [sessions.DETECTOR] for r in rows)


def test_novelty_known_links_the_matching_issue(home, tmp_path):
    rows = sessions.collect(home, 0, NOW, _cache(tmp_path, ["Flaky login test fails"]))
    known = {_pain(r) for r in rows if r["pain"].startswith("KNOWN")}
    assert known == {"stop", "steer", "retry", "correction"}  # every pain but the config-key error
    assert all(r["links"] == [ISSUE] for r in rows if r["pain"].startswith("KNOWN"))


def test_without_cache_rows_are_unchecked(home, tmp_path):
    rows = sessions.collect(home, 0, NOW, tmp_path / "missing")
    assert rows and all(r["pain"].startswith("UNCHECKED") for r in rows)


def test_window_drops_old_moments(home, tmp_path):
    assert sessions.collect(home, 1, datetime(2026, 9, 30, tzinfo=timezone.utc), tmp_path) == []


def test_cli_prints_counts_only(home, tmp_path, capsys):
    sessions.main(["--home", str(home), "--days", "0", "--cache-dir", str(_cache(tmp_path, ["unrelated"]))])
    out = json.loads(capsys.readouterr().out)
    assert out == {"signals": 5, "signals_by_novelty": {"new": 5}, "moments_by_novelty": {"new": 5}}
