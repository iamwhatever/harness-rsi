"""Harness RSI HTTP API under ``/api/apps/harness-rsi``: three reads, four owner-only writes.

``POST /refresh`` reads the allowlisted Slack channels through the owner's own Slack MCP
server (``adapters.slack``; off while no command is set) and merges those rows at once.
It also starts ``python -m adapters.github_issues`` as a
one-shot job (at most one at a time; it takes minutes) whose rows merge when it exits;
``GET /refresh/status`` reports that job; the job reads each repo in the settings' ``repos`` list
and records each one's last read. ``GET``/``POST /settings`` show and change the Slack and GitHub
settings (``backend.settings``), with the Slack connectors found in ``mcp.json`` by name. ``POST /round/run`` runs one design-crew round
(``backend.round_job``) with these same Slack and GitHub sources plus the owner's sessions; ``GET /round/status``
reports it and, when it ends, writes a ``manual_round`` run row. ``GET /schedule`` also
answers ``next_round_at`` and ``round_stats`` (the last-3 average). ``/schedule`` and ``/schedule/tick``: the owner's opt-in weekly round and daily
regress (``backend.schedule``), off by default. ``GET /outcomes`` reads the outcome ledger,
the scoring job and each linked PR's check rollup (``backend.outcome_checks``, cached); ``POST /outcomes/link`` links a card to a KiroCrew PR; ``POST /score/run``
starts ``python -m backend.autoscore`` (single-flight; the tick does when scoring is on).
``GET /prompt-changes`` lists the proposer's prompt-change cards; ``POST /prompt-changes/decide``
records the owner's choice, and 做 applies the change (``backend.prompt_changes``).
``/dispatch``: the owner's opt-in auto-dispatch (``backend.dispatch``, off by default); when on, 做 on a
proposal card opens one worker chat for it. ``POST /dispatch/start`` is the owner's Dispatch button: it
opens those chats for chosen 做 cards whether auto-dispatch is on or off. A prompt-change card never dispatches.
``GET /team`` folds the lead's and lanes' self-reported work-ledger snapshots (``backend.team``).
No timer in the gateway; loading the routes seals the data dir once (``judge.seal.migrate``, idempotent).
"""

import asyncio
import datetime as dt
import json
import subprocess
import sys
import time

from aiohttp import web

try:  # the gateway loads the backend as a subpackage of the app's own synthetic root
    from .. import adapters
    from ..adapters import sessions as _sessions  # noqa: F401 - binds adapters.sessions
    from ..adapters import slack as _slack  # noqa: F401 - binds adapters.slack
    from ..adapters.github_issues.adapter import distinct_ids
except ImportError:  # tests and the CLI import ``backend`` as a top-level package
    import adapters.sessions
    import adapters.slack
    from adapters.github_issues.adapter import distinct_ids

from . import dispatch, ledger, outcome_checks, prompt_changes, round_job, schedule, settings, store, team
from .ledger import seal

APP_NAME = "harness-rsi"
MAX_BODY = 2 * 1024 * 1024
GITHUB_TIMEOUT_S = 30 * 60
SCORE_TIMEOUT_S = 6 * 3600
#: The GitHub job: ``task`` is the running asyncio task, the rest is shown on the board.
JOB = {"task": None, "running": False, "started_at": None, "finished_at": None, "rows": None, "error": ""}
CLOCK = lambda: dt.datetime.now().astimezone()  # noqa: E731 - local time; tests pass a fake
SCHEDULED = set()  # tasks the tick started, held until they end
#: The scoring job (``backend.autoscore``), shown with ``/outcomes``; ``updated`` lists rows it changed.
SCORE = {"task": None, "running": False, "started_at": None, "finished_at": None, "updated": None, "error": ""}


def _err(status, code, message):
    return web.json_response({"ok": False, "code": code, "error": message}, status=status)


def _owner(request):
    """The dashboard owner or this app's own page; never an agent's internal-secret call."""
    if request.get("internal_auth"):
        return False
    try:
        from kiro_crew.dashboard.handlers.source_providers import is_owner_dashboard_request

        if is_owner_dashboard_request(request):
            return True
    except Exception:  # noqa: BLE001 - an unavailable predicate must not admit
        pass
    return request.get("app") == APP_NAME and request.get("is_dashboard_user") is not False


