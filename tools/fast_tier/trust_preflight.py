"""For each agent spec, list the tools a flow calls that its ``allowedTools`` does not cover.

    python3 tools/fast_tier/trust_preflight.py SPEC [SPEC ...] [--flow conductor|lane|worker] [--tool NAME ...]

SPEC is a path to an agent JSON or an agent name looked up as ``~/.kiro/agents/<name>.json``.
A tool missing from ``allowedTools`` asks for approval on every call unless the session is
trusted, so this is the pre-round check behind docs/design/crewmate-team.md §8. An entry covers a
tool when it is equal to it, is ``*``, is the tool's ``@server``, or matches it as a glob.
``--tool`` adds names to the flow's list. Exit 1 when any spec misses a tool.
"""

import argparse
import fnmatch
import json
import sys
from pathlib import Path

SHELL = "execute_bash"
FLOWS = {
    "conductor": (SHELL, "@kirocrew-dashboard/session_create", "@kirocrew-dashboard/session_send",
                  "@kirocrew-dashboard/session_close", "@kirocrew-dashboard/session_status",
                  "@kirocrew-work/work_ledger_read", "@kirocrew-work/work_ledger_record",
                  "@kirocrew-core/monitor_start"),
    "worker": (SHELL, "fs_write", "@kirocrew-work/work_brief", "@kirocrew-work/work_report"),
}
FLOWS["lane"] = FLOWS["conductor"] + ("@kirocrew-work/work_brief", "@kirocrew-work/work_report")
SHELL_ALIASES = {SHELL, "shell", "execute_cmd"}


def covers(entry, tool):
    if entry in ("*", tool):
        return True
    if tool == SHELL and entry in SHELL_ALIASES:
        return True
    if entry.startswith("@") and "/" not in entry and tool.startswith(entry + "/"):
        return True
    return any(ch in entry for ch in "*?[") and fnmatch.fnmatchcase(tool, entry)


def load_spec(ref, agents_dir):
    path = Path(ref).expanduser()
    if not path.suffix == ".json":
        path = agents_dir / f"{ref}.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def check(spec, tools):
    allowed = [e for e in spec.get("allowedTools") or [] if isinstance(e, str)]
    available = [e for e in spec.get("tools") or [] if isinstance(e, str)]
    missing = [t for t in tools if not any(covers(e, t) for e in allowed)]
    absent = [t for t in missing if available and not any(covers(e, t) for e in available)]
    return missing, absent


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("specs", nargs="+", help="agent spec path or name")
    ap.add_argument("--flow", choices=sorted(FLOWS), default="conductor")
    ap.add_argument("--tool", action="append", default=[], help="extra tool the flow calls")
    ap.add_argument("--agents-dir", type=Path, default=Path.home() / ".kiro" / "agents")
    args = ap.parse_args(argv)
    tools = list(dict.fromkeys(FLOWS[args.flow] + tuple(args.tool)))
    any_missing = False
    print(f"Flow '{args.flow}': {len(tools)} tools\n")
    print("| spec | missing from allowedTools | not in tools at all |\n|---|---|---|")
    for ref in args.specs:
        try:
            path, spec = load_spec(ref, args.agents_dir)
        except (OSError, ValueError) as exc:
            print(f"| {ref} | unreadable: {exc.__class__.__name__} | - |")
            any_missing = True
            continue
        missing, absent = check(spec, tools)
        any_missing |= bool(missing)
        print(f"| {spec.get('name') or path.stem} | {', '.join(missing) or 'none'} | {', '.join(absent) or '-'} |")
    return 1 if any_missing else 0


if __name__ == "__main__":
    sys.exit(main())
