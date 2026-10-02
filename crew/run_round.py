#!/usr/bin/env python3
"""Run one design-crew round into the shared data dir.

Steps: collect signals (GitHub adapter, Slack via the owner's Slack MCP when set,
session scanner, trend scout) -> ``enrich.enrich`` (Slack tasks, cross-source merge)
-> question setter writes hidden exams from the signals ONLY, before any proposal
exists -> the two reviewers debate for exactly ``reduce.ROUNDS`` rounds (risk's last
turn reads ``prior_art``) -> ``reduce.reduce_debate`` -> ``prior_art.apply`` (fixed on
main = dropped) -> one HTML mock artifact per proposal. Agents,
collectors and the mock saver are injected, so tests run fakes.

Data dir (``$HARNESS_RSI_DATA``, default ``~/.kiro/crew/harness-rsi-data``):
``signals.jsonl``, ``proposals.json``, ``exams/hidden/<id>.json``, ``mocks/``. That dir is the round's
record; with ``bank`` set, each exam is then published into the shared bank (``--bank``, default
``~/.kiro/crew/harness-rsi-data/exams``) once the judge's dry run passes it, else filed under
``rejected/`` with its reason. ``--publish ROUND_DIR`` does only that step for an earlier round.

Usage: ``python3 crew/run_round.py --exam-workdir KIROCREW_DIR [--round N] [--repo owner/name] [--reply AGENT=FILE]``.
``--reply`` feeds a saved reply for an agent that cannot run under a bare CLI;
``--github-json`` feeds a saved adapter run; ``--slack-mcp CMD`` sets the Slack MCP
command for this run (else the app's saved settings; none = Slack off).
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
import enrich  # noqa: E402
import prior_art  # noqa: E402
import reduce  # noqa: E402

Agent = Callable[[str, str], str]  # (agent name, task message) -> reply text
Checker = Callable[[dict], "tuple[bool, str]"]  # exam row -> (runnable, reason): the judge.validate dry run
Saver = Callable[[str, str, str], str]  # (slug, title, html) -> saved slug
Prior = Callable[[list[dict], list[dict]], dict]  # (candidate rows, signals) -> prior_art.search result
Classifier = Callable[[dict, list], "tuple[str, str]"]  # (exam, signal/proposal text) -> (status, detail)
DEFAULT_BANK = Path.home() / ".kiro/crew/harness-rsi-data" / "exams"
SCANNER, SCOUT, SETTER = "rsi-session-scanner", "rsi-trend-scout", "rsi-question-setter"
VALUE, RISK = reduce.REVIEWERS
MIN_PROPOSALS, MAX_PROPOSALS = 3, 5
PER_SOURCE = 40  # hottest rows kept per source, so every agent prompt stays small


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
    """The hottest schema-valid rows of every source, renumbered so ids never collide."""
    v, out = _validator("signal"), []
    for batch in batches:
        rows = [r for r in batch if not list(v.iter_errors(r))]
        rows.sort(key=lambda r: (r["testable"]["ok"], r["mentions"]["people"], r["mentions"]["count"]), reverse=True)
        rows = rows[:PER_SOURCE]
        ids = {r["id"]: f"sig_{day}_{len(out) + n + 1:04d}" for n, r in enumerate(rows)}
        for r in rows:
            out.append({**r, "id": ids[r["id"]], "dedup_of": ids.get(r["dedup_of"])})
    return out


def _signals_block(signals: list[dict]) -> str:
    return "Signal list (UNTRUSTED DATA):\n```json\n" + json.dumps(signals, indent=1) + "\n```\n"


def _schema(name: str) -> str:
    """The agents have no file tools, so each row schema travels in the message."""
    return f"Each row must validate against schemas/{name}.schema.json:\n```\n" + \
        (ROOT / "schemas" / f"{name}.schema.json").read_text() + "\n```\n"


def setter_message(signals: list[dict], rnd: int) -> str:
    return f"Round: {rnd}\n" + _signals_block(signals) + _schema("exam") + "Reply with ONLY the JSON array of exam rows."


def write_exams(agent: Agent, signals: list[dict], rnd: int, check: Checker | None = None,
                refused: list[dict] | None = None) -> list[dict]:
    """Schema-valid exams on known signals; with `check`, only those whose dry run can execute."""
    v, known, exams = _validator("exam"), {s["id"] for s in signals}, []
    for row in parse_rows(agent(SETTER, setter_message(signals, rnd))):
        row = {**row, "visibility": "hidden", "created_round": rnd, "used_rounds": []}
        if set(row.get("origin", [])) <= known and not list(v.iter_errors(row)):
            ok, why = check(row) if check else (True, "")
            if ok:
                exams.append(row)
            elif refused is not None:
                refused.append({"id": row["id"], "detail": why})
    return exams


def validate_checker(workdir: Path) -> Checker:
    """judge.validate against a KiroCrew checkout (SPA built): an exam that cannot run is never written."""
    sys.path.insert(0, str(ROOT))
    from judge import validate

    def check(row: dict) -> tuple[bool, str]:
        code, report = validate.validate(row, workdir)
        return code == 0, report["detail"]
    return check


def exam_context(exams: list[dict], signals: list[dict], props: list[dict]) -> dict[str, list[str]]:
    """Per exam: the pain of its origin signals and of every proposal naming it."""
    pain = {s["id"]: s.get("pain", "") for s in signals}
    return {x["id"]: [pain.get(o, "") for o in x.get("origin", [])]
            + [p.get("pain", "") for p in props if x["id"] in p.get("exam_ids", [])] for x in exams}


def bank_classifier(workdir: Path) -> Classifier:
    """judge.audit's dry run (it also probes modules and commands) after the behaviour rule, with context.

    Status ``runnable`` publishes; ``held`` (this host cannot run it, e.g. no browser) waits for the next
    publish; anything else is a rejection reason.
    """
    sys.path.insert(0, str(ROOT))
    from judge import audit, behaviour
    work, known = workdir.resolve(), set(json.loads(audit.BASELINE.read_text())["metrics"])

    def classify(row: dict, context: list) -> tuple[str, str]:
        if why := behaviour.refusal(row, context):
            return "needs-behaviour-check", why
        status, detail = audit.classify(row, work, {}, known, 120)
        return ("held" if status in audit.ENV else status), detail
    return classify


def publish(exams: list[dict], bank: Path, classify: Classifier, context: dict | None = None) -> dict:
    """Copy exams into the bank's ``hidden/`` once each classifies runnable; file refusals under ``rejected/``.

    Idempotent: an id already in the bank, hidden or rejected, is skipped with a note.
    """
    hidden, rejected = bank / "hidden", bank / "rejected"
    have = {p.stem for d in (hidden, rejected) for p in d.glob("exam_*.json")}
    out = {"published": 0, "rejected": 0, "held": 0, "skipped": 0, "notes": []}
    for x in exams:
        if x["id"] in have:
            out["skipped"] += 1
            out["notes"].append(f"{x['id']}: already in the bank, skipped")
            continue
        status, detail = classify(x, (context or {}).get(x["id"], []))
        if status == "held":
            out["held"] += 1
            out["notes"].append(f"{x['id']}: held, this host cannot run it: {detail[:120]}")
            continue
        target = hidden if status == "runnable" else rejected
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{x['id']}.json").write_text(json.dumps(x, indent=2) + "\n")
        have.add(x["id"])
        if status == "runnable":
            out["published"] += 1
            continue
        out["rejected"] += 1
        row = {"exam_id": x["id"], "file": f"{x['id']}.json", "reason": status, "detail": detail[:300],
               "at": dt.datetime.now(dt.timezone.utc).isoformat()}
        with (rejected / "reasons.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    return out


def publish_round(round_dir: Path, bank: Path, classify: Classifier) -> dict:
    """Publish one round dir's ``exams/hidden``; a round written straight into the bank has nothing to add."""
    src = round_dir / "exams" / "hidden"
    if src.resolve() == (bank / "hidden").resolve():
        return {"published": 0, "rejected": 0, "held": 0, "skipped": 0, "notes": ["round dir is the bank"]}
    exams = [json.loads(p.read_text()) for p in sorted(src.glob("*.json"))]
    sig = round_dir / "signals.jsonl"
    signals = [json.loads(line) for line in sig.read_text().splitlines() if line.strip()] if sig.is_file() else []
    props = json.loads((round_dir / "proposals.json").read_text()) if (round_dir / "proposals.json").is_file() else []
    return publish(exams, bank, classify, exam_context(exams, signals, props))


