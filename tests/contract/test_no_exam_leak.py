"""Hidden exams never reach a prompt: not the setter's, not the reviewers', not an applied or variant one."""

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "crew"), str(ROOT / "tests" / "crew")]
import ab  # noqa: E402
import prompts  # noqa: E402
from test_run_round import SIGNALS, FakeCrew, load  # noqa: E402

BANK = [{"id": "exam_bank_secret_one", "task": "Open the hidden panel and count exactly seven green rows."},
        {"id": "exam_bank_secret_two", "task": "Run the second hidden probe and expect exit status zero."}]


def data_dir(tmp_path):
    (tmp_path / "exams" / "hidden").mkdir(parents=True)
    for x in BANK:
        (tmp_path / "exams" / "hidden" / f"{x['id']}.json").write_text(json.dumps(x))
    return tmp_path


class FreshSetter(FakeCrew):
    """The setter's exams carry ids and tasks of their own, so any echo of them is visible."""

    @staticmethod
    def exam(s):
        row = FakeCrew.exam(s)
        return {**row, "id": "exam_fresh_" + s["id"][4:], "task": f"Fresh judge-only task for {s['id']}, never shown."}


def test_leaks_finds_ids_and_tasks_whatever_the_spacing():
    assert prompts.leaks("see exam_bank_secret_one", BANK) == ["exam_bank_secret_one"]
    assert prompts.leaks("RUN THE SECOND hidden probe\n and expect exit status zero.", BANK) == ["exam_bank_secret_two"]
    assert prompts.leaks("write exam_ids and an `exam_<short>` id", BANK) == []


@pytest.mark.parametrize("text", ["Copy exam_bank_secret_one.", "Tip: open the hidden panel and count exactly seven green rows."])
def test_apply_and_ab_refuse_a_prompt_naming_an_exam(tmp_path, text):
    data = data_dir(tmp_path)
    with pytest.raises(ValueError, match="exams never go into prompts"):
        prompts.apply(data, "rsi-question-setter", text, "pc_x")
    with pytest.raises(ValueError, match="exams never go into prompts"):
        ab.run(data, "rsi-question-setter", text, lambda *a: ("[]", None), None, [3], 1)
    assert not (data / "prompts").exists()


def test_no_agent_message_or_prompt_in_a_round_carries_an_exam(tmp_path):
    data, crew = data_dir(tmp_path), FreshSetter()
    out = load().run_round(agent=crew, collectors=[lambda: SIGNALS], data=data, rnd=2, day="20260930",
                           save_mock=lambda slug, title, page: slug)
    fresh = [{"id": x["id"], "task": x["task"]} for x in out["exams"]]
    assert fresh and all(x["id"].startswith("exam_fresh_") for x in fresh)
    for name, message in crew.calls:
        assert prompts.leaks(message, BANK + fresh) == [], name
    for name, text in prompts.effective(data).items():
        assert prompts.leaks(text, BANK + fresh) == [], name


def test_committed_prompts_name_no_fixture_exam():
    fixtures = json.loads((ROOT / "fixtures" / "exams.json").read_text())
    for name, text in prompts.effective(None).items():
        assert prompts.leaks(text, fixtures) == [], name



def test_the_setter_facts_show_a_regression_exam_and_never_a_hidden_one(tmp_path):
    from test_setter_facts import SIG, checkout

    bank, rows = tmp_path / "bank", []
    (bank / "hidden").mkdir(parents=True)
    for vis in ("hidden", "regression"):
        cmd = ["python3", "-c", f"import sys; sys.exit(0)  # the {vis} probe command"]
        rows.append({"id": f"exam_{vis}_probe", "visibility": vis, "task": f"A {vis} task, long enough to match.",
                     "check": {"kind": "exit_code", "cmd": cmd, "expect": 0}})
        (bank / "hidden" / f"{rows[-1]['id']}.json").write_text(json.dumps(rows[-1]))
    text = load().setter_facts(checkout(tmp_path), bank)([SIG])
    assert "the regression probe command" in text and prompts.leaks(text, rows) == []
    assert prompts.leaks("see " + json.dumps(rows[0]["check"]), rows) == ["exam_hidden_probe"]  # a check is exam text too
