"""Harness RSI HTTP API under ``/api/apps/harness-rsi``: two reads, two owner-only writes.

``POST /refresh`` takes the Slack Radar export rows the page fetched from
``GET /api/apps/slack-radar/signals`` (the page holds that grant, the backend holds no
token), runs ``python -m adapters.github_issues``, and merges both into signals.jsonl.
Nothing here runs on its own: no startup hook, no loop, no timer.
"""

import asyncio
import json
import subprocess
import sys

from aiohttp import web

from . import store

APP_NAME = "harness-rsi"
MAX_BODY = 2 * 1024 * 1024
GITHUB_TIMEOUT_S = 120
_REFRESH = asyncio.Lock()


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


async def _refresh(request, ctx):
    if not _owner(request):
        return _err(403, "owner_only", "only the dashboard owner can refresh")
    body = await _body(request)
    slack = body.get("slack") if body else None
    if not isinstance(slack, list):
        return _err(400, "bad_body", "body must be {\"slack\": [signal rows]}")
    slack = [r for r in slack if isinstance(r, dict) and str(r.get("source", "")).startswith("slack:")]
    async with _REFRESH:
        github, error = await asyncio.to_thread(github_rows)
        before = await asyncio.to_thread(store.read_signals)
        rows = store.merge(before, [slack, github])
        await asyncio.to_thread(store.write_signals, rows)
    return web.json_response({"ok": True, "total": len(rows), "added": len(rows) - len(before),
                              "errors": [error] if error else []})


def register_routes(ctx):
    """Named by ``backend.hooks.routes``; the host calls it only for an enabled app."""
    from kiro_crew.apps.route_registry import AppRoute

    return [
        AppRoute(method="GET", path="/signals", handler=_signals),
        AppRoute(method="GET", path="/proposals", handler=_proposals),
        AppRoute(method="POST", path="/decisions", handler=_decide),
        AppRoute(method="POST", path="/refresh", handler=_refresh),
    ]
