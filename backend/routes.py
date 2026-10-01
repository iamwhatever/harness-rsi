"""Harness RSI HTTP API under ``/api/apps/harness-rsi``: three reads, four owner-only writes.

``POST /refresh`` reads the allowlisted Slack channels through the owner's own Slack MCP
server (``adapters.slack``; off while no command is set) and merges those rows at once.
It also starts ``python -m adapters.github_issues`` as a
one-shot job (at most one at a time; it takes minutes) whose rows merge when it exits;
``GET /refresh/status`` reports that job. ``GET``/``POST /settings`` show and change the
Slack settings (``backend.settings``). ``POST /round/run`` runs one design-crew round
(``backend.round_job``) with these same Slack and GitHub sources; ``GET /round/status``
reports it. Nothing starts on its own: no startup hook, no timer.
"""

import asyncio
import json
import subprocess
import sys
import time

from aiohttp import web

import adapters.slack

from . import round_job, settings, store

APP_NAME = "harness-rsi"
MAX_BODY = 2 * 1024 * 1024
GITHUB_TIMEOUT_S = 30 * 60
#: The GitHub job: ``task`` is the running asyncio task, the rest is shown on the board.
JOB = {"task": None, "running": False, "started_at": None, "finished_at": None, "rows": None, "error": ""}


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
    """Signal rows from the GitHub adapter, or ``([], error)``."""
    try:
        r = subprocess.run([sys.executable, "-m", "adapters.github_issues"], cwd=store.ROOT,
                           capture_output=True, text=True, timeout=GITHUB_TIMEOUT_S)
        rows = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return [], f"github: {type(exc).__name__}"
    return (rows, "") if isinstance(rows, list) else ([], f"github: adapter exit {r.returncode}")


def slack_rows():
    """Signal rows from the Slack MCP, or ``([], note)``; a note also when collection is off."""
    conf = settings.read()
    if not conf["command"]:
        return [], "slack: off (no Slack MCP command set)"
    try:
        return adapters.slack.collect(conf), ""
    except Exception as exc:  # noqa: BLE001 - a Slack failure must not stop the GitHub refresh
        return [], f"slack: {type(exc).__name__}"


async def _settings_get(request, ctx):
    return web.json_response({"ok": True, "settings": await asyncio.to_thread(settings.read)})


async def _settings_post(request, ctx):
    if not _owner(request):
        return _err(403, "owner_only", "only the dashboard owner can change settings")
    body = await _body(request)
    if body is None:
        return _err(400, "bad_body", "body must be a JSON object")
    new, errors = settings.validate(body, await asyncio.to_thread(settings.read))
    if errors:
        return _err(400, "bad_settings", "; ".join(errors))
    try:
        await asyncio.to_thread(settings.write, new)
    except Exception as exc:  # noqa: BLE001 - no vault outside a gateway
        return _err(503, "no_vault", f"settings not saved: {type(exc).__name__}")
    return web.json_response({"ok": True, "settings": new})


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
    known = {p["id"] for p in await asyncio.to_thread(store.read_proposals)}
    if pid not in known:
        return _err(404, "unknown_proposal", "no such proposal")
    appended = await asyncio.to_thread(store.append_decision, pid, decision)
    return web.json_response({"ok": True, "appended": appended})


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


def regress_runs():
    """Stored post-merge regression runs, newest first; an unreadable file is left out."""
    runs = []
    for path in (store.data_dir() / "regress").glob("*.json"):
        try:
            r = json.loads(path.read_text(encoding="utf-8"))
            runs.append({k: r[k] for k in ("sha", "at", "baseline", "counts", "regressions")})
        except (OSError, ValueError, KeyError, TypeError):
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
    if not round_job.start(rnd, [lambda: github_rows(), lambda: slack_rows()]):  # looked up when the round runs
        return _err(409, "round_running", "a round is already running")
    return web.json_response({"ok": True, "round": round_job.view()}, status=202)


async def _round_status(request, ctx):
    return web.json_response({"ok": True, "round": round_job.view()})


def register_routes(ctx):
    """Named by ``backend.hooks.routes``; the host calls it only for an enabled app."""
    from kiro_crew.apps.route_registry import AppRoute

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
    ]
