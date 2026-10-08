"""What Home reads from the schedule: ``next_round_at`` by the same rule as ``round_due``,
and each run row's duration and cost, averaged over the last three good rounds."""

import asyncio
import datetime as dt
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import round_job, routes, schedule  # noqa: E402

MON, DAY = dt.datetime(2026, 10, 5, 9, 5, tzinfo=dt.timezone.utc), dt.timedelta(days=1)  # a Monday, 09:05
ROUND_ONLY = {**schedule.DEFAULTS, "round_enabled": True}


class Req(dict):
    def __init__(self, body=None, **flags):
        super().__init__({"app": "harness-rsi", **flags})
        self.app, self.content_length, self.json = {"state": None}, 1, lambda: asyncio.sleep(0, body)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path))
    return tmp_path


def test_a_run_row_carries_its_duration_and_an_unknown_cost(env):
    schedule.record("round", MON, MON + dt.timedelta(minutes=50), signals=1, cards=3, error="")
    schedule.record("regress", MON, MON + dt.timedelta(seconds=7), cost=0.5, sha="a", regressions=0, errors=0, error="")
    reg, rnd = schedule.runs()
    assert (rnd["duration_s"], rnd["cost"]) == (3000, None) and (reg["duration_s"], reg["cost"]) == (7, 0.5)


def test_round_stats_average_the_last_three_good_rounds(env):
    assert schedule.round_stats() == {"n": 0, "avg_duration_s": None, "avg_cost": None, "from": None, "to": None}
    for i, (mins, cost, err) in enumerate([(10, 1.0, ""), (60, 2.0, ""), (99, 9.0, "boom"), (30, 3.0, ""), (90, 4.0, "")]):
        kind = "manual_round" if i == 3 else "round"
        schedule.record(kind, MON + DAY * i, MON + DAY * i + dt.timedelta(minutes=mins), cost=cost, error=err)
    schedule.record("regress", MON, MON + dt.timedelta(hours=5), error="")  # not a round
    assert schedule.round_stats() == {"n": 3, "avg_duration_s": 60 * 60, "avg_cost": 3.0, "from": (MON + DAY).isoformat(),
                                      "to": (MON + DAY * 4 + dt.timedelta(minutes=90)).isoformat()}
    schedule.record("round", MON + DAY * 9, MON + DAY * 9 + dt.timedelta(minutes=60))  # cost not measured
    assert schedule.round_stats()["avg_cost"] is None  # one unknown makes the average unknown, never a guess


def test_old_rows_without_a_duration_still_average(env):
    (env / schedule.RUNS).write_text(json.dumps({"kind": "round", "start": MON.isoformat(), "cost": None,
                                                 "end": (MON + dt.timedelta(minutes=40)).isoformat()}) + "\n")
    assert schedule.round_stats()["avg_duration_s"] == 2400


def _first_due(conf, now, hours=24 * 21):
    return next(now + dt.timedelta(hours=h) for h in range(hours) if not schedule.round_due(conf, now + dt.timedelta(hours=h), False))


@pytest.mark.parametrize("last,now,conf", [
    (None, MON - DAY * 2, ROUND_ONLY),  # fresh: this Monday's slot
    (None, MON, ROUND_ONLY),  # fresh, slot passed: due now
    (MON, MON + DAY, ROUND_ONLY),  # done this week: next Monday
    (MON + DAY * 2, MON + DAY * 3, {**ROUND_ONLY, "weekday": 3}),  # moved day, ran 1 day ago: 6 days after it
    (MON - DAY * 10, MON + DAY * 2, {**ROUND_ONLY, "hour": 23}),  # missed slot: due now
])
def test_next_round_at_is_the_first_tick_round_due_lets_through(env, last, now, conf):
    if last:
        schedule.record("round", last, last)
    at = dt.datetime.fromisoformat(schedule.next_round_at(conf, now))
    first = _first_due(conf, now)
    assert at <= first < at + dt.timedelta(hours=1), (at, first)


def test_next_round_at_is_none_when_off_and_ignores_manual_rounds(env):
    assert schedule.next_round_at(schedule.DEFAULTS, MON) is None
    schedule.record("manual_round", MON, MON)
    assert schedule.next_round_at(ROUND_ONLY, MON) == MON.isoformat()
    assert schedule.round_due(ROUND_ONLY, MON, False) == ""


def test_get_schedule_answers_next_round_and_the_average(env, monkeypatch):
    monkeypatch.setattr(routes, "CLOCK", lambda: MON + DAY)
    monkeypatch.setattr(schedule, "read", lambda: ROUND_ONLY)
    schedule.record("round", MON, MON + dt.timedelta(minutes=45))
    body = json.loads(asyncio.run(routes._schedule_get(Req(), None)).text)
    assert body["next_round_at"] == (MON + DAY * 7 - dt.timedelta(minutes=5)).isoformat()
    stats = body["round_stats"]
    assert (stats["n"], stats["avg_duration_s"], stats["avg_cost"]) == (1, 2700, None)
    assert body["read_at"] == (MON + DAY).isoformat() and body["runs"][0]["duration_s"] == 2700


def test_a_manual_round_writes_its_own_row(env, monkeypatch):
    clock = iter([MON, MON + dt.timedelta(minutes=30)])
    monkeypatch.setattr(routes, "CLOCK", lambda: next(clock))
    monkeypatch.setattr(round_job, "STATE", {**round_job.STATE, "running": False, "task": None})
    monkeypatch.setattr(round_job, "_run", lambda rnd, sources: {"signals": [1, 2], "proposals": [1, 2, 3]})

    async def go():
        resp = await routes._round_run(Req({"round": 3}), None)
        await asyncio.gather(*routes.SCHEDULED)
        return resp.status

    assert asyncio.run(go()) == 202
    (row,) = schedule.runs()
    assert {k: row[k] for k in ("kind", "signals", "cards", "error", "duration_s", "cost")} == \
        {"kind": "manual_round", "signals": 2, "cards": 3, "error": "", "duration_s": 1800, "cost": None}
    assert schedule.round_due(ROUND_ONLY, MON + dt.timedelta(hours=1), False) == ""  # the weekly rule does not count it
