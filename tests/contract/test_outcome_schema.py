"""Outcome-ledger contract: fixture rows validate, their ids resolve, and known-bad rows are refused."""

import copy
import json
import pathlib

import pytest
from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas/outcome.schema.json").read_text(encoding="utf-8"))
ROWS = json.loads((ROOT / "fixtures/outcomes.json").read_text(encoding="utf-8"))
V = Draft202012Validator(SCHEMA)


def test_schema_is_valid_and_closed():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["additionalProperties"] is False and set(SCHEMA["required"]) == set(SCHEMA["properties"])


@pytest.mark.parametrize("row", ROWS, ids=[r["pr"] for r in ROWS])
def test_fixture_row_is_valid(row):
    assert [e.message for e in V.iter_errors(row)] == []


def test_fixtures_cover_the_lifecycle_and_ids_resolve():
    cards = {p["id"]: p for p in json.loads((ROOT / "fixtures/proposals.json").read_text(encoding="utf-8"))}
    exams = {e["id"] for e in json.loads((ROOT / "fixtures/exams.json").read_text(encoding="utf-8"))}
    assert {r["state"] for r in ROWS} == {"open", "merged"} and {r["link"] for r in ROWS} == {"board", "detected", "backfill"}
    assert any(r["card_id"] is None and "no card" in r["note"] for r in ROWS)
    for r in ROWS:
        assert r["card_id"] is None or r["card_id"] in cards
        assert set(r["exam_ids"]) | set(r["promoted"]) | {i for g in r["regress"] for i in g["exams"]} <= exams
        assert r["merged_sha"] is None or r["state"] == "merged"
        assert all(run is None or run["pass"] + run["fail"] + run["error"] >= 1 for run in (r["score"]["base"], r["score"]["head"]))


BAD = [
    ("pr has no number", ["pr"], "example-org/example-repo"),
    ("short sha", ["merged_sha"], "3333333"),
    ("decision outside do/skip/later", ["decision"], "maybe"),
    ("unknown verdict", ["score", "head", "verdict"], "ok"),
    ("regress exam status not pass/fail/error", ["regress", 0, "exams", "exam_bg_task_survives_close"], True),
    ("extra key", ["body"], "PR body text"),
    ("missing score", ["score"], "__delete__"),
    ("card id not a proposal id", ["card_id"], "card_1"),
]


@pytest.mark.parametrize("path,value", [b[1:] for b in BAD], ids=[b[0] for b in BAD])
def test_bad_row_is_refused(path, value):
    row = copy.deepcopy(ROWS[0])
    *parents, leaf = path
    target = row
    for key in parents:
        target = target[key]
    target.pop(leaf) if value == "__delete__" else target.__setitem__(leaf, value)
    assert not V.is_valid(row)
