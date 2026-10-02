"""The outcome ledger: ``$HARNESS_RSI_DATA/outcomes.jsonl``, one ``outcome`` schema row per line.

A change appends the whole row; the newest row per ``(card_id, pr)`` is current, and a row that
fails the schema is left out. Card -> decision -> PR -> merge sha -> judge score -> regress.
"""

import datetime as dt
import json
import threading

from . import store

FILE = "outcomes.jsonl"
_LOCK = threading.Lock()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def blank(pr, card_id=None, link="board"):
    return {"card_id": card_id, "decision": None, "pr": pr, "link": link, "state": "open", "base_sha": None, "head_sha": None,
            "merged_sha": None, "exam_ids": [], "score": {"base": None, "head": None, "metrics": None}, "promoted": [],
            "regress": [], "note": "", "at": now()}


def rows():
    """Current rows, in the order they were first written."""
    latest = {}
    for r in (r for r in store._jsonl(FILE) if store.valid("outcome", r)):
        latest[(r["card_id"], r["pr"])] = r
    return list(latest.values())


def get(card_id, pr):
    return next((r for r in rows() if r["card_id"] == card_id and r["pr"] == pr), None)


def put(row):
    """Append ``row`` stamped now; ValueError when it fails the schema."""
    if not store.valid("outcome", row := {**row, "at": now()}):
        raise ValueError(f"outcome row for {row.get('pr')} fails the schema")
    with _LOCK:
        (path := store.data_dir() / FILE).parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def link(card_id, pr, how="board"):
    """Link a card to a PR once; an existing link is returned as it is."""
    return get(card_id, pr) or put(blank(pr, card_id, how))
