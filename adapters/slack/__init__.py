"""Turn Slack threads into signal rows (schemas/signal.schema.json), read through the
owner's own Slack MCP server.

Only allowlisted channel ids are read. What leaves this module: a keyword gist as the
pain line (never message text), a permalink, and counts. No name, user id or body.

The MCP client speaks JSON-RPC 2.0 over stdio, one message per line, and spawns the
command with a fixed argv (no shell). Every tool call passes :func:`guard` first, so a
tool that is not a listed read, or whose name reads like a write, never reaches it.
"""

import contextlib
import datetime as dt
import itertools
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time

READ_TOOLS = frozenset({"batch_get_conversation_history"})
_WRITE_RE = re.compile(r"post|send|react|edit|update|delete|remove|create|upload|draft|write|set|add|dm|open|mark", re.I)
_ARG_RE = re.compile(r"^[A-Za-z0-9._/~+=:@-]{1,256}$")
_ENV_DENY = ("KIROCREW_INTERNAL_SECRET", "KIROCREW_OWNER_ID", "KIROCREW_TOKEN")


class SlackMcpError(RuntimeError):
    """The server could not be started, answered badly, or reported an error."""


class ToolRefused(SlackMcpError):
    """A tool outside the read allowlist, or one whose name reads like a write."""


def guard(call):
    """Wrap ``call(name, args)`` so only read tools reach it."""
    def checked(name, args):
        if name not in READ_TOOLS or _WRITE_RE.search(name):
            raise ToolRefused(f"refused tool {name!r}")
        return call(name, args)
    return checked


def check_argv(command, args):
    """An error string for an unusable command line, or ''."""
    ok = command and all(isinstance(p, str) and _ARG_RE.match(p) for p in [command, *args])
    return "" if ok else "command and args must be plain words (no spaces or shell syntax)"


def _payload(reply):
    result = reply.get("result") or {}
    text = "".join(b.get("text", "") for b in result.get("content") or [] if isinstance(b, dict))
    value = _json_line(text) if text else None
    if "error" in reply or result.get("isError") or (text and value is None):
        raise SlackMcpError("the Slack MCP reported an error")  # its text may quote Slack: not kept
    return value


@contextlib.contextmanager
def stdio_call(command, args=(), timeout=60.0):
    """Start the server for one refresh and yield its guarded ``call(tool, args)``."""
    args = list(args)
    if check_argv(command, args) or not shutil.which(command):
        raise SlackMcpError(check_argv(command, args) or f"{command!r} not found on PATH")
    env = {k: v for k, v in os.environ.items() if k not in _ENV_DENY}
    proc = subprocess.Popen([shutil.which(command), *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,  # noqa: S603
                            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
    lines, ids = queue.Queue(), itertools.count(1)
    threading.Thread(target=lambda: [*map(lines.put, proc.stdout), lines.put("")], daemon=True).start()

    def send(msg):
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def rpc(method, params):
        mid, deadline = next(ids), time.monotonic() + timeout
        send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        while True:
            try:
                line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                line = ""
            if not line:
                raise SlackMcpError(f"{method}: no answer")
            msg = _json_line(line)
            if isinstance(msg, dict) and msg.get("id") == mid:
                return msg
    try:
        rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "harness-rsi", "version": "0.1.0"}})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        yield guard(lambda name, a: _payload(rpc("tools/call", {"name": name, "arguments": a})))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _json_line(line):
    try:
        return json.loads(line)
    except ValueError:
        return None  # a stray log line


DEFAULT_CHANNELS = ("C0AGA4Y4NP7",)
DEFAULT_WINDOW_DAYS = 14
MAX_PAGES, PAGE_LIMIT, GIST_WORDS = 10, 200, 8
CHANNEL_RE = re.compile(r"^[CG](?=[A-Z0-9]*[0-9])[A-Z0-9]{8,12}$")
_STRIP_RE = re.compile(r"```.*?```|`[^`]*`|<[^>]*>|https?://\S+|\S+@\S+|:[a-z0-9_+-]+:", re.S)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'-]{2,}")
_PAIN_RE = re.compile(r"\?|error|fail|broke|bug|crash|stuck|slow|hang|lost|can't|cannot|doesn't|won't|not work"
                      r"|issue|problem|wrong|timeout|how do|how to|confus", re.I)
_STOP = set("""the and for are but not you your yours with this that these those from have has had was were
will would can could should into onto about there their them they then than what when where which who whom
why how any all our ours out get got just also some such very more most much many been being does did doing
its it's i'm i've i'd let lets like here hey anyone someone else still even only again today yesterday thanks
thank please know need want trying tried using use used way one two new see seems seem make made""".split())


