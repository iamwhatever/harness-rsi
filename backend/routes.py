"""Harness RSI HTTP API under ``/api/apps/harness-rsi``: two reads and one owner-only write.

Nothing here runs on its own: no startup hook, no loop, no timer.
"""

import asyncio

from aiohttp import web

from . import store

APP_NAME = "harness-rsi"
MAX_BODY = 64 * 1024


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


def register_routes(ctx):
    """Named by ``backend.hooks.routes``; the host calls it only for an enabled app."""
    from kiro_crew.apps.route_registry import AppRoute

    return [
        AppRoute(method="GET", path="/signals", handler=_signals),
        AppRoute(method="GET", path="/proposals", handler=_proposals),
        AppRoute(method="POST", path="/decisions", handler=_decide),
    ]
