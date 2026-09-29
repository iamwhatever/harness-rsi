"""Guards for the R0 baseline: screenshots are present and metrics stay aggregate-only."""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "baseline" / "screenshots"
METRICS = ROOT / "baseline" / "metrics.json"

PAGES = {"chat", "sidebar-folders", "artifacts", "apps", "slack-radar", "issue-radar", "settings"}
METRIC_KEYS = {
    "turn_count", "owner_typed_turn_count", "turn_success_rate", "error_rate", "user_stop_rate",
    "first_token_latency_ms_p50", "first_token_latency_ms_p90", "total_latency_ms_p50",
    "total_latency_ms_p90", "tokens_per_turn_p50", "credits_per_turn_p50",
    "user_correction_rate", "steer_rate",
}
# Anything that smells like transcript content, an identity or a machine path.
LEAKY = re.compile(r"\n|^/|^~|/home/|/Users/|chat-\d|dashboard:|@|https?://|[A-Za-z0-9+/=-]{32,}", re.IGNORECASE)


def _strings(node, trail=()):
    if isinstance(node, str):
        yield trail, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield trail + (key,), key
            yield from _strings(value, trail + (key,))
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item, trail)


def test_manifest_lists_every_page_and_each_file_exists():
    manifest = json.loads((SHOTS / "manifest.json").read_text())
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["kirocrew_commit"])
    assert manifest["viewport"]["width"] > 0 and manifest["viewport"]["height"] > 0
    assert {shot["page"] for shot in manifest["shots"]} == PAGES
    for shot in manifest["shots"]:
        png = SHOTS / shot["file"]
        assert png.is_file(), shot["file"]
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", shot["file"]


def test_metrics_has_every_key_as_number_or_null_with_a_reason():
    data = json.loads(METRICS.read_text())
    metrics = data["metrics"]
    assert set(metrics) == METRIC_KEYS
    for key, value in metrics.items():
        assert value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)), key
        if value is None:
            assert data["null_reasons"].get(key), f"{key} is null without a reason"
    assert data["window"]["days"] > 0 and data["window"]["start"] < data["window"]["end"]
    assert data["source"]["store"]


def test_metrics_json_holds_no_transcript_like_strings():
    data = json.loads(METRICS.read_text())
    for trail, text in _strings(data):
        assert len(text) <= 160, trail
        assert not LEAKY.search(text), (trail, text)


def test_metrics_script_counts_turns_errors_and_stops(tmp_path):
    spec = importlib.util.spec_from_file_location("metrics", ROOT / "baseline" / "metrics.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ts = "2026-09-29T10:00:00+00:00"
    stats = {"turn_stats": {"elapsed_ms": 1000, "credits": 2.0}}
    stop = json.dumps({"kind": "stop_event", "outcome": "soft"})
    rows = [
        {"_type": "session_meta", "origin": "user", "memory_mode": "persistent"},
        {"role": "user", "ts": ts, "content": "a"}, {"role": "assistant", "meta": stats},
        {"role": "error", "meta": {"kind": "transient_retry"}, "content": "⟳ retrying"},
        {"role": "nudge", "ts": ts}, {"role": "error", "content": "failed"},
        {"role": "user", "ts": ts, "content": "b"}, {"role": "system", "content": stop},
    ]
    (tmp_path / "sessions").mkdir()
    lines = "\n".join(json.dumps(r) for r in rows)
    (tmp_path / "sessions" / "dashboard_x.jsonl").write_text(lines)
    incognito = lines.replace('"persistent"', '"incognito"')
    (tmp_path / "sessions" / "dashboard_y.jsonl").write_text(incognito)
    out = mod.compute(tmp_path, 14, datetime(2026, 9, 30, tzinfo=timezone.utc))["metrics"]
    assert out["turn_count"] == 3 and out["owner_typed_turn_count"] == 2
    assert out["turn_success_rate"] == round(1 / 3, 4)
    assert out["error_rate"] == round(1 / 3, 4) and out["user_stop_rate"] == round(1 / 3, 4)
    assert out["total_latency_ms_p50"] == 1000