async def _body(request):
    if (request.content_length or 0) > MAX_BODY:
        return None
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def github_rows():
    """Signal rows from the GitHub adapter for every repo in the settings, or ``([], error)``.

    Each repo is its own adapter run, so one failing repo keeps the others' rows, and each
    repo's last read is kept for the Settings tab (``settings.record_fetch``).
    """
    rows, errors, taken = [], [], set()
    repos = settings.read()["repos"]
    if not repos:
        return [], "github: off (no repo set)"
    for repo in repos:
        got, error = _github_repo(repo)
        rows += distinct_ids(got, taken)
        if error:
            errors.append(error)
        try:
            settings.record_fetch(repo, time.time(), None if error else len(got), error)
        except OSError:
            pass  # the state line is for the page; the rows still count
    return rows, "; ".join(errors)


def _github_repo(repo):
    try:
        r = subprocess.run([sys.executable, "-m", "adapters.github_issues", "--repo", repo], cwd=store.ROOT,
                           capture_output=True, text=True, timeout=GITHUB_TIMEOUT_S)
        rows = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return [], f"github: {repo}: {type(exc).__name__}"
    return (rows, "") if isinstance(rows, list) else ([], f"github: {repo}: adapter exit {r.returncode}")


def slack_rows():
    """Signal rows from the Slack MCP, or ``([], note)``; a note also when collection is off."""
    conf = settings.read()
    if not conf["command"]:
        return [], "slack: off (no Slack connector set)"
    try:
        return adapters.slack.collect(conf), ""
    except Exception as exc:  # noqa: BLE001 - a Slack failure must not stop the GitHub refresh
        return [], f"slack: {type(exc).__name__}"


def session_rows():
    """Signal rows from the owner's own dashboard sessions (``adapters.sessions``), or ``([], note)``."""
    try:
        return adapters.sessions.collect(), ""
    except Exception as exc:  # noqa: BLE001 - a session read failure must not stop the round
        return [], f"sessions: {type(exc).__name__}"


def round_sources():
    """The round's collectors, looked up when the round runs: GitHub, Slack, the owner's sessions."""
    return [lambda: github_rows(), lambda: slack_rows(), lambda: session_rows()]


async def _settings_get(request, ctx):
    return web.json_response({"ok": True, "settings": await asyncio.to_thread(lambda: settings.view(settings.read()))})


async def _save(request, mod, what, show):
    """Owner-only write of ``mod``'s vault-backed ``what``: validate, then ``mod.write``."""
    if not _owner(request):
        return _err(403, "owner_only", f"only the dashboard owner can change {what}")
    body = await _body(request)
    if body is None:
        return _err(400, "bad_body", "body must be a JSON object")
    new, errors = mod.validate(body, await asyncio.to_thread(mod.read))
    if errors:
        return _err(400, f"bad_{what}", "; ".join(errors))
    try:
        await asyncio.to_thread(mod.write, new)
    except Exception as exc:  # noqa: BLE001 - no vault outside a gateway
        return _err(503, "no_vault", f"{what} not saved: {type(exc).__name__}")
    return web.json_response({"ok": True, what: show(new)})


async def _settings_post(request, ctx):
    return await _save(request, settings, "settings", settings.view)


async def _signals(request, ctx):
    rows = await asyncio.to_thread(store.read_signals)
    return web.json_response({"ok": True, "signals": store.by_heat(rows)})


async def _proposals(request, ctx):
    return web.json_response({"ok": True, "proposals": await asyncio.to_thread(store.read_proposals)})