def gist(msg):
    """Up to GIST_WORDS keywords in first-seen order, or '' when too few survive."""
    profile = msg.get("user_profile") or {}
    names = {p for k in ("real_name", "display_name", "name") for p in str(profile.get(k) or "").casefold().split()}
    words = []
    for w in _WORD_RE.findall(_STRIP_RE.sub(" ", str(msg.get("text") or ""))):
        low = w.casefold().strip("'-")
        if not ((w[0].isupper() and not w.isupper()) or low in _STOP or low in names or low in words):
            words.append(low)  # any capitalized word is dropped: it may be a name
    return " ".join(words[:GIST_WORDS]).capitalize() if len(words) >= 3 else ""


def _link(channel, msg, workspace_url):
    return f"{(workspace_url or 'https://slack.com').rstrip('/')}/archives/{channel}/p{msg['ts'].replace('.', '')}"


def _history(call, channels, oldest):
    """``{channel: [top-level messages]}`` for allowlisted channels only."""
    out, pending = {c: [] for c in channels}, {c: "" for c in channels}
    for _ in range(MAX_PAGES):
        if not pending:
            break
        req = [{"channelId": c, "oldest": oldest, "limit": PAGE_LIMIT, **({"cursor": k} if k else {})}
               for c, k in pending.items()]
        nxt = {}
        for entry in call("batch_get_conversation_history", {"channels": req}) or []:
            cid = entry.get("channelId") if isinstance(entry, dict) else None
            if cid not in out:
                continue  # never keep a channel that was not asked for
            result = entry.get("result") or {}
            out[cid] += [m for m in result.get("messages") or [] if isinstance(m, dict)]
            cursor = (result.get("response_metadata") or {}).get("next_cursor")
            if result.get("has_more") and cursor:
                nxt[cid] = cursor
        pending = nxt
    return out


def build_signals(call, channels=DEFAULT_CHANNELS, window_days=DEFAULT_WINDOW_DAYS, workspace_url="", now=None):
    """Signal rows from ``call(tool, args)``; only read tools get through."""
    call, now = guard(call), now or dt.datetime.now(dt.timezone.utc)
    allow = [c for c in dict.fromkeys(channels) if isinstance(c, str) and CHANNEL_RE.match(c)]
    oldest = (now - dt.timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    picked = []
    for cid, msgs in _history(call, allow, oldest).items():
        for m in msgs:
            ts = str(m.get("ts") or "")
            if (not re.match(r"^\d+\.\d+$", ts) or m.get("bot_id") or m.get("subtype")
                    or m.get("thread_ts", ts) != ts or not _PAIN_RE.search(str(m.get("text") or ""))):
                continue
            pain = gist(m)
            if pain:
                people = {m.get("user")} | set(m.get("reply_users") or [])
                picked.append((float(ts), cid, m, pain, people - {None}))
    picked.sort(key=lambda p: p[0])
    rows, per_day, groups = [], {}, []
    for stamp, cid, m, pain, people in picked:
        day = dt.datetime.fromtimestamp(stamp, dt.timezone.utc).strftime("%Y%m%d")
        per_day[day] = per_day.get(day, 0) + 1
        row = {"id": f"sig_{day}_{per_day[day]:04d}", "source": f"slack:{cid}", "links": [_link(cid, m, workspace_url)],
               "pain": pain, "mentions": {"count": 1 + int(m.get("reply_count") or 0), "people": max(len(people), 1),
                                          "window_days": window_days},
               "layer": "real", "testable": {"ok": False, "task": None}, "dedup_of": None}
        keys = set(pain.casefold().split())
        head = next((g for g in groups if len(keys & g[1]) >= 3 and len(keys & g[1]) * 2 >= len(keys | g[1])), None)
        if head:  # same pain: point at the first row and add this thread's counts to it
            row["dedup_of"] = head[0]["id"]
            head[2].update(people)
            head[0]["mentions"]["count"] += row["mentions"]["count"]
            head[0]["mentions"]["people"] = max(len(head[2]), 1)
        else:
            groups.append((row, keys, set(people)))
        rows.append(row)
    return rows


def collect(settings):
    """Rows for the saved settings; [] when the command is empty (Slack collection off)."""
    if not settings.get("command"):
        return []
    with stdio_call(settings["command"], settings.get("args") or []) as call:
        return build_signals(call, settings.get("channels") or DEFAULT_CHANNELS,
                             int(settings.get("window_days") or DEFAULT_WINDOW_DAYS), settings.get("workspace_url", ""))
