"""Score linked KiroCrew PRs with the judge; on merge promote the card's exams and record regress.

``python -m backend.autoscore --kirocrew KC`` reads the 50 newest PRs and every linked one in one
GraphQL call, links a PR whose title or body names a card id, and when a linked PR's head moved runs
the card's exams on its base and head in throwaway worktrees. Fork PRs are linked, never run.
``--pr N [--card ID]`` scores one PR now (back-fill). Exit 0 done, 2 error, 3 rate limit.
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import types

from adapters.github_issues.adapter import DEFAULT_MIN_REMAINING, RateLimitLow, gh_budget
from judge import core, png, regress

from . import ledger, store

REPO = "kirodotdev/KiroCrew"
SPA_BUILD = "cd website && npm ci --no-audit --no-fund && npm run build"
_ID_RE = re.compile(r"\bprop_[a-z0-9_]+")
_FIELDS = "number title body state isCrossRepository baseRefOid headRefOid mergeCommit { oid }"


def cards():
    """Every card from every round, keyed by id; the board's proposals.json wins; latest decision applied."""
    docs = sorted(store.data_dir().glob("rounds/round-*/proposals.json")) + [store.data_dir() / "proposals.json"]
    out = {p["id"]: p for d in docs if d.is_file() for p in json.loads(d.read_text(encoding="utf-8")) if store.valid("proposal", p)}
    latest = {r["proposal_id"]: r["decision"] for r in store.read_decisions()}
    return {i: {**p, "decision": latest.get(i, p["decision"])} for i, p in out.items()}


def round_versions(card_id):
    """The prompt versions of the round that wrote ``card_id`` (its ``prompt_versions.json``), or None."""
    for d in [*sorted(store.data_dir().glob("rounds/round-*")), store.data_dir()]:
        doc, used = d / "proposals.json", d / "prompt_versions.json"
        if card_id and doc.is_file() and used.is_file() and any(
                p.get("id") == card_id for p in json.loads(doc.read_text(encoding="utf-8"))):
            return json.loads(used.read_text(encoding="utf-8"))
    return None


def exams_for(card, bank):
    return [e for e in bank if e["id"] in card["exam_ids"] or set(e.get("origin", [])) & set(card["signal_ids"])]


def fetch(numbers, run=subprocess.run):
    """``{number: pr}`` for the recent PRs and ``numbers``, in one GraphQL call."""
    owner, name = REPO.split("/")
    alias = " ".join(f"p{n}: pullRequest(number: {int(n)}) {{ {_FIELDS} }}" for n in sorted(set(numbers)))
    q = (f'{{ repository(owner: "{owner}", name: "{name}") {{ recent: pullRequests(first: 50, '
         f"orderBy: {{field: UPDATED_AT, direction: DESC}}) {{ nodes {{ {_FIELDS} }} }} {alias} }} }}")
    proc = run(["gh", "api", "graphql", "-f", f"query={q}"], capture_output=True, text=True)
    if proc.returncode:
        raise (RateLimitLow if "rate limit" in proc.stderr.lower() else RuntimeError)(proc.stderr.strip()[:200])
    repo = json.loads(proc.stdout)["data"]["repository"]
    prs = repo.pop("recent")["nodes"] + [v for v in repo.values() if v]
    return {p["number"]: p for p in prs}


def judge_at(kc, sha, exams):
    """One judge run of ``exams`` at ``sha`` in a throwaway worktree: the ledger's run object."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = pathlib.Path(tmp) / "kc"
        regress._git(kc, "worktree", "add", "-q", "--detach", str(tree), sha)
        try:
            if any(e["check"]["kind"] == "dom_assert" for e in exams):
                subprocess.run(["bash", "-c", SPA_BUILD], cwd=tree, capture_output=True, check=True)
            rnd = max(e["created_round"] for e in exams)
            ctx = {"workdir": tree, "shots": tree / "shots", "metrics": {}, "round": rnd, "comparer": png.diff_ratio}
            out = core.judge({"suites": ["hidden"], "exam_ids": [e["id"] for e in exams]}, exams, ctx)
        except (subprocess.CalledProcessError, core.JudgeError) as exc:
            out = {"verdict": "error", "evidence": [{"status": "error", "detail": type(exc).__name__}]}
        finally:
            regress._git(kc, "worktree", "remove", "--force", str(tree))
    status = [e["status"] for e in out["evidence"]]
    verdict = "error" if "error" in status else out["verdict"]
    return {"sha": sha, "verdict": verdict, **{k: status.count(k) for k in ("pass", "fail", "error")}, "at": ledger.now()}


def metrics_for(pr):
    """Paired metric rows from ``$HARNESS_RSI_DATA/metrics/<owner>__<repo>__<n>.json`` (judge.metrics_in), or None."""
    path = store.data_dir() / "metrics" / (pr.replace("/", "__").replace("#", "__") + ".json")
    return core.paired(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else None


def regress_rows(row, ids):
    """The merge commit's regress run and every stored run after it, as entries for this card's exams."""
    good, bad = regress.runs(store.data_dir() / "regress")
    if bad:
        raise core.JudgeError(f"{bad} stored regress run(s) unsigned or tampered; regress for this card is void")
    runs = sorted(good, key=lambda r: r["at"])
    start = next((r["at"] for r in runs if r["sha"] == row["merged_sha"]), None)
    out = []
    for run in (r for r in runs if start and r["at"] >= start):
        errs = set(run.get("errors", []))
        exams = {i: "error" if i in errs else "pass" if run["exams"][i] else "fail" for i in ids if i in run["exams"] or i in errs}
        bad = sum(1 for r in run.get("regressions", []) if r.get("exam_id") in ids)
        out.append({"sha": run["sha"], "at": run["at"], "exams": exams, "regressions": bad})
    return out


def on_merge(kc, row, exams):
    """Promote the card's hidden exams, run the regression suite on the merge commit once."""
    promoted = list(row["promoted"])
    for e in (e for e in exams if e["id"] not in promoted):
        if e["visibility"] == "hidden":
            regress.promote(regress.data_dir() / "exams", e["id"], e["created_round"])
        promoted.append(e["id"])
    if not (store.data_dir() / "regress" / f"{row['merged_sha']}.json").is_file():
        suite = [e for e in regress.load_all(regress.data_dir() / "exams") if e["visibility"] == "regression"]
        build = SPA_BUILD if any(e["check"]["kind"] == "dom_assert" for e in suite) else None
        regress.run(types.SimpleNamespace(exams=str(regress.data_dir() / "exams"), metrics=None, workdir=None, kirocrew=kc,
                                          commit=row["merged_sha"], fetch=False, since_last=False, build=build), regress.default_runner)
    return promoted


