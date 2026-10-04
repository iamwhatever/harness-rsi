"""The exam audit: probes, sorting, conversion and --fix, on fake exams and a fake validator."""

import io
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from judge import audit, seal  # noqa: E402

OK = {"kind": "exit_code", "cmd": ["true"], "expect": 0}
SHOT = {"kind": "screenshot_diff", "baseline": "b/x.png", "max_diff_ratio": 0.1}
MISSING_IMAGE = (1, "missing image x.png")


def store(tmp, *exams):
    (tmp / "exams/hidden").mkdir(parents=True)
    for name, check, task in exams:
        e = {"id": f"exam_{name}", "layer": 2, "origin": ["sig_20260101_0001"], "visibility": "hidden", "task": task,
             "check": check, "created_round": 0, "used_rounds": []}
        (tmp / f"exams/hidden/exam_{name}.json").write_text(json.dumps(e), encoding="utf-8")
    return tmp / "exams"


def fake(outcomes):
    def runner(e, work, shots, metrics):
        out = outcomes[e["check"]["kind"]]
        if isinstance(out, Exception):
            raise out
        return out[0], {"detail": out[1]}
    return runner


def test_probe_catches_commands_that_cannot_start(tmp_path):
    assert audit.probe(["python3", "-m", "no_such_mod_r5h4.sub"], tmp_path) == "missing-module"
    assert audit.probe(["pytest", "-q"], tmp_path) == audit.probe(["python3", "-m", "pytest"], tmp_path) == "whole-suite"
    assert audit.probe(["bash", "-lc", "no-such-r5h4 --x"], tmp_path) == audit.probe(["no-such-r5h4"], tmp_path) == "missing-command"
    assert audit.probe(["python3", "-m", "json.tool", "--help"], tmp_path) is audit.probe(["bash", "-c", "true"], tmp_path) is None


def test_dry_run_counts_and_moves_nothing(tmp_path):
    met = {"kind": "metric_threshold", "stat": "mean", "op": "lt", "value": 1, "runs": 3}
    root = store(tmp_path, ("ok", OK, "t"), ("gone", {**OK, "cmd": ["no-such-binary-r5h4"]}, "t"),
                 ("file", {"kind": "file_assert", "path": "a", "contains": "x"}, "t"),
                 ("met", {**met, "metric": "tokens"}, "t"), ("odd", {**met, "metric": "nobody_makes_this"}, "t"),
                 ("ui", {"kind": "dom_assert", "path": "/", "selector": "#a", "text": "b"}, "t"))
    runner = fake({"exit_code": (0, "exit 0"), "file_assert": (1, "missing file a"), "dom_assert": RuntimeError("driver"),
                   "metric_threshold": (1, "tokens: 0 runs, need 3 (pass --metrics)")})
    s = audit.audit(root, tmp_path, known={"tokens"}, runner=runner)
    assert (s["total"], s["runnable"], s["converted"]) == (6, 1, 0)
    assert s["kept"] == {"needs-metrics": 1, "check-crashed": 1}
    assert s["rejected"] == {"missing-command": 1, "missing-file": 1, "unmeasured-metric": 1}
    assert len(list(root.rglob("*.json"))) == 6


def test_fix_moves_never_deletes_and_records_the_reason(tmp_path):
    root = store(tmp_path, ("ok", OK, "t"), ("shot", SHOT, "the page looks right"))
    s = audit.audit(root, tmp_path, fix=True, runner=fake({"exit_code": (0, "exit 0"), "screenshot_diff": MISSING_IMAGE}))
    assert s["rejected"] == {"missing-image": 1} and not (root / "hidden/exam_shot.json").exists()
    assert json.loads((root / "rejected/exam_shot.json").read_text(encoding="utf-8"))["id"] == "exam_shot"
    row = json.loads((root / "rejected/reasons.jsonl").read_text(encoding="utf-8"))
    assert (row["exam_id"], row["reason"]) == ("exam_shot", "missing-image")
    assert audit.audit(root, tmp_path, runner=fake({"exit_code": (0, "")}))["total"] == 1  # rejected/ is skipped


def test_screenshot_label_exam_becomes_dom_assert_only_when_clear_and_runnable(tmp_path):
    root = store(tmp_path, ("chip", SHOT, 'The chip `[data-testid=model-chip]` must read "Auto" by default.'),
                 ("two", SHOT, 'Labels "A" and "B" on `.x`.'))
    s = audit.audit(root, tmp_path, fix=True, runner=fake({"screenshot_diff": MISSING_IMAGE, "dom_assert": (0, "ok")}))
    assert (s["converted"], s["runnable"], s["rejected"]) == (1, 1, {"missing-image": 1})
    check = seal.read_json(root / "hidden/exam_chip.json")["check"]
    assert check == {"kind": "dom_assert", "root": "website/dist", "path": "/", "selector": "[data-testid=model-chip]", "text": "Auto"}
    root = store(tmp_path / "b", ("cant", SHOT, '`#x` reads "Y"'))
    s = audit.audit(root, tmp_path, runner=fake({"screenshot_diff": MISSING_IMAGE, "dom_assert": (1, "missing file q")}))
    assert s["converted"] == 0 and s["rejected"] == {"missing-image": 1}


def test_cli_prints_counts_not_exam_text(tmp_path):
    out = io.StringIO()
    assert audit.main(["--exams", str(store(tmp_path, ("ok", OK, "secret task"))), "--workdir", str(tmp_path)], out) == 0
    assert json.loads(out.getvalue())["runnable"] == 1 and "secret" not in out.getvalue()
