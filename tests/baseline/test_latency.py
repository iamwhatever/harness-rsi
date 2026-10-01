"""First-token latency, tokens and the judge metrics file built from baseline output."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)
TS = "2026-09-29T10:00:00+00:00"
SECRET = "my secret prompt about /home/someone/project chat-123"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "baseline" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


metrics = _load("metrics")
judge_metrics = _load("judge_metrics")


def _home(tmp_path, ttfts, extra=()):
    rows = [{"_type": "session_meta", "origin": "user", "memory_mode": "persistent", "title": SECRET}]
    for i, ttft in enumerate(ttfts):
        stats = {"elapsed_ms": 5000, "credits": 1.0}
        if ttft is not None:
            stats["ttft_ms"] = ttft
        rows += [{"role": "user", "ts": TS, "content": SECRET, "meta": {"mid": f"m-{i}"}},
                 {"role": "assistant", "content": SECRET, "meta": {"turn_stats": stats}}]
    rows += list(extra)
    (tmp_path / "sessions").mkdir()
    lines = "\n".join(json.dumps(r) for r in rows)
    (tmp_path / "sessions" / "dashboard_chat-123.jsonl").write_text(lines, encoding="utf-8")
    return tmp_path


def test_percentiles_are_nearest_rank_over_turns_with_the_field(tmp_path):
    home = _home(tmp_path, [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, None])
    doc = metrics.compute(home, 14, NOW)
    assert doc["metrics"]["first_token_latency_ms_p50"] == 500
    assert doc["metrics"]["first_token_latency_ms_p90"] == 900
    assert doc["source"]["ttft_turns"] == 10
    assert "first_token_latency_ms_p50" not in doc["null_reasons"]


def test_bad_values_are_ignored(tmp_path):
    home = _home(tmp_path, [0, -5, True, "300", 250])
    out = metrics.compute(home, 14, NOW)["metrics"]
    assert out["first_token_latency_ms_p50"] == out["first_token_latency_ms_p90"] == 250


def test_missing_source_gives_null_with_reason(tmp_path):
    doc = metrics.compute(_home(tmp_path, [None, None]), 14, NOW)
    for key in ("first_token_latency_ms_p50", "first_token_latency_ms_p90", "tokens_per_turn_p50"):
        assert doc["metrics"][key] is None
        assert doc["null_reasons"][key]
    assert doc["source"]["ttft_turns"] == 0
    assert "credits" in doc["null_reasons"]["tokens_per_turn_p50"]


def test_output_holds_no_prompt_text_paths_or_ids(tmp_path):
    text = json.dumps(metrics.compute(_home(tmp_path, [120, 340]), 14, NOW))
    for needle in ("secret", "/home/", "chat-123", "m-0", "dashboard_chat"):
        assert needle not in text


def test_judge_file_pairs_latency_and_credits_with_success(tmp_path):
    cur_ttft = 400.0
    def doc(path, ttft, credits, success):
        m = {"first_token_latency_ms_p50": ttft, "total_latency_ms_p50": 5000, "credits_per_turn_p50": credits,
             "turn_success_rate": success, "tokens_per_turn_p50": None}
        path.write_text(json.dumps({"metrics": m}), encoding="utf-8")
        return str(path)

    before = [doc(tmp_path / f"b{i}.json", 500.0, 8.0, 0.9) for i in range(2)]
    after = [doc(tmp_path / "a.json", cur_ttft, 8.0, 0.9)]
    out = tmp_path / "metrics.json"
    assert judge_metrics.main(["--before", *before, "--after", *after, "--out", str(out)]) == 0
    written = json.loads(out.read_text())
    assert written["pairs"] == [list(p) for p in judge_metrics.PAIRS]
    assert ("credits_per_turn_p50", "turn_success_rate") in judge_metrics.PAIRS
    assert written["current"]["first_token_latency_ms_p50"] == [cur_ttft]
    assert "tokens_per_turn_p50" not in written["baseline"]

    from judge.core import paired

    rows = {r["a"]: r for r in paired(written)}
    assert rows["first_token_latency_ms_p50"]["ok"] is True
    assert rows["credits_per_turn_p50"]["ok"] is True


def test_judge_pair_fails_when_latency_drops_but_success_falls(tmp_path):
    from judge.core import paired

    base = {"first_token_latency_ms_p50": [500.0, 500.0], "turn_success_rate": [0.9, 0.9]}
    cur = {"first_token_latency_ms_p50": [300.0], "turn_success_rate": [0.7]}
    rows = paired({"baseline": base, "current": cur, "pairs": judge_metrics.PAIRS})
    assert rows == [{"a": "first_token_latency_ms_p50", "b": "turn_success_rate",
                     "delta_a": -200.0, "delta_b": -0.2, "ok": False}]
