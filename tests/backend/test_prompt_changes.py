"""The proposer's prompt-change card on the board: listed, applied only on the owner's 做, never with an exam."""

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "crew"), str(ROOT / "tests" / "backend")]
import prompts  # noqa: E402
import propose  # noqa: E402
from backend import prompt_changes, routes  # noqa: E402
from judge import seal  # noqa: E402
from test_backend import Req, call  # noqa: E402

SETTER = "rsi-question-setter"
EXAM = {"id": "exam_bank_hidden_probe", "task": "Probe the hidden thing and expect exit status zero."}
AB = {"agent": SETTER, "A": "v0", "B": "v1", "rounds": [3, 4], "reps": 2,
      "metrics": {"setter_hit_rate": {"A": 0.1, "B": 0.1, "verdict": "same"}}, "failures": {"A": {"cannot run": 5}, "B": {}}}


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path))
    (tmp_path / "exams" / "hidden").mkdir(parents=True)
    (tmp_path / "exams" / "hidden" / f"{EXAM['id']}.json").write_text(json.dumps(EXAM))
    (tmp_path / "outcomes.jsonl").write_text(json.dumps(seal.sign(seal.OUTCOMES, {"card_id": "prop_a", "pr": "o/r#1", "state": "merged", "note": "",
                                                          "exam_ids": [EXAM["id"]], "score": {"base": {"verdict": "fail"},
                                                                                              "head": {"verdict": "pass"}}})) + "\n")
    return tmp_path


def proposer(reply):
    seen = []
    return seen, lambda name, text, message: (seen.append((name, text, message)) or (json.dumps(reply), 0.1))


def test_proposer_sees_counts_not_exams_and_writes_one_pending_change(data):
    new = prompts.effective(data)[SETTER] + "\nEach exam must run on a plain checkout with no extra files."
    seen, call_ = proposer({"prompt": new, "summary": "Exams must run on a plain checkout."})
    card = propose.propose(data, AB, call_)
    [(name, text, message)] = seen
    assert name == propose.PROPOSER and prompts.leaks(message, [EXAM]) == [] and '"cannot run": 5' in message
    assert (card["status"], card["from"], card["to"]) == ("pending", prompts.version(prompts.effective(data)[SETTER]), prompts.version(new))
    assert "+Each exam must run on a plain checkout" in card["diff"]
    assert prompts.effective(data)[SETTER] != new  # proposing never applies
    with pytest.raises(ValueError, match="did not test this change"):
        propose.attach(data, card["id"], AB)
    assert propose.attach(data, card["id"], {**AB, "B": card["to"]})["ab"]["metrics"]["setter_hit_rate"]["verdict"] == "same"
    # the card carries when and how the A/B ran, so Needs you can show it; an older A/B has neither
    got = propose.attach(data, card["id"], {**AB, "B": card["to"], "ran_at": "2026-10-08T09:00:00+00:00", "command": "python3 crew/ab.py --agent x"})["ab"]
    assert (got["ran_at"], got["command"]) == ("2026-10-08T09:00:00+00:00", "python3 crew/ab.py --agent x")
    assert [c["ab"]["ran_at"] for c in prompt_changes.read()] == ["2026-10-08T09:00:00+00:00"]


@pytest.mark.parametrize("reply", [{"prompt": "Write exam_bank_hidden_probe again.", "summary": "x"},
                                   {"prompt": "x", "summary": "probe the hidden thing and expect exit status zero."},
                                   {"summary": "no prompt"}])
def test_an_unusable_or_leaking_proposal_is_refused(data, reply):
    with pytest.raises(ValueError):
        propose.propose(data, AB, proposer(reply)[1])
    assert not (data / "prompt_changes").exists()


def test_board_lists_the_card_and_do_applies_it_once(data):
    new = "A new setter prompt."
    card = propose.change_card(data, SETTER, new, "Shorter.")
    propose.save(data, card)
    status, body = call(routes._prompt_changes)
    assert status == 200 and [c["id"] for c in body["changes"]] == [card["id"]] and "prompt" not in body["changes"][0]
    assert call(routes._prompt_decide, Req({"id": card["id"], "decision": "do"}, internal_auth=True))[0] == 403
    assert call(routes._prompt_decide, Req({"id": "pc_0000000000", "decision": "do"}))[0] == 404
    assert call(routes._prompt_decide, Req({"id": card["id"], "decision": "maybe"}))[0] == 400
    assert prompts.effective(data)[SETTER] != new
    status, body = call(routes._prompt_decide, Req({"id": card["id"], "decision": "do"}))
    assert status == 200 and body["change"]["status"] == "applied" and prompts.effective(data)[SETTER] == new
    assert [r["version"] for r in map(json.loads, (data / "prompt_versions.jsonl").read_text().splitlines())] == [card["to"]]
    call(routes._prompt_decide, Req({"id": card["id"], "decision": "do"}))
    assert len((data / "prompt_versions.jsonl").read_text().splitlines()) == 1  # applied once


def test_a_stale_card_does_not_overwrite_a_newer_prompt(data):
    card = propose.change_card(data, SETTER, "Candidate one.", "One.")
    propose.save(data, card)
    prompts.apply(data, SETTER, "Someone else's newer prompt.", "pc_other")
    status, body = call(routes._prompt_decide, Req({"id": card["id"], "decision": "do"}))
    assert status == 409 and "changed since" in body["error"]
    assert prompts.effective(data)[SETTER] == "Someone else's newer prompt."
    assert prompt_changes.read()[0]["status"] == "pending"
