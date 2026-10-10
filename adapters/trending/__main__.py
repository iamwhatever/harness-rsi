"""``python -m adapters.trending [--topic T ...] [--window-days N]``: rows as JSON on stdout, notes on stderr.

Without ``--topic`` it reads the owner's topics (``backend.topics``; the defaults outside a gateway).
"""

import argparse
import json
import sys

from . import DEFAULT_WINDOW_DAYS, collect


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--topic", action="append", default=[], help="a topic to search; repeat for more")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    args = ap.parse_args(argv)
    topics = args.topic
    if not topics:
        from backend import topics as conf
        topics = conf.read()["topics"]
    rows, notes = collect(topics, window_days=args.window_days)
    json.dump(rows, sys.stdout, indent=1)
    print()
    for n in notes:
        print(n, file=sys.stderr)
    return 0


sys.exit(main())