async def _decide(request, ctx):
    if not _owner(request):
        return _err(403, "owner_only", "only the dashboard owner can decide")
    body = await _body(request) or {}
    pid, decision = body.get("proposal_id"), body.get("decision")
    if decision not in store.DECISIONS:
        return _err(400, "bad_decision", "decision must be do, skip or later")
    card = next((p for p in await asyncio.to_thread(store.read_proposals) if p["id"] == pid), None)
    if card is None:
        return _err(404, "unknown_proposal", "no such proposal")
    out = {"ok": True, "appended": await asyncio.to_thread(store.append_decision, pid, decision)}
    if decision == "do" and (row := await dispatch.on_do(request, card)) is not None:
        out["dispatch"] = row
    return web.json_response(out)


def _job_view():
    return {k: v for k, v in JOB.items() if k != "task"}


async def _github_job():
    try:
        rows, error = await asyncio.to_thread(github_rows)
        if rows:
            await asyncio.to_thread(store.refresh, [rows])
        JOB.update(rows=len(rows), error=error)
    except Exception as exc:  # noqa: BLE001 - the job must always report and release
        JOB.update(rows=None, error=f"github: {type(exc).__name__}")
    finally:
        JOB.update(running=False, finished_at=time.time(), task=None)


async def _refresh(request, ctx):
    if not _owner(request):
        return _err(403, "owner_only", "only the dashboard owner can refresh")
    if round_job.STATE["running"]:  # the round rewrites signals.jsonl when it ends
        return _err(409, "round_running", "a round is running; refresh when it ends")
    slack, note = await asyncio.to_thread(slack_rows)
    total, added = await asyncio.to_thread(store.refresh, [slack])
    if not JOB["running"]:  # single-flight: a refresh during a run only reports it
        JOB.update(running=True, started_at=time.time(), finished_at=None, rows=None, error="")
        JOB["task"] = asyncio.get_running_loop().create_task(_github_job())
    return web.json_response({"ok": True, "total": total, "added": added, "errors": [note] if note else [], "github": _job_view()})


def score_run(kc):
    """``(updated rows, error)`` from one ``python -m backend.autoscore`` run (exit 3: rate limit)."""
    try:
        r = subprocess.run([sys.executable, "-m", "backend.autoscore", "--kirocrew", kc], cwd=store.ROOT,
                           capture_output=True, text=True, timeout=SCORE_TIMEOUT_S)
        return (json.loads(r.stdout)["updated"], "") if r.returncode == 0 else (None, f"score: exit {r.returncode}")
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
        return None, f"score: {type(exc).__name__}"


async def _score_job(kc):
    try:
        SCORE.update(zip(("updated", "error"), await asyncio.to_thread(score_run, kc)))
    except Exception as exc:  # noqa: BLE001 - the job must always report and release
        SCORE.update(updated=None, error=f"score: {type(exc).__name__}")
    finally:
        SCORE.update(running=False, finished_at=time.time(), task=None)


def start_score(kc):
    """Start the scoring job unless one runs (single-flight); True when started."""
    if SCORE["running"]:
        return False
    SCORE.update(running=True, started_at=time.time(), finished_at=None, updated=None, error="")
    SCORE["task"] = asyncio.get_running_loop().create_task(_score_job(kc))
    return True


async def _outcomes(request, ctx):
    score = {k: v for k, v in SCORE.items() if k != "task"}
    outcomes, sent, seen = await asyncio.to_thread(lambda: (ledger.rows(), dispatch.rows(), ledger.integrity()))
    ci = await asyncio.to_thread(outcome_checks.read, outcomes)
    return web.json_response({"ok": True, "outcomes": outcomes, "score": score, "dispatches": sent, "ci": ci, **seen})


async def _link(request, ctx):
    if not _owner(request):
        return _err(403, "owner_only", "only the dashboard owner can link a PR")
    body = await _body(request) or {}
    if not (isinstance(n := body.get("pr"), int) and not isinstance(n, bool) and 0 < n < 10**7):
        return _err(400, "bad_pr", "pr must be a KiroCrew pull request number")
    if body.get("proposal_id") not in {p["id"] for p in await asyncio.to_thread(store.read_proposals)}:
        return _err(404, "unknown_proposal", "no such proposal")
    row = await asyncio.to_thread(ledger.link, body["proposal_id"], f"kirodotdev/KiroCrew#{n}", "board")
    return web.json_response({"ok": True, "outcome": row})