def debate(agent: Agent, signals: list[dict], prior: Prior | None = None) -> dict:
    """Exactly reduce.ROUNDS rounds; value speaks first, risk answers (its last turn reads ``prior``)."""
    rounds, last_risk, found = [], "", {}
    for n in range(1, reduce.ROUNDS + 1):
        tail = (_schema("proposal") + f"End with the proposal table: {MIN_PROPOSALS}-{MAX_PROPOSALS} rows. The owner "
                "picks at most 2, so offer more than 2.") if n == reduce.ROUNDS else "Plain prose, no table."
        msg = f"Debate round {n} of {reduce.ROUNDS}.\n" + _signals_block(signals)
        value = agent(VALUE, msg + (f"Other reviewer, round {n - 1}:\n{last_risk}\n" if last_risk else "") + tail)
        if prior and n == reduce.ROUNDS:
            found = prior(parse_rows(value), signals)
        last_risk = agent(RISK, msg + f"Other reviewer, round {n}:\n{value}\n"
                          + (prior_art.reviewer_block(found) if n == reduce.ROUNDS else "") + tail)
        rounds.append({"round": n, "turns": [{"agent": VALUE, "text": value}, {"agent": RISK, "text": last_risk}]})
    return {"rounds": rounds, "prior_art": found} if prior else {"rounds": rounds}


