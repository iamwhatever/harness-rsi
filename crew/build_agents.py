#!/usr/bin/env python3
"""Copy each prompt from crew/agents/prompts/<name>.md into crew/agents/<name>.json.

The .md files are the source; the JSON "prompt" is generated. Run
``python3 crew/build_agents.py``; ``--check`` exits 1 instead of writing when a
JSON is out of date or a prompt has no agent JSON.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AGENTS = Path(__file__).resolve().parent / "agents"
PROMPTS = AGENTS / "prompts"


def render(agent: dict) -> str:
    return json.dumps(agent, indent=2, ensure_ascii=False) + "\n"


def stale(write: bool) -> list[str]:
    bad = []
    for md in sorted(PROMPTS.glob("*.md")):
        target = AGENTS / f"{md.stem}.json"
        if not target.is_file():
            bad.append(f"no agent JSON for {md.name}")
            continue
        agent = json.loads(target.read_text(encoding="utf-8"))
        agent["prompt"] = md.read_text(encoding="utf-8").rstrip("\n")
        text = render(agent)
        if target.read_text(encoding="utf-8") != text:
            bad.append(f"out of date: {target.name}")
            if write:
                target.write_text(text, encoding="utf-8")
    return bad


def main(argv: list[str]) -> int:
    check = "--check" in argv
    bad = stale(write=not check)
    for line in bad:
        print(line, file=sys.stderr)
    return 1 if check and bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
