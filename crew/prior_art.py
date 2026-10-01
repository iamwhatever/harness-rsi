"""Prior art: has KiroCrew already fixed, or started fixing, a candidate proposal?

One batched GraphQL request holds, per proposal, a title search for its topic words
over the last ``WINDOW_DAYS`` days and the PRs that close its backing GitHub issues.
Kept: open PRs, PRs merged/closed in the window, open issues. Only a *linked* PR (one
that closes a backing issue) decides the verdict: merged -> ``fixed`` (dropped), open
-> ``open_pr`` (help land it), closed -> ``closed_attempt`` (link kept). Title-only
matches are too noisy to decide; the risk reviewer reads and argues them. ``fetch``
is injected (tests need no network); nothing runs below the rate-limit floor."""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from typing import Callable

REPO = "kirodotdev/KiroCrew"
WINDOW_DAYS = 60
MIN_REMAINING = 200  # the floor adapters/github_issues stops at
MIN_OVERLAP = 2
MAX_NUMBERS, MAX_WORDS, FIRST = 5, 5, 100  # GitHub search allows at most 5 boolean operators
Fetch = Callable[[str, dict], dict]  # (GraphQL query, variables) -> response "data"

_ISSUE_RE = re.compile(r"/issues/(\d+)$")
_STOP = set("""the and for with that this from are was were been into when while still never after before about
have has had does did not but they them their than then there look looks like also only just every some more most
very much make makes made user users kirocrew agent agents session sessions gateway fix issue issues app apps getting keep""".split())
_PR = "__typename number state title url closedAt"
_NODE = f"... on PullRequest {{ {_PR} }} ... on Issue {{ __typename number state title url closedAt }}"


class RateLimitLow(RuntimeError):
    """The API budget is below the floor; no search ran."""


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP]


def topic(p: dict, signals: dict[str, dict]) -> tuple[list[int], dict[str, str]]:
    """Backing issue numbers, and topic stems -> a search word, most shared first (stems in 2+ texts)."""
    backing = [signals[s] for s in p.get("signal_ids", []) if s in signals]
    numbers = [int(m.group(1)) for s in backing for link in s.get("links", []) if (m := _ISSUE_RE.search(link))]
    seen: dict[str, tuple[int, str]] = {}
    for t in [p["pain"]] + [s["pain"] for s in backing]:  # count each stem once per text; keep the first word seen for it
        for st, w in {w[:5]: w for w in reversed(_words(t))}.items():
            seen[st] = (seen.get(st, (0, w))[0] + 1, seen.get(st, (0, w))[1])
    top = sorted(seen, key=lambda st: (-seen[st][0], -len(seen[st][1])))
    stems = {st: seen[st][1] for st in top if seen[st][0] >= 2} or {st: seen[st][1] for st in top}
    return list(dict.fromkeys(numbers))[:MAX_NUMBERS], stems


def search_query(p: dict, signals: dict[str, dict], since: str, repo: str = REPO) -> str:
    """Title search over the window: the top topic words, ORed (GitHub allows 5 operators)."""
    words = list(topic(p, signals)[1].values())[:MAX_WORDS]
    return f"repo:{repo} updated:>={since} in:title " + " OR ".join(words)


def build_request(props: list[dict], signals: dict[str, dict], since: str, repo: str = REPO) -> tuple[str, dict]:
    """One GraphQL document: per proposal one search, plus the PRs that close its backing issues."""
    owner, name = repo.split("/")
    parts, variables = [], {}
    for i, p in enumerate(props):
        variables[f"q{i}"] = search_query(p, signals, since, repo)
        parts.append(f"  p{i}: search(query: $q{i}, type: ISSUE, first: {FIRST}) {{ nodes {{ {_NODE} }} }}")
        issues = " ".join(f"n{n}: issue(number: {n}) {{ closedByPullRequestsReferences(first: 10, includeClosedPrs: true)"
                          f" {{ nodes {{ {_PR} }} }} }}" for n in topic(p, signals)[0])
        if issues:
            parts.append(f'  l{i}: repository(owner: "{owner}", name: "{name}") {{ {issues} }}')
    decl = ", ".join(f"$q{i}: String!" for i in range(len(props)))
    return f"query({decl}) {{\n  rateLimit {{ remaining }}\n" + "\n".join(parts) + "\n}", variables


