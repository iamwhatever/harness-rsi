"""The sealed store: the two break inputs from docs/design/crewmate-team.md section 6.1 now fail."""

import asyncio
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "crew")]
import ab  # noqa: E402
import prompts  # noqa: E402
import propose  # noqa: E402
from backend import ledger, routes  # noqa: E402
from judge import core, regress, seal  # noqa: E402

TASK = "The model chip in the composer must read Auto on a fresh install."
EXAM = {"id": "exam_chip_reads_auto", "origin": ["sig_1"], "visibility": "hidden", "task": TASK,
        "check": {"kind": "exit_code", "cmd": ["true"], "expect": 0}, "created_round": 3, "used_rounds": []}


@pytest.fixture
def data(tmp_path, monkeypatch):
    """A data dir as it was before sealing: plaintext exams, unsigned ledger rows and regress runs."""
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path))
    (tmp_path / "exams" / "hidden").mkdir(parents=True)
    (tmp_path / "exams" / "hidden" / f"{EXAM['id']}.json").write_text(json.dumps(EXAM, indent=2) + "\n")
    (tmp_path / "rounds/round-3/exams/hidden").mkdir(parents=True)
    (tmp_path / "rounds/round-3/exams/hidden" / f"{EXAM['id']}.json").write_text(json.dumps(EXAM))
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "rsi-question-setter.md").write_text("Owner's setter prompt.\n")
    row = {**ledger.blank("kirodotdev/KiroCrew#7", "prop_chip"), "state": "merged"}
    (tmp_path / "outcomes.jsonl").write_text(json.dumps(row) + "\n")
    (tmp_path / "regress").mkdir()
    (tmp_path / "regress" / "s1.json").write_text(json.dumps(
        {"sha": "s1", "at": "2026-01-01T00:00:00+00:00", "baseline": None, "exams": {}, "counts": None, "regressions": []}))
    return tmp_path


def forge(data):
    """Break input 1 from a plain shell: the doc's line verbatim (but for the path), then a schema-valid forged row."""
    fake = {**ledger.blank("kirodotdev/KiroCrew#1", "prop_chip"), "state": "merged",
            "score": {"base": {"verdict": "fail"}, "head": {"verdict": "pass"}, "metrics": None}}
    code = ("import json,os,sys;f=open(os.path.join(os.environ['HARNESS_RSI_DATA'],'outcomes.jsonl'),'a');"
            "f.write(json.dumps({'card_id':'prop_chip','pr':1,'score':'pass'})+'\\n');f.write(sys.argv[1]+'\\n')")
    subprocess.run([sys.executable, "-c", code, json.dumps(fake)], check=True)


def cat(data):
    """Break input 2: what ``cat exams/hidden/*.json`` prints."""
    paths = sorted((data / "exams" / "hidden").glob("*.json"))
    return subprocess.run(["cat", *map(str, paths)], capture_output=True, text=True, check=True).stdout


def test_cat_hidden_exams_shows_ciphertext_and_the_judge_still_reads_them(data):
    assert TASK in cat(data)  # before: the exam text is in the clear
    seal.migrate()
    shown = cat(data)
    assert shown.startswith(seal.MAGIC.decode()) and TASK not in shown and EXAM["id"] not in shown.split(":", 1)[1]
    for path in seal.sealed_files(data):
        assert TASK not in path.read_text() and "setter prompt" not in path.read_text()
    assert [e["id"] for e in core.load_exams(data / "exams" / "hidden")] == [EXAM["id"]]
    assert [e["task"] for e in regress.load_all(data / "exams")] == [TASK]
    assert prompts.effective(data)["rsi-question-setter"] == "Owner's setter prompt."
    assert [e["id"] for e in prompts.bank(data)] == [EXAM["id"]] * 2


