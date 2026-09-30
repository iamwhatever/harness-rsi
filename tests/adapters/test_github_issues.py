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


class Recorder:
    """A fake GitHub built from the REST fixture: pages of two, every call recorded, a settable budget."""

    def __init__(self, remaining=4000):
        self.issues = json.loads(json.dumps(FAKE[f"repos/{REPO}/issues"]))
        for issue in self.issues:
            comments = FAKE.get(f"repos/{REPO}/issues/{issue['number']}/comments", [])
            issue["commenters"] = [c["user"]["login"] for c in comments]
        self.calls, self.remaining = [], remaining

    def __call__(self, repo, since, states, after):
        self.calls.append((repo, since, list(states), after))
        rows = [i for i in self.issues if (i.get("updated_at") or i["created_at"]) >= since
                and i.get("state", "open").upper() in states]
        start = int(after or 0)
        nxt = str(start + 2) if start + 2 < len(rows) else None
        return json.loads(json.dumps(rows[start:start + 2])), nxt, self.remaining


def fake_fetch(repo, since, states, after):
    return Recorder()(repo, since, states, after)


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path / "data"))

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
    assert adapter.main(["--repo", REPO, "--window-days", "14"], fetch=fake_fetch, out=buf, now=NOW, budget=None) == 0
    text = buf.getvalue()
    for bad in ("fake-alice", "fake-bob", "fake-carol", "fake-dave", "@", "SECRET-", "login", "body"):
        assert bad not in text
    assert len(json.loads(text)) == 3


def test_summary_is_short_and_from_title():
    assert adapter.summarize("[Bug] fix: crash  on start") == "Crash on start"
    assert len(adapter.summarize("x" * 500)) <= adapter.PAIN_MAX
    assert adapter.summarize("") == "Untitled issue"


def test_window_is_passed_to_fetch():
    fake = Recorder()
    adapter.build_signals(REPO, fake, window_days=7, now=NOW)
    assert fake.calls == [(REPO, "2026-01-03T00:00:00Z", ["OPEN"], None)]
    fake.calls.clear()
    assert len(adapter.build_signals(REPO, fake, window_days=14, now=NOW)) == 3
    assert [c[3] for c in fake.calls] == [None, "2"]  # a second page, via the cursor from the first


def test_bad_args_refused():
    with pytest.raises(SystemExit):
        adapter.main(["--repo", "not a repo"], fetch=fake_fetch, out=io.StringIO())


def run(fake, tmp_path, now=NOW, **kw):
    return adapter.build_signals(REPO, fake, 14, now=now, cache_dir=tmp_path / "cache", **kw)


def test_second_run_without_changes_makes_at_most_two_calls(tmp_path):
    fake = Recorder()
    first = run(fake, tmp_path)
    fake.calls.clear()
    assert run(fake, tmp_path, now=NOW + dt.timedelta(hours=1)) == first
    assert len(fake.calls) <= 2
    assert fake.calls[0][1] == "2026-01-09T23:55:00Z"  # the cursor, not the window start


def test_updated_issue_replaces_its_cached_row(tmp_path):
    fake = Recorder()
    run(fake, tmp_path)
    fake.issues[2].update(title="feat(ui): one-step undo of every file edit", comments=2, reactions={"+1": 4},
                          updated_at="2026-01-10T00:30:00Z", commenters=["fake-alice", "fake-erin"])
    fake.calls.clear()
    out = run(fake, tmp_path, now=NOW + dt.timedelta(hours=1))
    assert len(out) == 3 and len(fake.calls) == 1
    row = out[2]
    assert row["pain"] == "One-step undo of every file edit"
    assert row["mentions"]["count"] == 1 + 4 + 2 and row["mentions"]["people"] == 3


def test_closed_issue_leaves_the_output(tmp_path):
    fake = Recorder()
    run(fake, tmp_path)
    fake.issues[0].update(state="closed", updated_at="2026-01-10T00:30:00Z")
    out = run(fake, tmp_path, now=NOW + dt.timedelta(hours=1))
    assert [r["id"] for r in out] == ["sig_20260102_0102", "sig_20260103_0103"]


def test_full_ignores_the_cursor(tmp_path):
    fake = Recorder()
    first = run(fake, tmp_path)
    fake.calls.clear()
    assert run(fake, tmp_path, now=NOW + dt.timedelta(hours=1), full=True) == first
    assert fake.calls[0][1:3] == ("2025-12-27T01:00:00Z", ["OPEN"])  # window start, not the cursor


def test_low_rate_limit_stops_cleanly(tmp_path):
    fake = Recorder()
    run(fake, tmp_path)
    cache = (tmp_path / "cache" / "example-org__example-repo.json").read_text()
    fake.remaining = 5
    out, err = io.StringIO(), io.StringIO()
    code = adapter.main(["--repo", REPO, "--cache-dir", str(tmp_path / "cache"), "--min-remaining", "50"],
                        fetch=fake, out=out, err=err, now=NOW, budget=lambda: fake.remaining)
    assert code == 3 and out.getvalue() == ""
    assert "below the floor of 50" in err.getvalue()
    assert (tmp_path / "cache" / "example-org__example-repo.json").read_text() == cache


def test_incremental_output_still_validates(tmp_path):
    fake = Recorder()
    run(fake, tmp_path)
    for row in run(fake, tmp_path, now=NOW + dt.timedelta(hours=1)):
        assert [e.message for e in VALIDATOR.iter_errors(row)] == []


def test_cache_holds_no_login_or_body(tmp_path):
    run(Recorder(), tmp_path)
    text = (tmp_path / "cache" / "example-org__example-repo.json").read_text()
    for bad in ("fake-alice", "fake-bob", "fake-carol", "SECRET-"):
        assert bad not in text
