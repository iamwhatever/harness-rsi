"""Auto-dispatch: off dispatches nothing; on opens one worker chat per card, within the daily cap."""

import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests" / "backend")]
from backend import dispatch, ledger, routes  # noqa: E402
from test_backend import PROPOSALS, SIGNALS, Req, call  # noqa: E402

ON = {**dispatch.DEFAULTS, "auto_dispatch": True}
CARD, OTHER = PROPOSALS[0]["id"], PROPOSALS[1]["id"]


class Sock(Req):
    """A request that arrived on the gateway's own socket."""

    transport = types.SimpleNamespace(get_extra_info=lambda k: ("0.0.0.0", 4321) if k == "sockname" else None)


@pytest.fixture
def opened(tmp_path, monkeypatch):
    """Data dir with the fixture cards; every chat the backend would open is recorded, none is."""
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path))
    (tmp_path / "signals.jsonl").write_text("".join(json.dumps(r) + "\n" for r in SIGNALS))
    (tmp_path / "proposals.json").write_text(json.dumps(PROPOSALS))
    seen = []

    async def fake_open(base, name, title, text):
        seen.append({"base": base, "name": name, "title": title, "text": text})
        return name

    monkeypatch.setattr(dispatch, "open_chat", fake_open)
    return seen


def turn(monkeypatch, conf):
    monkeypatch.setattr(dispatch, "read", lambda: conf)


def do(card=CARD, decision="do"):
    return call(routes._decide, Sock({"proposal_id": card, "decision": decision}))


def test_off_by_default_dispatches_nothing(opened, tmp_path):
    assert dispatch.read() == dispatch.DEFAULTS and dispatch.DEFAULTS["auto_dispatch"] is False
    assert do() == (200, {"ok": True, "appended": True})
    assert opened == [] and not (tmp_path / dispatch.FILE).exists()


def test_on_opens_one_chat_and_a_second_click_does_nothing(opened, monkeypatch):
    turn(monkeypatch, ON)
    status, body = do()
    assert status == 200 and body["dispatch"]["state"] == "dispatched" and len(opened) == 1
    assert opened[0]["base"] == "http://127.0.0.1:4321" and body["dispatch"]["session"] == opened[0]["name"]
    assert do(decision="later")[0] == 200 and do()[1]["dispatch"] == body["dispatch"]  # single-flight per card
    assert len(opened) == 1
    assert call(routes._outcomes)[1]["dispatches"] == [body["dispatch"]]
    row = ledger.link(CARD, "kirodotdev/KiroCrew#9", "detected")  # the worker's PR appears
    assert row["dispatch"]["session"] == opened[0]["name"] and ledger.get(CARD, "kirodotdev/KiroCrew#9") == row


def test_skip_and_later_never_dispatch(opened, monkeypatch):
    turn(monkeypatch, ON)
    assert "dispatch" not in do(decision="skip")[1] and "dispatch" not in do(decision="later")[1] and opened == []


def test_the_daily_cap_is_honored_and_a_refusal_uses_none_of_it(opened, monkeypatch):
    turn(monkeypatch, {**ON, "daily_cap": 1})
    assert do(CARD)[1]["dispatch"]["state"] == "dispatched"
    capped = do(OTHER)[1]["dispatch"]
    assert (capped["state"], len(opened)) == ("error", 1) and "daily cap of 1" in capped["error"]
    assert do(OTHER)[1]["dispatch"]["error"] == capped["error"] and len(opened) == 1  # still capped, still nothing opened


def test_a_failed_dispatch_shows_on_the_card_and_may_retry(opened, monkeypatch):
    turn(monkeypatch, ON)

    async def down(*a):
        raise ConnectionError("gateway refused")

    monkeypatch.setattr(dispatch, "open_chat", down)
    failed = do()[1]["dispatch"]
    assert failed["state"] == "error" and failed["error"] == "ConnectionError: gateway refused"
    assert call(routes._outcomes)[1]["dispatches"][0]["error"] == failed["error"]
    monkeypatch.setattr(dispatch, "open_chat", lambda *a: _done("rsi-retry"))
    assert do(decision="later")[0] == 200 and do()[1]["dispatch"]["session"] == "rsi-retry"


async def _done(key):
    return key


def test_a_repo_off_the_allowlist_is_refused(opened, monkeypatch):
    turn(monkeypatch, {**ON, "repos": ["someone/else"]})
    row = do()[1]["dispatch"]
    assert row["state"] == "error" and "not on the allowlist" in row["error"] and opened == []


def test_no_gateway_address_is_an_error_not_a_crash(opened, monkeypatch):
    turn(monkeypatch, ON)
    status, body = call(routes._decide, Req({"proposal_id": CARD, "decision": "do"}))
    assert status == 200 and body["appended"] and "no gateway address" in body["dispatch"]["error"] and opened == []


def test_a_prompt_change_card_never_dispatches(opened, monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT / "crew"))
    import propose

    turn(monkeypatch, ON)
    card = propose.change_card(tmp_path, "rsi-question-setter", "A new setter prompt.", "Shorter.")
    propose.save(tmp_path, card)
    status, body = call(routes._prompt_decide, Sock({"id": card["id"], "decision": "do"}))
    assert status == 200 and body["change"]["status"] == "applied" and "dispatch" not in body
    assert opened == [] and not (tmp_path / dispatch.FILE).exists()


