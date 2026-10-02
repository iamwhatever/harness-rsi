"""Outcome routes: owner-only link, the ledger read, and the single-flight scoring job."""

import asyncio
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import routes  # noqa: E402

PROPOSALS = json.loads((ROOT / "fixtures/proposals.json").read_text(encoding="utf-8"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path))
    (tmp_path / "proposals.json").write_text(json.dumps(PROPOSALS), encoding="utf-8")
    return tmp_path


class Req(dict):
    def __init__(self, body=None, **flags):
        super().__init__({"app": "harness-rsi", **flags})
        self._body, self.content_length = body, 10

    async def json(self):
        return self._body


def call(handler, req):
    resp = asyncio.run(handler(req, None))
    return resp.status, json.loads(resp.text)


def test_link_route_is_owner_only_and_checked(env):
    assert call(routes._link, Req({"proposal_id": "prop_bg_tasks", "pr": 5}, internal_auth=True))[0] == 403
    assert call(routes._link, Req({"proposal_id": "prop_bg_tasks", "pr": "5"}))[0] == 400
    assert call(routes._link, Req({"proposal_id": "prop_nope", "pr": 5}))[0] == 404
    status, body = call(routes._link, Req({"proposal_id": "prop_bg_tasks", "pr": 5}))
    assert status == 200 and body["outcome"]["pr"] == "kirodotdev/KiroCrew#5"
    assert [r["pr"] for r in call(routes._outcomes, Req())[1]["outcomes"]] == ["kirodotdev/KiroCrew#5"]


def test_score_job_is_single_flight(env, monkeypatch):
    calls = []
    monkeypatch.setattr(routes, "score_run", lambda kc: (calls.append(kc), ([{"pr": "x"}], ""))[1])

    async def go():
        assert routes.start_score("/kc") and not routes.start_score("/kc")
        await routes.SCORE["task"]
        return json.loads((await routes._outcomes(Req(), None)).text)["score"]
    view = asyncio.run(go())
    assert calls == ["/kc"] and view["running"] is False and view["updated"] == [{"pr": "x"}]


@pytest.mark.parametrize("conf,want", [({"score_enabled": False, "kirocrew_dir": "/kc"}, "off"),
                                       ({"score_enabled": True, "kirocrew_dir": ""}, "off"),
                                       ({"score_enabled": True, "kirocrew_dir": "/kc"}, "started")])
def test_tick_starts_scoring_only_when_the_owner_turned_it_on(env, monkeypatch, conf, want):
    from backend import schedule
    started = []
    monkeypatch.setattr(schedule, "read", lambda: {**schedule.DEFAULTS, **conf})
    monkeypatch.setattr(routes, "start_score", lambda kc: started.append(kc) or True)
    assert schedule.DEFAULTS["score_enabled"] is False  # off until the owner saves it
    out = call(routes._schedule_tick, Req(internal_auth=True))[1]
    assert out["score"] == want and started == (["/kc"] if want == "started" else [])
