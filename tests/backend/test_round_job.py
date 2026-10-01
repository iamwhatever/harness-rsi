"""POST /round/run: owner-only, single-flight, and fed the app's own Slack and GitHub sources."""

import asyncio
import importlib.util
import json
import pathlib
import sys
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import round_job, routes, settings  # noqa: E402

_spec = importlib.util.spec_from_file_location("rsi_test_run_round", ROOT / "tests" / "crew" / "test_run_round.py")
_crew_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_crew_tests)
FakeCrew, SIGNALS = _crew_tests.FakeCrew, _crew_tests.SIGNALS
SECRET = "slack-mcp-secret-cmd"


class Req(dict):
    def __init__(self, body=None, **flags):
        super().__init__({"app": "harness-rsi", **flags})
        self._body, self.content_length = body, len(json.dumps(body)) if body is not None else 0

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def text(resp):
    return resp.status, json.loads(resp.text)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fake GitHub and Slack sources, a fake crew (session scanner and trend scout included)."""
    monkeypatch.setenv("HARNESS_RSI_DATA", str(tmp_path))
    monkeypatch.delenv(round_job.EXAM_ENV, raising=False)
    monkeypatch.setattr(round_job, "STATE", {**round_job.STATE, "task": None, "running": False, "notes": []})
    monkeypatch.setattr(routes, "JOB", {**routes.JOB, "task": None, "running": False})
    github = [s for s in SIGNALS if s["source"].startswith("github:")]
    slack = [s for s in SIGNALS if s["source"].startswith("slack:")]
    assert github and slack
    gate, seen, crew = threading.Event(), [], FakeCrew()
    gate.set()
    monkeypatch.setattr(routes, "github_rows", lambda: (seen.append("github"), gate.wait(10), (github, ""))[2])
    monkeypatch.setattr(settings, "read", lambda: {**settings.DEFAULTS, "command": SECRET})  # as the vault answers
    monkeypatch.setattr(routes.adapters.slack, "collect", lambda conf: seen.append(conf["command"]) or slack)
    monkeypatch.setattr(round_job, "make_agent", lambda data: crew)
    monkeypatch.setattr(round_job, "make_saver", lambda data: lambda slug, title, page: slug)
    return {"data": tmp_path, "gate": gate, "seen": seen, "crew": crew}


def run(*coros):
    async def go():
        out = [text(await c) for c in coros]
        if round_job.STATE["task"]:
            await round_job.STATE["task"]
        return out, text(await routes._round_status(Req(), None))[1]["round"]
    return asyncio.run(go())


def test_round_runs_with_the_apps_own_collectors(env):
    (status, body), = run(routes._round_run(Req(), None))[0]
    assert status == 202 and body["round"]["running"] is True and body["round"]["round"] == 1
    done = run()[1]
    assert done["running"] is False and done["error"] == "" and done["counts"]["proposals"] >= 3
    assert env["seen"] == ["github", SECRET]  # Slack read with the vault's command, in-process
    names = [n for n, _ in env["crew"].calls]
    assert "rsi-session-scanner" in names and "rsi-trend-scout" in names
    rows = [json.loads(x) for x in (env["data"] / "signals.jsonl").read_text().splitlines()]
    assert {r["source"].split(":")[0] for r in rows} >= {"github", "slack", "session", "trend"}
    assert done["notes"] == [f"exams: not dry-run ({round_job.EXAM_ENV} unset)"]
    assert round_job.next_round() == 2


def test_round_is_single_flight(env):
    env["gate"].clear()

    async def go():
        first = text(await routes._round_run(Req(), None))
        second = text(await routes._round_run(Req({"round": 7}), None))
        refresh = text(await routes._refresh(Req(), None))
        env["gate"].set()
        await round_job.STATE["task"]
        return first, second, refresh

    first, second, refresh = asyncio.run(go())
    assert first[0] == 202 and second == (409, {"ok": False, "code": "round_running", "error": "a round is already running"})
    assert refresh[0] == 409 and env["seen"].count("github") == 1


@pytest.mark.parametrize("req,status", [(Req(internal_auth=True), 403), (Req(app="other-app"), 403),
                                        (Req({"round": "2"}), 400), (Req({"round": 0}), 400)])
def test_refused_runs_start_nothing(env, req, status):
    assert text(asyncio.run(routes._round_run(req, None)))[0] == status
    assert round_job.STATE["started_at"] is None and env["seen"] == []


def test_failed_round_reports_and_frees_the_slot(env):
    env["crew"].n_props = 2
    done = run(routes._round_run(Req(), None))[1]
    assert done["running"] is False and done["error"].startswith("only 2 proposals")
    assert not (env["data"] / "proposals.json").exists()


def test_secret_never_in_a_response(env, monkeypatch):
    monkeypatch.setattr(routes.adapters.slack, "collect", lambda conf: (_ for _ in ()).throw(OSError(SECRET)))
    (_, body), = run(routes._round_run(Req(), None))[0]
    done = run()[1]
    assert "slack: OSError" in done["notes"]
    for out in (body, done):
        assert SECRET not in json.dumps(out)


def test_cli_round_says_slack_is_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(settings, "read", lambda: dict(settings.DEFAULTS))  # a bare CLI: no vault
    assert round_job.crew().slack_collector(None)() == []
    assert "slack: UNAVAILABLE" in capsys.readouterr().err


def test_backend_loads_as_a_subpackage_like_the_gateway_does(monkeypatch):
    """The host loads routes.py under a synthetic root with no sys.path entry for the app."""
    import importlib
    import importlib.machinery

    root = importlib.util.module_from_spec(importlib.machinery.ModuleSpec("rsi_app_root", None, is_package=True))
    root.__path__ = [str(ROOT)]
    monkeypatch.setitem(sys.modules, "rsi_app_root", root)
    mod = importlib.import_module("rsi_app_root.backend.routes")
    assert mod.adapters.__name__ == "rsi_app_root.adapters" and mod.round_job.__name__ == "rsi_app_root.backend.round_job"
    assert importlib.import_module("rsi_app_root.backend.settings").CHANNEL_RE.match("C0FAKE00001")
