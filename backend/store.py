"""The shared data dir: schema-checked reads, the decision append and the signal merge.

Under ``$HARNESS_RSI_DATA`` (default ``~/.kiro/crew/harness-rsi-data``): ``signals.jsonl``,
``proposals.json`` (a list), ``decisions.jsonl`` (append-only ``{proposal_id, decision, ts}``).
A missing dir or file reads as an empty list; a row that fails its schema is left out.
"""

import functools
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = ("do", "skip", "later")
_LOCK = threading.Lock()
_WORD_RE = re.compile(r"[\W_]+")


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


def web_rank(row):
    """0 for a web page the trend scout read, 1 for a rising repo (``trend:github:``), 2 for everything else."""
    return 0 if row["layer"] == "external" and not row["source"].startswith("trend:github:") else 1 if row["layer"] == "external" else 2


def by_heat(rows):
    """Primaries first; among them web sources first (``web_rank``), then most people, then most mentions."""
    return sorted(rows, key=lambda r: (bool(r["dedup_of"]), web_rank(r), -r["mentions"]["people"], -r["mentions"]["count"]))


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


def _free_id(sid, taken):
    return next((c for c in (f"sig_{sid[4:12]}_{n:04d}" for n in range(1, 10000)) if c not in taken), None)


def merge(existing, batches):
    """One item (first link) is one row, refreshed in place; an id another item holds is
    swapped for a free one; a primary whose pain matches an earlier primary joins it."""
    rows = [dict(r) for r in existing]
    at = {r["links"][0]: i for i, r in enumerate(rows)}
    for batch in batches:
        batch, taken, remap = [r for r in batch if valid("signal", r)], {r["id"] for r in rows}, {}
        for r in batch:
            i = at.get(r["links"][0])
            remap[r["id"]] = rows[i]["id"] if i is not None else r["id"] if r["id"] not in taken else _free_id(r["id"], taken)
            taken.add(remap[r["id"]])
        for r in (r for r in batch if remap[r["id"]]):
            r = {**r, "id": remap[r["id"]], "dedup_of": remap.get(r["dedup_of"], r["dedup_of"])}
            i = at.setdefault(r["links"][0], len(rows))
            rows[i:i + 1] = [r]
    first, ids = {}, {r["id"]: r for r in rows}
    for r in rows:
        key = _WORD_RE.sub(" ", r["pain"].casefold()).strip()
        if not r["dedup_of"] and first.setdefault(key, r["id"]) != r["id"]:
            r["dedup_of"] = first[key]
    for r in rows:  # a row merged into a merged row points at the root
        for _ in rows:
            if not (r["dedup_of"] in ids and ids[r["dedup_of"]]["dedup_of"]):
                break
            r["dedup_of"] = ids[r["dedup_of"]]["dedup_of"]
        if r["dedup_of"] == r["id"]:
            r["dedup_of"] = None
    return rows


def write_signals(rows):
    path = data_dir() / "signals.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".signals.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    os.replace(tmp, path)


def refresh(batches):
    """Merge ``batches`` into signals.jsonl under the lock; returns ``(total, added)``."""
    with _LOCK:
        before = read_signals()
        rows = merge(before, batches)
        write_signals(rows)
        return len(rows), len(rows) - len(before)
