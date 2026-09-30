"""Design-crew specs: least-privilege tools, schema-named prompts, and the debate reducer."""

import copy
import importlib.util
import json
import pathlib

import pytest
from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENTS = ROOT / "crew" / "agents"
HERE = pathlib.Path(__file__).resolve().parent

# Role -> (schema it emits, the only tools it may name besides "thinking").
ROLES = {
    "rsi-trend-scout": ("signal", {"web_search", "web_fetch"}),
    "rsi-session-scanner": (
        "signal",
        {"@kirocrew-core/list_sessions", "@kirocrew-core/search_chat_history", "@kirocrew-core/get_chat_session"},
    ),
    "rsi-reviewer-value": ("proposal", set()),
    "rsi-reviewer-risk": ("proposal", set()),
    "rsi-question-setter": ("exam", set()),
}
# Tools that could write files, run code, push, merge, spawn, or reach Slack.
FORBIDDEN_WORDS = ("write", "bash", "shell", "aws", "slack", "send", "post", "spawn", "session_send", "git", "merge")


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "crew" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def agent(name):
    return json.loads((AGENTS / f"{name}.json").read_text(encoding="utf-8"))


def validator(name):
    return Draft202012Validator(json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text()))


def test_every_role_has_exactly_one_spec_and_prompt():
    assert {p.stem for p in AGENTS.glob("*.json")} == set(ROLES)
    assert {p.stem for p in (AGENTS / "prompts").glob("*.md")} == set(ROLES)


def test_json_prompts_match_markdown_sources():
    assert load_module("build_agents").stale(write=False) == []


@pytest.mark.parametrize("name", sorted(ROLES))
def test_spec_names_only_allowed_tools(name):
    spec = agent(name)
    allowed = ROLES[name][1]
    assert spec["name"] == name
    assert set(spec["tools"]) <= allowed | {"thinking"}
    assert set(spec["allowedTools"]) <= set(spec["tools"])
    assert not set(spec) - {"name", "model", "description", "prompt", "tools", "allowedTools"}
    for tool in spec["tools"]:
        assert not any(w in tool.lower() for w in FORBIDDEN_WORDS), tool


def test_question_setter_has_no_access_path_to_proposals():
    spec = agent("rsi-question-setter")
    assert spec["tools"] == ["thinking"] and spec["allowedTools"] == []
    # No read tool, no MCP server, no resources, no hooks: input arrives only in its task message.
    assert "resources" not in spec and "mcpServers" not in spec and "hooks" not in spec
    assert "proposal.schema.json" not in spec["prompt"]


@pytest.mark.parametrize("name", sorted(ROLES))
def test_prompt_names_its_schema_and_its_must_nots(name):
    prompt = agent(name)["prompt"]
    assert f"schemas/{ROLES[name][0]}.schema.json" in prompt
    assert "Must not:" in prompt
    for word in ("push", "merge", "Slack"):
        assert word in prompt.split("Must not:")[1]


def reduce_fixture():
    transcript = json.loads((HERE / "debate_transcript.json").read_text())
    exams = json.loads((ROOT / "fixtures" / "exams.json").read_text())
    return load_module("reduce"), transcript, exams


def test_debate_reduces_to_valid_proposals():
    reduce, transcript, exams = reduce_fixture()
    props = reduce.reduce_debate(transcript, exams)
    v = validator("proposal")
    assert [p["id"] for p in props] == ["prop_bg_tasks", "prop_plain_errors"]
    for p in props:
        assert [e.message for e in v.iter_errors(p)] == []
        assert p["decision"] is None
    # Both reviewers' signals and risks are kept; cost takes the larger estimate.
    assert props[0]["signal_ids"] == ["sig_20260101_0001", "sig_20260101_0002"]
    assert props[0]["cost"]["files"] == 4 and props[0]["exam_ids"] == ["exam_bg_task_survives_close"]


@pytest.mark.parametrize("cut", ["drop_round", "add_round", "drop_turn", "drop_table"])
def test_unfinished_debate_is_refused(cut):
    reduce, transcript, exams = reduce_fixture()
    t = copy.deepcopy(transcript)
    if cut == "drop_round":
        t["rounds"].pop()
    elif cut == "add_round":
        t["rounds"].append({**copy.deepcopy(t["rounds"][1]), "round": 3})
    elif cut == "drop_turn":
        t["rounds"][1]["turns"].pop()
    else:
        t["rounds"][1]["turns"][1]["text"] = "no table"
    with pytest.raises(reduce.DebateError):
        reduce.reduce_debate(t, exams)