def test_settings_are_owner_only_and_validated(opened, monkeypatch):
    assert call(routes._dispatch_get)[1]["dispatch"] == dispatch.DEFAULTS
    assert call(routes._dispatch_post, Req({"auto_dispatch": True}, internal_auth=True))[0] == 403
    for bad in ({"auto_dispatch": "yes"}, {"daily_cap": 0}, {"daily_cap": 11}, {"repos": ["not a repo"]}, {"repos": "o/r"}):
        assert call(routes._dispatch_post, Req(bad))[0] == 400, bad
    written = []
    monkeypatch.setattr(dispatch, "write", written.append)
    status, body = call(routes._dispatch_post, Req({"auto_dispatch": True, "daily_cap": 3}))
    assert status == 200 and written == [body["dispatch"]] and body["dispatch"]["repos"] == ["kirodotdev/KiroCrew"]


def test_the_seed_carries_the_card_signals_prior_art_and_rules(opened, tmp_path):
    p = PROPOSALS[0]
    (tmp_path / "debate.json").write_text(json.dumps({"prior_art": {p["id"]: {"verdict": None, "matches": [
        {"number": 42, "state": "CLOSED", "title": "An earlier try", "kind": "pr", "linked": False, "url": "u"}]}}}))
    text = dispatch.seed(p, SIGNALS, dispatch.prior_art(p["id"]))
    sig = next(s for s in SIGNALS if s["id"] == p["signal_ids"][0])
    for part in (p["id"], p["pain"], sig["pain"], "#42 CLOSED: An earlier try", "10 files and 300 lines",
                 "one small PR, drive CI green, do not merge", f"Put {p['id']} in the PR body"):
        assert part in text, part


# Manual dispatch: POST /dispatch/start, whatever auto_dispatch says.
DO, LATER, FRESH = "prop_plain_errors", "prop_one_step_undo", "prop_bg_tasks"  # fixture decisions: do, later, none


def start(*ids, req=Sock):
    return call(routes._dispatch_start, req({"proposal_ids": list(ids)}))


def test_dispatch_starts_a_do_card_with_auto_dispatch_off(opened, tmp_path):
    assert dispatch.read()["auto_dispatch"] is False
    status, body = start(DO)
    [r] = body["results"]
    assert status == 200 and r["result"] == "started" and len(opened) == 1
    assert r["session"] == opened[0]["name"] and r["link"] == f"/chat?slot={opened[0]['name']}"
    assert body["dispatches"] == [r["row"]] and r["row"]["state"] == "dispatched"


def test_dispatch_is_idempotent_per_card(opened, monkeypatch):
    first = start(DO)[1]["results"][0]
    again = start(DO, DO)[1]["results"]
    assert len(again) == 1 and again[0]["result"] == "already" and again[0]["row"] == first["row"]
    assert again[0]["link"] == first["link"] and len(opened) == 1
    turn(monkeypatch, ON)  # auto-dispatch on top of a manual one: still one chat
    assert do(DO)[1]["dispatch"] == first["row"] and len(opened) == 1


def test_dispatch_refuses_a_card_not_decided_do(opened):
    body = start(LATER, FRESH, "prop_nope")[1]
    assert [(r["id"], r["result"]) for r in body["results"]] == [(LATER, "not_do"), (FRESH, "not_do"), ("prop_nope", "error")]
    assert opened == [] and body["dispatches"] == []


def test_dispatch_honors_the_daily_cap_and_writes_no_refusal_row(opened, monkeypatch, tmp_path):
    turn(monkeypatch, {**dispatch.DEFAULTS, "daily_cap": 1})
    do(FRESH)  # auto off: decided, not started
    results = start(DO, FRESH)[1]["results"]
    assert [r["result"] for r in results] == ["started", "over_cap"] and results[1]["reason"] == "waiting for tomorrow"
    assert len(opened) == 1 and dispatch.current(FRESH) is None


def test_dispatch_obeys_the_allowlist(opened, monkeypatch):
    turn(monkeypatch, {**dispatch.DEFAULTS, "repos": ["someone/else"]})
    [r] = start(DO)[1]["results"]
    assert r["result"] == "error" and "not on the allowlist" in r["reason"] and opened == [] and dispatch.current(DO) is None


def test_a_failed_open_is_an_error_result_and_may_retry(opened, monkeypatch):
    async def down(*a):
        raise ConnectionError("gateway refused")

    monkeypatch.setattr(dispatch, "open_chat", down)
    [r] = start(DO)[1]["results"]
    assert r["result"] == "error" and r["reason"] == "ConnectionError: gateway refused" and dispatch.current(DO)["state"] == "error"
    monkeypatch.setattr(dispatch, "open_chat", lambda *a: _done("rsi-retry"))
    assert start(DO)[1]["results"][0]["session"] == "rsi-retry"


def test_dispatch_is_owner_only_and_validates_the_body(opened):
    assert start(DO, req=lambda b: Req(b, internal_auth=True))[0] == 403
    for bad in ({}, {"proposal_ids": []}, {"proposal_ids": "x"}, {"proposal_ids": [1]}, {"proposal_ids": ["p"] * 51}):
        assert call(routes._dispatch_start, Sock(bad))[0] == 400, bad
    assert opened == []


def test_auto_dispatch_off_still_starts_nothing_on_do(opened, tmp_path):
    status, body = do(DO)
    assert status == 200 and "dispatch" not in body
    assert opened == [] and not (tmp_path / dispatch.FILE).exists()
