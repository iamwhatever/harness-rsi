"""First-token latency and token aggregates for baseline/metrics.py.

First-token latency comes from `turn_stats.ttft_ms` on a turn's assistant row:
user message to first visible model output, the same value KiroCrew emits as
the `kirocrew.chat.first_token.duration` metric. Turns recorded before KiroCrew
stored that field carry none and are left out; when no turn in the window has
it, the percentiles are null with a reason.

Tokens stay null: the backend bills credits, and neither the dashboard
transcripts nor the usage rows hold token counts. credits_per_turn_p50 is the
cost metric instead.

Output is aggregates only: numbers and fixed reason strings, never row content.
"""

from __future__ import annotations

import math

NO_TTFT = "no turn in the window carries turn_stats.ttft_ms; KiroCrew stores it only from the build that added the field"
NO_TOKENS = "backend bills credits; no token counts are recorded; credits_per_turn_p50 is the cost metric"


def pct(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def ttft_of(stats: object) -> int | None:
    """The turn's first-token latency in ms, or None when absent or not a positive number."""
    value = stats.get("ttft_ms") if isinstance(stats, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def latency_metrics(turns: list[dict]) -> tuple[dict, dict, dict]:
    """(metrics, null_reasons, coverage) for the latency and token keys of a turn list."""
    ttft = [t["ttft"] for t in turns if t.get("ttft") is not None]
    metrics = {
        "first_token_latency_ms_p50": pct(ttft, 0.5),
        "first_token_latency_ms_p90": pct(ttft, 0.9),
        "tokens_per_turn_p50": None,
    }
    reasons = {"tokens_per_turn_p50": NO_TOKENS}
    if not ttft:
        reasons["first_token_latency_ms_p50"] = reasons["first_token_latency_ms_p90"] = NO_TTFT
    return metrics, reasons, {"ttft_turns": len(ttft)}
