"""Prompt-change cards: ``$HARNESS_RSI_DATA/prompt_changes/<id>.json``, written by ``crew/propose.py``.

``read`` lists them, newest first, without the full prompt text. ``decide`` records the owner's choice;
``do`` applies the change through ``crew/prompts.apply`` (which refuses a prompt naming an exam and
writes the new version), so nothing is applied without the owner's click.
"""

import functools
import importlib.util
import json
import re

from . import store

_ID = re.compile(r"^pc_[0-9a-f]{10}$")
_SHOWN = ("id", "agent", "from", "to", "summary", "diff", "ab", "status", "at")


@functools.cache
def prompts():
    spec = importlib.util.spec_from_file_location("harness_rsi_prompts", store.ROOT / "crew" / "prompts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load(path):
    try:
        card = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return card if isinstance(card, dict) and _ID.match(str(card.get("id"))) and path.stem == card["id"] else None


def read():
    cards = (_load(p) for p in (store.data_dir() / "prompt_changes").glob("pc_*.json"))
    return sorted(({k: c.get(k) for k in _SHOWN} for c in cards if c), key=lambda c: str(c["at"]), reverse=True)


def decide(change_id, decision):
    """The updated card; LookupError for an unknown id, ValueError when the change cannot apply."""
    path = store.data_dir() / "prompt_changes" / f"{change_id}.json"
    card = _load(path) if _ID.match(str(change_id)) else None
    if card is None:
        raise LookupError(change_id)
    if decision == "do" and card["status"] != "applied":
        current = prompts().versions(prompts().effective(store.data_dir())).get(card["agent"])
        if current != card["from"]:
            raise ValueError(f"the {card['agent']} prompt changed since this card was written; propose again")
        card["applied"] = prompts().apply(store.data_dir(), card["agent"], card["prompt"], card["id"])
    card["status"] = "applied" if decision == "do" or card["status"] == "applied" else decision
    path.write_text(json.dumps(card, indent=1) + "\n", encoding="utf-8")
    return {k: card.get(k) for k in _SHOWN}
