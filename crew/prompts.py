"""Crew prompt versions (``v`` + 10 hex digits of the text's sha256). The owner's applied change lives in
``$HARNESS_RSI_DATA/prompts/<agent>.md`` (sealed: ``judge.seal``), wins over ``crew/agents/prompts/``, and is logged to
``prompt_versions.jsonl``. Exam files are sealed too: ``bank`` decrypts them in this process.
Hidden exams never reach a prompt: ``leaks`` finds a hidden exam's id, task or check, and ``apply`` refuses it."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from judge import seal  # noqa: E402

PROMPTS = ROOT / "crew" / "agents" / "prompts"


def version(text: str) -> str:
    return "v" + hashlib.sha256(text.rstrip("\n").encode("utf-8")).hexdigest()[:10]


def effective(data: Path | None) -> dict[str, str]:
    """Agent -> prompt text: the owner's applied change in ``data`` when there is one, else the repo's."""
    pick = lambda md: o if data and (o := Path(data) / "prompts" / md.name).is_file() else md  # noqa: E731
    return {md.stem: seal.read_text(pick(md)).rstrip("\n") for md in sorted(PROMPTS.glob("*.md"))}


def versions(texts: dict[str, str]) -> dict[str, str]:
    return {name: version(text) for name, text in sorted(texts.items())}


def bank(data: Path) -> list[dict]:
    """Every exam row under ``data/exams`` (any visibility) and under each saved round."""
    rows = []
    for path in sorted(Path(data).glob("exams/**/*.json")) + sorted(Path(data).glob("rounds/*/exams/**/*.json")):
        try:
            rows.append(seal.read_json(path))
        except (OSError, ValueError, seal.SealError):
            continue
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)]


def leaks(text: str, exams: list[dict]) -> list[str]:
    """The hidden exams whose id, task sentence or check (any case or spacing) appears in ``text``."""
    norm = lambda t: re.sub(r"\s+", " ", t).strip().lower()  # noqa: E731
    flat, words = norm(text), set(re.findall(r"exam_[a-z0-9_]+", text))
    said = lambda x: [norm(t) for t in (x.get("task", ""), json.dumps(x.get("check", ""))) if len(norm(t)) >= 24]  # noqa: E731
    return sorted({x["id"] for x in exams if x.get("visibility", "hidden") == "hidden" and (x["id"] in words or any(t in flat for t in said(x)))})


def apply(data: Path, agent: str, text: str, change_id: str) -> dict:
    """Make ``text`` the agent's prompt in ``data``; ValueError for an unknown agent or a text naming an exam."""
    old = effective(data)
    if agent not in old:
        raise ValueError(f"no such agent: {agent}")
    if found := leaks(text, bank(data)):
        raise ValueError(f"prompt names {len(found)} exam(s); exams never go into prompts")
    seal.write_text(Path(data) / "prompts" / f"{agent}.md", text.rstrip("\n") + "\n")
    row = {"agent": agent, "version": version(text), "from": version(old[agent]), "change_id": change_id,
           "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    with (Path(data) / "prompt_versions.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row
