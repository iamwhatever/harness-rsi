"""Backend routes on fake data: schema-valid reads, decision append, refresh merge, nothing on import."""

import asyncio
import copy
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import routes, store  # noqa: E402

SIGNALS = json.loads((ROOT / "fixtures/signals.json").read_text())
PROPOSALS = json.loads((ROOT / "fixtures/proposals.json").read_text())


class Req(dict):
    """The parts of an aiohttp request the handlers read; no server, no socket."""

    def __init__(self, body=None, **flags):
        super().__init__({"app": "harness-rsi", **flags})
        self._body, self.content_length = body, len(json.dumps(body)) if body is not None else 0

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def call(handler, req=None):
    resp = asyncio.run(handler(req or Req(), None))
    return resp.status, json.loads(resp.text)


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path))
    (tmp_path / "signals.jsonl").write_text("".join(json.dumps(r) + "\n" for r in SIGNALS) + "not json\n{}\n")
    (tmp_path / "proposals.json").write_text(json.dumps(PROPOSALS + [{"id": "bad"}]))
    return tmp_path


def test_missing_data_dir_is_empty_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path / "nope"))
    assert call(routes._signals) == (200, {"ok": True, "signals": []})
    assert call(routes._proposals) == (200, {"ok": True, "proposals": []})


def test_reads_are_schema_valid_and_signals_hottest_first(data):
    _, sig = call(routes._signals)
    _, prop = call(routes._proposals)
    assert sorted(r["id"] for r in sig["signals"]) == sorted(r["id"] for r in SIGNALS)  # bad lines dropped
    assert all(store.valid("signal", r) for r in sig["signals"])
    assert [p["id"] for p in prop["proposals"]] == [p["id"] for p in PROPOSALS]
    assert all(store.valid("proposal", p) for p in prop["proposals"])
    primary = [r for r in sig["signals"] if not r["dedup_of"]]
    assert sig["signals"][: len(primary)] == primary
    assert [r["mentions"]["people"] for r in primary] == sorted((r["mentions"]["people"] for r in primary), reverse=True)


def test_decision_append_is_idempotent_per_click(data):
    body = {"proposal_id": "prop_bg_tasks", "decision": "skip"}
    assert call(routes._decide, Req(body)) == (200, {"ok": True, "appended": True})
    assert call(routes._decide, Req(body)) == (200, {"ok": True, "appended": False})  # a repeated click
    call(routes._decide, Req({**body, "decision": "do"}))
    rows = [json.loads(x) for x in (data / "decisions.jsonl").read_text().splitlines()]
    assert [(r["proposal_id"], r["decision"]) for r in rows] == [("prop_bg_tasks", "skip"), ("prop_bg_tasks", "do")]
    assert all(set(r) == {"proposal_id", "decision", "ts"} for r in rows)
    assert call(routes._proposals)[1]["proposals"][0]["decision"] == "do"


@pytest.mark.parametrize("req,status", [
    (Req({"proposal_id": "prop_bg_tasks", "decision": "maybe"}), 400),
    (Req({"proposal_id": "prop_nope", "decision": "do"}), 404),
    (Req({"proposal_id": "prop_bg_tasks", "decision": "do"}, internal_auth=True), 403),
    (Req({"proposal_id": "prop_bg_tasks", "decision": "do"}, app="other-app"), 403),
])
def test_decision_refusals_write_nothing(data, req, status):
    assert call(routes._decide, req)[0] == status
    assert not (data / "decisions.jsonl").exists()


def row(sid, source, link, pain, dedup_of=None, people=1):
    return {"id": sid, "source": source, "links": [f"https://example.com/{link}"], "pain": pain,
            "mentions": {"count": people, "people": people, "window_days": 1}, "layer": "real",
            "testable": {"ok": False, "task": None}, "dedup_of": dedup_of}


def test_refresh_merges_and_dedups_stubbed_sources(data, monkeypatch):
    slack = [row("sig_20260101_0001", "slack:C0FAKE00003", "s/1", "Brand new pain", people=3),
             row("sig_20260101_0002", "slack:C0FAKE00003", "s/2", "brand new pain!", "sig_20260101_0001"),
             row("sig_20260101_0099", "github:x/y", "s/3", "not from slack"), {"id": "junk"}]
    fresh = copy.deepcopy(SIGNALS[6])
    fresh["mentions"]["count"] = 9  # the same issue again, with a new count
    github = [fresh, row("sig_20260101_0050", "github:example-org/example-repo", "g/50", "Brand New Pain")]
    monkeypatch.setattr(routes, "github_rows", lambda: (github, ""))
    status, doc = call(routes._refresh, Req({"slack": slack}))
    assert status == 200 and doc["errors"] == [] and doc["added"] == 3
    rows = [json.loads(x) for x in (data / "signals.jsonl").read_text().splitlines()]
    assert all(store.valid("signal", r) for r in rows) and len({r["id"] for r in rows}) == len(rows) == 13
    by_link = {r["links"][0]: r for r in rows}
    head = by_link["https://example.com/s/1"]
    assert head["id"] not in {r["id"] for r in SIGNALS} and head["dedup_of"] is None  # clashing id replaced
    assert by_link["https://example.com/s/2"]["dedup_of"] == head["id"]
    assert by_link["https://example.com/g/50"]["dedup_of"] == head["id"]  # same pain across sources
    assert by_link[SIGNALS[6]["links"][0]]["mentions"]["count"] == 9 and by_link[SIGNALS[6]["links"][0]]["id"] == SIGNALS[6]["id"]
    assert call(routes._refresh, Req({"slack": slack}))[1]["added"] == 0  # a second refresh adds nothing


def test_refresh_needs_owner_and_a_body(data):
    assert call(routes._refresh, Req({"slack": []}, internal_auth=True))[0] == 403
    assert call(routes._refresh, Req({"slack": "x"}))[0] == 400


def test_disabled_app_registers_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path / "d"))
    m = json.loads((ROOT / "app.json").read_text())
    assert m["defaultEnabled"] is False and set(m["backend"]["hooks"]) == {"routes"}  # no startup work to run
    assert not (tmp_path / "d").exists()  # importing the backend touched no data
    fake = types.ModuleType("kiro_crew.apps.route_registry")
    fake.AppRoute = lambda **kw: kw
    monkeypatch.setitem(sys.modules, "kiro_crew.apps.route_registry", fake)
    got = [(r["method"], r["path"]) for r in routes.register_routes(None)]
    assert got == [("GET", "/signals"), ("GET", "/proposals"), ("POST", "/decisions"), ("POST", "/refresh")]
    assert not (tmp_path / "d").exists()
