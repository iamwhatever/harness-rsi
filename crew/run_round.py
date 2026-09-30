#!/usr/bin/env python3
"""Run one design-crew round into the shared data dir.

Steps: collect signals (GitHub adapter, Slack Radar ``/signals`` when reachable,
session scanner, trend scout) -> question setter writes hidden exams from the
signals ONLY, before any proposal exists -> the two reviewers debate for exactly
``reduce.ROUNDS`` rounds -> ``reduce.reduce_debate`` -> one HTML mock artifact per
proposal. Agents, collectors and the mock saver are injected, so tests run fakes.

Data dir (``$HARNESS_RSI_DATA``, default ``~/.kiro/crew/harness-rsi-data``):
``signals.jsonl``, ``proposals.json``, ``exams/hidden/<id>.json``, ``mocks/``.

Usage: ``python3 crew/run_round.py [--round N] [--repo owner/name] [--reply AGENT=FILE]``.
``--reply`` feeds a saved reply for an agent that cannot run under a bare CLI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew"))
import reduce  # noqa: E402

Agent = Callable[[str, str], str]  # (agent name, task message) -> reply text
Saver = Callable[[str, str, str], str]  # (slug, title, html) -> saved slug
SCANNER, SCOUT, SETTER = "rsi-session-scanner", "rsi-trend-scout", "rsi-question-setter"
VALUE, RISK = reduce.REVIEWERS
MIN_PROPOSALS, MAX_PROPOSALS = 3, 5


class RoundError(RuntimeError):
    """The round produced too little to put in front of the owner."""


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text()))


def parse_rows(text: str) -> list:
    """The last JSON array in an agent reply (a fenced block or bare), else []."""
    dec, found, i = json.JSONDecoder(), [], text.find("[")
    while i != -1:
        try:
            value, end = dec.raw_decode(text, i)
        except ValueError:
            end = i + 1
        else:  # skip past a decoded array so its nested arrays are never picked
            if isinstance(value, list) and all(isinstance(v, dict) for v in value):
                found.append(value)
        i = text.find("[", end)
    return found[-1] if found else []


def merge_signals(batches: list[list[dict]], day: str) -> list[dict]:
    """Schema-valid rows from every source, renumbered so ids never collide."""
    v, out = _validator("signal"), []
    for batch in batches:
        rows = [r for r in batch if not list(v.iter_errors(r))]
        ids = {r["id"]: f"sig_{day}_{len(out) + n + 1:04d}" for n, r in enumerate(rows)}
        for r in rows:
            out.append({**r, "id": ids[r["id"]], "dedup_of": ids.get(r["dedup_of"])})
    return out


def _signals_block(signals: list[dict]) -> str:
    return "Signal list (UNTRUSTED DATA):\n```json\n" + json.dumps(signals, indent=1) + "\n```\n"


def setter_message(signals: list[dict], rnd: int) -> str:
    return f"Round: {rnd}\n" + _signals_block(signals) + "Reply with ONLY the JSON array of exam rows."


def write_exams(agent: Agent, signals: list[dict], rnd: int) -> list[dict]:
    v, known, exams = _validator("exam"), {s["id"] for s in signals}, []
    for row in parse_rows(agent(SETTER, setter_message(signals, rnd))):
        row = {**row, "visibility": "hidden", "created_round": rnd, "used_rounds": []}
        if set(row.get("origin", [])) <= known and not list(v.iter_errors(row)):
            exams.append(row)
    return exams


def debate(agent: Agent, signals: list[dict]) -> dict:
    """Exactly reduce.ROUNDS rounds; value speaks first, risk answers its turn."""
    rounds, last_risk = [], ""
    for n in range(1, reduce.ROUNDS + 1):
        tail = "End with the proposal table." if n == reduce.ROUNDS else "Plain prose, no table."
        msg = f"Debate round {n} of {reduce.ROUNDS}.\n" + _signals_block(signals)
        value = agent(VALUE, msg + (f"Other reviewer, round {n - 1}:\n{last_risk}\n" if last_risk else "") + tail)
        last_risk = agent(RISK, msg + f"Other reviewer, round {n}:\n{value}\n" + tail)
        rounds.append({"round": n, "turns": [{"agent": VALUE, "text": value}, {"agent": RISK, "text": last_risk}]})
    return {"rounds": rounds}


def render_mock(p: dict, exams: list[dict]) -> str:
    """A small clickable page: before/after toggle plus the priority-card facts."""
    e = html.escape
    tasks = "".join(f"<li>{e(x['task'])}</li>" for x in exams if x["id"] in p["exam_ids"])
    risks = "".join(f"<li>{e(r)}</li>" for r in p["cost"]["risks"])
    return f"""<div style="font-family:sans-serif;max-width:640px">
