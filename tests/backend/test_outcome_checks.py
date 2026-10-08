"""GET /outcomes carries each linked PR's check rollup: one gh call, cached, never fatal."""

import asyncio
import json
import subprocess

import pytest

from backend import ledger, outcome_checks, routes


def gh_answer(states):
    """A fake ``gh api graphql`` runner answering ``states`` ({"owner/name#N": rollup state or None})."""
    calls = []

    def run(argv, **kw):
        calls.append(argv[-1])
        q = argv[-1]
        data = {}
        repos = sorted({p.split("#")[0] for p in states})
        for i, repo in enumerate(repos):
            owner, name = repo.split("/")
            if f'repository(owner: "{owner}", name: "{name}")' not in q:
                continue
            pulls = {}
            for pr, st in states.items():
                n = pr.split("#")[1]
                if pr.startswith(repo + "#") and f"p{n}: pullRequest(number: {n})" in q:
                    pulls[f"p{n}"] = {"commits": {"nodes": [{"commit": {"statusCheckRollup": None if st is None else {"state": st}}}]}}
            data[f"r{i}"] = pulls
        return subprocess.CompletedProcess(argv, 0, json.dumps({"data": data}), "")
    run.calls = calls
    return run


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path))
    return tmp_path


ROWS = [{"pr": "o/r#1", "state": "open"}, {"pr": "o/r#2", "state": "merged"}, {"pr": "o/r#3", "state": "open"}, {"pr": "x/y#9", "state": "closed"}]


def test_one_call_maps_every_state_and_caches_it(env):
    run = gh_answer({"o/r#1": "SUCCESS", "o/r#2": "FAILURE", "o/r#3": "PENDING", "x/y#9": None})
    got = outcome_checks.read(ROWS, now=lambda: 1000.0, run=run)
    assert got["checks"] == {"o/r#1": "pass", "o/r#2": "fail", "o/r#3": "pending", "x/y#9": "none"}
    assert got["error"] == "" and got["read_at"].startswith("1970-01-01T00:16:40") and len(run.calls) == 1
    # Within the TTL nothing is read again.
    assert outcome_checks.read(ROWS, now=lambda: 1000.0 + outcome_checks.TTL_S - 1, run=run)["checks"] == got["checks"]
    assert len(run.calls) == 1


def test_after_the_ttl_only_open_or_unsettled_prs_are_read_again(env):
    outcome_checks.read(ROWS, now=lambda: 0.0, run=gh_answer({"o/r#1": "SUCCESS", "o/r#2": "FAILURE", "o/r#3": "PENDING", "x/y#9": None}))
    run = gh_answer({"o/r#1": "FAILURE", "o/r#3": "SUCCESS", "o/r#2": "SUCCESS", "x/y#9": "SUCCESS"})
    got = outcome_checks.read(ROWS, now=lambda: float(outcome_checks.TTL_S), run=run)
    assert got["checks"] == {"o/r#1": "fail", "o/r#2": "fail", "o/r#3": "pass", "x/y#9": "none"}
    assert "number: 2)" not in run.calls[0] and "x/y" not in run.calls[0]  # merged/closed and settled: not read again


def test_a_failing_gh_keeps_the_cache_and_says_why(env):
    outcome_checks.read(ROWS[:1], now=lambda: 0.0, run=gh_answer({"o/r#1": "SUCCESS"}))
    fail = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "API rate limit exceeded")  # noqa: E731
    got = outcome_checks.read(ROWS[:2], now=lambda: 10_000.0, run=fail)
    assert got["checks"] == {"o/r#1": "pass"} and got["error"] == "ci: GitHub rate limit"
    boom = lambda argv, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(argv, 20))  # noqa: E731
    assert outcome_checks.read(ROWS[:2], now=lambda: 20_000.0, run=boom)["error"] == "ci: TimeoutExpired"


def test_only_well_formed_pr_ids_reach_the_query(env):
    assert outcome_checks.parse('o"/r#1') is None and outcome_checks.parse("o/r#x") is None and outcome_checks.parse("o/r#12") == ("o", "r", 12)
    run = gh_answer({})
    got = outcome_checks.read([{"pr": 'evil"){x}/r#1', "state": "open"}], now=lambda: 0.0, run=run)
    assert got == {"checks": {}, "read_at": None, "error": ""} and run.calls == []


def test_outcomes_route_returns_ci_and_survives_gh_failing(env, monkeypatch):
    (env / "proposals.json").write_text("[]", encoding="utf-8")
    ledger.link(None, "kirodotdev/KiroCrew#5", "board")
    req = {"app": "harness-rsi"}
    body = json.loads(asyncio.run(routes._outcomes(req, None)).text)  # conftest: gh is off
    assert body["ok"] and body["ci"] == {"checks": {}, "read_at": None, "error": "ci: gh exit 1"}
    monkeypatch.setattr(outcome_checks, "RUN", gh_answer({"kirodotdev/KiroCrew#5": "SUCCESS"}))
    body = json.loads(asyncio.run(routes._outcomes(req, None)).text)
    assert body["ci"]["checks"] == {"kirodotdev/KiroCrew#5": "pass"} and body["ci"]["error"] == ""