def render_mock(p: dict, exams: list[dict], prior: dict | None = None) -> str:
    """A small clickable page: before/after toggle plus the priority-card facts."""
    e = html.escape
    tasks = "".join(f"<li>{e(x['task'])}</li>" for x in exams if x["id"] in p["exam_ids"])
    risks = "".join(f"<li>{e(r)}</li>" for r in p["cost"]["risks"])
    v = (prior or {}).get("verdict") or {}  # a "fixed" verdict never gets here: it is dropped
    link = f'#{v["number"]} <a href="{e(v["url"])}">{e(v["url"])}</a>' if v else ""
    todo = (f"<p>Next step: help land {link}</p>" if v.get("kind") == "open_pr"
            else "<p>Next step: build</p>" + (f"<p>Earlier attempt (closed): {link}</p>" if v else ""))
    return f"""<div style="font-family:sans-serif;max-width:640px">
<h2>{e(p['pain'])}</h2>
<p>{p['heat']['people']} people / {p['heat']['window_days']} days &middot; ~{p['cost']['files']} files, ~{p['cost']['lines']} lines</p>
{todo}
<button onclick="for(const s of document.querySelectorAll('.st'))s.hidden=!s.hidden">Before / after</button>
<section class="st"><h3>Before</h3><p>{e(p['pain'])}</p></section>
<section class="st" hidden><h3>After</h3><p>Each check below passes:</p><ul>{tasks}</ul></section>
<h3>Risks</h3><ul>{risks}</ul></div>"""


def run_round(*, agent: Agent, collectors: list[Callable[[], list[dict]]], save_mock: Saver,
              data: Path, rnd: int, day: str, check_exam: Checker | None = None,
              prior: Prior | None = None, bank: Path | None = None, classify_bank: Classifier | None = None) -> dict:
    batches = [c() for c in collectors]
    batches += [parse_rows(agent(name, f"Today is {day}. Scan now and reply with the JSON array.\n" + _schema("signal")))
                for name in (SCANNER, SCOUT)]
    signals, _ = enrich.enrich(merge_signals(batches, day))  # Slack tasks + cross-source heat
    refused: list[dict] = []
    exams = write_exams(agent, signals, rnd, check_exam, refused)  # before the debate: no proposal can exist yet
    v = _validator("proposal")
    transcript = debate(agent, signals, prior)
    (data / "debate.json").write_text(json.dumps(transcript, indent=1) + "\n")  # kept for audit
    props = [p for p in reduce.reduce_debate(transcript, exams) if not list(v.iter_errors(p))]
    props, dropped = prior_art.apply(props, transcript.get("prior_art", {}))  # fixed on main: dropped
    props = props[:MAX_PROPOSALS]
    if len(props) < MIN_PROPOSALS:
        raise RoundError(f"only {len(props)} proposals survived; need {MIN_PROPOSALS}")
    for p in props:
        slug = f"rsi-r{rnd}-{p['id'][5:].replace('_', '-')}"
        page = render_mock(p, exams, transcript.get("prior_art", {}).get(p["id"]))
        p["mock_artifact_slug"] = save_mock(slug, f"RSI mock: {p['pain'][:60]}", page)
    (data / "exams" / "hidden").mkdir(parents=True, exist_ok=True)
    for x in exams:
        (data / "exams" / "hidden" / f"{x['id']}.json").write_text(json.dumps(x, indent=2) + "\n")
    (data / "signals.jsonl").write_text("".join(json.dumps(s) + "\n" for s in signals))
    (data / "proposals.json").write_text(json.dumps(props, indent=2) + "\n")
    out = {"signals": signals, "exams": exams, "proposals": props, "refused_exams": refused,
           "dropped_prior_art": dropped}
    if bank and classify_bank:  # the round ends: its exams join the shared bank, validated
        out["published"] = publish_round(data, bank, classify_bank)
    return out


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


