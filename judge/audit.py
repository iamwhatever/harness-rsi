"""Dry-run every exam in the store; count runnable, kept and rejected-by-reason. `--fix` acts on it.

Usage: python -m judge.audit [--exams DIR] --workdir KIROCREW_CHECKOUT [--metrics FILE] [--fix]  (see README)
"""

import argparse
import collections
import datetime
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

from .regress import data_dir
from .validate import validate

ENV = {"no-spa", "no-browser", "needs-metrics", "check-crashed"}  # the exam is fine; this host or run is not
_REASONS = [("missing-file", "missing file"), ("missing-image", "missing image"), ("no-spa", "no built SPA"), ("no-browser",
            "playwright not installed"), ("no-browser", "cannot launch browser"), ("missing-command", "cannot run command"),
            ("needs-metrics", "runs, need")]
_SELECTOR = re.compile(r"`([.#\[][^`]+)`|(\[data-testid=[\"']?[\w-]+[\"']?\])")
_LABEL = re.compile(r"\"([^\"\n]{1,80})\"|“([^”\n]{1,80})”")
# A failed run whose last error line is one of these broke on the exam's own code, not the product's behaviour.
_BROKEN = [("missing-module", re.compile(r"ModuleNotFoundError|ImportError|module '[\w.]+' has no attribute")),
           ("broken-check", re.compile(r"SyntaxError|NameError|IndentationError|AttributeError|TypeError"))]
BASELINE = pathlib.Path(__file__).resolve().parents[1] / "baseline/metrics.json"  # what baseline/metrics.py measures
_FIND = "import importlib.util, sys; sys.exit(importlib.util.find_spec(sys.argv[1]) is None)"


def probe(cmd, work):
    """Why an exit_code command cannot even start in `work` (a plain run reads that as a fail), or None."""
    exe, flags_only = cmd[0], lambda rest: all(a.startswith("-") for a in rest)
    if (exe == "pytest" and flags_only(cmd[1:])) or (cmd[1:3] == ["-m", "pytest"] and flags_only(cmd[3:])):
        return "whole-suite"
    if not (shutil.which(exe) or (work / exe).is_file()):
        return "missing-command"
    ask = None  # ask the interpreter itself whether the module or script word resolves in `work`
    if exe in ("python", "python3") and len(cmd) > 2 and cmd[1] == "-m":
        ask = [exe, "-c", _FIND, cmd[2]], "missing-module"
    if exe in ("bash", "sh") and len(cmd) > 2 and cmd[1] in ("-c", "-lc"):
        ask = [exe, cmd[1], f"command -v {shlex.quote((shlex.split(cmd[2]) or [''])[0])}"], "missing-command"
    return ask[1] if ask and subprocess.run(ask[0], cwd=work, capture_output=True).returncode else None


def classify(exam, work, metrics, known, cap, runner=validate):
    check = exam.get("check", {})
    if check.get("kind") == "exit_code" and check.get("cmd"):
        if why := probe(check["cmd"], work):
            return why, why
        exam = {**exam, "check": {**check, "timeout_s": min(check.get("timeout_s", 600), cap)}}
    if check.get("kind") == "metric_threshold" and known is not None and check.get("metric") not in known:
        return "unmeasured-metric", f"no producer for {check.get('metric')}"  # it can never get samples
    try:
        code, report = runner(exam, work, None, metrics)
    except Exception as exc:  # e.g. a browser driver that dies on this host: the exam is not at fault
        return "check-crashed", type(exc).__name__
    if code == 0 and report.get("status") == "fail":
        if broken := next((k for k, rx in _BROKEN if rx.search(report["detail"])), None):
            return broken, report["detail"]
    reason = next((k for k, n in _REASONS if n in report["detail"]), "refused")
    return {0: "runnable", 2: "invalid"}.get(code, reason), report["detail"]


def to_dom(exam, spa_root):
    sels = {a or b for a, b in _SELECTOR.findall(exam.get("task", ""))}
    labels = {a or b for a, b in _LABEL.findall(exam.get("task", ""))}
    if exam.get("check", {}).get("kind") != "screenshot_diff" or len(sels) != 1 or len(labels) != 1:
        return None
    return {"kind": "dom_assert", "root": spa_root, "path": "/", "selector": sels.pop(), "text": labels.pop()}


def audit(root, work, metrics=None, known=None, fix=False, spa_root="website/dist", cap=120, runner=validate):
    root, work = pathlib.Path(root), pathlib.Path(work).resolve()
    out = {"total": 0, "runnable": 0, "kept": collections.Counter(), "converted": 0, "rejected": collections.Counter()}
    for path in sorted(p for p in root.rglob("*.json") if "rejected" not in p.relative_to(root).parts):
        exam = json.loads(path.read_text(encoding="utf-8"))  # the store holds one exam per file
        out["total"] += 1
        status, detail = classify(exam, work, metrics, known, cap, runner)
        dom = to_dom(exam, spa_root) if status not in ENV | {"runnable"} else None
        if dom and classify({**exam, "check": dom}, work, metrics, known, cap, runner)[0] == "runnable":
            out["converted"], status = out["converted"] + 1, "runnable"
            if fix:
                path.write_text(json.dumps({**exam, "check": dom}, indent=2) + "\n", encoding="utf-8")
        if status == "runnable":
            out["runnable"] += 1
        else:
            out["kept" if status in ENV else "rejected"][status] += 1
        if fix and status not in ENV | {"runnable"}:
            (root / "rejected").mkdir(exist_ok=True)
            os.replace(path, root / "rejected" / path.name)
            row = {"exam_id": exam.get("id"), "file": path.name, "reason": status, "detail": detail[:300],
                   "at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
            with (root / "rejected/reasons.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    return {**{k: dict(v) if isinstance(v, collections.Counter) else v for k, v in out.items()}, "fixed": fix}


def main(argv=None, stdout=None):
    ap = argparse.ArgumentParser(prog="python -m judge.audit", description=__doc__.splitlines()[0])
    ap.add_argument("--exams", default=str(data_dir() / "exams"), help="exam dir (outside git)")
    ap.add_argument("--workdir", required=True, help="KiroCrew checkout the checks run in, SPA built")
    ap.add_argument("--metrics", help="metrics JSON, as for the judge")
    ap.add_argument("--fix", action="store_true", help="move rejected exams to rejected/, convert UI-label exams")
    args = ap.parse_args(argv)
    metrics = json.loads(pathlib.Path(args.metrics).read_text(encoding="utf-8")) if args.metrics else {}
    known = set(json.loads(BASELINE.read_text(encoding="utf-8"))["metrics"])
    (stdout or sys.stdout).write(json.dumps(audit(args.exams, args.workdir, metrics, known, args.fix), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
