"""Walk a lead's session tree in the crew log and print each session's trust posture, plus the
owner's manual restart-test checklist (docs/design/crewmate-team.md §3.2, §11 fast tier ①).

    python3 tools/fast_tier/trust_restart_check.py LEAD_SLOT [--since WHEN] [--root DIR]
    python3 tools/fast_tier/trust_restart_check.py --checklist

Trust itself lives only in gateway memory and is not written to the crew log. What the log
does show is its effect: a trusted session's tool calls raise no ``approval/requested``. So the
posture is read from activity after ``--since`` (the gateway restart, ISO-8601 or epoch ms):

    asked       an approval/requested at or after --since  -> interactive (not trusted)
    declined    as asked, and the host auto-declined one   -> stalled on the 600 s timeout
    no prompt   tool calls at or after --since, none asked  -> consistent with trusted
    idle        no tool call at or after --since            -> not observable yet
    no log      no crew log for the slot                    -> not observable

Read-only. Exit 0 always; this is a reader, the verdict is the owner's.
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crewlog  # noqa: E402

CHECKLIST = """\
Restart test (owner, by hand). Record each row in the table below.

 1. Open the lead crewmate's thread. Turn trust ON for it.
 2. From the lead, open a lane conductor (session_create).
 3. From the lane, open a worker (session_create).
 4. Have each of the three run one shell command. Expect: no approval prompt anywhere.
 5. Note the time T, then restart the gateway.
 6. Have each of the three run one shell command again.
    Expect: all three ask for approval (trust is in memory only).
    Run: trust_restart_check.py LEAD --since T   -> lead, lane, worker all 'asked'.
 7. Turn trust ON again for the lead only. Note the time T2.
 8. From the lead, open a NEW child session. Have it and the OLD lane run one shell command.
    Expect: new child 'no prompt' (inherits trust at creation), old lane still 'asked'.
    Run: trust_restart_check.py LEAD --since T2
 9. Stop line: if the new child still asks after step 7, write the one-place fix PR first.

| layer  | before restart | after restart | after re-trust |
|--------|----------------|---------------|----------------|
| lead   |                |               |                |
| lane   |                |               |                |
| worker |                |               |  (old: asked)  |
| new    |       -        |       -       |                |
"""


def parse_when(text):
    if text is None:
        return 0
    if text.isdigit():
        return int(text)
    when = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return int(when.timestamp() * 1000)


def posture(unit, since_ms):
    if unit is None:
        return "no log", 0, 0
    after = [e for e in unit.entries if (e.get("time") or 0) >= since_ms]
    asked = [e for e in after if e["type"] == "approval/requested"]
    declined = [e for e in after if e["type"] == "approval/decided" and e["data"].get("by") == "host"]
    calls = [e for e in after if e["type"] == "tool/called"]
    if declined:
        label = "declined"
    elif asked:
        label = "asked"
    elif calls:
        label = "no prompt"
    else:
        label = "idle"
    return label, len(asked), len(calls)


def report(units, lead, since_ms):
    rows = []
    for slot, depth in crewlog.subtree(units, lead):
        unit = units.get(slot)
        label, asked, calls = posture(unit, since_ms)
        rows.append({"slot": slot, "depth": depth, "agent": crewlog.agent_of(unit), "posture": label,
                     "asked": asked, "calls": calls})
    return rows


def render(rows, lead, since_ms):
    since = dt.datetime.fromtimestamp(since_ms / 1000, dt.timezone.utc).isoformat() if since_ms else "start of log"
    out = [f"Trust posture under {lead}, from {since}:", "",
           "| session | depth | agent | posture | approvals asked | tool calls |", "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {'  ' * r['depth']}{r['slot']} | {r['depth']} | {r['agent'] or '-'} | {r['posture']} | "
                   f"{r['asked']} | {r['calls']} |")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("lead", nargs="?", help="lead session slot key")
    ap.add_argument("--since", help="only read entries at or after this time (ISO-8601 or epoch ms)")
    ap.add_argument("--root", type=Path, help="crew-log sessions dir (default: $KIROCREW_HOME/crew-log/sessions)")
    ap.add_argument("--checklist", action="store_true", help="print only the manual checklist")
    args = ap.parse_args(argv)
    if args.lead and not args.checklist:
        units = crewlog.load_units(args.root)
        if args.lead not in units:
            print(f"(no crew log for {args.lead} under {args.root or crewlog.default_root()})", file=sys.stderr)
        since_ms = parse_when(args.since)
        print(render(report(units, args.lead, since_ms), args.lead, since_ms))
        print()
    print(CHECKLIST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
