"""The opt-in schedule on a fake clock and fake jobs: off runs nothing, a week runs one round,
an overlap is skipped, a regression sends one critical note, nothing posts to Slack or merges."""

import asyncio
import datetime as dt
import json
import pathlib
import subprocess
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import routes, schedule  # noqa: E402

MON, DAY = dt.datetime(2026, 10, 5, 9, 5, tzinfo=dt.timezone.utc), dt.timedelta(days=1)  # a Monday, 09:05
ON = {**schedule.DEFAULTS, "round_enabled": True, "regress_enabled": True, "kirocrew_dir": "/kc"}
ROUND_ONLY = {**ON, "regress_enabled": False}


class Req(dict):
    def __init__(self, state=None, body=None, **flags):
        super().__init__({"app": "harness-rsi", **flags})
        self.app, self.content_length, self.json = {"state": state}, 1, lambda: asyncio.sleep(0, body)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path))
    monkeypatch.setitem(schedule.REGRESS, "running", False)
    fake = types.ModuleType("kiro_crew.notifications.bus")
    fake.NotificationPayload = lambda **kw: types.SimpleNamespace(priority=None, **kw)
    monkeypatch.setitem(sys.modules, "kiro_crew.notifications.bus", fake)
    calls = {"rounds": 0, "notes": [], "data": tmp_path}
    calls["fns"] = [lambda: calls.update(rounds=calls["rounds"] + 1) or asyncio.sleep(0, ({"proposals": 3, "signals": 12}, "")),
                    lambda kc: pytest.fail("regress ran"), lambda *n: calls["notes"].append(n)]
    return calls


def tick(env, at, conf, busy=False):
    async def go():
        out, jobs = schedule.tick(lambda: at, conf, busy, *env["fns"])
        await asyncio.gather(*jobs)
        return out
    return asyncio.run(go())


def test_off_by_default_runs_nothing(env):
    assert schedule.read() == schedule.DEFAULTS  # no vault outside a gateway reads as off
    assert {tuple(tick(env, MON + DAY * d, schedule.DEFAULTS).values()) for d in range(8)} == {("off", "off")}
    assert env["rounds"] == 0 and env["notes"] == [] and not list(env["data"].iterdir())
    assert asyncio.run(routes._schedule_post(Req(body={"round_enabled": True}, internal_auth=True), None)).status == 403


def test_weekly_round_fires_once_and_notifies(env):
    assert tick(env, MON - dt.timedelta(minutes=10), ROUND_ONLY)["round"] == "started"  # last week's slot, caught up
    for at in (MON, MON + dt.timedelta(hours=5), MON + DAY * 3):
        assert tick(env, at, ROUND_ONLY)["round"] in ("done this week", "ran under 6 days ago")
    assert env["rounds"] == 1 and env["notes"] == [("rounds", "Harness RSI: 3 new card(s)", "3 card(s) to pick from on the board.")]
    assert tick(env, MON + DAY * 7, ROUND_ONLY)["round"] == "started" and env["rounds"] == 2
    (run, _), want = schedule.runs(), {"kind": "round", "cards": 3, "signals": 12, "cost": None, "start": (MON + DAY * 7).isoformat()}
    assert {k: run[k] for k in want} == want and run["end"]


def test_overlap_and_recent_round_are_skipped(env):
    assert tick(env, MON, ROUND_ONLY, busy=True)["round"] == "busy"
    env["fns"][0] = lambda: None  # a manual round won the race
    assert tick(env, MON, ROUND_ONLY)["round"] == "busy" and schedule.runs() == []
    schedule.record("round", MON, MON)
    assert tick(env, MON + DAY * 3, {**ROUND_ONLY, "weekday": 3})["round"] == "ran under 6 days ago"  # moved day
    assert len(schedule.validate({"weekday": 7, "hour": "9", "round_enabled": 1, "kirocrew_dir": "rel"}, ON)[1]) == 4


def test_regression_sends_one_critical_note_and_nothing_else(env, monkeypatch):
    argvs, notes, channels = [], [], {}
    out = json.dumps({"sha": "f00dfeed1234", "regressions": [{"kind": "exam", "exam_id": f"ex_{n}"} for n in ("chip", "undo")]})
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: argvs.append(argv) or types.SimpleNamespace(returncode=1, stdout=out))
    monkeypatch.setattr(schedule, "read", lambda: {**ON, "round_enabled": False})
    monkeypatch.setattr(routes, "CLOCK", lambda: MON)
    monkeypatch.setattr(routes.adapters.slack, "collect", lambda conf: pytest.fail("Slack read"))
    bus = types.SimpleNamespace(is_registered=channels.__contains__, register_channel=channels.__setitem__, push=notes.append)
    state = types.SimpleNamespace(notification_bus=bus, notification_rate_limiter=None)

    async def go():
        out = json.loads((await routes._schedule_tick(Req(state, internal_auth=True), None)).text)
        await asyncio.gather(*routes.SCHEDULED)
        return out["round"], out["regress"]
    assert [asyncio.run(go()) for _ in range(2)] == [("off", "started"), ("off", "not due")]
    assert argvs == [[sys.executable, "-m", "judge.regress", "--since-last", "--kirocrew", "/kc"]]  # no gh, no merge
    (note,) = notes
    assert channels == {"harness-rsi.regressions": "critical"} and note.channel == "harness-rsi.regressions"
    assert note.url == "/apps/harness-rsi" and note.body == "KiroCrew f00dfeed1234: ex_chip, ex_undo"
    assert [(r["kind"], r["sha"], r["regressions"], r["errors"]) for r in schedule.runs()] == [("regress", "f00dfeed1234", 2, 0)]


def test_exams_that_could_not_run_send_one_plain_note(env):
    summary = {"sha": "f00dfeed1234", "regressions": [], "errors": ["exam_chip"]}
    env["fns"][1] = lambda kc: (summary, "")
    assert tick(env, MON, {**ON, "round_enabled": False})["regress"] == "started"
    ((channel, title, body),) = env["notes"]
    assert channel == "rounds" and title == "Harness RSI: 1 regression exam(s) could not run"
    assert "exam_chip" in body and "Not a regression" in body
    assert [(r["regressions"], r["errors"]) for r in schedule.runs(kind="regress")] == [(0, 1)]
