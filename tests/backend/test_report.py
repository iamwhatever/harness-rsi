"""The web side's backend: topics validation, the catch-up report (built from signals, no agent) and its routes."""

import asyncio
import datetime as dt
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import report, round_job, routes, store, topics  # noqa: E402

SIGNALS = json.loads((ROOT / "fixtures/signals.json").read_text())
PROPOSALS = json.loads((ROOT / "fixtures/proposals.json").read_text())
FIXTURE = json.loads((ROOT / "fixtures/report.json").read_text())
NOW = dt.datetime(2026, 1, 6, 9, 0, tzinfo=dt.timezone.utc)


class Req(dict):
    def __init__(self, body=None, **flags):
        super().__init__({"app": "harness-rsi", **flags})
        self._body, self.content_length = body, len(json.dumps(body)) if body is not None else 0

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def text(resp):
    return resp.status, json.loads(resp.text)


class Route(dict):
    def __init__(self, **kw):
        super().__init__(kw)


HANDLERS = {(r["method"], r["path"]): r["handler"] for r in report.routes(Route, routes._owner, routes._err, routes._save)}


def call(method, path, req=None):
    return text(asyncio.run(HANDLERS[(method, path)](req or Req(), None)))


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path))
    (tmp_path / "signals.jsonl").write_text("".join(json.dumps(r) + "\n" for r in SIGNALS))
    (tmp_path / "proposals.json").write_text(json.dumps(PROPOSALS))
    monkeypatch.setattr(report, "JOB", {**report.JOB, "task": None, "running": False})
    monkeypatch.setattr(round_job, "STATE", {**round_job.STATE, "task": None, "running": False})
    return tmp_path


def test_topics_validate_normalise_and_refuse():
    conf, errors = topics.validate({"topics": [" coding   agent ", "MCP", "MCP"], "sites": ["https://www.Example.com/blog", "news.example.org"],
                                    "x_handles": ["@example_1"]}, topics.DEFAULTS)
    assert errors == [] and conf == {"topics": ["coding agent", "MCP"], "sites": ["example.com", "news.example.org"], "x_handles": ["example_1"]}
    for bad in ({"topics": []}, {"topics": ['"x" OR stars:>1']}, {"topics": ["a" * 61]}, {"topics": [f"t{i}" for i in range(11)]},
                {"sites": ["not a site"]}, {"sites": "example.com"}, {"x_handles": ["has space"]}, {"x_handles": ["a" * 16]}):
        assert topics.validate(bad, topics.DEFAULTS)[1], bad


def test_topics_read_defaults_without_a_vault_and_brief_names_each_one():
    assert topics.read() == topics.DEFAULTS
    brief = topics.brief({"topics": ["MCP"], "sites": ["example.com"], "x_handles": ["example"]})
    assert "- MCP" in brief and "- example.com" in brief and "- @example" in brief
    assert "Trusted sites" not in topics.brief(topics.DEFAULTS) and "X accounts" not in topics.brief(topics.DEFAULTS)


def test_report_is_built_from_signals_web_first_and_the_fixture_matches():
    doc = report.build(SIGNALS, PROPOSALS, ["https://example.com/fake/paywalled/1", "http://plain", 3], topics.DEFAULTS, NOW, "round", 4)
    assert doc == FIXTURE, "fixtures/report.json is report.build over the fixtures (regenerate it when build changes)"
    assert [i["kind"] for i in doc["trends"]] == ["web", "web", "web", "repo", "release"]  # web pages rank first
    assert doc["not_reachable"] == ["https://example.com/fake/paywalled/1"]
    undo = next(i for i in doc["trends"] if i["card"])
    assert undo["card"]["id"] == "prop_one_step_undo" and "card prop_one_step_undo picks this up" in doc["markdown"]
    for head in ("# Industry catch-up, 2026-01-06", "## Top 5 trends", "## New repos and releases", "## What it may mean for us",
                 "## Not reachable", "## Links"):
        assert head in doc["markdown"]
    assert doc["slack"].startswith("Industry catch-up, 2026-01-06 (coding agent, MCP, agent harness)\n1. ")


