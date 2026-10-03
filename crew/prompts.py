"""Crew prompt versions. A prompt's source is ``crew/agents/prompts/<agent>.md``; a change the owner applied
lives in ``$HARNESS_RSI_DATA/prompts/<agent>.md`` and wins, and each apply appends to ``prompt_versions.jsonl``.
A version id is ``v`` + the first 10 hex digits of the text's sha256. Hidden exams never reach a prompt:
``leaks`` finds a bank exam's id or task in a text, and ``apply`` refuses such a text.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent / "agents" / "prompts"


def version(text: str) -> str:
    return "v" + hashlib.sha256(text.rstrip("\n").encode("utf-8")).hexdigest()[:10]


def effective(data: Path | None) -> dict[str, str]:
    """Agent -> prompt text: the owner's applied change in ``data`` when there is one, else the repo's."""
    pick = lambda md: o if data and (o := Path(data) / "prompts" / md.name).is_file() else md  # noqa: E731
    return {md.stem: pick(md).read_text(encoding="utf-8").rstrip("\n") for md in sorted(PROMPTS.glob("*.md"))}


def versions(texts: dict[str, str]) -> dict[str, str]:
    return {name: version(text) for name, text in sorted(texts.items())}


def bank(data: Path) -> list[dict]:
    """Every exam row under ``data/exams`` (any visibility) and under each saved round."""
    rows = []
    for path in sorted(Path(data).glob("exams/**/*.json")) + sorted(Path(data).glob("rounds/*/exams/**/*.json")):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)]


def leaks(text: str, exams: list[dict]) -> list[str]:
    """The exams whose id, or whose task sentence (any case or spacing), appears in ``text``."""
    norm = lambda t: re.sub(r"\s+", " ", t).strip().lower()  # noqa: E731
    flat, words = norm(text), set(re.findall(r"exam_[a-z0-9_]+", text))
    return sorted({x["id"] for x in exams if x["id"] in words or (len(norm(x.get("task", ""))) >= 24 and norm(x["task"]) in flat)})


def apply(data: Path, agent: str, text: str, change_id: str) -> dict:
    """Make ``text`` the agent's prompt in ``data``; ValueError for an unknown agent or a text naming an exam."""
    old = effective(data)
    if agent not in old:
        raise ValueError(f"no such agent: {agent}")
    if found := leaks(text, bank(data)):
        raise ValueError(f"prompt names {len(found)} exam(s); exams never go into prompts")
    (Path(data) / "prompts").mkdir(parents=True, exist_ok=True)
    (Path(data) / "prompts" / f"{agent}.md").write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    row = {"agent": agent, "version": version(text), "from": version(old[agent]), "change_id": change_id,
           "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    with (Path(data) / "prompt_versions.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row
