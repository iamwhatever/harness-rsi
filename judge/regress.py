"""Post-merge regression run: re-run every `regression` exam on one KiroCrew commit and diff it.

``python -m judge.regress [--kirocrew DIR] [--commit REF]`` runs the `regression` suite plus the
paired metrics on that commit, stores ``$HARNESS_RSI_DATA/regress/<sha>.json`` and compares it with
the most recent earlier run: an exam that passed and now fails, or a paired metric that got worse
beyond its noise band, is a regression. One JSON summary on stdout; exit 0 clean, 1 regression, 2 error.

``python -m judge.regress promote EXAM_ID`` turns a hidden exam that has had its one round into a
regression exam. It is explicit on purpose: nothing inside a judge run changes an exam.
"""

import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile

from . import png
from .core import JudgeError, judge, paired

SUITES = ["regression", "metrics"]


def data_dir():
    return pathlib.Path(os.environ.get("HARNESS_RSI_DATA") or pathlib.Path.home() / ".kiro/crew/harness-rsi-data").expanduser()


def _files(root):
    root = pathlib.Path(root)
    if not root.is_dir():
        raise JudgeError(f"exams dir not found: {root.name}")
    return sorted(root.rglob("*.json"))  # hidden/ and any other subdir count too


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JudgeError(f"cannot read {path.name}: {exc}") from exc


def load_all(root):
    exams = [e for f in _files(root) for e in (lambda d: d if isinstance(d, list) else [d])(_read(f))]
    if len({e.get("id") for e in exams}) != len(exams):
        raise JudgeError("duplicate exam id in exams dir")
    return exams


def default_runner(exams, workdir, metrics):
    ctx = {"workdir": workdir, "shots": workdir / "shots", "metrics": metrics, "round": None, "comparer": png.diff_ratio}
    return judge({"suites": SUITES}, exams, ctx)


def _git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode:
        raise JudgeError(f"git {args[0]} failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def previous(runs_dir, sha):
    runs = [_read(p) for p in runs_dir.glob("*.json") if p.stem != sha]
    return max(runs, key=lambda r: r["at"]) if runs else None


def diff(now, before, metrics):
    if before is None:
        return []
    out = [{"kind": "exam", "exam_id": i} for i, ok in sorted(now["exams"].items()) if not ok and before["exams"].get(i)]
    band = {"current": now["samples"], "baseline": before["samples"], **{k: metrics[k] for k in ("pairs", "noise_floor") if k in metrics}}
    return out + [{"kind": "metric", **row} for row in paired(band) if not row["ok"]]


def run(args, runner):
    exams = [e for e in load_all(args.exams) if e.get("visibility") == "regression"]
    metrics = _read(pathlib.Path(args.metrics)) if args.metrics else {}
    if args.workdir:
        sha = args.commit or "local"
        result = runner(exams, pathlib.Path(args.workdir).resolve(), metrics)
    else:
        if not args.kirocrew:
            raise JudgeError("pass --kirocrew DIR (a KiroCrew clone) or --workdir DIR")
        if args.fetch:
            _git(args.kirocrew, "fetch", "-q", "origin", "main")
        sha = _git(args.kirocrew, "rev-parse", "--verify", f"{args.commit or 'origin/main'}^{{commit}}")
        with tempfile.TemporaryDirectory() as tmp:
            tree = pathlib.Path(tmp) / "kc"
            _git(args.kirocrew, "worktree", "add", "-q", "--detach", str(tree), sha)
            try:
                result = runner(exams, tree, metrics)
            finally:
                _git(args.kirocrew, "worktree", "remove", "--force", str(tree))
    ok = {e["exam_id"]: e["ok"] for e in result["evidence"] if "exam_id" in e}  # suite-level notes carry no id
    now = {"sha": sha, "at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "exams": ok,
           "counts": {"run": len(ok), "pass": sum(ok.values()), "fail": len(ok) - sum(ok.values())},
           "samples": metrics.get("current", {}), "judge": result}
    runs_dir = data_dir() / "regress"
    runs_dir.mkdir(parents=True, exist_ok=True)
    before = previous(runs_dir, sha)
    now["baseline"] = before["sha"] if before else None
    now["regressions"] = diff(now, before, metrics)
    (runs_dir / f"{sha}.json").write_text(json.dumps(now, indent=2) + "\n", encoding="utf-8")
    summary = {k: now[k] for k in ("sha", "baseline", "counts", "regressions")}
    summary["note"] = "no baseline: first regress run" if before is None else f"compared with {before['sha']}"
    return summary


def promote(exams_dir, exam_id):
    for path in _files(exams_dir):
        doc = _read(path)
        items = doc if isinstance(doc, list) else [doc]
        for exam in (e for e in items if e.get("id") == exam_id):
            if exam.get("visibility") != "hidden":
                raise JudgeError(f"{exam_id} is {exam.get('visibility')}, not hidden")
            if not exam.get("used_rounds"):
                raise JudgeError(f"{exam_id} has not been used for its hidden round yet")
            exam["visibility"] = "regression"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
            return {"promoted": exam_id, "used_rounds": exam["used_rounds"]}
    raise JudgeError(f"no exam {exam_id}")


def main(argv=None, stdout=None, runner=default_runner):
    argv, out = list(sys.argv[1:] if argv is None else argv), stdout or sys.stdout
    ap = argparse.ArgumentParser(prog="python -m judge.regress", description=__doc__)
    ap.add_argument("--exams", default=str(data_dir() / "exams"), help="exam dir (outside git)")
    if argv[:1] == ["promote"]:
        ap.add_argument("exam_id")
        args = ap.parse_args(argv[1:])
        job = lambda: promote(args.exams, args.exam_id)  # noqa: E731
    else:
        ap.add_argument("--kirocrew", help="a KiroCrew git clone; the commit runs in a throwaway worktree")
        ap.add_argument("--commit", help="commit to test (default: origin/main)")
        ap.add_argument("--fetch", action="store_true", help="git fetch origin main first")
        ap.add_argument("--workdir", help="run in this dir as-is instead of a worktree")
        ap.add_argument("--metrics", help="JSON {current: {metric: [samples]}, pairs?, noise_floor?}")
        args = ap.parse_args(argv)
        job = lambda: run(args, runner)  # noqa: E731
    try:
        summary = job()
    except JudgeError as exc:
        print(f"regress: error: {exc}", file=sys.stderr)
        return 2
    out.write(json.dumps(summary, indent=2) + "\n")
    return 1 if summary.get("regressions") else 0


if __name__ == "__main__":
    sys.exit(main())
