#!/usr/bin/env python3
"""The meta step: from the outcome ledger and one A/B's failures, write ONE prompt change for ONE agent.

The proposer agent sees counts only (ledger totals, failure reasons, metric means), never an exam's id
or text, and its prompt must not name one either. The change is never applied here: it is saved as
``$HARNESS_RSI_DATA/prompt_changes/<id>.json`` with status ``pending``, and shows on the board as a
prompt-change card; the owner's 做 applies it (``prompts.apply``), which writes the new version.

Usage: ``python3 crew/propose.py --ab AB.json`` (a ``crew/ab.py`` result) prints the change id;
``python3 crew/propose.py --attach ID --ab AB.json`` puts that A/B's numbers (B = the change) on the card.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew"))
import prompts  # noqa: E402
from judge import seal  # noqa: E402  (prompts put the repo root on the path)

PROPOSER = "rsi-prompt-proposer"


def ledger_counts(data: Path) -> dict:
    rows, (signed, bad) = {}, seal.signed_lines(data / "outcomes.jsonl", seal.OUTCOMES)
    if bad:
        raise ValueError(f"outcome ledger has {bad} unsigned or tampered line(s); this round is void")
    for r in signed:
        rows[(r["card_id"], r["pr"])] = r
    runs = [((r["score"]["base"] or {}).get("verdict"), (r["score"]["head"] or {}).get("verdict")) for r in rows.values()]
    return {"linked_prs": len(rows), "merged": sum(r["state"] == "merged" for r in rows.values()),
            "no_exam": sum("no exam" in r["note"] for r in rows.values()), "scored": dict(Counter(f"base {b} / head {h}" for b, h in runs if b))}


def brief(data: Path, ab: dict) -> str:
    agent = ab["agent"]
    numbers = {m: {k: v[k] for k in ("A", "B", "verdict")} for m, v in ab["metrics"].items()}
    return (f"Agent to improve: {agent}\nIts current prompt:\n````\n{prompts.effective(data)[agent]}\n````\n"
            f"Outcome ledger counts:\n{json.dumps(ledger_counts(data))}\n"
            f"Replay of rounds {ab['rounds']} with this prompt, why its exams missed (reason: count):\n"
            f"{json.dumps(ab['failures']['A'])}\nMetrics:\n{json.dumps(numbers)}\n")


def change_card(data: Path, agent: str, new: str, summary: str) -> dict:
    old = prompts.effective(data)[agent]
    if found := prompts.leaks(new + "\n" + summary, prompts.bank(data)):
        raise ValueError(f"proposed prompt names {len(found)} exam(s); exams never go into prompts")
    if prompts.version(new) == prompts.version(old):
        raise ValueError("the proposed prompt is the current one")
    diff = "".join(difflib.unified_diff((old + "\n").splitlines(True), (new.rstrip("\n") + "\n").splitlines(True), f"{agent}.md (A)", f"{agent}.md (B)"))
    return {"id": "pc_" + prompts.version(new)[1:], "agent": agent, "from": prompts.version(old), "to": prompts.version(new),
            "summary": summary[:300], "diff": diff, "prompt": new.rstrip("\n"), "ab": None, "status": "pending",
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def propose(data: Path, ab: dict, call) -> dict:
    """Ask the proposer once; save its one change as pending. ValueError when the reply is unusable."""
    reply, _ = call(PROPOSER, prompts.effective(data)[PROPOSER], brief(data, ab))
    try:
        got = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
        card = change_card(data, ab["agent"], str(got["prompt"]), str(got["summary"]))
    except (ValueError, KeyError) as exc:
        raise ValueError(f"no usable change: {exc}") from exc
    save(data, card)
    return card


def save(data: Path, card: dict) -> None:
    (data / "prompt_changes").mkdir(parents=True, exist_ok=True)
    (data / "prompt_changes" / f"{card['id']}.json").write_text(json.dumps(card, indent=1) + "\n", encoding="utf-8")


def attach(data: Path, change_id: str, ab: dict) -> dict:
    card = json.loads((data / "prompt_changes" / f"{change_id}.json").read_text(encoding="utf-8"))
    if (ab["agent"], ab["B"]) != (card["agent"], card["to"]):
        raise ValueError("that A/B did not test this change")
    card["ab"] = {"rounds": ab["rounds"], "reps": ab["reps"], "A": ab["A"], "B": ab["B"],
                  "metrics": {m: {k: v[k] for k in ("A", "B", "verdict")} for m, v in ab["metrics"].items()},
                  "ran_at": ab.get("ran_at"), "command": ab.get("command")}
    save(data, card)
    return card


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ab", type=Path, required=True, help="a crew/ab.py result file")
    ap.add_argument("--attach", metavar="ID", help="put this A/B on change ID instead of proposing")
    args = ap.parse_args(argv)
    data = Path(os.environ.get("HARNESS_RSI_DATA", Path.home() / ".kiro/crew/harness-rsi-data"))
    ab = json.loads(args.ab.read_text(encoding="utf-8"))
    if args.attach:
        card = attach(data, args.attach, ab)
    else:
        import ab as replay  # kiro-cli wiring lives beside the A/B
        card = propose(data, ab, replay.kiro_call(data / "ab" / ".run"))
    print(json.dumps({k: card[k] for k in ("id", "agent", "from", "to", "summary")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
