"""Fold the crew log for one or more root sessions into a team view.

    python3 tools/fast_tier/team_fold.py ROOT_SLOT [ROOT_SLOT ...] [--root DIR] [--json]

Per root: the session tree (``session/opened`` parent edges), credits summed over
``turn/completed``, host auto-declines (``approval/decided`` with ``by: host``, grouped by
``cause``), and ``work/recorded`` counts by worker status and by action. Markdown by default,
ready for a PR body. Read-only.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crewlog  # noqa: E402


def fold_session(unit):
    if unit is None:
        return {"turns": 0, "credits": 0.0, "approvals": 0, "host_declines": Counter(), "work_status": Counter(),
                "work_action": Counter(), "logged": False}
    credits = sum(float(e["data"].get("credits") or 0) for e in unit.of("turn/completed"))
    declines = Counter(e["data"].get("cause") or "(no cause)" for e in unit.of("approval/decided")
                       if e["data"].get("by") == "host")
    work = unit.of("work/recorded")
    return {
        "turns": len(unit.of("turn/completed")),
        "credits": round(credits, 4),
        "approvals": len(unit.of("approval/requested")),
        "host_declines": declines,
        "work_status": Counter(e["data"]["status"] for e in work if e["data"].get("status")),
        "work_action": Counter(e["data"]["action"] for e in work if e["data"].get("action")),
        "logged": True,
    }


def fold_team(units, root_slot):
    rows = []
    for slot, depth in crewlog.subtree(units, root_slot):
        unit = units.get(slot)
        rows.append({"slot": slot, "depth": depth, "agent": crewlog.agent_of(unit), **fold_session(unit)})
    total = {"sessions": len(rows), "turns": 0, "credits": 0.0, "approvals": 0, "host_declines": Counter(),
             "work_status": Counter(), "work_action": Counter()}
    for r in rows:
        for k in ("turns", "approvals"):
            total[k] += r[k]
        total["credits"] = round(total["credits"] + r["credits"], 4)
        for k in ("host_declines", "work_status", "work_action"):
            total[k] += r[k]
    return {"root": root_slot, "found": root_slot in units, "sessions": rows, "total": total}


def _counts(counter):
    return ", ".join(f"{k} {v}" for k, v in sorted(counter.items())) or "-"


def markdown(team):
    lines = [f"### `{team['root']}`", ""]
    if not team["found"]:
        lines.append("No crew log for this slot under the given root.")
        return "\n".join(lines)
    lines += ["| session | depth | agent | turns | credits | approvals asked | host auto-declines |",
              "|---|---|---|---|---|---|---|"]
    for r in team["sessions"]:
        name = ("  " * r["depth"]) + r["slot"] + ("" if r["logged"] else " (no log)")
        lines.append(f"| `{name}` | {r['depth']} | {r['agent'] or '-'} | {r['turns']} | {r['credits']} | "
                     f"{r['approvals']} | {sum(r['host_declines'].values())} |")
    t = team["total"]
    lines += ["", "| total | value |", "|---|---|",
              f"| sessions | {t['sessions']} |", f"| turns | {t['turns']} |", f"| credits | {t['credits']} |",
              f"| approvals asked | {t['approvals']} |",
              f"| host auto-declines | {sum(t['host_declines'].values())} ({_counts(t['host_declines'])}) |",
              f"| work/recorded by status | {_counts(t['work_status'])} |",
              f"| work/recorded by action | {_counts(t['work_action'])} |"]
    return "\n".join(lines)


def _jsonable(team):
    return json.loads(json.dumps(team, default=dict))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("roots", nargs="+", help="root session slot key(s)")
    ap.add_argument("--root", type=Path, help="crew-log sessions dir (default: $KIROCREW_HOME/crew-log/sessions)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    args = ap.parse_args(argv)
    units = crewlog.load_units(args.root)
    teams = [fold_team(units, r) for r in args.roots]
    if args.json:
        print(json.dumps([_jsonable(t) for t in teams], indent=2))
    else:
        if not units:
            print(f"(no session crew logs under {args.root or crewlog.default_root()})", file=sys.stderr)
        print("\n\n".join(markdown(t) for t in teams))
    return 0 if all(t["found"] for t in teams) else 1


if __name__ == "__main__":
    sys.exit(main())
