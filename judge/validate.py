"""Dry-run one exam before it is written: refuse it when it breaks the schema or its check cannot run.

Usage: python -m judge.validate EXAM.json --workdir DIR [--shots DIR] [--metrics FILE]
Exit 0 runnable (the check passed or failed), 1 refused (it could not run), 2 bad exam file.
A refused exam names what it is missing, e.g. a baseline image nobody produced or a SPA that was never built.
"""

import argparse
import json
import pathlib
import sys

from jsonschema import Draft202012Validator

from . import png
from .core import run_check

SCHEMA = pathlib.Path(__file__).resolve().parents[1] / "schemas" / "exam.schema.json"


def validate(exam, workdir, shots=None, metrics=None):
    errors = [e.message for e in Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(exam)]
    if errors:
        return 2, {"exam_id": exam.get("id") if isinstance(exam, dict) else None, "status": "invalid", "detail": "; ".join(errors)}
    work = pathlib.Path(workdir).resolve()
    ctx = {"workdir": work, "shots": pathlib.Path(shots) if shots else work / "shots", "metrics": metrics or {},
           "round": None, "comparer": png.diff_ratio}
    ok, detail = run_check(exam["check"], ctx)
    status = "refused" if ok is None else "pass" if ok else "fail"
    return (1 if ok is None else 0), {"exam_id": exam["id"], "status": status, "detail": detail}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m judge.validate", description=__doc__.splitlines()[0])
    ap.add_argument("exam", help="one exam JSON file")
    ap.add_argument("--workdir", required=True, help="dir the check runs in, as the judge would")
    ap.add_argument("--shots", help="dir of current screenshots (default: WORKDIR/shots)")
    ap.add_argument("--metrics", help="metrics JSON, as for the judge")
    args = ap.parse_args(argv)
    try:
        exam = json.loads(pathlib.Path(args.exam).read_text())
        metrics = json.loads(pathlib.Path(args.metrics).read_text()) if args.metrics else {}
    except (OSError, ValueError) as exc:
        print(f"validate: error: {exc}", file=sys.stderr)
        return 2
    code, report = validate(exam, args.workdir, args.shots, metrics)
    print(json.dumps(report))
    return code


if __name__ == "__main__":
    sys.exit(main())
