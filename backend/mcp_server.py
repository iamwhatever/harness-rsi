"""Harness RSI app MCP server (stdio): one tool, ``rsi_report_ledger``.

The lead and lane conductors push their own ``work_ledger_read compact=true`` here at the end of
every patrol cycle; ``backend.team`` checks it, caps it and stores it per reporter in the data dir,
and the app page folds the snapshots into the Team tab. The tool writes only ``<data dir>/team/``
and never reads anything back, so a caller learns nothing about the sealed exams or the ledger.
What lands is self-reported: this server cannot see which session called it.

Protocol: JSON-RPC 2.0, one message per line on stdin/stdout (MCP stdio transport). Stdlib only.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import team  # noqa: E402  (sibling module; this file runs as a script)

PROTOCOL_VERSION = "2024-11-05"
_ITEM = {"type": "object", "properties": {k: {} for k in (
    "item_id", "title", "state", "status", "summary", "verdict", "pr", "worker_session_key", "last_report_at",
    "orphaned", "stale")}, "required": ["item_id", "state"]}
TOOLS = [{
    "name": "rsi_report_ledger",
    "description": (
        "Push your work ledger to the Harness RSI Team tab. Call it at the end of every patrol cycle with "
        "role (lead or lane), round, and snapshot = the JSON that work_ledger_read compact=true returned "
        "(the whole object: conductor and items). reporter_session_key defaults to snapshot.conductor.slot_key. "
        "The app keeps one snapshot per reporter, replacing the last. Text fields are cut to their caps; a wrong "
        "type, an unknown state/status/verdict, over 200 items or over 256 KB is refused with the field named. "
        "What you push is shown to the owner as self-reported."),
    "inputSchema": {"type": "object", "properties": {
        "role": {"type": "string", "enum": list(team.ROLES)},
        "round": {"type": "integer", "minimum": 0, "maximum": 9999},
        "reporter_session_key": {"type": "string"},
        "snapshot": {"type": ["object", "string"], "properties": {
            "conductor": {"type": "object"}, "items": {"type": "array", "items": _ITEM, "maxItems": team.MAX_ITEMS}}},
    }, "required": ["role", "snapshot"]},
}]


def _text(obj, is_error=False):
    return {"content": [{"type": "text", "text": json.dumps(obj)}], "isError": is_error}


def report(args):
    try:
        return _text(team.ingest(args))
    except team.Refused as exc:
        return _text({"ok": False, "refused": str(exc)}, is_error=True)


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # a notification (notifications/initialized)
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "harness-rsi", "version": "1"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params") or {}
        if params.get("name") != "rsi_report_ledger":
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}}
        args = params.get("arguments")
        try:
            result = report(args if isinstance(args, dict) else {})
        except Exception as exc:  # noqa: BLE001 - report it, never crash the server
            result = _text({"ok": False, "error": type(exc).__name__}, is_error=True)
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main(stdin=sys.stdin, stdout=sys.stdout):
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(msg) if isinstance(msg, dict) else None
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


if __name__ == "__main__":
    main()
