"""Team view: the rsi_report_ledger tool (shape, caps, storage), the fold, the stdio server and the agents that call it."""

import asyncio
import calendar
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import mcp_server, routes, team  # noqa: E402

NOW = calendar.timegm(time.strptime("2026-01-06T09:10:00Z", "%Y-%m-%dT%H:%M:%SZ"))
SNAPSHOTS = json.loads((ROOT / "fixtures/team_snapshots.json").read_text())


def compact(slot="chat-lane-1", items=None):
    """What work_ledger_read compact=true returns (conductor header + rows), extra fields included."""
    return {"conductor": {"schema": 1, "slot_key": slot, "goal": "g", "round": 3, "depth": 1},
            "items": items if items is not None else [{
                "item_id": "it_0000abcd", "title": "t", "state": "open", "created_at": "2026-01-06T08:00:00Z",
                "status": "question", "summary": "s", "decision": "long conductor text", "verdict": None, "pr": None,
                "worker_session_key": "chat-w-1", "last_report_at": "2026-01-06T08:30:00+00:00",
                "orphaned": False, "stale": False, "acceptance_concrete": True}]}


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path))
    return tmp_path


def test_ingest_stores_one_trimmed_snapshot_per_reporter(data):
    got = team.ingest({"role": "lane", "round": 3, "snapshot": compact()}, now=lambda: NOW)
    assert got == {"ok": True, "reporter": "chat-lane-1", "role": "lane", "items": 1, "received_at": "2026-01-06T09:10:00Z"}
    files = list((data / "team").glob("*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    assert "decision" not in rec["items"][0] and "acceptance_concrete" not in rec["items"][0]  # only the shown columns
    # A second push from the same reporter replaces the first; a string snapshot is the same JSON.
    team.ingest({"role": "lane", "snapshot": json.dumps(compact(items=[]))}, now=lambda: NOW)
    assert [json.loads(p.read_text())["items"] for p in (data / "team").glob("*.json")] == [[]]


@pytest.mark.parametrize("args, field", [
    ({"role": "boss", "snapshot": compact()}, "role"),
    ({"role": "lane", "snapshot": {"items": []}}, "reporter_session_key"),
    ({"role": "lane", "reporter_session_key": "chat-other", "snapshot": compact()}, "differs"),
    ({"role": "lane", "reporter_session_key": "../../exams/x", "snapshot": {"items": []}}, "reporter_session_key"),
    ({"role": "lane", "snapshot": compact(items=[{"item_id": "it_0000abcd", "state": "done"}])}, "items[0].state"),
    ({"role": "lane", "snapshot": compact(items=[{"item_id": "x", "state": "open"}])}, "items[0].item_id"),
    ({"role": "lane", "snapshot": compact(items=[{"item_id": "it_0000abcd", "state": "open", "pr": "12"}])}, "items[0].pr"),
    ({"role": "lane", "snapshot": compact(items=[{"item_id": "it_0000abcd", "state": "open"}] * 201)}, "200 items"),
    ({"role": "lane", "snapshot": compact(items=[{"item_id": "it_0000abcd", "state": "open", "title": "x" * 300000}])}, "bytes"),
    ({"role": "lane", "snapshot": "{not json"}, "not JSON"),
])
def test_wrong_shapes_are_refused_with_the_field_named(data, args, field):
    with pytest.raises(team.Refused, match=field.replace("[", r"\[").replace("]", r"\]")):
        team.ingest(args)
    assert not list(data.glob("team/*.json"))


def test_long_text_is_cut_not_refused(data):
    team.ingest({"role": "lane", "snapshot": compact(items=[{"item_id": "it_0000abcd", "state": "open", "summary": "y" * 900}])})
    (rec,) = team.read_all()
    assert len(rec["items"][0]["summary"]) == team.TEXT_CAPS["summary"]


def test_a_linked_team_dir_is_never_written_through(data):
    """A planted link must not turn a snapshot write into a write to the sealed exams."""
    (data / "exams").mkdir()
    (data / "team").symlink_to(data / "exams")
    with pytest.raises(team.Refused, match="link"):
        team.ingest({"role": "lane", "snapshot": compact()})
    assert not list((data / "exams").iterdir())
    assert team.read_all() == []


def test_the_reporter_count_is_capped(data, monkeypatch):
    monkeypatch.setattr(team, "MAX_REPORTERS", 3)
    for n in range(5):
        team.ingest({"role": "lane", "snapshot": compact(slot=f"chat-lane-{n}", items=[])})
        os.utime(next(p for p in (data / "team").glob("*.json") if json.loads(p.read_text())["reporter"] == f"chat-lane-{n}"),
                 (NOW + n, NOW + n))
    assert sorted(r["reporter"] for r in team.read_all()) == ["chat-lane-2", "chat-lane-3", "chat-lane-4"]


def store_fixture(data):
    (data / "team").mkdir()
    for i, rec in enumerate(SNAPSHOTS):
        (data / "team" / f"{i}.json").write_text(json.dumps(rec))


def test_fold_builds_lead_lanes_workers_and_the_fixture_is_its_output(data):
    store_fixture(data)
    view = team.view(now=NOW)
    assert view == json.loads((ROOT / "fixtures/team.json").read_text())  # the page's demo data is the real fold
    (lead,) = view["leads"]
    assert [i["lane"]["key"] for i in lead["items"] if "lane" in i] == ["chat-fake-lane-find", "chat-fake-lane-build"]
    assert [r["key"] for r in view["loose_lanes"]] == ["chat-fake-lane-old"]  # no lead item names it
    assert [(n["why"], n["item_id"]) for n in view["needs_you"]] == [
        ("question", "it_00000003"), ("blocked", "it_00000011"), ("merge", "it_00000020")]
    build = next(i["lane"] for i in lead["items"] if i.get("lane", {}).get("key") == "chat-fake-lane-build")
    assert build["stale"] and not lead["stale"]  # 130 min vs 5 min since the last push
    assert view["counts"] == {"progress": 3, "question": 1, "blocked": 1, "accepted": 2, "abandoned": 1}


def test_get_team_route_answers_the_fold(data):
    store_fixture(data)
    resp = asyncio.run(routes._team(None, None))
    body = json.loads(resp.text)
    assert body["ok"] and body["team"]["reporters"] == len(SNAPSHOTS)


def rpc(lines, env):
    r = subprocess.run([sys.executable, str(ROOT / "backend/mcp_server.py")], input="".join(json.dumps(x) + "\n" for x in lines),
                       capture_output=True, text=True, timeout=30, env=env)
    return [json.loads(x) for x in r.stdout.splitlines()]


def test_the_stdio_server_lists_and_runs_the_one_tool(data):
    env = {**os.environ, "HARNESS_RSI_DATA": str(data)}
    out = rpc([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
               {"jsonrpc": "2.0", "method": "notifications/initialized"},
               {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
               {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "rsi_report_ledger",
                "arguments": {"role": "lead", "round": 5, "snapshot": compact(slot="chat-lead")}}},
               {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "rsi_report_ledger", "arguments": {"role": "x"}}},
               {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "other"}}], env)
    assert [m["id"] for m in out] == [1, 2, 3, 4, 5]
    assert [t["name"] for t in out[1]["result"]["tools"]] == ["rsi_report_ledger"]
    assert json.loads(out[2]["result"]["content"][0]["text"])["ok"] is True and not out[2]["result"]["isError"]
    assert out[3]["result"]["isError"] and "refused" in out[3]["result"]["content"][0]["text"]
    assert out[4]["error"]["code"] == -32602
    assert [r["reporter"] for r in team.read_all(data)] == ["chat-lead"]


