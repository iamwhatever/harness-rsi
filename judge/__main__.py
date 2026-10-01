"""CLI: judge `input` JSON on stdin, judge `output` JSON on stdout. Exit 0 pass, 1 fail, 2 setup error."""

import argparse
import importlib
import json
import os
import pathlib
import sys

from . import png
from .core import JudgeError, judge, load_exams

SUITES = {"new", "hidden", "regression", "metrics"}


def _load(text, what):
    try:
        return json.loads(text)
    except ValueError as exc:
        raise JudgeError(f"{what} is not JSON: {exc}") from exc


def _run(args, stdin):
    if not args.exams:
        raise JudgeError("no exams dir: pass --exams DIR or set HARNESS_RSI_DATA")
    inp = _load(stdin.read(), "stdin")
    if not isinstance(inp, dict) or not {"pr", "repo", "suites"} <= set(inp) <= {"pr", "repo", "suites", "exam_ids"}:
        raise JudgeError("input must be an object with pr, repo, suites and optional exam_ids")
    ids = inp.get("exam_ids")
    good = isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids) and len(set(ids)) == len(ids)
    if "exam_ids" in inp and not good:
        raise JudgeError("exam_ids must be a non-empty unique list of exam ids")
    suites = inp["suites"]
    if not isinstance(suites, list) or not suites or len(set(suites)) != len(suites) or not set(suites) <= SUITES:
        raise JudgeError(f"suites must be a non-empty unique list drawn from {sorted(SUITES)}")
    exams = load_exams(args.exams)
    try:
        metrics = _load(pathlib.Path(args.metrics).read_text(), "metrics file") if args.metrics else {}
    except OSError as exc:
        raise JudgeError(f"cannot read metrics file: {exc.strerror}") from exc
    module, _, name = (args.comparer or "").partition(":")
    work = pathlib.Path(args.workdir).resolve()
    ctx = {
        "workdir": work,
        "shots": pathlib.Path(args.shots) if args.shots else work / "shots",
        "metrics": metrics,
        "round": args.round,
        "comparer": getattr(importlib.import_module(module), name) if module else png.diff_ratio,
    }
    return judge(inp, exams, ctx)


def main(argv=None, stdin=None, stdout=None):
    data = os.environ.get("HARNESS_RSI_DATA")
    ap = argparse.ArgumentParser(prog="python -m judge", description=__doc__)
    ap.add_argument("--exams", default=os.path.join(data, "exams") if data else None, help="exam dir (outside git)")
    ap.add_argument("--workdir", default=".", help="dir that commands run in and paths resolve against")
    ap.add_argument("--shots", help="dir of current screenshots (default: WORKDIR/shots)")
    ap.add_argument("--metrics", help="JSON {current, baseline: {metric: [samples]}, pairs?, noise_floor?}")
    ap.add_argument("--round", type=int, help="current round; picks the `new` suite, allows a same-round rerun")
    ap.add_argument("--comparer", help="module:function(baseline, current) -> diff ratio (default: built-in PNG)")
    args, out = ap.parse_args(argv), stdout or sys.stdout
    try:
        result = _run(args, stdin or sys.stdin)
    except JudgeError as exc:
        print(f"judge: error: {exc}", file=sys.stderr)
        return 2
    out.write(json.dumps(result, indent=2) + "\n")
    return 0 if result["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
