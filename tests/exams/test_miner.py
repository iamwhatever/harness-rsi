"""Miner tests on a fake transcript fixture: detection, schema validity, output location, scope."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "transcripts.json"
NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)

_spec = importlib.util.spec_from_file_location("exam_miner", ROOT / "exams" / "miner" / "mine.py")
miner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(miner)


@pytest.fixture()
def home(tmp_path):
    sessions = tmp_path / "home" / "sessions"
    sessions.mkdir(parents=True)
    for name, rows in json.loads(FIXTURE.read_text()).items():
        (sessions / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp_path / "home"


@pytest.fixture()
def rows(home):
    return miner.mine(home, 0, NOW)


def _pain(row):
    return row["id"].split("_")[1]


def test_each_pain_type_is_detected_once(rows):
    assert sorted(_pain(r) for r in rows) == sorted(miner.PAINS)


def test_rows_validate_as_hidden_layer_two_exams(rows):
    schema = json.loads((ROOT / "schemas" / "exam.schema.json").read_text())
    validator = Draft202012Validator(schema)
    for row in rows:
        assert [e.message for e in validator.iter_errors(row)] == []
        assert row["layer"] == 2 and row["visibility"] == "hidden" and row["used_rounds"] == []
    assert len({r["id"] for r in rows}) == len(rows)


def test_testable_rows_replay_the_verify_command(rows):
    checks = {_pain(r): r["check"] for r in rows}
    for pain in ("stop", "steer", "retry"):
        assert checks[pain] == {"kind": "exit_code", "cmd": ["bash", "-c", "pytest tests/test_login.py"], "expect": 0, "timeout_s": 1800}
    assert checks["error"]["kind"] == checks["correction"]["kind"] == "metric_threshold"
    assert checks["error"]["metric"] == "error_rate"


def test_excluded_session_kinds_are_skipped(rows):
    text = " ".join(r["task"] for r in rows)
    for leaked in ("incognito", "Temporary", "App opened", "App steer"):
        assert leaked not in text


def test_transient_retry_is_not_a_turn_error(home, tmp_path):
    path = home / "sessions" / "dashboard_owner.jsonl"
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n")
    assert "error" not in {_pain(r) for r in miner.mine(home, 0, NOW)}


def test_days_window_drops_old_moments(home):
    assert miner.mine(home, 1, datetime(2026, 9, 30, tzinfo=timezone.utc)) == []


def test_output_dir_must_be_outside_the_repo(tmp_path):
    assert miner.seed_dir(str(tmp_path)) == tmp_path.resolve() / "exams" / "seed"
    for inside in (ROOT, ROOT / "exams"):
        with pytest.raises(SystemExit):
            miner.seed_dir(str(inside))
    with pytest.raises(SystemExit):
        miner.seed_dir(None)


def test_mined_paths_are_git_ignored():
    for rel in ("exams/seed/exam_x.json", "exams/miner/out/exam_x.json", "exams/x.jsonl"):
        assert subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT).returncode == 0, rel


def test_cli_writes_rows_and_prints_only_numbers(home, tmp_path):
    data = tmp_path / "data"
    env = {**os.environ, "HARNESS_RSI_DATA": str(data)}
    out = subprocess.run([sys.executable, str(ROOT / "exams" / "miner" / "mine.py"), "--home", str(home)],
                         env=env, capture_output=True, text=True, check=True).stdout
    report = json.loads(out)
    assert report["candidates"] == 5 and report["testable"] == 3
    assert report["by_pain"] == {p: 1 for p in miner.PAINS}

    def numbers(node):
        if isinstance(node, dict):
            return all(k in miner.PAINS or k in report for k in node) and all(numbers(v) for v in node.values())
        return isinstance(node, int)

    assert numbers(report)
    assert "login" not in out and "wrong file" not in out
    written = sorted((data / "exams" / "seed").glob("exam_*.json"))
    assert len(written) == 5
