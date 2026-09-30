"""Slack collector on a fake MCP: valid rows, allowlist, read-only tools, nothing personal out."""

import datetime as dt
import json
import pathlib
import sys

import pytest
from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from adapters import slack  # noqa: E402


V = Draft202012Validator(json.loads((ROOT / "schemas/signal.schema.json").read_text(encoding="utf-8")))
NOW = dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)
ALLOWED, OTHER = "C0AGA4Y4NP7", "C0OTHER0001"
BODY = "The background task is lost when I close the page, is this a bug?"
MESSAGES = {
    ALLOWED: [
        {"ts": "1790000000.000100", "user": "U0AAA", "text": BODY, "reply_count": 3, "reply_users": ["U0BBB", "U0CCC"],
         "user_profile": {"real_name": "Pat Example", "display_name": "pat"}},
        {"ts": "1790050000.000200", "user": "U0DDD", "text": "Background task lost again when I close the page. Bug?",
         "reply_users": ["U0AAA"]},
        {"ts": "1790060000.000300", "user": "U0EEE", "text": "Pat says the sidebar search is slow with many sessions?"},
        {"ts": "1790070000.000400", "bot_id": "B01", "text": "Deploy failed? bot noise here always"},
        {"ts": "1790080000.000500", "user": "U0FFF", "text": "Nice demo today, shipping the release notes soon"},
    ],
    OTHER: [{"ts": "1790000000.000900", "user": "U0ZZZ", "text": "Secret channel error with builds failing hard?"}],
}


class FakeMcp:
    """Answers batch_get_conversation_history like the Slack MCP; records every call."""

    def __init__(self, leak_other=True):
        self.calls, self.leak_other = [], leak_other

    def __call__(self, name, args):
        self.calls.append((name, args))
        asked = [c["channelId"] for c in args["channels"]]
        ids = asked + ([OTHER] if self.leak_other else [])  # a server that answers more than asked
        return [{"channelId": c, "result": {"messages": MESSAGES.get(c, []), "has_more": False}} for c in ids]


def rows(fake=None, **kw):
    return slack.build_signals(fake or FakeMcp(), channels=[ALLOWED], now=NOW, **kw)


def test_rows_validate_and_carry_slack_source_and_permalinks():
    out = rows(workspace_url="https://example.slack.com")
    assert out and all(not list(V.iter_errors(r)) for r in out)
    assert {r["source"] for r in out} == {f"slack:{ALLOWED}"}
    assert all(r["links"][0].startswith(f"https://example.slack.com/archives/{ALLOWED}/p") for r in out)
    assert all(r["mentions"]["window_days"] == 14 for r in out)


def test_same_pain_is_merged_and_counts_add_up():
    first, dup, *rest = rows()
    assert dup["dedup_of"] == first["id"] and first["dedup_of"] is None
    assert first["mentions"] == {"count": 5, "people": 4, "window_days": 14}
    assert all(r["dedup_of"] is None for r in rest)


def test_only_allowlisted_channels_are_asked_and_kept():
    fake = FakeMcp()
    out = rows(fake)
    assert [c["channelId"] for _, a in fake.calls for c in a["channels"]] == [ALLOWED]
    assert not any("Secret" in r["pain"] or OTHER in json.dumps(r) for r in out)
    assert slack.build_signals(fake, channels=["general", "#x"], now=NOW) == []


def test_bots_chatter_and_replies_are_skipped():
    pains = " ".join(r["pain"] for r in rows()).casefold()
    assert "deploy" not in pains and "demo" not in pains


@pytest.mark.parametrize("tool", ["post_message", "reaction_tool", "create_draft", "self_dm", "upload_file",
                                  "update_message", "batch_set_last_read", "delete_message", "list_channels"])
def test_write_like_or_unlisted_tools_are_refused_before_the_server(tool):
    fake = FakeMcp()
    with pytest.raises(slack.ToolRefused):
        slack.guard(fake)(tool, {})
    assert fake.calls == []


def test_no_name_user_id_or_message_body_in_output():
    text = json.dumps(rows())
    for bad in ("U0AAA", "U0BBB", "U0DDD", "Pat", "pat ", "Example", BODY, "close the page", "user"):
        assert bad not in text, bad
    for r in rows():  # a gist, not a copy: no 4-word run of any message survives
        words = r["pain"].casefold().split()
        runs = {" ".join(words[i:i + 4]) for i in range(len(words) - 3)}
        assert not any(run in m["text"].casefold() for run in runs for m in MESSAGES[ALLOWED])


def test_collect_is_off_with_an_empty_command():
    assert slack.collect({"command": "", "channels": [ALLOWED]}) == []


@pytest.mark.parametrize("argv", [("sh -c x", []), ("slack", ["a;b"]), ("", []), ("slack", ["$(id)"])])
def test_command_lines_with_shell_syntax_are_refused(argv):
    assert slack.check_argv(*argv)
    with pytest.raises(slack.SlackMcpError), slack.stdio_call(*argv):
        pass


def test_round_runner_reads_slack_itself_and_stays_off_without_a_command(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_round", ROOT / "crew" / "run_round.py")
    rr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rr)
    assert "slack-radar" not in (ROOT / "crew" / "run_round.py").read_text(encoding="utf-8")
    seen = []
    monkeypatch.setattr(slack, "collect", lambda conf: seen.append(conf) or ([{"id": "x"}] if conf["command"] else []))
    assert rr.slack_collector(None)() == [] and seen[-1]["command"] == ""  # no vault here: off
    assert rr.slack_collector("slack-mcp")() == [{"id": "x"}] and seen[-1]["command"] == "slack-mcp"
    monkeypatch.setattr(slack, "collect", lambda conf: (_ for _ in ()).throw(slack.SlackMcpError("down")))
    assert rr.slack_collector("slack-mcp")() == []
