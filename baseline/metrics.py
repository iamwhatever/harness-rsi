"""Compute the R0 hard-metric baseline from local KiroCrew dashboard transcripts.

Reads KIROCREW_HOME/sessions/dashboard_*.jsonl (default ~/.kiro/crew) and writes
AGGREGATE numbers only: no transcript text, prompt, title, session id or path
ever reaches the output.

Scope: persistent dashboard sessions the owner opened (origin "user" or unset);
incognito/temporary sessions and app-opened sessions are skipped.

A turn starts at a user/inject/nudge row (a mid-turn steer does not start one)
and runs to the next start. It succeeds when it has a reply with turn_stats, no
final error row (transient "retrying" rows do not count) and no user stop.
First-token latency and tokens come from baseline/collect_latency.py.

Usage: python3 baseline/metrics.py [--home DIR] [--days 14] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect_latency import latency_metrics, pct, ttft_of  # noqa: E402

STARTERS = ("user", "inject", "nudge")
OWNER_ORIGINS = ("user", None)


def is_stop(row: dict) -> bool:
    content = row.get("content")
    try:
        return row.get("role") == "system" and json.loads(content).get("kind") == "stop_event"
    except (TypeError, ValueError, AttributeError):
        return False


def is_final_error(row: dict) -> bool:
    if row.get("role") != "error":
        return False
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    content = row.get("content") if isinstance(row.get("content"), str) else ""
    return meta.get("kind") != "transient_retry" and not content.startswith("⟳")


def _is_json(line: str) -> bool:
    try:
        return isinstance(json.loads(line), dict)
    except ValueError:
        return False


def session_turns(path: Path) -> list[dict] | None:
    """Per-turn facts for one session, or None when the session is out of scope."""
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if _is_json(ln)]
    head = rows[0] if rows and isinstance(rows[0], dict) else {}
    if not head.get("_type") or head.get("memory_mode", "persistent") != "persistent" or head.get("origin") not in OWNER_ORIGINS:
        return None
    turns: list[dict] = []
    for row in rows[1:]:
        role = row.get("role")
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        if role in STARTERS and not meta.get("steer"):
            turns.append({"ts": row.get("ts"), "typed": role == "user", "elapsed": None,
                          "credits": 0.0, "ttft": None, "reply": False, "error": False, "stop": False, "steer": False})
            continue
        if not turns:
            continue
        turn = turns[-1]
        stats = meta.get("turn_stats")
        if role == "assistant" and isinstance(stats, dict):
            turn["reply"] = True
            if isinstance(stats.get("elapsed_ms"), (int, float)):
                turn["elapsed"] = max(turn["elapsed"] or 0, stats["elapsed_ms"])
            if isinstance(stats.get("credits"), (int, float)):
                turn["credits"] += stats["credits"]
            if turn["ttft"] is None:
                turn["ttft"] = ttft_of(stats)
        turn["error"] |= is_final_error(row)
        turn["stop"] |= is_stop(row)
        turn["steer"] |= role == "user" and bool(meta.get("steer"))
    return turns


def compute(home: Path, days: int, now: datetime) -> dict:
    start = now - timedelta(days=days)
    sessions, turns = 0, []
    for path in sorted((home / "sessions").glob("dashboard_*.jsonl")):
        found = session_turns(path)
        if found is None:
            continue
        in_window = [t for t in found if t["ts"] and start <= datetime.fromisoformat(t["ts"]) <= now]
        sessions += bool(in_window)
        turns += in_window
    n = len(turns)
    typed = [t for t in turns if t["typed"]]
    elapsed = [t["elapsed"] for t in turns if t["elapsed"] is not None]
    credits = [t["credits"] for t in turns if t["reply"]]

    def rate(k: str, pool: list[dict]) -> float | None:
        return round(sum(t[k] for t in pool) / len(pool), 4) if pool else None

    ok = [t for t in turns if t["reply"] and not t["error"] and not t["stop"]]
    latency, latency_reasons, coverage = latency_metrics(turns)
    return {
        "schema": "kirocrew-rsi/baseline-metrics/1",
        "generated_at": now.isoformat(timespec="seconds"),
        "window": {"start": start.isoformat(timespec="seconds"), "end": now.isoformat(timespec="seconds"), "days": days},
        "source": {
            "store": "KiroCrew dashboard session transcripts (dashboard_*.jsonl)",
            "scope": "owner-opened persistent dashboard sessions; incognito, temporary and app-opened excluded",
            "percentile": "nearest-rank",
            "sessions": sessions,
            **coverage,
        },
        "metrics": {
            "turn_count": n,
            "owner_typed_turn_count": len(typed),
            "turn_success_rate": round(len(ok) / n, 4) if n else None,
            "error_rate": rate("error", turns),
            "user_stop_rate": rate("stop", turns),
            "first_token_latency_ms_p50": latency["first_token_latency_ms_p50"],
            "first_token_latency_ms_p90": latency["first_token_latency_ms_p90"],
            "total_latency_ms_p50": pct(elapsed, 0.5),
            "total_latency_ms_p90": pct(elapsed, 0.9),
            "tokens_per_turn_p50": latency["tokens_per_turn_p50"],
            "credits_per_turn_p50": pct(credits, 0.5),
            "user_correction_rate": None,
            "steer_rate": rate("steer", typed),
        },
        "null_reasons": {
            **latency_reasons,
            "user_correction_rate": "no correction marker in transcripts; steer_rate (mid-turn owner messages per typed turn) is the nearest proxy",
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", type=Path, default=Path.home() / ".kiro" / "crew")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--out", type=Path, default=Path(__file__).with_name("metrics.json"))
    args = ap.parse_args()
    result = compute(args.home, args.days, datetime.now(timezone.utc))
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["metrics"], indent=2))


if __name__ == "__main__":
    main()
