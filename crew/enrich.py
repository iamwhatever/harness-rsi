"""Enrich the merged signal list before the question setter sees it.

Two deterministic steps, no model and no reads:

1. A Slack row still marked ``testable.ok: false`` is re-judged from its pain gist
   (``adapters.slack.testable``, injectable), so rows from an older collector run or a
   saved ``signals.jsonl`` get a checkable task when their pain is a concrete behaviour.
2. A Slack row whose pain matches a GitHub or session row is merged into it:
   the hotter row stays the head, the other gets ``dedup_of``, and the head's counts
   add up across sources (people are distinct per source, so they are summed). A
   testable row never hides behind an untestable head: its task moves to the head.

The schema is untouched: only ``testable``, ``mentions`` and ``dedup_of`` change.

Usage: ``python3 crew/enrich.py SIGNALS.jsonl`` prints the counts only (no text, no rows).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters import slack  # noqa: E402

_WORD = re.compile(r"[a-z][a-z0-9'-]{2,}")
_GENERIC = set("""kiro crew kirocrew kiro-crew getting keep keeps issue issues error errors random randomly facing
morning sure think thing things work working works anybody everyone something happening since same other until
agent agents subagent subagents session sessions tool tools default read developer chat chats run runs user""".split())
MIN_SHARED, MIN_SHARE = 2, 0.25  # shared words, and their share of the shorter row's words
_CROSS = ("github:", "session:")


def keys(pain: str) -> set[str]:
    """Content-word stems of a pain line."""
    out = set()
    for w in _WORD.findall(pain.casefold()):
        if w in slack._STOP or w in _GENERIC:
            continue
        for end in ("ing", "ed", "es", "s"):
            if w.endswith(end) and len(w) - len(end) >= 4:
                w = w[: -len(end)]
                break
        out.add(w)
    return out


def _same(a: set[str], b: set[str]) -> int:
    shared = len(a & b)
    return shared if shared >= MIN_SHARED and shared >= MIN_SHARE * min(len(a), len(b)) else 0


def _fold(head: dict, dup: dict, rows: list[dict]) -> None:
    for r in rows:  # anything pointing at the dup now points at the head
        if r["dedup_of"] == dup["id"]:
            r["dedup_of"] = head["id"]
    dup["dedup_of"] = head["id"]
    for k in ("count", "people"):
        head["mentions"][k] += dup["mentions"][k]
    head["mentions"]["window_days"] = max(head["mentions"]["window_days"], dup["mentions"]["window_days"])
    if not head["testable"]["ok"] and dup["testable"]["ok"]:
        head["testable"] = dict(dup["testable"])


def enrich(signals: list[dict], testable: Callable[[str], dict] = slack.testable) -> tuple[list[dict], dict]:
    """A copy of ``signals`` enriched in place of the originals, plus counts (no text)."""
    rows = [{**s, "mentions": dict(s["mentions"]), "testable": dict(s["testable"])} for s in signals]
    is_slack = [r["source"].startswith("slack:") for r in rows]
    before = sum(r["testable"]["ok"] for r, s in zip(rows, is_slack) if s)
    for r, s in zip(rows, is_slack):
        if s and not r["testable"]["ok"]:
            r["testable"] = testable(r["pain"])
    merges = 0
    for r, s in zip(rows, is_slack):
        if not s or r["dedup_of"]:
            continue
        mine = keys(r["pain"])
        best = max(((_same(mine, keys(o["pain"])), o["mentions"]["people"], n) for n, o in enumerate(rows)
                    if o["source"].startswith(_CROSS) and not o["dedup_of"]), default=(0, 0, -1))
        if best[0]:
            other = rows[best[2]]
            head, dup = (other, r) if other["mentions"]["people"] >= r["mentions"]["people"] else (r, other)
            _fold(head, dup, rows)
            merges += 1
    after = sum(r["testable"]["ok"] for r, s in zip(rows, is_slack) if s)
    top = max((r["mentions"]["people"] for r in rows if not r["dedup_of"]), default=0)
    return rows, {"slack_testable_before": before, "slack_testable_after": after,
                  "cross_source_merges": merges, "top_people": top}


def main(argv: list[str]) -> int:
    with open(argv[0], encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    print(json.dumps(enrich(rows)[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
