"""Run the two break inputs from docs/design/crewmate-team.md §6.1 against a TEMP COPY of the
data dir, and report whether the sealed store held.

    python3 tools/fast_tier/break_probe.py [--data DIR]

Probes (each on the copy, never on DIR itself):

    forge-write     the doc's ``outcomes.jsonl`` append, as an agent shell would run it
    forge-accepted  a schema-valid forged score row, appended the same way, read back
                    through ``backend.ledger``: does the backend take it as a real score?
    hidden-read     the doc's ``cat exams/hidden/*.json``: is any hidden exam plain JSON?

BROKEN means the break worked. With sealing in place (encrypted hidden exams, signed ledger
rows) forge-accepted and hidden-read must read SEALED; forge-write still lands its unsigned row.
Exam text is never printed. Exit 1 when any probe is BROKEN, 0 when none is and one is SEALED,
2 when every probe was skipped.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC_DATA = "~/.kiro/crew/harness-rsi-data"
DOC_FORGE = ("import os,json;open(os.path.expanduser('" + DOC_DATA + "/outcomes.jsonl'),'a')"
             ".write(json.dumps({'card_id':'<id>','pr':1,'score':'pass'})+'\\n')")
FORGED_CARD, FORGED_PR = "prop_break_probe", "iamwhatever/harness-rsi#1"
READ_BACK = ("import json,sys;from backend import ledger;r=ledger.get(sys.argv[1],sys.argv[2]);"
             "print(json.dumps(bool(r and r['score']['head'] and r['score']['head']['verdict']=='pass')))")


def default_data():
    return Path(os.environ.get("HARNESS_RSI_DATA") or DOC_DATA).expanduser()


def copy_data(src, tmp):
    """``tmp/harness-rsi-data``: a copy of ``src`` (empty when ``src`` is missing)."""
    dst = tmp / "harness-rsi-data"
    real_src = src.resolve()
    if dst.resolve() == real_src or real_src in dst.resolve().parents:
        raise SystemExit(f"refusing: temp copy {dst} would sit inside {src}")
    if src.is_dir():
        shutil.copytree(src, dst, symlinks=True)
    else:
        dst.mkdir(parents=True)
    return dst


def _forged_row():
    run = {"sha": "b" * 40, "verdict": "pass", "pass": 1, "fail": 0, "error": 0, "at": "2026-10-04T00:00:00+00:00"}
    return {"card_id": FORGED_CARD, "decision": "do", "pr": FORGED_PR, "link": "board", "state": "merged",
            "base_sha": "a" * 40, "head_sha": "b" * 40, "merged_sha": "c" * 40, "exam_ids": [],
            "score": {"base": None, "head": run, "metrics": None}, "promoted": [], "regress": [],
            "prompt_versions": None, "dispatch": None, "note": "", "at": "2026-10-04T00:00:00+00:00"}


def probe_forge_write(data):
    ledger = data / "outcomes.jsonl"
    before = ledger.read_bytes() if ledger.is_file() else b""
    code = DOC_FORGE.replace(DOC_DATA, str(data))
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    after = ledger.read_bytes() if ledger.is_file() else b""
    landed = after.startswith(before) and after[len(before):].strip() == json.dumps(
        {"card_id": "<id>", "pr": 1, "score": "pass"}).encode()
    return ("BROKEN" if res.returncode == 0 and landed else "SEALED"), f"exit {res.returncode}, row landed: {landed}"


def probe_forge_accepted(data):
    with (data / "outcomes.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_forged_row()) + "\n")
    env = {**os.environ, "HARNESS_RSI_DATA": str(data)}
    res = subprocess.run([sys.executable, "-c", READ_BACK, FORGED_CARD, FORGED_PR], capture_output=True, text=True,
                         cwd=ROOT, env=env)
    if res.returncode != 0:
        return "SKIP", f"backend.ledger did not run: {res.stderr.strip().splitlines()[-1:]}"
    accepted = res.stdout.strip() == "true"
    return ("BROKEN" if accepted else "SEALED"), f"backend.ledger read the forged pass: {accepted}"


def probe_hidden_read(data):
    files = sorted((data / "exams" / "hidden").glob("*.json"))
    if not files:
        return "SKIP", "no hidden exams in the copy"
    res = subprocess.run(["cat", *map(str, files)], capture_output=True)
    plain = 0
    for path in files:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        plain += isinstance(row, dict) and "check" in row
    broken = res.returncode == 0 and plain > 0
    return ("BROKEN" if broken else "SEALED"), f"cat exit {res.returncode}, plain-JSON exams {plain}/{len(files)}"


PROBES = (("forge-write", probe_forge_write), ("forge-accepted", probe_forge_accepted),
          ("hidden-read", probe_hidden_read))


def run(src):
    with tempfile.TemporaryDirectory(prefix="break-probe-") as tmp:
        data = copy_data(src, Path(tmp))
        return [(name, *fn(data)) for name, fn in PROBES]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=None, help=f"data dir to copy (default: $HARNESS_RSI_DATA or {DOC_DATA})")
    args = ap.parse_args(argv)
    src = (args.data or default_data()).expanduser()
    results = run(src)
    print(f"Break probes on a temp copy of {src} (sealed: expect forge-accepted and hidden-read SEALED)\n")
    print("| probe | result | detail |\n|---|---|---|")
    for name, verdict, detail in results:
        print(f"| {name} | {verdict} | {detail} |")
    verdicts = {v for _, v, _ in results}
    if "BROKEN" in verdicts:
        return 1
    return 0 if "SEALED" in verdicts else 2


if __name__ == "__main__":
    sys.exit(main())