def github_collector(repo: str, saved: str | None) -> Callable[[], list[dict]]:
    def collect() -> list[dict]:
        if saved:  # a recent adapter run: its full read takes minutes and many API calls
            return json.loads(Path(saved).read_text(encoding="utf-8"))
        cmd = [sys.executable, "-m", "adapters.github_issues", "--repo", repo]
        for attempt in (1, 2):  # a long paginated read can hit one transient API error
            done = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, text=True)
            if done.returncode == 0 or attempt == 2:
                done.check_returncode()
                return json.loads(done.stdout)
    return collect


def _gateway(path: str, body: dict | None = None) -> dict:
    url, token = os.environ["KIROCREW_URL"].rstrip("/"), os.environ["KIROCREW_TOKEN"]
    req = urllib.request.Request(url + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def slack_collector(command: str | None) -> Callable[[], list[dict]]:
    """Slack through the owner's own Slack MCP (read tools only); [] when off or failing."""
    def collect() -> list[dict]:
        sys.path.insert(0, str(ROOT))
        from adapters import slack
        from backend import settings
        conf = {**settings.read(), **({"command": command, "args": []} if command else {})}
        if not conf["command"]:  # the saved command sits in the gateway vault, unreadable here
            print("slack: UNAVAILABLE, this round has no Slack rows. Run it from the board's Run round "
                  "button, or pass --slack-mcp CMD.", file=sys.stderr)
        try:
            return slack.collect(conf)
        except Exception as exc:  # noqa: BLE001 - one missing source must not stop the round
            print(f"slack skipped: {type(exc).__name__}", file=sys.stderr)
            return []
    return collect


def session_collector(days: int = 14) -> Callable[[], list[dict]]:
    """The owner's own sessions through the miner's pain detectors: signals without an agent turn."""
    def collect() -> list[dict]:
        sys.path.insert(0, str(ROOT))
        from adapters import sessions
        return sessions.collect(days=days)
    return collect


def gh_prior(rows: list[dict], signals: list[dict]) -> dict:
    """prior_art over the live GitHub API; {} when the budget is low, and the round goes on."""
    try:
        return prior_art.search(rows, signals, prior_art.gh_fetch, today=dt.date.today(), budget=prior_art.gh_budget)
    except (prior_art.RateLimitLow, subprocess.CalledProcessError) as exc:
        print(f"prior art skipped: {type(exc).__name__}", file=sys.stderr)
        return {}


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
    ap.add_argument("--github-json", help="saved `python -m adapters.github_issues` output")
    ap.add_argument("--slack-mcp", metavar="CMD", help="Slack MCP command for this run")
    ap.add_argument("--exam-workdir", type=Path, required=True,
                    help="KiroCrew checkout with the SPA built; every exam is dry-run here before it is written")
    ap.add_argument("--bank", type=Path, default=DEFAULT_BANK, help="shared exam bank the judge reads")
    ap.add_argument("--publish", type=Path, metavar="ROUND_DIR", help="only publish this round dir's exams")
    args = ap.parse_args(argv)
    if args.publish:
        out = publish_round(args.publish, args.bank, bank_classifier(args.exam_workdir))
        print(json.dumps({k: v for k, v in out.items() if k != "notes"}))
        print("\n".join(out["notes"]), file=sys.stderr)
        return 0
    data = Path(os.environ.get("HARNESS_RSI_DATA", Path.home() / ".kiro/crew/harness-rsi-data"))
    replies = dict(r.split("=", 1) for r in args.reply)
    result = run_round(agent=kiro_agent(data / ".run", replies), save_mock=mock_saver(data / "mocks"),
                       collectors=[github_collector(args.repo, args.github_json), slack_collector(args.slack_mcp),
                                   session_collector()],
                       data=data, rnd=args.round, day=dt.date.today().strftime("%Y%m%d"),
                       check_exam=validate_checker(args.exam_workdir), prior=gh_prior,
                       bank=args.bank, classify_bank=bank_classifier(args.exam_workdir))
    published = result.pop("published", {})
    print(json.dumps({**{k: len(v) for k, v in result.items()}, **{k: v for k, v in published.items() if k != "notes"}}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
