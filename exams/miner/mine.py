"""Mine layer-2 candidate exams from the owner's own dashboard session transcripts.

Same source and scope as baseline/metrics.py: KIROCREW_HOME/sessions/dashboard_*.jsonl,
persistent sessions the owner opened (origin "user" or unset); incognito, temporary and
app-opened sessions are skipped.

Pain moments: a user stop, a mid-turn steer, a retry of the same prompt, a turn error,
and the owner correcting the agent. Each becomes one hidden exam row, valid against
schemas/exam.schema.json, written to $HARNESS_RSI_DATA/exams/seed/ -- a directory that
must sit outside the git checkout, because mined rows carry transcript text and the
repository is public. Only a count summary is printed.

A row is testable when the pained turn or the turn after it ran a test or build command:
its check replays that command (exit_code 0). Otherwise the check is a per-pain metric
threshold over replays of the task.

Usage: HARNESS_RSI_DATA=DIR python3 exams/miner/mine.py [--home DIR] [--days N] [--round N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from baseline.metrics import OWNER_ORIGINS, STARTERS, _is_json, is_final_error, is_stop  # noqa: E402

PAINS = ("stop", "steer", "retry", "error", "correction")
FALLBACK = {
    "stop": ("user_stop_rate", "le", 0),
    "steer": ("steer_rate", "le", 0),
    "retry": ("turn_success_rate", "ge", 1),
    "error": ("error_rate", "le", 0),
    "correction": ("user_correction_rate", "le", 0),
}
CORRECTION = re.compile(
    r"^(no\b|nope|wrong|that'?s (not|wrong)|that is (not|wrong)|not what i|you (didn'?t|did not|forgot|missed)"
    r"|why (did|are|do) you|i (said|asked|told you)|don'?t|do not|undo|revert that|actually,"
    r"|不对|不是|错了|你错|别|不要|你没|我说|我让|为什么你|为啥你|重新)",
    re.IGNORECASE,
)
VERIFY = re.compile(
    r"\b(pytest|vitest|jest|tsc|npm (run )?(test|build|lint)|npx (vitest|tsc|jest|eslint)|cargo (test|build)"
    r"|go (test|build)|make (test|check)|brazil-build|node --test|ruff check|black --check|mypy)\b"
)


def in_scope(head: dict) -> bool:
    return bool(head.get("_type")) and head.get("memory_mode", "persistent") == "persistent" and head.get("origin") in OWNER_ORIGINS


def _norm(text: object) -> str:
    return " ".join(str(text or "").split()).lower()


def _verify_cmd(row: dict) -> list[str] | None:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    if row.get("role") != "tool" or meta.get("kind") != "execute" or str(meta.get("done")) != "True":
        return None
    try:
        args = json.loads(meta.get("input") or "{}")
    except ValueError:
        return None
    cmd = args.get("command") if isinstance(args, dict) else None
    if not isinstance(cmd, str) or not VERIFY.search(cmd):
        return None
    cwd = args.get("cwd")
    return ["bash", "-c", f"cd {json.dumps(cwd)} && {cmd}" if isinstance(cwd, str) and cwd else cmd]


def session_turns(rows: list[dict]) -> list[dict]:
    turns: list[dict] = []
    for row in rows:
        role = row.get("role")
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        if role in STARTERS and not meta.get("steer"):
            turns.append({"ts": row.get("ts"), "typed": role == "user", "text": row.get("content"),
                          "stop": False, "steer": False, "error": False, "reply": False, "cmd": None})
            continue
        if not turns:
            continue
        turn = turns[-1]
        turn["stop"] |= is_stop(row)
        turn["steer"] |= role == "user" and bool(meta.get("steer"))
        turn["error"] |= is_final_error(row)
        turn["reply"] |= role == "assistant"
        turn["cmd"] = _verify_cmd(row) or turn["cmd"]
    return turns


def pain_moments(turns: list[dict]) -> list[tuple[str, int, int]]:
    """(pain, index of the turn that went wrong, index of the turn that shows the pain)."""
    found = []
    typed = [i for i, t in enumerate(turns) if t["typed"] and _norm(t["text"])]
    for i, turn in enumerate(turns):
        for pain in ("stop", "steer", "error"):
            if turn[pain]:
                found.append((pain, i, i))
    for prev, cur in zip(typed, typed[1:]):
        if _norm(turns[cur]["text"]) == _norm(turns[prev]["text"]):
            found.append(("retry", prev, cur))
        elif turns[prev]["reply"] and CORRECTION.match(_norm(turns[cur]["text"])):
            found.append(("correction", prev, cur))
    return found


def build_row(pain: str, sid: str, turns: list[dict], wrong: int, seen: int, rnd: int, seq: Counter) -> dict:
    day = (turns[seen]["ts"] or "1970-01-01")[:10].replace("-", "")
    seq[day] += 1
    digest = hashlib.sha256(f"{sid}:{pain}:{wrong}:{seen}".encode()).hexdigest()[:12]
    cmd = next((turns[j]["cmd"] for j in (seen + 1, seen, wrong) if j < len(turns) and turns[j]["cmd"]), None)
    if cmd:
        check = {"kind": "exit_code", "cmd": cmd, "expect": 0, "timeout_s": 1800}
    else:
        metric, op, value = FALLBACK[pain]
        check = {"kind": "metric_threshold", "metric": metric, "stat": "rate", "op": op, "value": value, "runs": 3}
    task = str(turns[wrong]["text"] or "").strip()[:4000]
    if pain == "correction":
        task += "\n\n[owner correction]\n" + str(turns[seen]["text"] or "").strip()[:2000]
    return {"id": f"exam_{pain}_{digest}", "layer": 2, "origin": [f"sig_{day}_{seq[day] % 10000:04d}"],
            "visibility": "hidden", "task": task or pain, "check": check, "created_round": rnd, "used_rounds": []}


def mine(home: Path, days: int, now: datetime, rnd: int = 1) -> list[dict]:
    start = now - timedelta(days=days) if days > 0 else None
    rows_out: list[dict] = []
    seq: Counter = Counter()
    for path in sorted((home / "sessions").glob("dashboard_*.jsonl")):
        rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if _is_json(ln)]
        if not rows or not in_scope(rows[0]):
            continue
        turns = session_turns(rows[1:])
        for pain, wrong, seen in pain_moments(turns):
            ts = turns[seen]["ts"]
            if start and (not ts or datetime.fromisoformat(ts) < start):
                continue
            rows_out.append(build_row(pain, path.stem, turns, wrong, seen, rnd, seq))
    return rows_out


def summary(rows: list[dict]) -> dict:
    by = Counter(r["id"].split("_")[1] for r in rows)
    ok = Counter(r["id"].split("_")[1] for r in rows if r["check"]["kind"] == "exit_code")
    return {"candidates": len(rows), "testable": sum(ok.values()),
            "by_pain": {p: by[p] for p in PAINS}, "testable_by_pain": {p: ok[p] for p in PAINS}}


def seed_dir(data: str | None) -> Path:
    if not data:
        raise SystemExit("HARNESS_RSI_DATA is not set")
    out = (Path(data).expanduser().resolve() / "exams" / "seed")
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("HARNESS_RSI_DATA must be outside the repository")
    return out


def write(rows: list[dict], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for row in rows:
        (out / f"{row['id']}.json").write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", type=Path, default=Path.home() / ".kiro" / "crew")
    ap.add_argument("--days", type=int, default=0, help="window in days; 0 = all history")
    ap.add_argument("--round", type=int, default=1)
    args = ap.parse_args()
    out = seed_dir(os.environ.get("HARNESS_RSI_DATA"))
    rows = mine(args.home, args.days, datetime.now(timezone.utc), args.round)
    write(rows, out)
    print(json.dumps(summary(rows), indent=2))


if __name__ == "__main__":
    main()
