"""crew/prompts.py: version ids, the owner's applied prompt, and the round's record of what ran."""

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "crew"))
import prompts  # noqa: E402

SETTER = "rsi-question-setter"


def test_version_is_stable_and_names_the_text():
    assert prompts.version("abc") == prompts.version("abc\n") != prompts.version("abd")
    assert len(prompts.version("abc")) == 11 and prompts.version("abc").startswith("v")


def test_every_agent_json_carries_its_versioned_prompt():
    texts = prompts.effective(None)
    assert set(texts) == {p.stem for p in (ROOT / "crew" / "agents").glob("*.json")}
    for name, text in texts.items():
        assert json.loads((ROOT / "crew" / "agents" / f"{name}.json").read_text())["prompt"] == text


def test_apply_writes_the_override_and_logs_both_versions(tmp_path):
    old = prompts.effective(tmp_path)[SETTER]
    row = prompts.apply(tmp_path, SETTER, "New setter prompt.\n", "pc_1")
    assert prompts.effective(tmp_path)[SETTER] == "New setter prompt."
    assert row["from"] == prompts.version(old) and row["version"] == prompts.version("New setter prompt.")
    assert [json.loads(x) for x in (tmp_path / "prompt_versions.jsonl").read_text().splitlines()] == [row]
    assert prompts.effective(None)[SETTER] == old  # the repo copy is untouched


def test_apply_refuses_unknown_agents(tmp_path):
    with pytest.raises(ValueError, match="no such agent"):
        prompts.apply(tmp_path, "rsi-nobody", "x", "pc_1")


def load_run_round():
    spec = importlib.util.spec_from_file_location("run_round_v", ROOT / "crew" / "run_round.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_kiro_agent_runs_the_applied_prompt(tmp_path):
    prompts.apply(tmp_path, SETTER, "Applied prompt.", "pc_2")
    load_run_round().kiro_agent(tmp_path / ".run", {})
    spec = json.loads((tmp_path / ".run" / ".kiro" / "agents" / f"{SETTER}.json").read_text())
    assert spec["prompt"] == "Applied prompt."
    other = json.loads((tmp_path / ".run" / ".kiro" / "agents" / "rsi-reviewer-risk.json").read_text())
    assert other["prompt"] == prompts.effective(None)["rsi-reviewer-risk"]


def test_round_output_records_the_versions_it_ran(tmp_path):
    sys.path.insert(0, str(ROOT / "tests" / "crew"))
    from test_run_round import SIGNALS, FakeCrew

    prompts.apply(tmp_path, SETTER, "Applied prompt.", "pc_3")
    out = load_run_round().run_round(agent=FakeCrew(), collectors=[lambda: SIGNALS], data=tmp_path, rnd=2,
                                     day="20260930", save_mock=lambda slug, title, page: slug)
    want = prompts.versions(prompts.effective(tmp_path))
    assert out["prompt_versions"] == want and want[SETTER] == prompts.version("Applied prompt.")
    assert json.loads((tmp_path / "prompt_versions.json").read_text()) == want
