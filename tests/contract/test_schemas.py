"""Contract tests: every fixture matches its schema, and fixtures/invalid.json rows are refused."""

import copy
import json
import pathlib

import pytest
from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILES = {"signal": "signals.json", "exam": "exams.json", "proposal": "proposals.json"}


def load(rel):
    return json.loads((ROOT / rel).read_text())


def validator(name):
    schema = load(f"schemas/{name}.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


ROWS = [(name, row) for name, f in FILES.items() for row in load(f"fixtures/{f}")] + [("judge", load("fixtures/judge.json"))]
BAD = load("fixtures/invalid.json")


@pytest.mark.parametrize("name,row", ROWS, ids=[r.get("id", n) for n, r in ROWS])
def test_fixture_is_valid(name, row):
    assert [e.message for e in validator(name).iter_errors(row)] == []


def test_fixtures_cover_the_contract_and_references_resolve():
    sigs, exams, props = (load(f"fixtures/{f}") for f in FILES.values())
    sig_ids, exam_ids = {s["id"] for s in sigs}, {e["id"] for e in exams}
    assert {e["check"]["kind"] for e in exams} == {"exit_code", "file_assert", "metric_threshold", "screenshot_diff"}
    assert {e["visibility"] for e in exams} == {"hidden", "regression", "retired"}
    assert len(sig_ids) == len(sigs) and len(exam_ids) == len(exams)
    assert any(s["dedup_of"] for s in sigs)
    assert all(s["dedup_of"] in sig_ids - {s["id"]} for s in sigs if s["dedup_of"])
    assert all(set(e["origin"]) <= sig_ids for e in exams)
    assert all(set(p["signal_ids"]) <= sig_ids and set(p["exam_ids"]) <= exam_ids for p in props)
    assert all(ev["exam_id"] in exam_ids for ev in load("fixtures/judge.json")["output"]["evidence"])


@pytest.mark.parametrize("case", BAD, ids=[c["name"] for c in BAD])
def test_invalid_row_is_refused(case):
    doc = copy.deepcopy(load(f"fixtures/{case['base']['file']}"))
    if case["base"]["index"] is not None:
        doc = doc[case["base"]["index"]]
    assert validator(case["schema"]).is_valid(doc), "base row must be valid so only the mutation can fail it"
    *parents, leaf = case["path"]
    target = doc
    for key in parents:
        target = target[key]
    target.pop(leaf) if case["value"] == "__delete__" else target.__setitem__(leaf, case["value"])
    assert not validator(case["schema"]).is_valid(doc)
