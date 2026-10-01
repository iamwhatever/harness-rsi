"""Build the judge's --metrics file from baseline/metrics.py outputs: one file per run, before and after.

Usage: python -m judge.metrics_in --before b1.json b2.json --after a1.json a2.json [--pair a,b] --out metrics.json

Each input is a `baseline/metrics.py --out` document; every non-null number under `metrics` becomes one sample.
"""

import argparse
import json
import pathlib
import sys

# Pairs over baseline/metrics.py names: speed or cost may not improve at the cost of success.
PAIRS = [("total_latency_ms_p50", "turn_success_rate"), ("credits_per_turn_p50", "turn_success_rate")]


def samples(paths):
    out = {}
    for path in paths:
        doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        for name, value in doc.get("metrics", {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out.setdefault(name, []).append(float(value))
    return out


def build(before, after, pairs=None):
    return {"baseline": samples(before), "current": samples(after), "pairs": [list(p) for p in pairs or PAIRS]}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m judge.metrics_in", description=__doc__.splitlines()[0])
    ap.add_argument("--before", nargs="+", required=True, help="metrics.json files measured on the base")
    ap.add_argument("--after", nargs="+", required=True, help="metrics.json files measured on the PR head")
    ap.add_argument("--pair", action="append", help="a,b metric names read together (repeatable)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    pairs = [tuple(p.split(",")) for p in args.pair or []]
    if any(len(p) != 2 or not all(p) for p in pairs):
        ap.error("--pair takes two metric names: a,b")
    try:
        doc = build(args.before, args.after, pairs)
    except (OSError, ValueError) as exc:
        print(f"metrics_in: error: {exc}", file=sys.stderr)
        return 2
    pathlib.Path(args.out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