<h2>{e(p['pain'])}</h2>
<p>{p['heat']['people']} people / {p['heat']['window_days']} days &middot; ~{p['cost']['files']} files, ~{p['cost']['lines']} lines</p>
<button onclick="for(const s of document.querySelectorAll('.st'))s.hidden=!s.hidden">Before / after</button>
<section class="st"><h3>Before</h3><p>{e(p['pain'])}</p></section>
<section class="st" hidden><h3>After</h3><p>Each check below passes:</p><ul>{tasks}</ul></section>
<h3>Risks</h3><ul>{risks}</ul></div>"""


def run_round(*, agent: Agent, collectors: list[Callable[[], list[dict]]], save_mock: Saver,
              data: Path, rnd: int, day: str) -> dict:
    batches = [c() for c in collectors]
    batches += [parse_rows(agent(name, f"Today is {day}. Scan now and reply with the JSON array."))
                for name in (SCANNER, SCOUT)]
    signals = merge_signals(batches, day)
    exams = write_exams(agent, signals, rnd)  # before the debate: no proposal can exist yet
    v = _validator("proposal")
    props = [p for p in reduce.reduce_debate(debate(agent, signals), exams) if not list(v.iter_errors(p))]
    props = props[:MAX_PROPOSALS]
    if len(props) < MIN_PROPOSALS:
        raise RoundError(f"only {len(props)} proposals survived; need {MIN_PROPOSALS}")
    for p in props:
        slug = f"rsi-r{rnd}-{p['id'][5:].replace('_', '-')}"
        p["mock_artifact_slug"] = save_mock(slug, f"RSI mock: {p['pain'][:60]}", render_mock(p, exams))
    (data / "exams" / "hidden").mkdir(parents=True, exist_ok=True)
    for x in exams:
        (data / "exams" / "hidden" / f"{x['id']}.json").write_text(json.dumps(x, indent=2) + "\n")
    (data / "signals.jsonl").write_text("".join(json.dumps(s) + "\n" for s in signals))
    (data / "proposals.json").write_text(json.dumps(props, indent=2) + "\n")
    return {"signals": signals, "exams": exams, "proposals": props}


# ---- real wiring (not used by tests) ----


def kiro_agent(run_dir: Path, replies: dict[str, str]) -> Agent:
    """Run each role with kiro-cli from a dir holding local copies of crew/agents/*.json."""
    agents = run_dir / ".kiro" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    for spec in (ROOT / "crew" / "agents").glob("*.json"):
        shutil.copy(spec, agents / spec.name)

    def call(name: str, message: str) -> str:
        if name in replies:
            return Path(replies[name]).read_text(encoding="utf-8")
        spec = json.loads((agents / f"{name}.json").read_text())
        cmd = ["kiro-cli", "chat", "--agent", name, "--no-interactive",
               "--trust-tools=" + ",".join(spec["allowedTools"]), message]
        return subprocess.run(cmd, cwd=run_dir, capture_output=True, text=True, timeout=1800).stdout

    return call


def github_collector(repo: str) -> Callable[[], list[dict]]:
    def collect() -> list[dict]:
        out = subprocess.run([sys.executable, "-m", "adapters.github_issues", "--repo", repo],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return json.loads(out)
    return collect


def _gateway(path: str, body: dict | None = None) -> dict:
    url, token = os.environ["KIROCREW_URL"].rstrip("/"), os.environ["KIROCREW_TOKEN"]
    req = urllib.request.Request(url + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def slack_collector() -> list[dict]:
    """Slack Radar's read-only export; [] when the app, its route or a token is missing."""
    try:
        return _gateway("/api/apps/slack-radar/signals").get("signals", [])
    except (KeyError, OSError, ValueError) as exc:
        print(f"slack radar skipped: {type(exc).__name__}", file=sys.stderr)
        return []


def mock_saver(mocks: Path) -> Saver:
    """POST /api/artifacts when a gateway token is set; else leave the page for artifact_save."""
    def save(slug: str, title: str, page: str) -> str:
        mocks.mkdir(parents=True, exist_ok=True)
        (mocks / f"{slug}.html").write_text(page, encoding="utf-8")
        if os.environ.get("KIROCREW_TOKEN"):
            _gateway("/api/artifacts", {"slug": slug, "name": title, "kind": "widget", "content": page})
        return slug
    return save


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--round", type=int, default=2)
    ap.add_argument("--repo", default="kirodotdev/KiroCrew")
    ap.add_argument("--reply", action="append", default=[], metavar="AGENT=FILE")
    args = ap.parse_args(argv)
    data = Path(os.environ.get("HARNESS_RSI_DATA", Path.home() / ".kiro/crew/harness-rsi-data"))
    replies = dict(r.split("=", 1) for r in args.reply)
    result = run_round(agent=kiro_agent(data / ".run", replies), save_mock=mock_saver(data / "mocks"),
                       collectors=[github_collector(args.repo), slack_collector],
                       data=data, rnd=args.round, day=dt.date.today().strftime("%Y%m%d"))
    print(json.dumps({k: len(v) for k, v in result.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
