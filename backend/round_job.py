"""One design-crew round run inside the app, so its Slack collector reads the gateway
vault exactly as ``POST /refresh`` does (a bare CLI cannot).

``start`` runs ``crew/run_round.run_round`` in a worker thread, at most one at a time;
``view`` is what ``GET /round/status`` shows. Agents and the mock saver come from the
crew's own wiring; the collectors are passed in by the caller.
"""

import asyncio
import datetime as dt
import functools
import importlib.util
import json
import os
import time
from pathlib import Path

from . import schedule, store

EXAM_ENV = "HARNESS_RSI_EXAM_WORKDIR"
STATE = {"task": None, "running": False, "round": None, "started_at": None, "finished_at": None,
         "counts": None, "notes": [], "error": ""}


@functools.cache
def crew():
    """``crew/run_round.py`` as a module (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("harness_rsi_run_round", store.ROOT / "crew" / "run_round.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_agent(data):
    return crew().kiro_agent(data / ".run", {})


def make_saver(data):
    return crew().mock_saver(data / "mocks")


def make_prior():
    """Prior art over the live GitHub API, as the CLI round uses it."""
    return crew().gh_prior


def exam_workdir():
    """Where exams are dry-run: ``$HARNESS_RSI_EXAM_WORKDIR``, else the schedule's KiroCrew clone."""
    return os.environ.get(EXAM_ENV) or schedule.read()["kirocrew_dir"]


def next_round():
    """One past the newest round that wrote a hidden exam; 1 for a fresh data dir."""
    rounds = [0]
    for path in (store.data_dir() / "exams" / "hidden").glob("*.json"):
        try:
            rounds.append(int(json.loads(path.read_text(encoding="utf-8"))["created_round"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return max(rounds) + 1


def collector(source, notes):
    """A ``() -> rows`` collector over a ``() -> (rows, note)`` source; a note is kept for the board."""
    def collect():
        rows, note = source()
        if note:
            notes.append(note)
        return rows
    return collect


def view():
    return {k: v for k, v in STATE.items() if k != "task"}


def _run(rnd, sources):
    data, notes = store.data_dir(), STATE["notes"]
    data.mkdir(parents=True, exist_ok=True)
    workdir = exam_workdir()
    if not workdir:
        notes.append(f"exams: not dry-run ({EXAM_ENV} unset, no KiroCrew clone in the schedule)")
    return crew().run_round(agent=make_agent(data), save_mock=make_saver(data),
                            collectors=[collector(s, notes) for s in sources], data=data, rnd=rnd,
                            day=dt.date.today().strftime("%Y%m%d"),
                            check_exam=crew().validate_checker(Path(workdir)) if workdir else None,
                            prior=make_prior())


async def _job(rnd, sources):
    try:
        out = await asyncio.to_thread(_run, rnd, sources)
        STATE.update(counts={k: len(v) for k, v in out.items()})
    except Exception as exc:  # noqa: BLE001 - the job must always report and release
        is_round = isinstance(exc, crew().RoundError)
        STATE.update(error=str(exc) if is_round else type(exc).__name__)  # other text may quote a path
    finally:
        STATE.update(running=False, finished_at=time.time(), task=None)
    return STATE["counts"] or {}, STATE["error"]


def start(rnd, sources):
    """Start a round unless one runs (None); the task ends with ``(counts, error)``. Call with no
    ``await`` between the check and this."""
    if STATE["running"]:
        return None
    STATE.update(running=True, round=rnd, started_at=time.time(), finished_at=None, counts=None, notes=[], error="")
    STATE["task"] = asyncio.get_running_loop().create_task(_job(rnd, sources))
    return STATE["task"]