def test_untrusted_pain_text_stays_one_line():
    row = {**SIGNALS[3], "pain": "line one\n## injected heading\x07"}
    doc = report.build([row], [], [], topics.DEFAULTS, NOW, "button")
    assert doc["trends"][0]["pain"] == "line one ## injected heading" and "\n## injected" not in doc["markdown"]
    assert "Made 2026-01-06T09:00:00+00:00 by the Make catch-up report button." in doc["markdown"]


def test_empty_report_names_the_next_step(data):
    (data / "signals.jsonl").write_text("")
    doc = report.write("button", now=NOW)
    assert doc["trends"] == [] and "None yet: run a round." in doc["markdown"] and doc["slack"].endswith("No web trends yet.")


def test_write_then_get_latest(data):
    assert call("GET", "/report") == (200, {"ok": True, "report": None, "job": report.view()})
    (data / "web_reach.json").write_text(json.dumps({"not_reachable": ["https://example.com/fake/paywalled/1"]}))
    report.write("round", 4, now=NOW)
    report.write("button", now=NOW + dt.timedelta(days=7))
    assert sorted(p.name for p in (data / "reports").iterdir()) == ["2026-01-06.json", "2026-01-06.md", "2026-01-13.json", "2026-01-13.md"]
    status, body = call("GET", "/report")
    assert status == 200 and body["report"]["date"] == "2026-01-13" and body["report"]["how"] == "button"
    assert body["report"]["not_reachable"] == ["https://example.com/fake/paywalled/1"]
    assert (data / "reports" / "2026-01-06.md").read_text() == FIXTURE["markdown"]


def test_make_is_owner_only_single_flight_and_merges_rising_repos(data, monkeypatch):
    trend = {**SIGNALS[10], "id": "sig_20261009_0001", "links": ["https://github.com/example-org/new-one"],
             "source": "trend:github:example-org/new-one", "pain": "Fast-rising MCP repo example-org/new-one: 80 stars in 2 days"}
    monkeypatch.setattr(report, "trending_rows", lambda: ([trend], "trending: a note"))
    assert call("POST", "/report/make", Req(internal_auth=True))[0] == 403
    assert call("POST", "/report/make", Req(app="other-app"))[0] == 403
    round_job.STATE["running"] = True
    assert call("POST", "/report/make")[1]["code"] == "round_running"
    round_job.STATE["running"] = False

    async def go():
        first = text(await HANDLERS[("POST", "/report/make")](Req(), None))
        second = text(await HANDLERS[("POST", "/report/make")](Req(), None))
        await report.JOB["task"]
        return first, second
    first, second = asyncio.run(go())
    assert first[0] == 202 and first[1]["job"]["running"] is True
    assert second == (409, {"ok": False, "code": "report_running", "error": "a report is already being made"})
    assert report.view()["running"] is False and report.view()["error"] == "trending: a note"
    assert any(r["links"] == trend["links"] for r in store.read_signals()), "the button's rising repos join the signal list"
    assert report.latest()["how"] == "button" and report.latest()["counts"]["repos"] == 2


def test_topics_routes_read_defaults_and_refuse_non_owner_and_bad_body(data):
    assert call("GET", "/topics") == (200, {"ok": True, "topics": topics.DEFAULTS})
    assert call("POST", "/topics", Req({"topics": ["x"]}, internal_auth=True))[0] == 403
    status, body = call("POST", "/topics", Req({"topics": []}))
    assert status == 400 and body["code"] == "bad_topics"
    assert call("POST", "/topics", Req({"topics": ["MCP"]}))[1]["code"] == "no_vault"  # no gateway vault in tests


def test_trending_rows_reads_the_owners_topics(monkeypatch):
    seen = []
    monkeypatch.setattr(topics, "read", lambda: {"topics": ["MCP"], "sites": [], "x_handles": []})
    monkeypatch.setattr(report.adapters.trending, "collect", lambda t: seen.append(t) or ([], ["trending: x"]))
    assert report.trending_rows() == ([], "trending: x") and seen == [["MCP"]]
    monkeypatch.setattr(report.adapters.trending, "collect", lambda t: 1 / 0)
    assert report.trending_rows() == ([], "trending: ZeroDivisionError")
