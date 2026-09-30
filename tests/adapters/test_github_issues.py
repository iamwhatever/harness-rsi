"""GitHub issue adapter: rows validate, near-duplicates merge, nothing private leaks, no network."""

import datetime as dt
import io
import json
import pathlib
import socket
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.github_issues import adapter  # noqa: E402

REPO = "example-org/example-repo"
FAKE = json.loads((ROOT / "adapters/github_issues/testdata/fake_gh.json").read_text())
NOW = dt.datetime(2026, 1, 10, tzinfo=dt.timezone.utc)
VALIDATOR = Draft202012Validator(json.loads((ROOT / "schemas/signal.schema.json").read_text()))


def fake_fetch(path, params):
    return json.loads(json.dumps(FAKE.get(path, [])))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network or subprocess touched")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def rows():
    return adapter.build_signals(REPO, fake_fetch, window_days=14, now=NOW)


def test_every_row_validates():
    out = rows()
    assert len(out) == 3  # the pull request is skipped
    for row in out:
        assert [e.message for e in VALIDATOR.iter_errors(row)] == []
        assert row["source"] == f"github:{REPO}"
        assert row["layer"] == "real"
        assert row["mentions"]["window_days"] == 14


def test_counts():
    first = rows()[0]
    assert first["id"] == "sig_20260101_0101"
    assert first["links"] == ["https://github.com/example-org/example-repo/issues/101"]
    assert first["mentions"]["count"] == 1 + 3 + 2  # issue + thumbs up + comments
    assert first["mentions"]["people"] == 2  # alice and bob, distinct


def test_near_duplicate_titles_merge():
    a, b, c = rows()
    assert a["dedup_of"] is None
    assert b["dedup_of"] == a["id"]
    assert c["dedup_of"] is None


def test_no_login_email_or_body_in_output():
    buf = io.StringIO()
    assert adapter.main(["--repo", REPO, "--window-days", "14"], fetch=fake_fetch, out=buf) == 0
    text = buf.getvalue()
    for bad in ("fake-alice", "fake-bob", "fake-carol", "fake-dave", "@", "SECRET-", "login", "body"):
        assert bad not in text
    assert len(json.loads(text)) == 3


def test_summary_is_short_and_from_title():
    assert adapter.summarize("[Bug] fix: crash  on start") == "Crash on start"
    assert len(adapter.summarize("x" * 500)) <= adapter.PAIN_MAX
    assert adapter.summarize("") == "Untitled issue"


def test_window_is_passed_to_fetch():
    seen = []
    adapter.build_signals(REPO, lambda p, q: seen.append((p, q)) or [], window_days=7, now=NOW)
    assert seen == [(f"repos/{REPO}/issues", {"state": "open", "since": "2026-01-03T00:00:00Z", "per_page": 100})]


def test_bad_args_refused():
    with pytest.raises(SystemExit):
        adapter.main(["--repo", "not a repo"], fetch=fake_fetch, out=io.StringIO())
