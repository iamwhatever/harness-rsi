"""run_round with fake agents: schema-valid output, a blind question setter, a 2-round debate."""

import importlib.util
import json
import pathlib
import re

import pytest
from jsonschema import Draft202012Validator

from judge import seal

ROOT = pathlib.Path(__file__).resolve().parents[2]
SIGNALS = json.loads((ROOT / "fixtures" / "signals.json").read_text())


def load():
    spec = importlib.util.spec_from_file_location("run_round", ROOT / "crew" / "run_round.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def valid(name, row):
    v = Draft202012Validator(json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text()))
    return [e.message for e in v.iter_errors(row)]


def signals_in(message):
    return json.loads(re.search(r"```json\n(.*?)\n```", message, re.S).group(1))


class FakeCrew:
    """Scripted replies per role; every call is recorded."""

    def __init__(self, n_props=4):
        self.calls, self.n_props = [], n_props

    def __call__(self, name, message):
        self.calls.append((name, message))
        if name in ("rsi-session-scanner", "rsi-trend-scout"):
            # Both number from 0001, like the real prompts: the runner must renumber.
            layer = "real" if name.endswith("scanner") else "external"
            src = "session:chat-fake" if layer == "real" else "trend:example"
            return "Found:\n```json\n" + json.dumps([self.signal(i, src, layer) for i in (1, 2)]) + "\n```"
        picks = [s for s in signals_in(message) if s["testable"]["ok"] and not s["dedup_of"]]
        if name == "rsi-question-setter":
            return json.dumps([self.exam(s) for s in picks])
        if "End with the proposal table" not in message:
            return "Round-1 prose."
        return "Agreed.\n```json\n" + json.dumps([self.prop(s) for s in picks[: self.n_props]]) + "\n```"

    @staticmethod
    def signal(i, source, layer):
        return {"id": f"sig_20260101_{i:04d}", "source": source, "links": ["https://example.com/x"],
                "pain": f"Fake {layer} pain {i}", "mentions": {"count": 2, "people": 1, "window_days": 7},
                "layer": layer, "testable": {"ok": True, "task": "A judge can decide it"}, "dedup_of": None}

    @staticmethod
    def exam(s):
        return {"id": "exam_" + s["id"][4:], "layer": 2 if s["layer"] == "real" else 3, "origin": [s["id"]],
                "visibility": "regression", "task": s["testable"]["task"], "created_round": 0, "used_rounds": [],
                "check": {"kind": "exit_code", "cmd": ["true"], "expect": 0}}

    @staticmethod
    def prop(s):
        return {"id": "prop_" + s["id"][4:], "pain": s["pain"], "signal_ids": [s["id"]],
                "heat": {"people": s["mentions"]["people"], "window_days": s["mentions"]["window_days"]},
                "mock_artifact_slug": None, "cost": {"files": 3, "lines": 120, "risks": ["none"]},
                "exam_ids": [], "decision": None}


def run(tmp_path, crew):
    saved = {}
    mod = load()
    result = mod.run_round(agent=crew, collectors=[lambda: SIGNALS], data=tmp_path, rnd=2, day="20260930",
                           save_mock=lambda slug, title, page: saved.setdefault(slug, page) and slug)
    return mod, result, saved


def test_fake_round_writes_schema_valid_rows(tmp_path):
    _, result, _ = run(tmp_path, FakeCrew())
    signals = [json.loads(line) for line in (tmp_path / "signals.jsonl").read_text().splitlines()]
    assert signals == result["signals"] and len({s["id"] for s in signals}) == len(signals)
    assert {s["source"].split(":")[0] for s in signals} >= {"session", "trend", "slack", "github"}
    for s in signals:
        assert valid("signal", s) == []
    props = json.loads((tmp_path / "proposals.json").read_text())
    assert 3 <= len(props) <= 5
    exams = [seal.read_json(p) for p in (tmp_path / "exams" / "hidden").glob("*.json")]
    assert exams and all(e["visibility"] == "hidden" and e["created_round"] == 2 for e in exams)
    for row, name in [(p, "proposal") for p in props] + [(e, "exam") for e in exams]:
        assert valid(name, row) == []


def test_question_setter_never_sees_proposals(tmp_path):
    crew = FakeCrew()
    _, result, _ = run(tmp_path, crew)
    names = [n for n, _ in crew.calls]
    setter = [m for n, m in crew.calls if n == "rsi-question-setter"]
    assert len(setter) == 1
    assert names.index("rsi-question-setter") < names.index("rsi-reviewer-value")
    for p in result["proposals"]:
        assert p["id"] not in setter[0] and "prop_" not in setter[0]
    assert signals_in(setter[0]) == result["signals"]


