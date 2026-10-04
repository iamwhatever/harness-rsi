#!/usr/bin/env python3
"""Offline A/B of one crew prompt: replay saved rounds with A (the current prompt) and B (a variant).

Hard metrics per round and repeat: ``setter_hit_rate`` (exams that run, fail on a merged fix PR's base and pass on
its head, over the exams and uncovered signals sharing a link with the fix card's signals or linking the PR),
``adopt_rate`` (proposals on a pain marked 做 or solved by a merged PR), ``prior_art_fp_rate`` (on a pain whose PR
merged before the round), ``credits_per_turn``. ``judge.core.paired`` reads each both ways. Replies are cached by
prompt version. Usage: ``python3 crew/ab.py --agent AGENT --variant B.md --kirocrew KC [--rounds 3 4 5] [--reps 3]``
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "crew")]
import prompts  # noqa: E402
import reduce  # noqa: E402
import run_round as rr  # noqa: E402
from judge import behaviour, core, png, seal  # noqa: E402

Call = Callable[[str, str, str], "tuple[str, float | None]"]  # (agent, prompt text, message) -> (reply, credits)
FLOOR = {"setter_hit_rate": 0.05, "adopt_rate": 0.05, "prior_art_fp_rate": 0.05}
LOWER = {"prior_art_fp_rate", "credits_per_turn"}
URL = "https://github.com/kirodotdev/KiroCrew/pull/"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.is_file() else []


def outcome_rows(data: Path) -> list[dict]:
    """The signed outcome rows; ValueError when any line is unsigned or tampered, since every score read from it is void."""
    rows, bad = seal.signed_lines(data / "outcomes.jsonl", seal.OUTCOMES)
    if bad:
        raise ValueError(f"outcome ledger has {bad} unsigned or tampered line(s); this round is void")
    return rows


def saved_rounds(data: Path, wanted: list[int]) -> dict[int, Path]:
    return {n: data / "rounds" / f"round-{n}" for n in wanted if (data / "rounds" / f"round-{n}" / "signals.jsonl").is_file()}


def cards(data: Path) -> dict[str, dict]:
    """Card id -> the links of its signals, its latest decision and its merged ledger rows."""
    out, decided = {}, {r["proposal_id"]: r["decision"] for r in _jsonl(data / "decisions.jsonl")}
    for d in [*sorted(data.glob("rounds/round-*")), data]:
        if (d / "proposals.json").is_file():
            links = {s["id"]: set(s["links"]) for s in _jsonl(d / "signals.jsonl")}
            for p in json.loads((d / "proposals.json").read_text(encoding="utf-8")):
                out[p["id"]] = {"links": set().union(*(links.get(i, set()) for i in p["signal_ids"])),
                                "decision": decided.get(p["id"], p.get("decision")), "merged": {}}
    for r in outcome_rows(data):
        if r["card_id"] in out and r["state"] == "merged":
            out[r["card_id"]]["merged"][r["pr"]] = r  # the newest row per PR wins
    return out


def fixes(data: Path) -> list[dict]:
    return [{"pr": r["pr"], "url": URL + r["pr"].split("#")[1], "base": r["base_sha"], "head": r["head_sha"],
             "links": c["links"]} for c in cards(data).values() for r in c["merged"].values() if r["base_sha"] and r["head_sha"]]


def judge_exam(exam: dict, base: Path, head: Path, context: list) -> str:
    """``hit`` when the exam runs, fails on base and passes on head; else why not (for a head failure, its error class)."""
    if behaviour.refusal(exam, context):
        return "refused by behaviour rule"
    got = [core.run_check(exam["check"], {"workdir": t, "shots": t / "shots", "metrics": {}, "round": None,
                                          "comparer": png.diff_ratio})[0] for t in (base, head)]
    if None in got or got[0] or got[1]:
        return "cannot run" if None in got else "passes on base" if got[0] else "hit"
    return "fails on head: " + error_class(exam["check"], head)


def error_class(check: dict, tree: Path) -> str:
    """The last ``*Error`` / ``*Exception`` name an exit-code check prints, else its exit code: a reason, no exam text."""
    if check["kind"] != "exit_code":
        return check["kind"]
    try:
        p = subprocess.run(check["cmd"], cwd=tree, capture_output=True, text=True, timeout=check.get("timeout_s", 120))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return type(exc).__name__
    return (re.findall(r"\b[A-Z][A-Za-z]*(?:Error|Exception)\b", p.stderr + p.stdout) or [f"exit {p.returncode}"])[-1]


def setter_sample(reply: str, signals: list[dict], rnd: int, fx: list[dict], trees: Callable) -> tuple[float | None, Counter]:
    v, pain, why = rr._validator("exam"), {s["id"]: s["pain"] for s in signals}, Counter()
    rows = [{**r, "visibility": "hidden", "created_round": rnd, "used_rounds": []} for r in rr.parse_rows(reply)]
    rows = [r for r in rows if set(r.get("origin", [])) <= set(pain) and not list(v.iter_errors(r))]
    for fix in fx:
        aim = {s["id"] for s in signals if s["testable"]["ok"] and not s["dedup_of"]
               and (set(s["links"]) & fix["links"] or fix["url"] in s["links"])}
        mine = [r for r in rows if set(r["origin"]) & aim]
        if missed := len(aim - {o for r in mine for o in r["origin"]}):
            why["no exam for signal"] += missed
        for r in mine:
            why[judge_exam(r, *trees(fix), [pain[o] for o in r["origin"]])] += 1
    return (why["hit"] / sum(why.values()) if why else None), why


def reviewer_sample(transcript: dict, signals: list[dict], exams: list[dict], deck: dict, merged_at: dict) -> dict:
    try:
        props = [p for p in reduce.reduce_debate(transcript, exams) if not list(rr._validator("proposal").iter_errors(p))]
    except reduce.DebateError:  # an unfinished debate offers nothing
        props = []
    links = {s["id"]: set(s["links"]) for s in signals}
    day = dt.datetime.strptime(signals[0]["id"].split("_")[1], "%Y%m%d").date().isoformat() if signals else ""
    good = set().union(*(c["links"] for c in deck.values() if c["decision"] == "do" or c["merged"]))
    done = {URL + n for n, at in merged_at.items() if at and at[:10] < day}
    mine = [set().union(*(links.get(i, set()) for i in p["signal_ids"])) for p in props]
    n = len(mine) or None
    return {"adopt_rate": n and sum(bool(m & good) for m in mine) / n, "prior_art_fp_rate": n and sum(bool(m & done) for m in mine) / n}


def verdict(metric: str, a: list[float], b: list[float]) -> str:
    if not a or not b:
        return "no data"
    s = -1.0 if metric in LOWER and metric not in core.LOWER_IS_BETTER else 1.0  # paired reads it higher-is-better
    doc = lambda base, cur: {"baseline": {metric: [s * x for x in base]}, "current": {metric: [s * x for x in cur]},  # noqa: E731
                             "noise_floor": {metric: FLOOR.get(metric, 0.0)}, "pairs": [[metric, metric]]}
    return "worse" if not core.paired(doc(a, b))[0]["ok"] else "better" if not core.paired(doc(b, a))[0]["ok"] else "same"


def replay(data: Path, agent: str, texts: dict, call: Call, signals: list[dict], rnd: int, rep: int, facts: str = "") -> dict:
    """One arm's saved (or new) reply for one round and repeat: the setter's turn, or the whole debate."""
    key = texts[agent] if agent == rr.SETTER else "\n".join(texts[r] for r in reduce.REVIEWERS)
    path = data / "ab" / "cache" / ("setter" if agent == rr.SETTER else "reviewers") / prompts.version(key) / f"r{rnd}-{rep}.json"
    if not path.is_file():
        spent = []
        def turn(name: str, message: str) -> str:  # noqa: E306
            reply, credits = call(name, texts[name], message)
            spent.append(credits)
            return reply
        got = turn(rr.SETTER, rr.setter_message(signals, rnd, facts)) if agent == rr.SETTER else rr.debate(turn, signals)
        seal.write_text(path, json.dumps({"reply": got, "credits": None if None in spent else sum(spent), "turns": len(spent)}))
    return seal.read_json(path)


def run(data: Path, agent: str, variant: str, call: Call, trees: Callable, rounds: list[int], reps: int,
        merged_at: dict | None = None, facts: Callable | None = None) -> dict:
    if found := prompts.leaks(variant, prompts.bank(data)):
        raise ValueError(f"variant names {len(found)} exam(s); exams never go into prompts")
    texts = prompts.effective(data)
    arms = {"A": texts, "B": {**texts, agent: variant.rstrip("\n")}}
    deck, fx, samples, why = cards(data), fixes(data), {k: {} for k in arms}, {k: Counter() for k in arms}
    for rnd, d in saved_rounds(data, rounds).items():
        signals = _jsonl(d / "signals.jsonl")
        exams = [seal.read_json(p) for p in sorted((d / "exams" / "hidden").glob("*.json"))]
        for arm, rep in ((a, r) for a in arms for r in range(reps)):
            got = replay(data, agent, arms[arm], call, signals, rnd, rep, facts(signals) if facts else "")
            hit, c = setter_sample(got["reply"], signals, rnd, fx, trees) if agent == rr.SETTER else (None, Counter())
            why[arm].update(c)
            vals = {"setter_hit_rate": hit} if agent == rr.SETTER else reviewer_sample(got["reply"], signals, exams, deck, merged_at or {})
            vals["credits_per_turn"] = got["credits"] / got["turns"] if got["credits"] is not None else None
            for m, x in vals.items():
                samples[arm].setdefault(m, []).extend([] if x is None else [x])
    mean = lambda xs: round(statistics.fmean(xs), 4) if xs else None  # noqa: E731
    metrics = {m: {"A": mean(a), "B": mean(samples["B"][m]), "verdict": verdict(m, a, samples["B"][m]),
                   "samples": {"A": a, "B": samples["B"][m]}} for m, a in samples["A"].items()}
    return {"agent": agent, "A": prompts.version(texts[agent]), "B": prompts.version(variant), "rounds": sorted(saved_rounds(data, rounds)),
            "reps": reps, "metrics": metrics, "failures": {k: dict(v) for k, v in why.items()}, "at": dt.datetime.now(dt.timezone.utc).isoformat()}


def kiro_call(run_dir: Path) -> Call:
    """One kiro-cli turn with the given prompt text; the reply and credits from its stream-json events."""
    def call(name: str, text: str, message: str) -> tuple[str, float | None]:
        spec = {**json.loads((ROOT / "crew" / "agents" / f"{name}.json").read_text(encoding="utf-8")), "prompt": text}
        (run_dir / ".kiro" / "agents").mkdir(parents=True, exist_ok=True)
        (run_dir / ".kiro" / "agents" / f"{name}.json").write_text(json.dumps(spec), encoding="utf-8")
        out = subprocess.run(["kiro-cli", "chat", "--agent", name, "--output-format", "stream-json", "--trust-tools="
                              + ",".join(spec["allowedTools"]), message], cwd=run_dir, capture_output=True, text=True, timeout=1800).stdout
        events = [json.loads(x) for x in out.splitlines() if x.startswith("{")]
        text_out = "".join(e["data"]["update"]["content"].get("text", "") for e in events if e.get("type") == "sessionUpdate"
                           and e["data"].get("update", {}).get("sessionUpdate") == "agent_message_chunk")
        credits = [u["value"] for e in events for u in (e.get("data") or {}).get("meteringUsage", []) if u.get("unit") == "credit"]
        return text_out, (sum(credits) if credits else None)
    return call


def tree_maker(kc: Path, root: Path) -> Callable:
    """``fix -> (base tree, head tree)``: detached worktrees of ``kc`` with the SPA built, made once under ``root``."""
    def tree(sha: str, pr: str) -> Path:
        if not (root / sha).is_dir():
            subprocess.run(["git", "-C", str(kc), "fetch", "-q", "origin", f"pull/{pr.split('#')[1]}/head", "main"], check=True)
            subprocess.run(["git", "-C", str(kc), "worktree", "add", "-q", "--detach", str(root / sha), sha], check=True)
            subprocess.run(["bash", "-c", "cd website && npm ci --no-audit --no-fund && npm run build"], cwd=root / sha,
                           capture_output=True, check=True)
        return root / sha
    return lambda fix: (tree(fix["base"], fix["pr"]), tree(fix["head"], fix["pr"]))


def merged_dates(data: Path) -> dict:  # KiroCrew PR number -> mergedAt, for the ledger's PRs
    nums = sorted({int(r["pr"].split("#")[1]) for r in _jsonl(data / "outcomes.jsonl")})
    q = 'query { repository(owner: "kirodotdev", name: "KiroCrew") { ' + " ".join(f"p{n}: pullRequest(number: {n}) {{ mergedAt }}" for n in nums) + " } }"
    out = subprocess.run(["gh", "api", "graphql", "-f", f"query={q}"], capture_output=True, text=True, check=True).stdout
    return {k[1:]: (v or {}).get("mergedAt") for k, v in json.loads(out)["data"]["repository"].items()}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--agent", required=True, choices=[rr.SETTER, *reduce.REVIEWERS])
    ap.add_argument("--variant", type=Path, required=True, help="the B prompt text (a whole .md)")
    ap.add_argument("--kirocrew", type=Path, required=True, help="KiroCrew clone: fix PR base/head trees come from it")
    ap.add_argument("--rounds", type=int, nargs="+", default=[3, 4, 5])
    ap.add_argument("--reps", type=int, default=3)
    args = ap.parse_args(argv)
    data = Path(os.environ.get("HARNESS_RSI_DATA", Path.home() / ".kiro/crew/harness-rsi-data"))
    out = run(data, args.agent, args.variant.read_text(encoding="utf-8"), kiro_call(data / "ab" / ".run"),
              tree_maker(args.kirocrew, data / "ab" / "trees"), args.rounds, args.reps,
              merged_dates(data) if args.agent != rr.SETTER else {}, rr.setter_facts(args.kirocrew, data / "exams"))
    (data / "ab" / f"{args.agent}-{out['A']}-{out['B']}.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({m: {k: v[k] for k in ("A", "B", "verdict")} for m, v in out["metrics"].items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