def test_a_forged_outcome_line_is_dropped_and_voids_the_round(data):
    seal.migrate()
    assert len(ledger.rows()) == 1 and ledger.integrity() == {"bad_lines": 0, "void": False}
    forge(data)
    assert [r["pr"] for r in ledger.rows()] == ["kirodotdev/KiroCrew#7"]
    assert ledger.integrity() == {"bad_lines": 2, "void": True}
    body = json.loads(asyncio.run(routes._outcomes(None, None)).text)
    assert (body["void"], body["bad_lines"], len(body["outcomes"])) == (True, 2, 1)
    with pytest.raises(ValueError, match="void"):
        ab.outcome_rows(data)
    with pytest.raises(ValueError, match="void"):
        propose.ledger_counts(data)


def test_a_signed_row_edited_in_place_is_dropped(data):
    seal.migrate()
    row = json.loads((data / "outcomes.jsonl").read_text())
    (data / "outcomes.jsonl").write_text(json.dumps({**row, "state": "closed"}) + "\n")  # keeps the old sig
    assert ledger.rows() == [] and ledger.integrity()["void"]


def test_migrate_is_idempotent_keeps_a_backup_deletes_nothing_and_never_signs_a_later_forgery(data):
    before = {p.relative_to(data) for p in data.rglob("*") if p.is_file()}
    first = seal.migrate(now=lambda: 0)
    assert (first["encrypted"], first["signed_rows"], first["signed_runs"]) == (3, 1, 1)
    backup = data / first["backup"]
    assert json.loads((backup / "exams/hidden" / f"{EXAM['id']}.json").read_text())["task"] == TASK
    assert "sig" not in json.loads((backup / "outcomes.jsonl").read_text())
    assert before <= {p.relative_to(data) for p in data.rglob("*") if p.is_file()}
    sealed = {p: p.read_bytes() for p in seal.sealed_files(data)}
    forge(data)
    assert seal.migrate()["note"] == "already sealed"
    assert {p: p.read_bytes() for p in seal.sealed_files(data)} == sealed
    assert ledger.integrity()["bad_lines"] == 2  # the rerun did not sign the forged line


def test_after_sealing_a_planted_plaintext_exam_or_a_swapped_ciphertext_is_refused(data):
    seal.migrate()
    hidden = data / "exams" / "hidden"
    (hidden / "exam_planted.json").write_text(json.dumps({**EXAM, "id": "exam_planted"}))
    with pytest.raises(core.JudgeError, match="plaintext"):
        core.load_exams(hidden)
    (hidden / "exam_planted.json").write_bytes((hidden / f"{EXAM['id']}.json").read_bytes())  # bound to its own name
    with pytest.raises(core.JudgeError, match="bad sealed file"):
        core.load_exams(hidden)
    assert [e["id"] for e in prompts.bank(data)] == [EXAM["id"]] * 2  # the bank leaves both out


def test_a_tampered_regress_run_is_dropped_and_voids_the_comparison(data):
    seal.migrate()
    run = json.loads((data / "regress/s1.json").read_text())
    (data / "regress/s1.json").write_text(json.dumps({**run, "exams": {EXAM["id"]: True}}))
    assert routes.regress_runs() == []
    with pytest.raises(core.JudgeError, match="void"):
        regress.previous(data / "regress", "s2")


def test_an_applied_setter_prompt_is_stored_sealed(data):
    seal.migrate()
    prompts.apply(data, "rsi-question-setter", "New setter prompt.", "pc_0123456789")
    raw = (data / "prompts" / "rsi-question-setter.md").read_bytes()
    assert seal.is_sealed(raw) and b"New setter" not in raw
    assert prompts.effective(data)["rsi-question-setter"] == "New setter prompt."


def test_without_a_vault_key_writes_fail_closed_and_no_row_verifies(data, monkeypatch):
    seal.migrate()

    def no_vault():
        raise seal.SealError("no seal key")
    monkeypatch.setattr(seal, "_load", no_vault)
    seal._CACHE.clear()
    with pytest.raises(seal.SealError):
        seal.write_text(data / "exams" / "hidden" / "exam_new.json", "{}")
    with pytest.raises(seal.SealError):
        ledger.put(ledger.blank("kirodotdev/KiroCrew#8", "prop_chip"))
    assert ledger.rows() == [] and ledger.integrity()["void"]
