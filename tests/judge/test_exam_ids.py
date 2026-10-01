"""Judge input `exam_ids`, per-exam evidence with status, `error` for checks that cannot run, metrics_in."""

import copy
import io
import json
import pathlib
import sys

import pytest
from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from judge import metrics_in  # noqa: E402
from judge.__main__ import main  # noqa: E402

EX = {e["id"]: e for e in json.loads((ROOT / "fixtures/exams.json").read_text())}
SCHEMA = Draft202012Validator(json.loads((ROOT / "schemas/judge.schema.json").read_text()))
OK_CMD = {"kind": "exit_code", "cmd": [sys.executable, "-c", "pass"], "expect": 0}


def exam(exam_id, visibility="hidden", check=None):
    e = copy.deepcopy(EX["exam_bg_task_survives_close"])
    e.update(id=exam_id, visibility=visibility, check=check or OK_CMD)
    return e


def run(tmp, exams, inp, metrics=None):
    (tmp / "exams").mkdir(exist_ok=True)
    (tmp / "exams/set.json").write_text(json.dumps(exams))
    argv = ["--exams", str(tmp / "exams"), "--workdir", str(tmp)]
    if metrics is not None:
        (tmp / "m.json").write_text(json.dumps(metrics))
        argv += ["--metrics", str(tmp / "m.json")]
    inp = {"pr": 1, "repo": "example-org/example-repo", **inp}
    out = io.StringIO()
    code = main(argv, io.StringIO(json.dumps(inp)), out)
    if code == 2:
        return code, None
    doc = json.loads(out.getvalue())
    assert [e.message for e in SCHEMA.iter_errors({"input": inp, "output": doc})] == []
    return code, doc


def test_exam_ids_pick_exactly_those_exams(tmp_path):
    exams = [exam("exam_a"), exam("exam_b"), exam("exam_c", "regression")]
    code, doc = run(tmp_path, exams, {"suites": ["hidden"], "exam_ids": ["exam_b", "exam_c"]})
    assert code == 0 and [e["exam_id"] for e in doc["evidence"]] == ["exam_b", "exam_c"]
    assert all(e["status"] == "pass" for e in doc["evidence"])
    _, doc = run(tmp_path, exams, {"suites": ["hidden"]})  # without ids: suite membership as before
    assert [e["exam_id"] for e in doc["evidence"]] == ["exam_a", "exam_b"]


def test_unknown_exam_id_is_a_setup_error(tmp_path, capsys):
    code, _ = run(tmp_path, [exam("exam_a")], {"suites": ["hidden"], "exam_ids": ["exam_nope"]})
    assert code == 2 and "unknown exam id(s): exam_nope" in capsys.readouterr().err


@pytest.mark.parametrize("ids", [[], ["exam_a", "exam_a"], "exam_a", None])
def test_bad_exam_ids_are_refused(tmp_path, ids):
    assert run(tmp_path, [exam("exam_a")], {"suites": ["hidden"], "exam_ids": ids})[0] == 2


@pytest.mark.parametrize("check,detail", [
    ({"kind": "exit_code", "cmd": ["no-such-tool-r4j"], "expect": 0}, "cannot run command"),
    ({"kind": "file_assert", "path": "nope.txt", "contains": "x"}, "missing file nope.txt"),
    ({"kind": "screenshot_diff", "baseline": "baselines/x.png", "max_diff_ratio": 0.02}, "missing image x.png"),
])
def test_a_check_that_cannot_run_is_error(tmp_path, check, detail):
    code, doc = run(tmp_path, [exam("exam_a", check=check)], {"suites": ["hidden"], "exam_ids": ["exam_a"]})
    (entry,) = doc["evidence"]
    assert code == 1 and entry["status"] == "error" and entry["ok"] is False and entry["detail"].startswith(detail)


def test_empty_suite_and_missing_metrics_say_so(tmp_path):
    code, doc = run(tmp_path, [exam("exam_a")], {"suites": ["hidden", "regression", "metrics"], "exam_ids": ["exam_a"]})
    notes = {e["suite"]: e["detail"] for e in doc["evidence"] if "suite" in e}
    assert code == 1 and doc["verdict"] == "fail"
    assert notes == {"regression": "0 exams ran in suite regression", "metrics": "no metrics fed (pass --metrics)"}


def test_metrics_in_feeds_the_judge(tmp_path):
    def shot(name, latency, success):
        (tmp_path / name).write_text(json.dumps({"metrics": {"total_latency_ms_p50": latency, "turn_success_rate": success,
                                                             "tokens_per_turn_p50": None}}))
        return str(tmp_path / name)

    out = tmp_path / "m.json"
    before = [shot("b1", 900, 0.9), shot("b2", 1000, 0.9)]
    assert metrics_in.main(["--before", *before, "--after", shot("a1", 700, 0.9), shot("a2", 800, 0.9), "--out", str(out)]) == 0
    metrics = json.loads(out.read_text())
    assert metrics["current"] == {"total_latency_ms_p50": [700.0, 800.0], "turn_success_rate": [0.9, 0.9]}
    _, doc = run(tmp_path, [exam("exam_a")], {"suites": ["metrics"]}, metrics)
    assert doc["paired_metrics"][0] == {"a": "total_latency_ms_p50", "b": "turn_success_rate", "delta_a": -200.0, "delta_b": 0.0,
                                        "ok": True}
    worse = {**metrics, "current": {"total_latency_ms_p50": [2000.0] * 2, "turn_success_rate": [0.9] * 2}}
    assert run(tmp_path, [exam("exam_a")], {"suites": ["metrics"]}, worse)[1]["paired_metrics"][0]["ok"] is False


@pytest.mark.parametrize("entry,valid", [
    ({"exam_id": "exam_a", "ok": True, "status": "pass", "detail": "exit 0"}, True),
    ({"suite": "regression", "ok": False, "status": "error", "detail": "0 exams ran"}, True),
    ({"exam_id": "exam_a", "ok": True, "detail": "exit 0"}, False),  # no status
    ({"exam_id": "exam_a", "suite": "hidden", "ok": True, "status": "pass", "detail": "x"}, False),  # both
    ({"ok": False, "status": "error", "detail": "x"}, False),  # neither
])
def test_contract_evidence_rows(entry, valid):
    doc = json.loads((ROOT / "fixtures/judge.json").read_text())
    doc["output"]["evidence"] = [entry]
    assert SCHEMA.is_valid(doc) is valid
