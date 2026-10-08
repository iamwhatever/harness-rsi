"""The check rollup (pass / fail / pending / none) of each linked PR's head, for ``GET /outcomes``.

One ``gh api graphql`` call reads every PR that needs it; the answer is cached in
``$HARNESS_RSI_DATA/cache/ci.json``. An open PR is read again after ``TTL_S``; a merged or closed PR
whose checks have settled (pass, fail or none) is never read again. When the call fails the page keeps
the cached states and the reply says why (``error``), so ``/outcomes`` itself never fails on GitHub.
"""

import datetime as dt
import json
import re
import subprocess
import time

from . import store

TTL_S = 300
TIMEOUT_S = 20
#: The runner for ``gh``; tests swap it for a fake (tests/conftest.py).
RUN = subprocess.run
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_STATE = {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail", "PENDING": "pending", "EXPECTED": "pending"}
SETTLED = {"pass", "fail", "none"}


def _path():
    return store.data_dir() / "cache" / "ci.json"


def _iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat(timespec="seconds")


def parse(pr):
    """``(owner, name, number)`` for ``owner/name#N``, or None when it is not a PR id the query may hold."""
    m = re.fullmatch(r"([^/#]+)/([^/#]+)#(\d{1,7})", str(pr))
    return (m[1], m[2], int(m[3])) if m and _NAME.match(m[1]) and _NAME.match(m[2]) else None


def load():
    """The cache: ``{pr: {"ci": state, "at": epoch}}``; a missing or unreadable file is empty."""
    try:
        doc = json.loads(_path().read_text(encoding="utf-8"))
        return {k: v for k, v in doc.items() if isinstance(v, dict) and v.get("ci") in SETTLED | {"pending"} and isinstance(v.get("at"), (int, float))}
    except (OSError, ValueError, AttributeError):
        return {}


def save(cache):
    (path := _path()).parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def query(prs):
    """The GraphQL text for ``prs`` (parsed ids), one repository alias per repo."""
    repos = {}
    for owner, name, n in prs:
        repos.setdefault((owner, name), set()).add(n)
    parts = []
    for i, ((owner, name), nums) in enumerate(sorted(repos.items())):
        pulls = " ".join(f"p{n}: pullRequest(number: {n}) {{ commits(last: 1) {{ nodes {{ commit {{ statusCheckRollup {{ state }} }} }} }} }}"
                         for n in sorted(nums))
        parts.append(f'r{i}: repository(owner: "{owner}", name: "{name}") {{ {pulls} }}')
    return "{ " + " ".join(parts) + " }", sorted(repos)


def fetch(prs, run=None):
    """``{pr: state}`` for ``prs`` (``owner/name#N`` strings) in one call; raises RuntimeError on failure."""
    ids = [p for p in map(parse, prs) if p]
    if not ids:
        return {}
    q, repos = query(ids)
    try:
        proc = (run or RUN)(["gh", "api", "graphql", "-f", f"query={q}"], capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ci: {type(exc).__name__}") from exc
    if proc.returncode:
        raise RuntimeError("ci: GitHub rate limit" if "rate limit" in (proc.stderr or "").lower() else f"ci: gh exit {proc.returncode}")
    try:
        data = json.loads(proc.stdout)["data"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("ci: unreadable gh answer") from exc
    out = {}
    for i, (owner, name) in enumerate(repos):
        for alias, pull in ((data or {}).get(f"r{i}") or {}).items():
            nodes = ((pull or {}).get("commits") or {}).get("nodes") or []
            roll = ((nodes[0] or {}).get("commit") or {}).get("statusCheckRollup") if nodes else None
            out[f"{owner}/{name}#{alias[1:]}"] = _STATE.get((roll or {}).get("state"), "none") if pull else "none"
    return out


def read(rows, now=time.time, run=None):
    """``{"checks": {pr: state}, "read_at": iso or None, "error": str}`` for the outcome ``rows``."""
    cache, t = load(), now()
    final = {r["pr"] for r in rows if r.get("state") in ("merged", "closed")}
    want = sorted({r["pr"] for r in rows if parse(r.get("pr"))})
    due = [p for p in want if p not in cache or not (p in final and cache[p]["ci"] in SETTLED) and t - cache[p]["at"] >= TTL_S]
    error = ""
    if due:
        try:
            got = fetch(due, run)
            cache.update({p: {"ci": got.get(p, "none"), "at": t} for p in due})
            save(cache)
        except (RuntimeError, OSError) as exc:
            error = str(exc)[:120]
    have = {p: cache[p] for p in want if p in cache}
    return {"checks": {p: v["ci"] for p, v in have.items()},
            "read_at": _iso(min(v["at"] for v in have.values())) if have else None, "error": error}
