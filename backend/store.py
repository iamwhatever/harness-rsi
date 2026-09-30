"""The shared data dir: schema-checked reads and the decision append.

Under ``$HARNESS_RSI_DATA`` (default ``~/.kiro/crew/harness-rsi-data``): ``signals.jsonl``,
``proposals.json`` (a list), ``decisions.jsonl`` (append-only ``{proposal_id, decision, ts}``).
A missing dir or file reads as an empty list; a row that fails its schema is left out.
"""

import functools
import json
import os
import threading
import time
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = ("do", "skip", "later")
_LOCK = threading.Lock()


def data_dir():
    return Path(os.environ.get("HARNESS_RSI_DATA") or Path.home() / ".kiro/crew/harness-rsi-data").expanduser()


@functools.cache
def _validator(name):
    return Draft202012Validator(json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8")))


def valid(name, row):
    return isinstance(row, dict) and _validator(name).is_valid(row)


def _load(line):
    try:
        return json.loads(line)
    except ValueError:
        return None


def _jsonl(name):
    try:
        return [_load(x) for x in (data_dir() / name).read_text(encoding="utf-8").splitlines()]
    except FileNotFoundError:
        return []


def read_signals():
    return [r for r in _jsonl("signals.jsonl") if valid("signal", r)]


def by_heat(rows):
    """Primaries first, then most people, then most mentions."""
    return sorted(rows, key=lambda r: (bool(r["dedup_of"]), -r["mentions"]["people"], -r["mentions"]["count"]))


def read_decisions():
    return [r for r in _jsonl("decisions.jsonl") if isinstance(r, dict)
            and r.get("decision") in DECISIONS and isinstance(r.get("proposal_id"), str)]


def read_proposals():
    """Proposals with each one's latest decision applied."""
    try:
        doc = _load((data_dir() / "proposals.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    latest = {r["proposal_id"]: r["decision"] for r in read_decisions()}
    rows = [p for p in doc if valid("proposal", p)] if isinstance(doc, list) else []
    return [{**p, "decision": latest.get(p["id"], p["decision"])} for p in rows]


def append_decision(proposal_id, decision, now=time.time):
    """Append one row; a repeat of the proposal's latest decision appends nothing."""
    with _LOCK:
        last = next((r["decision"] for r in reversed(read_decisions()) if r["proposal_id"] == proposal_id), None)
        if last == decision:
            return False
        path = data_dir() / "decisions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now()))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"proposal_id": proposal_id, "decision": decision, "ts": ts}) + "\n")
        return True

