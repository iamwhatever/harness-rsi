"""Turn the owner's own dashboard sessions into signal rows (schemas/signal.schema.json).

Deterministic, no model and no API call: the pain detectors of ``exams/miner/mine.py``
(user stop, mid-turn steer, retry of the same prompt, turn error, owner correction) run
over the owner-opened persistent dashboard sessions in the window; incognito, temporary
and app-opened sessions are skipped by the miner's own scope test.

Each pain moment is checked for novelty against the GitHub adapter's cache
(``$HARNESS_RSI_DATA/cache/github_issues/*.json``): a moment whose prompt shares enough
content words with a cached issue is ``known``, else ``new``. Moments are clustered by
(pain, novelty) into one row each. Only counts and fixed wording leave this module: no
prompt text, session title, session key or path is ever written out. A ``NEW`` pain line
marks a pain no GitHub issue covers, so reviewers see it flagged.

Usage: ``python3 -m adapters.sessions [--home DIR] [--days 14]`` prints counts only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "crew"))
import enrich  # noqa: E402

_spec = importlib.util.spec_from_file_location("exam_miner", ROOT / "exams" / "miner" / "mine.py")
miner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(miner)

SOURCE = "session:owner"
DETECTOR = "https://github.com/iamwhatever/harness-rsi/blob/main/exams/miner/mine.py"
PROMPT_HEAD = 600  # the ask sits at the start of a prompt; the long tail only adds noise words
MIN_SHARED, MIN_SHARE = 3, 0.5  # shared content words, and their share of the issue's words
MAX_LINKS = 5
DESC = {
    "stop": "the owner stopped the agent mid-turn",
    "steer": "the owner had to steer the agent mid-turn",
    "retry": "the owner re-sent the same prompt",
    "error": "a turn ended in an error",
    "correction": "the owner corrected the agent's answer",
}
TAG = {"new": "NEW (no GitHub issue)", "known": "KNOWN (GitHub issue exists)", "unchecked": "UNCHECKED (no GitHub cache)"}


def default_cache_dir() -> Path:
    data = os.environ.get("HARNESS_RSI_DATA") or Path.home() / ".kiro/crew/harness-rsi-data"
    return Path(data).expanduser() / "cache" / "github_issues"


def load_issues(cache_dir: Path) -> list[tuple[str, set[str]]] | None:
    """(issue url, content words) from every cached repo; None when there is no cache."""
    files = sorted(Path(cache_dir).glob("*.json")) if cache_dir and Path(cache_dir).is_dir() else []
    if not files:
        return None
    out = []
    for f in files:
        try:
            recs = json.loads(f.read_text(encoding="utf-8")).get("issues", {})
        except (OSError, ValueError):
            continue
        out += [(r["html_url"], enrich.keys(r["pain"])) for r in recs.values() if r.get("html_url")]
    return out


def match(prompt: str, issues: list[tuple[str, set[str]]]) -> list[str]:
    """Urls of issues whose words the prompt's head mostly covers."""
    mine = enrich.keys(str(prompt or "")[:PROMPT_HEAD])
    hits = []
    for url, words in issues:
        shared = len(mine & words)
        if shared >= MIN_SHARED and shared >= MIN_SHARE * len(words):
            hits.append(url)
    return hits


def moments(home: Path, days: int, now: datetime):
    """(session file stem, pain, prompt of the turn that went wrong) per in-window pain moment."""
    start = now - timedelta(days=days) if days > 0 else None
    for path in sorted((Path(home) / "sessions").glob("dashboard_*.jsonl")):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        rows = [json.loads(ln) for ln in lines if miner._is_json(ln)]
        if not rows or not miner.in_scope(rows[0]):
            continue
        turns = miner.session_turns(rows[1:])
        for pain, wrong, seen in miner.pain_moments(turns):
            ts = turns[seen]["ts"]
            if start and (not ts or datetime.fromisoformat(ts) < start):
                continue
            yield path.stem, pain, turns[wrong]["text"]


def collect(home: Path | None = None, days: int = 14, now: datetime | None = None,
            cache_dir: Path | None = None) -> list[dict]:
    home = Path(home or Path.home() / ".kiro" / "crew")
    now = now or datetime.now(timezone.utc)
    issues = load_issues(cache_dir if cache_dir is not None else default_cache_dir())
    groups: dict[tuple[str, str], dict] = {}
    for sid, pain, prompt in moments(home, days, now):
        hits = match(prompt, issues) if issues is not None else []
        novelty = "unchecked" if issues is None else ("known" if hits else "new")
        g = groups.setdefault((pain, novelty), {"count": 0, "sessions": set(), "links": Counter()})
        g["count"] += 1
        g["sessions"].add(sid)
        g["links"].update(hits)
    day = now.strftime("%Y%m%d")
    out = []
    order = {p: n for n, p in enumerate(miner.PAINS)}
    for n, ((pain, novelty), g) in enumerate(sorted(groups.items(), key=lambda kv: (kv[0][1] != "new", order[kv[0][0]]))):
        metric, op, value = miner.FALLBACK[pain]
        links = [u for u, _ in g["links"].most_common(MAX_LINKS)] or [DETECTOR]
        out.append({
            "id": f"sig_{day}_{n + 1:04d}",
            "source": SOURCE,
            "links": links,
            "pain": f"{TAG[novelty]}: {DESC[pain]} ({g['count']} times in {len(g['sessions'])} sessions)",
            "mentions": {"count": g["count"], "people": 1, "window_days": max(days, 1)},
            "layer": "real",
            "testable": {"ok": True, "task": f"Replaying the pained turns 3 times, {metric} stays {op} {value}."},
            "dedup_of": None,
        })
    return out


def counts(rows: list[dict]) -> dict:
    tag = {v: k for k, v in TAG.items()}
    by = Counter(tag[r["pain"].split(":", 1)[0]] for r in rows)
    moments_by = Counter()
    for r in rows:
        moments_by[tag[r["pain"].split(":", 1)[0]]] += r["mentions"]["count"]
    return {"signals": len(rows), "signals_by_novelty": dict(by), "moments_by_novelty": dict(moments_by)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", type=Path, default=Path.home() / ".kiro" / "crew")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--cache-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    print(json.dumps(counts(collect(args.home, args.days, cache_dir=args.cache_dir))))
    return 0
