"""Post-merge regression runner and exam promotion, on fake exams and a fake runner."""

import io
import json
import pathlib
import subprocess
import sys

from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import routes  # noqa: E402
from judge import regress  # noqa: E402

_JUDGE = json.loads((ROOT / "schemas/judge.schema.json").read_text(encoding="utf-8"))
OUTPUT = Draft202012Validator({"$ref": "#/$defs/output", "$defs": _JUDGE["$defs"]})
EXAM = Draft202012Validator(json.loads((ROOT / "schemas/exam.schema.json").read_text(encoding="utf-8")))


def exam(name, visibility="regression", used=(1,), cmd=("true",)):
    return {"id": f"exam_{name}", "layer": 2, "origin": ["sig_20260101_0001"], "visibility": visibility, "task": "fake",
            "check": {"kind": "exit_code", "cmd": list(cmd), "expect": 0}, "created_round": 0, "used_rounds": list(used)}


def setup(tmp, monkeypatch, exams):
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp / "data"))
    (tmp / "data/exams/hidden").mkdir(parents=True)
    (tmp / "data/exams/hidden/set.json").write_text(json.dumps(exams), encoding="utf-8")


def fake(outcomes):
    def runner(exams, workdir, metrics):
        ev = [{"exam_id": e["id"], "ok": outcomes[e["id"]], "detail": "fake"} for e in exams]
        return {"verdict": "pass", "scores": {"regression": 1.0}, "paired_metrics": [], "evidence": ev}
    return runner


def go(tmp, sha, outcomes=None, metrics=None, runner=None):
    argv = ["--workdir", str(tmp), "--commit", sha]
    if metrics is not None:
        (tmp / "m.json").write_text(json.dumps(metrics), encoding="utf-8")
        argv += ["--metrics", str(tmp / "m.json")]
    out = io.StringIO()
    code = regress.main(argv, out, runner or fake(outcomes))
    return code, json.loads(out.getvalue())


def test_first_run_has_no_baseline_and_says_so(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [exam("a"), exam("b")])
    code, s = go(tmp_path, "s1", {"exam_a": True, "exam_b": False})
    assert code == 0 and s["baseline"] is None and s["regressions"] == []
    assert s["note"].startswith("no baseline") and s["counts"] == {"run": 2, "pass": 1, "fail": 1}
    assert json.loads((tmp_path / "data/regress/s1.json").read_text())["exams"] == {"exam_a": True, "exam_b": False}


def test_pass_to_fail_flip_is_a_regression(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [exam("a"), exam("b")])
    go(tmp_path, "s1", {"exam_a": True, "exam_b": False})
    code, s = go(tmp_path, "s2", {"exam_a": False, "exam_b": False})  # b failed before too: not a flip
    assert code == 1 and s["baseline"] == "s1" and s["regressions"] == [{"kind": "exam", "exam_id": "exam_a"}]
    code, s = go(tmp_path, "s3", {"exam_a": True, "exam_b": True})
    assert code == 0 and s["baseline"] == "s2"  # always the most recent earlier run
    assert [r["sha"] for r in routes.regress_runs()] == ["s3", "s2", "s1"]
    assert routes.regress_runs()[1]["regressions"] == [{"kind": "exam", "exam_id": "exam_a"}]


def test_metric_noise_within_band_is_not_flagged_but_a_real_drop_is(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [])
    base = {"first_token_ms": [1000, 1100, 900, 1000], "success_rate": [0.9, 0.92, 0.88, 0.9]}
    go(tmp_path, "s1", metrics={"current": base})
    code, s = go(tmp_path, "s2", metrics={"current": {"first_token_ms": [1050, 1100, 1000, 1050], "success_rate": [0.9, 0.89, 0.9, 0.89]}})
    assert code == 0 and s["regressions"] == []
    code, s = go(tmp_path, "s3", metrics={"current": {"first_token_ms": [800, 800, 800, 800], "success_rate": [0.6, 0.6, 0.6, 0.6]}})
    assert code == 1 and [(r["kind"], r["a"], r["b"]) for r in s["regressions"]] == [("metric", "first_token_ms", "success_rate")]


def test_real_judge_runs_only_regression_exams_and_output_validates(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [exam("ok"), exam("bad", cmd=("false",)), exam("secret", "hidden", used=())])
    code, s = go(tmp_path, "s1", runner=regress.default_runner)
    stored = json.loads((tmp_path / "data/regress/s1.json").read_text())
    assert stored["exams"] == {"exam_ok": True, "exam_bad": False}  # the hidden exam never ran
    assert list(OUTPUT.iter_errors(stored["judge"])) == []


def test_worktree_run_uses_the_commit_sha_and_cleans_up(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [exam("a")])
    repo = tmp_path / "kc"
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "PATH": "/usr/bin:/bin"}
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "x"], check=True, env=env)
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out = io.StringIO()
    assert regress.main(["--kirocrew", str(repo), "--commit", "HEAD"], out, fake({"exam_a": True})) == 0
    assert json.loads(out.getvalue())["sha"] == sha and (tmp_path / f"data/regress/{sha}.json").is_file()
    wt = subprocess.run(["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True).stdout
    assert len(wt.splitlines()) == 1


def test_promote_moves_a_used_hidden_exam_and_refuses_an_unused_one(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch, [exam("used", "hidden", used=(3,)), exam("fresh", "hidden", used=())])
    out = io.StringIO()
    assert regress.main(["promote", "exam_used"], out) == 0 and json.loads(out.getvalue())["promoted"] == "exam_used"
    assert regress.main(["promote", "exam_fresh"], io.StringIO()) == 2
    assert "not been used" in capsys.readouterr().err
    assert regress.main(["promote", "exam_used"], io.StringIO()) == 2  # already regression
    doc = json.loads((tmp_path / "data/exams/hidden/set.json").read_text())
    assert [e["visibility"] for e in doc] == ["regression", "hidden"]
    assert all(EXAM.is_valid(e) for e in doc)
