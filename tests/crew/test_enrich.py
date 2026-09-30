"""Signal enrichment on fake rows: Slack pains get tasks, opinions do not, heat adds up across sources."""

import json
import pathlib
import sys

from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "crew"))
import enrich  # noqa: E402
from adapters import slack  # noqa: E402

V = Draft202012Validator(json.loads((ROOT / "schemas/signal.schema.json").read_text(encoding="utf-8")))


def row(n, source, pain, people, count, ok=False):
    return {"id": f"sig_20260930_{n:04d}", "source": source, "links": [f"https://example.com/{n}"], "pain": pain,
            "mentions": {"count": count, "people": people, "window_days": 14}, "layer": "real",
            "testable": {"ok": ok, "task": "A judge can check it" if ok else None}, "dedup_of": None}


SIGNALS = [
    row(1, "github:example/repo", "Background workers leak host memory until the box swaps", 7, 9),
    row(2, "slack:C0AAAA0001", "Idle gateway memory leak grows overnight", 5, 20),
    row(3, "slack:C0AAAA0001", "Wondering whether bold colours look better", 6, 30),
    row(4, "slack:C0AAAA0001", "Scheduled cronjobs keep getting paused", 4, 8),
    row(5, "github:example/repo", "Add a dark theme option to settings", 3, 3),
]


def test_concrete_slack_pain_becomes_testable_with_a_task():
    t = slack.testable("Scheduled cronjobs keep getting paused")
    assert t["ok"] and "cronjobs" in t["task"] and "paus" in t["task"]
    assert slack.testable("Scheduled cronjobs keep getting paused") == t  # deterministic


def test_opinion_question_and_praise_stay_untestable():
    for pain in ("Wondering whether bold colours look better", "Support nesting one crew inside another",
                 "Nice demo shipping release notes", "Anyone else around today"):
        assert slack.testable(pain) == {"ok": False, "task": None}, pain


def test_enrich_fills_slack_tasks_and_leaves_other_sources_alone():
    out, counts = enrich.enrich(SIGNALS)
    assert all(not list(V.iter_errors(r)) for r in out)
    by = {r["id"]: r for r in out}
    assert by["sig_20260930_0004"]["testable"]["ok"] and not by["sig_20260930_0003"]["testable"]["ok"]
    assert by["sig_20260930_0005"]["testable"] == {"ok": False, "task": None}  # not a Slack row
    assert counts["slack_testable_before"] == 0 and counts["slack_testable_after"] == 2
    assert SIGNALS[3]["testable"]["ok"] is False  # input untouched


def test_cross_source_merge_adds_people_and_count_to_the_head():
    out, counts = enrich.enrich(SIGNALS)
    head, dup = out[0], out[1]
    assert dup["dedup_of"] == head["id"] and head["dedup_of"] is None
    assert head["mentions"] == {"count": 29, "people": 12, "window_days": 14}
    assert head["testable"]["ok"]  # the Slack task moved to the head the setter will read
    assert counts["cross_source_merges"] == 1 and counts["top_people"] == 12
    assert all(r["dedup_of"] is None for r in out[2:])


def test_unrelated_rows_do_not_merge_and_the_injected_judge_is_used():
    out, counts = enrich.enrich(SIGNALS, testable=lambda pain: {"ok": False, "task": None})
    assert counts["slack_testable_after"] == 0 and counts["cross_source_merges"] == 1
    assert out[4]["dedup_of"] is None


def test_counts_hold_no_names_ids_or_text():
    _, counts = enrich.enrich(SIGNALS)
    assert all(isinstance(v, int) for v in counts.values())
    text = json.dumps(counts)
    assert "sig_" not in text and "C0AAAA" not in text and "leak" not in text.casefold()


def test_cli_prints_counts_only(tmp_path, capsys):
    p = tmp_path / "signals.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in SIGNALS))
    assert enrich.main([str(p)]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"slack_testable_before", "slack_testable_after",
                                                        "cross_source_merges", "top_people"}