async def _score_post(request, ctx):
    if not _owner(request):
        return _err(403, "owner_only", "only the dashboard owner can start scoring")
    if not (kc := (await asyncio.to_thread(schedule.read))["kirocrew_dir"]):
        return _err(409, "no_kirocrew", "set the KiroCrew clone on the Settings tab first")
    if not start_score(kc):
        return _err(409, "score_running", "scoring is already running")
    return await _outcomes(request, ctx)


def regress_runs():
    """Stored post-merge regression runs, newest first; an unreadable or unsigned file is left out."""
    runs = []
    for path in (store.data_dir() / "regress").glob("*.json"):
        try:
            r = seal.read_run(path)
            runs.append({**{k: r[k] for k in ("sha", "at", "baseline", "counts", "regressions")}, "errors": r.get("errors", [])})
        except (KeyError, TypeError):  # None: unsigned or tampered
            continue
    return sorted(runs, key=lambda r: r["at"], reverse=True)


async def _regress(request, ctx):
    return web.json_response({"ok": True, "runs": await asyncio.to_thread(regress_runs)})


async def _refresh_status(request, ctx):
    return web.json_response({"ok": True, "github": _job_view()})


async def _round_run(request, ctx):
    if not _owner(request):
        return _err(403, "owner_only", "only the dashboard owner can run a round")
    body = await _body(request) or {}
    rnd = body["round"] if "round" in body else await asyncio.to_thread(round_job.next_round)
    if not (isinstance(rnd, int) and not isinstance(rnd, bool) and 1 <= rnd <= 9999):
        return _err(400, "bad_round", "round must be 1-9999")
    if JOB["running"]:
        return _err(409, "refresh_running", "a GitHub refresh is running; run the round when it ends")
    started = CLOCK()
    if (task := round_job.start(rnd, round_sources())) is None:
        return _err(409, "round_running", "a round is already running")
    _hold(_record_manual(task, started))
    return web.json_response({"ok": True, "round": round_job.view()}, status=202)


def _hold(job):
    """Run ``job`` as a task held in ``SCHEDULED`` until it ends."""
    task = asyncio.get_running_loop().create_task(job)
    SCHEDULED.add(task)
    task.add_done_callback(SCHEDULED.discard)


async def _record_manual(task, started):
    """A manual round's run row, so Home's average covers it; the weekly rule reads only ``round`` rows."""
    counts, error = await task
    await asyncio.to_thread(schedule.record, "manual_round", started, CLOCK(), signals=counts.get("signals"),
                            cards=counts.get("proposals", 0), error=error)


async def _round_status(request, ctx):
    return web.json_response({"ok": True, "round": round_job.view()})


async def _schedule_get(request, ctx):
    conf, runs, stats = await asyncio.to_thread(lambda: (schedule.read(), schedule.runs(), schedule.round_stats()))
    now = CLOCK()
    return web.json_response({"ok": True, "schedule": conf, "runs": runs, "next_round_at": schedule.next_round_at(conf, now),
                              "round_stats": stats, "read_at": now.isoformat(timespec="seconds")})


async def _schedule_post(request, ctx):
    return await _save(request, schedule, "schedule", dict)


async def _schedule_tick(request, ctx):
    """Called hourly by the app cron; starts only what the owner turned on and is due."""
    conf = await asyncio.to_thread(schedule.read)
    rnd = await asyncio.to_thread(round_job.next_round) if conf["round_enabled"] else None
    app = getattr(request, "app", None) or getattr(ctx, "http_app", None)
    state = app.get("state") if hasattr(app, "get") else None

    start = lambda: None if JOB["running"] else round_job.start(rnd, round_sources())  # noqa: E731
    out, jobs = schedule.tick(CLOCK, conf, round_job.STATE["running"] or JOB["running"], start,
                              schedule.run_regress, lambda *note: schedule.push(state, *note))
    on = conf["score_enabled"] and conf["kirocrew_dir"]
    out["score"] = "off" if not on else "started" if start_score(conf["kirocrew_dir"]) else "busy"
    for job in jobs:
        _hold(job)
    return web.json_response({"ok": True, **out})