def test_the_manifest_server_finds_the_installed_app(data, tmp_path):
    """The manifest's -c locator runs backend/mcp_server.py from <crew home>/apps/harness-rsi."""
    home = tmp_path / "crew"
    (home / "apps").mkdir(parents=True)
    (home / "apps" / "harness-rsi").symlink_to(ROOT)
    spec = json.loads((ROOT / "app.json").read_text())["mcpServers"]["team"]
    env = {**os.environ, "HARNESS_RSI_DATA": str(data), "KIROCREW_HOME": str(home)}
    r = subprocess.run([sys.executable, *spec["args"]], input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n",
                       capture_output=True, text=True, timeout=30, env=env)
    assert spec["command"] == "python3"
    assert json.loads(r.stdout)["result"]["tools"][0]["name"] == "rsi_report_ledger", r.stderr


def test_the_server_imports_nothing_outside_the_stdlib():
    """It runs under kiro-cli, where the app's requirements may not be installed."""
    for name in ("mcp_server.py", "team.py"):
        src = (ROOT / "backend" / name).read_text()
        assert "jsonschema" not in src and "aiohttp" not in src and "from ." not in src
    assert mcp_server.TOOLS[0]["inputSchema"]["required"] == ["role", "snapshot"]


LEAD_AND_LANES = ["rsi-lead", *(f"rsi-lane-{x}" for x in ("find", "propose", "exam", "build", "prompt"))]


def test_the_lead_and_lanes_ship_and_push_every_patrol_cycle():
    manifest = json.loads((ROOT / "app.json").read_text())
    assert manifest["agents"] == [f"crew/agents/{n}.json" for n in LEAD_AND_LANES]
    for name in LEAD_AND_LANES:
        agent = json.loads((ROOT / f"crew/agents/{name}.json").read_text())
        assert "@harness-rsi:team" in agent["tools"]
        assert "@harness-rsi:team/rsi_report_ledger" in agent["allowedTools"]
        role = "lead" if name == "rsi-lead" else "lane"
        assert "At the end of EVERY patrol cycle" in agent["prompt"] and f'role "{role}"' in agent["prompt"]
        assert "work_ledger_read with compact=true" in agent["prompt"]
        assert not {"execute_bash", "fs_write"} & set(agent["tools"])  # conductors: no shell, no file writes
