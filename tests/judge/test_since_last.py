"""`regress --since-last` against a fake KiroCrew origin, `--build`, and `promote --used-round`."""

import io
import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from judge import regress  # noqa: E402

def git(repo, *args):
    env = {"PATH": "/usr/bin:/bin", **{f"GIT_{w}_{k}": "t@t" for w in ("AUTHOR", "COMMITTER") for k in ("NAME", "EMAIL")}}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def exam(name, visibility="regression", used=(1,)):
    return {"id": f"exam_{name}", "layer": 2, "origin": ["sig_20260101_0001"], "visibility": visibility, "task": "fake",
            "check": {"kind": "exit_code", "cmd": ["true"], "expect": 0}, "created_round": 0, "used_rounds": list(used)}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path / "data"))
    (tmp_path / "data/exams/hidden").mkdir(parents=True)
    (tmp_path / "data/exams/hidden/a.json").write_text(json.dumps(exam("a")), encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main", "origin")
    git(tmp_path / "origin", "commit", "-q", "--allow-empty", "-m", "one")
    git(tmp_path, "clone", "-q", "origin", "kc")
    return tmp_path, tmp_path / "origin", tmp_path / "kc"


def since(clone, *extra, status="pass"):
    calls, out = [], io.StringIO()

    def runner(exams, workdir, metrics):
        calls.append(sorted(p.name for p in workdir.iterdir()))
        ev = [{"exam_id": e["id"], "ok": status == "pass", "status": status, "detail": "fake"} for e in exams]
        return {"verdict": "pass", "scores": {}, "paired_metrics": [], "evidence": ev}
    code = regress.main(["--since-last", "--kirocrew", str(clone), *extra], out, runner)
    return code, (json.loads(out.getvalue()) if out.getvalue() else None), calls


def test_runs_once_per_new_main_head(world):
    tmp, origin, clone = world
    code, first, calls = since(clone)
    assert code == 0 and len(calls) == 1 and first["sha"] == git(origin, "rev-parse", "HEAD")
    assert first["counts"] == {"run": 1, "pass": 1, "fail": 0} and (tmp / f"data/regress/{first['sha']}.json").is_file()
    code, s, calls = since(clone)
    assert code == 0 and calls == [] and s["note"] == "no new merge since last regress"
    git(origin, "commit", "-q", "--allow-empty", "-m", "two")
    code, s, calls = since(clone)
    assert len(calls) == 1 and s["sha"] == git(origin, "rev-parse", "HEAD") and s["baseline"] == first["sha"]
    git(origin, "commit", "-q", "--allow-empty", "-m", "three")
    code, s, _ = since(clone, status="error")  # could not run: listed, never a pass-to-fail flip
    assert code == 0 and s["errors"] == ["exam_a"] and s["regressions"] == [] and s["counts"]["run"] == 0


def test_build_runs_in_the_worktree_and_a_failed_build_is_an_error(world, capsys):
    _, _, clone = world
    _, _, calls = since(clone, "--build", "echo nope >&2; exit 3")
    assert calls == [] and "build failed: nope" in capsys.readouterr().err
    _, _, calls = since(clone, "--build", "touch built.txt")
    assert "built.txt" in calls[0] and not (clone / "built.txt").exists()
    with pytest.raises(SystemExit):  # --since-last picks the commit itself
        regress.main(["--since-last", "--kirocrew", str(clone), "--commit", "HEAD"], io.StringIO())


def test_promote_records_an_unrecorded_round_and_rejected_is_ignored(world):
    tmp = world[0]
    (tmp / "data/exams/hidden/b.json").write_text(json.dumps(exam("b", "hidden", ())), encoding="utf-8")
    assert regress.main(["promote", "exam_b"], io.StringIO()) == 2  # no round recorded, none given
    assert regress.main(["promote", "exam_b", "--used-round", "2"], io.StringIO()) == 0
    saved = json.loads((tmp / "data/exams/hidden/b.json").read_text(encoding="utf-8"))
    assert (saved["visibility"], saved["used_rounds"]) == ("regression", [2])
    (tmp / "data/exams/rejected").mkdir()
    (tmp / "data/exams/rejected/a.json").write_text(json.dumps(exam("a")), encoding="utf-8")  # same id would clash
    assert [e["id"] for e in regress.load_all(tmp / "data/exams")] == ["exam_a", "exam_b"]
