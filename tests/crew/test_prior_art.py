"""prior_art with fake GitHub search results: no network."""

import datetime as dt
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
URL = "https://github.com/kirodotdev/KiroCrew"
PAINS = {"reap": (101, "Leaked runtimes are never reclaimed"), "links": (202, "Redaction mangles shared links"),
         "crash": (303, "Crash after upgrade: incomplete bundle")}
SIGNALS = [{"id": f"sig_20261001_{n:04d}", "links": [f"{URL}/issues/{issue}"], "pain": pain}
           for n, (issue, pain) in enumerate(PAINS.values(), 1)]
PROPS = [{"id": f"prop_{k}", "pain": pain, "signal_ids": [f"sig_20261001_{n:04d}"], "exam_ids": [],
          "heat": {"people": 3, "window_days": 14}, "cost": {"files": 2, "lines": 40, "risks": ["r"]}}
         for n, (k, (_, pain)) in enumerate(PAINS.items(), 1)]


def load(name):
    sys.path.insert(0, str(ROOT / "crew"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "crew" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pa = load("prior_art")


def pr(number, state, title, closed=None):
    return {"__typename": "PullRequest", "number": number, "state": state, "title": title,
            "url": f"{URL}/pull/{number}", "closedAt": closed}


def fake_github(linked, hits=None, calls=None):
    """Linked PRs per backing issue and title-search hits per proposal; records each request."""
    def fetch(query, variables):
        (calls if calls is not None else []).append((query, variables))
        data = {"rateLimit": {"remaining": 4000}}
        for i, (k, (issue, _)) in enumerate(PAINS.items()):
            data[f"p{i}"] = {"nodes": (hits or {}).get(k, [])}
            data[f"l{i}"] = {f"n{issue}": {"closedByPullRequestsReferences": {"nodes": linked.get(issue, [])}}}
        return data
    return fetch


def run(fetch, **kw):
    return pa.search(PROPS, SIGNALS, fetch, today=dt.date(2026, 10, 1), **kw)


def test_fixed_on_main_is_dropped():
    res = run(fake_github({303: [pr(13268, "MERGED", "retry while the bundled backend installs", "2026-09-26T22:52Z")]}))
    kept, dropped = pa.apply(PROPS, res)
    assert [p["id"] for p in kept] == ["prop_reap", "prop_links"]
    assert dropped == [{"id": "prop_crash", "number": 13268, "url": f"{URL}/pull/13268"}]


def test_open_pr_is_marked_and_card_says_help_land():
    res = run(fake_github({202: [pr(9811, "OPEN", "stop redacting shared document links")]}))
    kept, dropped = pa.apply(PROPS, res)
    assert not dropped and kept[1]["cost"]["risks"][-1].startswith("prior_art: open PR #9811")
    page = load("run_round").render_mock(kept[1], [], res["prop_links"])
    assert "Next step: help land #9811" in page and "Next step: build" not in page


def test_closed_attempt_keeps_a_link():
    res = run(fake_github({101: [pr(12612, "CLOSED", "reclaim untracked agent runtimes", "2026-09-30T20:57Z")]}))
    kept, _ = pa.apply(PROPS, res)
    assert kept[0]["cost"]["risks"][-1] == f"prior_art: closed attempt #12612 ({URL}/pull/12612)"
    assert "Next step: build" in (page := load("run_round").render_mock(kept[0], [], res["prop_reap"])) and f"#12612 <a href=\"{URL}/pull/12612\"" in page


def test_one_batched_request_window_and_title_only_hits():
    old = pr(50, "CLOSED", "reclaim leaked runtimes", "2026-06-01T00:00Z")  # outside 60 days
    title_only = pr(60, "MERGED", "reclaim leaked runtimes faster", "2026-09-20T00:00Z")
    res = run(fake_github({101: [old]}, {"reap": [title_only]}, calls := []))
    (query, variables), = calls
    assert query.count("search(") == 3 and "rateLimit" in query
    assert "updated:>=2026-08-02 in:title" in variables["q0"]
    assert [m["number"] for m in res["prop_reap"]["matches"]] == [60]
    assert res["prop_reap"]["verdict"] is None  # a title-only match decides nothing
    assert '"number": 60' in pa.reviewer_block(res)
    with pytest.raises(pa.RateLimitLow):  # below the floor: no request at all
        run(fake_github({}, calls=calls), budget=lambda: 10)
    assert len(calls) == 1


def test_debate_gives_risk_reviewer_the_prior_art():
    rr, seen = load("run_round"), []

    def agent(name, message):
        seen.append((name, message))
        return '```json\n[{"id": "prop_links", "pain": "Redaction mangles shared links"}]\n```'

    out = rr.debate(agent, [], lambda rows, _: {r["id"]: {"verdict": None, "matches": []} for r in rows})
    assert list(out["prior_art"]) == ["prop_links"]
    assert "Prior art in KiroCrew" in [m for n, m in seen if n == rr.RISK][-1]
