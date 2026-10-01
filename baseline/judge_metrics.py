"""Write the file `python -m judge --metrics` reads, from baseline/metrics.py outputs.

Reuses judge.metrics_in.build and only fixes the pairs: speed or cost may not
improve at the cost of success. Credits are the cost metric because the
backend records no token counts.

Usage: python3 baseline/judge_metrics.py --before b1.json ... --after a1.json ... --out metrics.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from judge.metrics_in import build  # noqa: E402

PAIRS = [
    ("first_token_latency_ms_p50", "turn_success_rate"),
    ("total_latency_ms_p50", "turn_success_rate"),
    ("credits_per_turn_p50", "turn_success_rate"),
]


def write(before: list[str], after: list[str], out: Path) -> dict:
    doc = build(before, after, PAIRS)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--before", nargs="+", required=True, help="metrics.json files measured on the base")
    ap.add_argument("--after", nargs="+", required=True, help="metrics.json files measured on the PR head")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    write(args.before, args.after, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