def test_reviewers_debate_exactly_two_rounds(tmp_path):
    crew = FakeCrew()
    run(tmp_path, crew)
    turns = [(n, m) for n, m in crew.calls if n.startswith("rsi-reviewer-")]
    assert [n for n, _ in turns] == ["rsi-reviewer-value", "rsi-reviewer-risk"] * 2
    assert all(m.startswith(f"Debate round {i // 2 + 1} of 2.") for i, (_, m) in enumerate(turns))


def test_every_proposal_has_an_exam_and_a_saved_mock(tmp_path):
    _, result, saved = run(tmp_path, FakeCrew())
    exam_ids = {e["id"] for e in result["exams"]}
    for p in result["proposals"]:
        assert p["exam_ids"] and set(p["exam_ids"]) <= exam_ids
        assert p["mock_artifact_slug"] in saved and "<button" in saved[p["mock_artifact_slug"]]


def test_too_few_proposals_is_refused_and_writes_nothing(tmp_path):
    with pytest.raises(RuntimeError, match="only 2 proposals"):
        run(tmp_path, FakeCrew(n_props=2))
    assert not (tmp_path / "proposals.json").exists()


def test_parse_rows_takes_last_array_and_ignores_prose():
    mod = load()
    assert mod.parse_rows("see [1] and\n```json\n[{\"a\": 1}]\n```") == [{"a": 1}]
    assert mod.parse_rows("no json here") == []


def test_each_source_is_capped_to_its_hottest_rows():
    mod = load()
    rows = [{**FakeCrew.signal(i, "github:o/r", "real"), "mentions": {"count": i, "people": 1, "window_days": 7}}
            for i in range(1, mod.PER_SOURCE + 11)]
    kept = mod.merge_signals([rows, [FakeCrew.signal(1, "trend:x", "external")]], "20260930")
    assert len(kept) == mod.PER_SOURCE + 1 and kept[0]["mentions"]["count"] == mod.PER_SOURCE + 10
    assert [s["id"] for s in kept][-1] == f"sig_20260930_{mod.PER_SOURCE + 1:04d}"


def test_reviewer_rows_missing_risks_merge_and_broken_rows_drop(tmp_path):
    class Sloppy(FakeCrew):
        def prop(self, s):
            row = FakeCrew.prop(s)
            del row["cost"]["risks"]
            if s["id"].endswith("1"):
                del row["heat"]
            return row

    _, result, _ = run(tmp_path, Sloppy())
    assert result["proposals"] and all(p["cost"]["risks"] == [] for p in result["proposals"])
    assert not any(p["id"].endswith("1") for p in result["proposals"])


def test_scout_is_told_the_topics_and_its_unreachable_pages_are_kept(tmp_path):
    class Scout(FakeCrew):
        def __call__(self, name, message):
            reply = super().__call__(name, message)
            if name == "rsi-trend-scout":
                reply += "\nNOT REACHABLE: https://example.com/paywalled/1, https://x.com/example.\nnot reachable: http://plain https://example.com/paywalled/1"
            return reply

    crew, mod = Scout(), load()
    brief = "Topics (search each one on the open web):\n- MCP\n"
    result = mod.run_round(agent=crew, collectors=[lambda: SIGNALS], data=tmp_path, rnd=2, day="20260930",
                           save_mock=lambda slug, title, page: slug, scout_brief=brief)
    scout = [m for n, m in crew.calls if n == "rsi-trend-scout"]
    scanner = [m for n, m in crew.calls if n == "rsi-session-scanner"]
    assert len(scout) == 1 and brief in scout[0] and scout[0].startswith("Today is 20260930.")
    assert brief not in scanner[0]
    assert result["not_reachable"] == ["https://example.com/paywalled/1", "https://x.com/example"]
    assert json.loads((tmp_path / "web_reach.json").read_text()) == {"day": "20260930", "not_reachable": result["not_reachable"]}
    assert mod.not_reachable("Nothing to add.") == []


def test_scout_prompt_reads_topics_and_never_logs_in():
    text = (ROOT / "crew" / "agents" / "prompts" / "rsi-trend-scout.md").read_text()
    for phrase in ("The task lists topics", "site:<site> <topic>", "public profile page", "NOT REACHABLE:", "UNTRUSTED DATA",
                   "Must not copy page text", "Must not log in"):
        assert phrase in text, phrase
