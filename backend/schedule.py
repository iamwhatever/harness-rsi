"""The owner's opt-in weekly round and daily post-merge regress, both off by default.

The hourly app cron posts ``/schedule/tick``; :func:`tick` starts what is on and due. Switches
live in the vault (only the owner-only ``POST /schedule`` writes them); runs in ``schedule-runs.jsonl``.
Out goes one note per round, one critical note per regression, one note when regression exams could
not run; no Slack post, PR or merge.
"""

import asyncio
import datetime as dt
import json
import os
import subprocess
import sys

from . import settings, store

APP, VAULT_NAME, RUNS, BOARD_URL = "harness-rsi", "harness-rsi.schedule", "schedule-runs.jsonl", "/apps/harness-rsi"
CHANNELS = {"rounds": "default", "regressions": "critical"}  # as app.json declares them
DEFAULTS = {"round_enabled": False, "regress_enabled": False, "weekday": 0, "hour": 9, "kirocrew_dir": ""}
REGRESS = {"running": False}


def validate(patch, current):
    """``(conf, errors)``: ``patch`` over ``current``; any error refuses the write."""
    new = {**current, **{k: patch[k] for k in DEFAULTS if k in patch}}
    errors = [f"{k} must be true or false" for k in DEFAULTS if k.endswith("_enabled") and type(new[k]) is not bool]
    errors += [f"{k} must be 0-{top}" for k, top in (("weekday", 6), ("hour", 23)) if type(new[k]) is not int or not 0 <= new[k] <= top]
    if not (isinstance(d := new["kirocrew_dir"], str) and len(d) <= 400 and (not d or os.path.isabs(d))):
        errors.append("kirocrew_dir must be an absolute path to a KiroCrew clone")
    return new, errors


def read():
    """The owner's schedule; no vault (tests, a bare CLI) or a bad value reads as all off."""
    try:
        secret = settings._vault().get(VAULT_NAME)
        new, errors = validate(json.loads(secret.reveal()) if secret is not None else {}, DEFAULTS)
    except Exception:  # noqa: BLE001 - unreadable reads as off
        return DEFAULTS
    return DEFAULTS if errors else new


def write(conf):
    settings._vault().set_sync(VAULT_NAME, json.dumps({k: conf[k] for k in DEFAULTS}))


def runs(limit=5, kind=None):
    """The newest ``limit`` run records (of ``kind``), newest first."""
    return [r for r in store._jsonl(RUNS)[::-1] if isinstance(r, dict) and kind in (None, r.get("kind"))][:limit]


def record(kind, started, end, **row):
    (path := store.data_dir() / RUNS).parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": kind, "start": started.isoformat(timespec="seconds"),
                             "end": end.isoformat(timespec="seconds"), "cost": None, **row}) + "\n")


def _last_start(kind):
    return next((dt.datetime.fromisoformat(r["start"]) for r in runs(1, kind)), None)


def round_due(conf, now, busy):
    """Why no scheduled round starts now, or ``""``: one a week, at least 6 days apart."""
    slot = (now - dt.timedelta(days=(now.weekday() - conf["weekday"]) % 7)).replace(
        hour=conf["hour"], minute=0, second=0, microsecond=0)
    slot -= dt.timedelta(days=7 if slot > now else 0)
    start = _last_start("round")
    return ("off" if not conf["round_enabled"] else "busy" if busy else "done this week" if start and start >= slot
            else "ran under 6 days ago" if start and now - start < dt.timedelta(days=6) else "")


def regress_due(conf, now):
    start = _last_start("regress")
    return ("off" if not (conf["regress_enabled"] and conf["kirocrew_dir"]) else "busy" if REGRESS["running"]
            else "not due" if now.hour < conf["hour"] or (start and start.date() == now.date()) else "")


def run_regress(kirocrew_dir):
    """``python -m judge.regress --since-last``: ``(summary, error)``."""
    try:
        r = subprocess.run([sys.executable, "-m", "judge.regress", "--since-last", "--kirocrew", kirocrew_dir],
                           cwd=store.ROOT, capture_output=True, text=True, timeout=3 * 3600)
        summary = json.loads(r.stdout) if r.returncode in (0, 1) else None
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return None, f"regress: {type(exc).__name__}"
    return (summary, "") if isinstance(summary, dict) else (None, f"regress: exit {r.returncode}")


def push(state, channel, title, body):
    """One note on a declared channel through the gateway bus and its rate limit; False if not sent."""
    bus, limiter, full = getattr(state, "notification_bus", None), getattr(state, "notification_rate_limiter", None), f"{APP}.{channel}"
    try:
        from kiro_crew.notifications.bus import NotificationPayload
        if bus is None or (limiter is not None and not limiter.allow(APP)):
            return False
        if not bus.is_registered(full):
            bus.register_channel(full, CHANNELS[channel])
        bus.push(NotificationPayload(source=f"app:{APP}", channel=full, title=title, body=body, url=BOARD_URL))
        return True
    except Exception:  # noqa: BLE001 - a lost note must not lose the run record
        return False


async def _round(clock, started, done, notify):
    counts, error = await done
    record("round", started, clock(), signals=counts.get("signals"), cards=(n := counts.get("proposals", 0)), error=error)
    notify("rounds", *(("Harness RSI round failed", error) if error else
                       (f"Harness RSI: {n} new card(s)", f"{n} card(s) to pick from on the board.")))


async def _regress(clock, started, conf, regress, notify):
    try:
        summary, error = await asyncio.to_thread(regress, conf["kirocrew_dir"])
    finally:
        REGRESS["running"] = False
    found, unrun = ((summary or {}).get(k) or [] for k in ("regressions", "errors"))
    record("regress", started, clock(), sha=(summary or {}).get("sha"), regressions=len(found), errors=len(unrun), error=error)
    if found:
        names = ", ".join(r.get("exam_id") or f"metrics {r.get('a')}/{r.get('b')}" for r in found)
        notify("regressions", "Harness RSI: regression after merge", f"KiroCrew {summary['sha']}: {names}")
    if unrun:  # an exam this host cannot run (e.g. no Playwright in the gateway's Python) is not a pass
        notify("rounds", f"Harness RSI: {len(unrun)} regression exam(s) could not run",
               f"KiroCrew {summary['sha']}: {', '.join(unrun)}. Not a regression: this host cannot run them (see Regress on the board).")


def tick(clock, conf, busy, start_round, regress, notify):
    """``(reasons, coroutines to run)``; ``start_round()`` is an awaitable of ``(counts, error)`` or None."""
    now = clock()
    out, jobs = {"round": round_due(conf, now, busy), "regress": regress_due(conf, now)}, []
    if not out["round"]:
        done = start_round()
        out["round"] = "busy" if done is None else "started"
        jobs += [_round(clock, now, done, notify)] if done is not None else []
    if not out["regress"]:
        REGRESS["running"], out["regress"] = True, "started"
        jobs.append(_regress(clock, now, conf, regress, notify))
    return out, jobs