def score(kc, row, pr, card, bank):
    """``row`` brought up to date with ``pr``: state, scores when the head moved, merge follow-up."""
    state = "merged" if pr["mergeCommit"] and pr["state"] == "MERGED" else pr["state"].lower()
    new = {**row, "state": state, "base_sha": pr["baseRefOid"], "head_sha": pr["headRefOid"],
           "merged_sha": (pr["mergeCommit"] or {}).get("oid") if state == "merged" else None,
           "decision": card["decision"] if card else None}
    new["prompt_versions"] = round_versions(row["card_id"])
    exams = exams_for(card, bank) if card else []
    new["exam_ids"] = [e["id"] for e in exams]
    new["note"] = "; ".join(n for n in ("" if card else "no card", "" if exams else "no exam",
                                        "fork PR: not run" if pr["isCrossRepository"] else "") if n)
    if exams and not pr["isCrossRepository"]:
        if (row["score"]["head"] or {}).get("sha") != pr["headRefOid"]:
            regress._git(kc, "fetch", "-q", "origin", f"pull/{pr['number']}/head")
            new["score"] = {"base": judge_at(kc, pr["baseRefOid"], exams), "head": judge_at(kc, pr["headRefOid"], exams),
                            "metrics": metrics_for(row["pr"])}
        if state == "merged":
            new["promoted"] = on_merge(kc, new, exams)
            new["regress"] = regress_rows(new, new["exam_ids"])
    return new if {k: v for k, v in new.items() if k != "at"} != {k: v for k, v in row.items() if k != "at"} else row


def tick(kc, only=None, run=subprocess.run, budget=gh_budget):
    """Link, score and follow up every linked PR (or just ``only``); returns a summary."""
    if budget() < DEFAULT_MIN_REMAINING:
        raise RateLimitLow("GitHub API points below the floor")
    if (seen := ledger.integrity())["void"]:
        raise ValueError(f"outcome ledger has {seen['bad_lines']} unsigned or tampered line(s); this scoring round is void")
    deck = cards()
    prs = fetch({int(r["pr"].split("#")[1]) for r in ledger.rows() if r["pr"].startswith(REPO + "#")} | {only} - {None}, run)
    for n, pr in ({} if only else prs).items():
        for cid in {i for i in _ID_RE.findall(f"{pr['title']}\n{pr['body'] or ''}") if i in deck}:
            ledger.link(cid, f"{REPO}#{n}", "detected")  # the body is read for ids only; none of it is stored
    regress._git(kc, "fetch", "-q", "origin", "main")
    bank, scored = regress.load_all(regress.data_dir() / "exams"), []
    for row in ledger.rows():
        n = int(row["pr"].split("#")[1])
        if row["pr"].startswith(REPO + "#") and n in prs and (not only or n == only):
            try:
                new = score(kc, row, prs[n], deck.get(row["card_id"]), bank)
            except (core.JudgeError, OSError, ValueError, KeyError) as exc:  # one PR's failure must not stop the rest
                new = {**row, "note": f"not scored: {type(exc).__name__}: {str(exc)[:120]}"}
            if new is not row and new != row:
                ledger.put(new)
                scored.append({"pr": row["pr"], "card": row["card_id"], "base": (new["score"]["base"] or {}).get("verdict"),
                               "head": (new["score"]["head"] or {}).get("verdict"), "note": new["note"]})
    return {"updated": scored}


def main(argv=None, out=sys.stdout, run=subprocess.run, budget=gh_budget):
    ap = argparse.ArgumentParser(prog="python -m backend.autoscore", description=__doc__.splitlines()[0])
    ap.add_argument("--kirocrew", required=True, help="a KiroCrew git clone (PR refs are fetched into it)")
    ap.add_argument("--pr", type=int, help="score this KiroCrew PR now")
    ap.add_argument("--card", help="link --pr to this card id (default: none, a PR no card asked for)")
    args = ap.parse_args(argv)
    try:
        if args.pr:
            ledger.link(args.card, f"{REPO}#{args.pr}", "backfill")
        summary = tick(args.kirocrew, args.pr, run, budget)
    except RateLimitLow as exc:
        print(f"autoscore: stopped, {exc}", file=sys.stderr)
        return 3
    except (core.JudgeError, RuntimeError, OSError, ValueError) as exc:
        print(f"autoscore: error: {exc}", file=sys.stderr)
        return 2
    out.write(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
