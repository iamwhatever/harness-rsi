"""The outcome ledger: ``$HARNESS_RSI_DATA/outcomes.jsonl``, one ``outcome`` schema row per line.

A change appends the whole row, signed (``judge.seal``); the newest row per ``(card_id, pr)`` is current.
An unsigned or tampered line is left out and counted by ``integrity``: any such line voids the round
that reads the ledger. A row that fails the schema is left out. Card -> decision -> PR -> merge sha -> judge score -> regress, plus the crew prompt versions behind the card and the worker chat dispatched for it.
"""

import datetime as dt
import json
import threading

try:  # see settings.py: a subpackage in the gateway, top-level in tests and the CLI
    from ..judge import seal
except ImportError:
    from judge import seal

from . import dispatch, store

FILE = "outcomes.jsonl"
_LOCK = threading.Lock()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def blank(pr, card_id=None, link="board"):
    return {"card_id": card_id, "decision": None, "pr": pr, "link": link, "state": "open", "base_sha": None, "head_sha": None,
            "merged_sha": None, "exam_ids": [], "score": {"base": None, "head": None, "metrics": None}, "promoted": [],
            "regress": [], "prompt_versions": None, "dispatch": None, "note": "", "at": now()}


def _signed():
    return seal.signed_lines(store.data_dir() / FILE, seal.OUTCOMES)


def integrity():
    """``{"bad_lines": n, "void": n > 0}``: lines that are unsigned or fail their signature."""
    bad = _signed()[1]
    return {"bad_lines": bad, "void": bad > 0}


def rows():
    """Current signed rows, in the order they were first written; a row from before prompt versions or dispatch reads them as null."""
    latest = {}
    old = ({"prompt_versions": None, "dispatch": None, **r} for r in _signed()[0])
    for r in (r for r in old if store.valid("outcome", r)):
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
            fh.write(json.dumps(seal.sign(seal.OUTCOMES, row), ensure_ascii=False) + "\n")
    return row


def link(card_id, pr, how="board"):
    """Link a card to a PR once, with the worker chat that was dispatched for it; an existing link is returned as it is."""
    sent = dispatch.current(card_id) if card_id else None
    chat = {"session": sent["session"], "at": sent["at"]} if sent and sent["session"] else None
    return get(card_id, pr) or put({**blank(pr, card_id, how), "dispatch": chat})