async def _dispatch_get(request, ctx):
    return web.json_response({"ok": True, "dispatch": await asyncio.to_thread(dispatch.read)})


async def _dispatch_post(request, ctx):
    return await _save(request, dispatch, "dispatch", dict)


MAX_START = 50


async def _dispatch_start(request, ctx):
    """Owner-only Dispatch: ``{proposal_ids: [...]}`` -> one result per distinct id (``dispatch.start``)."""
    if not _owner(request):
        return _err(403, "owner_only", "only the dashboard owner can dispatch")
    ids = (await _body(request) or {}).get("proposal_ids")
    if not (isinstance(ids, list) and 0 < len(ids) <= MAX_START and all(isinstance(i, str) for i in ids)):
        return _err(400, "bad_ids", f"proposal_ids must be 1-{MAX_START} proposal ids")
    results = await dispatch.start(request, list(dict.fromkeys(ids)))
    return web.json_response({"ok": True, "results": results, "dispatches": await asyncio.to_thread(dispatch.rows)})


async def _team(request, ctx):
    return web.json_response({"ok": True, "team": await asyncio.to_thread(team.view)})


async def _prompt_changes(request, ctx):
    return web.json_response({"ok": True, "changes": await asyncio.to_thread(prompt_changes.read)})


async def _prompt_decide(request, ctx):
    if not _owner(request):
        return _err(403, "owner_only", "only the dashboard owner can change a crew prompt")
    body = await _body(request) or {}
    if body.get("decision") not in store.DECISIONS:
        return _err(400, "bad_decision", "decision must be do, skip or later")
    try:
        card = await asyncio.to_thread(prompt_changes.decide, body.get("id"), body["decision"])
    except LookupError:
        return _err(404, "unknown_change", "no such prompt change")
    except ValueError as exc:
        return _err(409, "not_applied", str(exc))
    return web.json_response({"ok": True, "change": card})


def register_routes(ctx):
    """Named by ``backend.hooks.routes``; the host calls it only for an enabled app. Seals the data dir once (idempotent)."""
    from kiro_crew.apps.route_registry import AppRoute

    try:
        seal.migrate()
    except (seal.SealError, OSError, ValueError) as exc:  # the store stays as it was; reads still fail closed
        print(f"harness-rsi: seal migrate failed: {type(exc).__name__}", file=sys.stderr)

    return [
        AppRoute(method="GET", path="/signals", handler=_signals),
        AppRoute(method="GET", path="/proposals", handler=_proposals),
        AppRoute(method="POST", path="/decisions", handler=_decide),
        AppRoute(method="POST", path="/refresh", handler=_refresh),
        AppRoute(method="GET", path="/refresh/status", handler=_refresh_status),
        AppRoute(method="GET", path="/settings", handler=_settings_get),
        AppRoute(method="POST", path="/settings", handler=_settings_post),
        AppRoute(method="GET", path="/regress", handler=_regress),
        AppRoute(method="POST", path="/round/run", handler=_round_run),
        AppRoute(method="GET", path="/round/status", handler=_round_status),
        AppRoute(method="GET", path="/schedule", handler=_schedule_get),
        AppRoute(method="POST", path="/schedule", handler=_schedule_post),
        AppRoute(method="POST", path="/schedule/tick", handler=_schedule_tick),
        AppRoute(method="GET", path="/outcomes", handler=_outcomes),
        AppRoute(method="POST", path="/outcomes/link", handler=_link),
        AppRoute(method="POST", path="/score/run", handler=_score_post),
        AppRoute(method="GET", path="/dispatch", handler=_dispatch_get),
        AppRoute(method="POST", path="/dispatch", handler=_dispatch_post),
        AppRoute(method="POST", path="/dispatch/start", handler=_dispatch_start),
        AppRoute(method="GET", path="/prompt-changes", handler=_prompt_changes),
        AppRoute(method="POST", path="/prompt-changes/decide", handler=_prompt_decide),
        AppRoute(method="GET", path="/team", handler=_team),
    ]
