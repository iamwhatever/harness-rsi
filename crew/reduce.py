"""Reduce a 2-round reviewer debate to the proposal list for the priority card.

A transcript is ``{"rounds": [{"round": 1, "turns": [{"agent", "text"}, ...]}, ...]}``.
Each reviewer's round-2 turn ends with a fenced ``json`` block holding its proposal
table. A proposal survives only when BOTH reviewers keep its id. Exam ids are
filled from the exams' ``origin`` (the reviewers never see exams); a proposal no
exam can judge is dropped. ``decision`` is always null: only the owner decides.

Usage: ``python3 crew/reduce.py TRANSCRIPT.json EXAMS.json`` prints the list.
"""

from __future__ import annotations

import json
import re
import sys

REVIEWERS = ("rsi-reviewer-value", "rsi-reviewer-risk")
ROUNDS = 2
_NEEDS = (("id", str), ("pain", str), ("signal_ids", list), ("heat", dict), ("cost", dict))
_TABLE = re.compile(r"```json\s*\n(.*?)\n```", re.S)


class DebateError(ValueError):
    """The transcript is not a finished 2-round debate between the two reviewers."""


def _check_shape(transcript: dict) -> dict[str, str]:
    rounds = transcript.get("rounds")
    if not isinstance(rounds, list) or len(rounds) != ROUNDS:
        raise DebateError(f"debate must have exactly {ROUNDS} rounds")
    for want, rnd in enumerate(rounds, start=1):
        agents = [t.get("agent") for t in rnd.get("turns", [])]
        if rnd.get("round") != want or sorted(agents) != sorted(REVIEWERS):
            raise DebateError(f"round {want} must hold one turn from each reviewer")
    return {t["agent"]: t["text"] for t in rounds[-1]["turns"]}


def _table(agent: str, text: str) -> dict[str, dict]:
    blocks = _TABLE.findall(text)
    if not blocks:
        raise DebateError(f"{agent} ended round {ROUNDS} without a proposal table")
    rows = json.loads(blocks[-1])
    if not isinstance(rows, list):
        raise DebateError(f"{agent} proposal table is not a list")
    return {row["id"]: row for row in rows if _complete(row)}


def _complete(row: dict) -> bool:
    """A row the merge can read; a missing risk list counts as no risks."""
    try:
        row["cost"].setdefault("risks", [])
        return all(isinstance(row[k], t) for k, t in _NEEDS) and all(
            isinstance(v, int) for v in (row["heat"]["people"], row["heat"]["window_days"],
                                         row["cost"]["files"], row["cost"]["lines"]))
    except (KeyError, TypeError, AttributeError):
        return False


def _merge(value: dict, risk: dict, exam_ids: list[str]) -> dict:
    signal_ids = list(dict.fromkeys(value["signal_ids"] + risk["signal_ids"]))
    risks = list(dict.fromkeys(value["cost"]["risks"] + risk["cost"]["risks"]))
    return {
        "id": value["id"],
        "pain": value["pain"],
        "signal_ids": signal_ids,
        "heat": {k: max(value["heat"][k], risk["heat"][k]) for k in ("people", "window_days")},
        "mock_artifact_slug": None,
        "cost": {
            "files": max(value["cost"]["files"], risk["cost"]["files"]),
            "lines": max(value["cost"]["lines"], risk["cost"]["lines"]),
            "risks": risks,
        },
        "exam_ids": exam_ids,
        "decision": None,
    }


def reduce_debate(transcript: dict, exams: list[dict]) -> list[dict]:
    """Return the agreed proposals, in the value reviewer's order."""
    final = _check_shape(transcript)
    value, risk = (_table(a, final[a]) for a in REVIEWERS)
    out = []
    for pid, row in value.items():
        if pid not in risk:
            continue
        merged = _merge(row, risk[pid], [])
        sigs = set(merged["signal_ids"])
        merged["exam_ids"] = [e["id"] for e in exams if sigs & set(e["origin"])]
        if merged["exam_ids"]:
            out.append(merged)
    return out


def main(argv: list[str]) -> int:
    with open(argv[0], encoding="utf-8") as fh:
        transcript = json.load(fh)
    with open(argv[1], encoding="utf-8") as fh:
        exams = json.load(fh)
    json.dump(reduce_debate(transcript, exams), sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