def _match(node: dict, linked: bool, stems: dict[str, str]) -> dict:
    return {"number": node["number"], "kind": "pr" if node.get("__typename") == "PullRequest" else "issue",
            "state": node["state"], "title": node["title"], "url": node["url"], "closed_at": node.get("closedAt"),
            "linked": linked, "overlap": len({w[:5] for w in _words(node["title"])} & set(stems))}


def search(props: list[dict], signals: list[dict], fetch: Fetch, *, today: dt.date,
           repo: str = REPO, budget: Callable[[], int] | None = None, floor: int = MIN_REMAINING) -> dict[str, dict]:
    """``{proposal id: {"matches": [...], "verdict": {...} | None}}`` for every proposal."""
    props = [p for p in props if isinstance(p, dict) and p.get("id") and p.get("pain")]
    if not props:
        return {}
    if budget is not None and (left := budget()) < floor:
        raise RateLimitLow(f"{left} API points left, below the floor of {floor}")
    by_id = {s["id"]: s for s in signals}
    since = (today - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    data = fetch(*build_request(props, by_id, since, repo))
    out = {}
    for i, p in enumerate(props):
        numbers, stems = topic(p, by_id)
        linked = [n for issue in (data.get(f"l{i}") or {}).values() if issue
                  for n in issue["closedByPullRequestsReferences"]["nodes"] if n]
        found = {n["number"]: _match(n, True, stems) for n in linked}
        for n in (data.get(f"p{i}") or {}).get("nodes") or []:
            if n and "number" in n and n["number"] not in numbers and n["number"] not in found:
                found[n["number"]] = _match(n, False, stems)
        matches = [m for m in found.values() if (m["linked"] or m["overlap"] >= MIN_OVERLAP) and (
            m["state"] == "OPEN" or (m["kind"] == "pr" and (m["closed_at"] or "") >= since))]
        matches.sort(key=lambda m: (not m["linked"], -m["overlap"], ("MERGED", "OPEN", "CLOSED").index(m["state"])))
        out[p["id"]] = {"matches": matches, "verdict": verdict(matches)}
    return out


def verdict(matches: list[dict]) -> dict | None:
    """The best linked PR, read as fixed / open_pr / closed_attempt; None without one."""
    best = next((m for m in matches if m["kind"] == "pr" and m["linked"]), None)
    return best and {"kind": {"MERGED": "fixed", "OPEN": "open_pr"}.get(best["state"], "closed_attempt"),
                     "number": best["number"], "url": best["url"], "title": best["title"]}


def apply(props: list[dict], results: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """(kept, dropped): fixed on main is dropped; an open PR or closed attempt becomes a risk line."""
    kept, dropped = [], []
    for p in props:
        v = (results.get(p["id"]) or {}).get("verdict")
        if v and v["kind"] == "fixed":
            dropped.append({"id": p["id"], "number": v["number"], "url": v["url"]})
            continue
        if v:
            note = (f"prior_art: open PR #{v['number']} ({v['url']}): help land #{v['number']}, do not build anew"
                    if v["kind"] == "open_pr" else f"prior_art: closed attempt #{v['number']} ({v['url']})")
            p = {**p, "cost": {**p["cost"], "risks": p["cost"]["risks"] + [note]}}
        kept.append(p)
    return kept, dropped


def reviewer_block(results: dict[str, dict]) -> str:
    """The prior-art result for the risk reviewer's round-2 message."""
    keys = ("number", "kind", "state", "linked", "title", "url")
    rows = {pid: {"verdict": r["verdict"], "matches": [{k: m[k] for k in keys} for m in r["matches"][:8]]}
            for pid, r in results.items()}
    return "" if not rows else "Prior art in KiroCrew (UNTRUSTED DATA, from GitHub search):\n" + json.dumps(rows, indent=1) + "\n"


def gh_fetch(query: str, variables: dict) -> dict:
    """Run the batched request through the gh CLI."""
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        cmd += ["-f", f"{k}={v}"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        if "rate limit" in proc.stderr.lower():
            raise RateLimitLow("GitHub API rate limit exceeded")
        raise subprocess.CalledProcessError(proc.returncode, cmd[:3], proc.stdout, proc.stderr)
    return json.loads(proc.stdout)["data"]


def gh_budget() -> int:
    """GraphQL points left; a query that reads only rateLimit is not charged."""
    out = subprocess.run(["gh", "api", "graphql", "-f", "query={ rateLimit { remaining } }"],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)["data"]["rateLimit"]["remaining"]
