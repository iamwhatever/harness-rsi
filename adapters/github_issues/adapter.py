"""Turn GitHub issues into signal rows (schemas/signal.schema.json).

Only summaries and counts leave this module: the pain line comes from the
issue title, people is a count of distinct authors, and no login, email or
issue body is ever written out.

Runs are incremental. A cursor and one cached record per issue live in
``$HARNESS_RSI_DATA/cache/github_issues/`` (outside git); each run reads only
issues updated since the cursor, 100 per GraphQL call with their reaction and
comment counts and commenters inline (no per-issue calls), and
stops cleanly when the API rate limit drops below a floor. Author logins are
cached only as short hashes, so the cache can count people without keeping names.
"""

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

DEFAULT_REPO = "kirodotdev/KiroCrew"
DEFAULT_WINDOW_DAYS = 14
DEFAULT_MIN_REMAINING = 200
DEDUP_RATIO = 0.85
PAIN_MAX = 200
CURSOR_SKEW = dt.timedelta(minutes=5)
CACHE_VERSION = 1

_PREFIX_RE = re.compile(r"^\s*(\[[^\]]{1,30}\]\s*|[a-z]+(\([^)]{0,40}\))?!?:\s*)+", re.I)
_EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
_HANDLE_RE = re.compile(r"(?<![\w/])@[\w-]+")
_URL_RE = re.compile(r"https?://\S+")
_ISO = "%Y-%m-%dT%H:%M:%SZ"


class RateLimitLow(Exception):
    """The API budget is below the floor; the run stops without writing anything."""


_QUERY = """query($owner: String!, $name: String!, $since: DateTime!, $states: [IssueState!], $after: String) {
  rateLimit { remaining }
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $after, states: $states, filterBy: {since: $since}, orderBy: {field: UPDATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes { number url title createdAt updatedAt state author { login }
        reactions(content: THUMBS_UP) { totalCount }
        comments(first: 100) { totalCount nodes { author { login } } } }
    }
  }
}"""


def gh_fetch(repo, since, states, after):
    """One page of up to 100 issues, with their commenters, in one GraphQL call.

    Returns (issues, next_after or None, rate-limit points remaining). Issues are
    shaped like the REST list payload plus a ``commenters`` login list.
    """
    owner, name = repo.split("/")
    cmd = ["gh", "api", "graphql", "-f", f"query={_QUERY}", "-f", f"owner={owner}", "-f", f"name={name}",
           "-f", f"since={since}"] + [a for st in states for a in ("-f", f"states[]={st}")]
    if after:
        cmd += ["-f", f"after={after}"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        if "rate limit" in proc.stderr.lower():
            raise RateLimitLow("GitHub API rate limit exceeded")
        raise subprocess.CalledProcessError(proc.returncode, cmd[:3], proc.stdout, proc.stderr)
    data = json.loads(proc.stdout)["data"]
    conn = data["repository"]["issues"]
    issues = [{
        "number": n["number"], "html_url": n["url"], "title": n["title"],
        "created_at": n["createdAt"], "updated_at": n["updatedAt"], "state": n["state"].lower(),
        "user": n["author"], "comments": n["comments"]["totalCount"],
        "reactions": {"+1": n["reactions"]["totalCount"]},
        "commenters": [(c["author"] or {}).get("login") for c in n["comments"]["nodes"]],
    } for n in conn["nodes"]]
    nxt = conn["pageInfo"]["endCursor"] if conn["pageInfo"]["hasNextPage"] else None
    return issues, nxt, data["rateLimit"]["remaining"]


def summarize(title):
    """A short pain line from an issue title, with handles and emails removed."""
    text = _PREFIX_RE.sub("", title or "")
    text = _URL_RE.sub("", _EMAIL_RE.sub("", _HANDLE_RE.sub("", text)))
    text = " ".join(text.split()).strip(" .:-")
    if not text:
        text = "Untitled issue"
    text = text[0].upper() + text[1:]
    if len(text) > PAIN_MAX:
        text = text[: PAIN_MAX - 1].rstrip() + "…"
    return text


def _norm(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def gh_budget():
    """GraphQL points left; a query that reads only rateLimit is not charged."""
    cmd = ["gh", "api", "graphql", "-f", "query={ rateLimit { remaining } }"]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return json.loads(out)["data"]["rateLimit"]["remaining"]


def _hash(login):
    return hashlib.sha256(login.encode()).hexdigest()[:16] if login else None


class _Pager:
    """Pages through ``fetch`` and refuses to start a call below the rate floor."""

    def __init__(self, fetch, floor, budget=None):
        self.fetch, self.floor, self.calls = fetch, floor, 0
        self.remaining = budget() if budget else None

    def all(self, repo, since, states):
        rows, after = [], None
        while True:
            if self.remaining is not None and self.remaining < self.floor:
                raise RateLimitLow(f"{self.remaining} API points left, below the floor of {self.floor}")
            got, after, remaining = self.fetch(repo, since, states, after)
            self.calls += 1
            self.remaining = remaining if remaining is not None else self.remaining
            rows.extend(got)
            if not after:
                return rows


def default_cache_dir():
    data = os.environ.get("HARNESS_RSI_DATA") or pathlib.Path.home() / ".kiro/crew/harness-rsi-data"
    return pathlib.Path(data).expanduser() / "cache" / "github_issues"


def _cache_file(cache_dir, repo):
    return pathlib.Path(cache_dir) / (repo.replace("/", "__") + ".json")


def _load(path):
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cache if cache.get("version") == CACHE_VERSION else None


def sync(repo, pager, cache, window_start, now):
    """Fold issues updated since the cursor into ``cache`` (in place).

    A rebuild reads open issues only; an incremental read also takes closed ones
    so an issue closed since the last run leaves the cache.
    """
    cursor = cache.get("cursor")
    since = max(cursor, window_start) if cursor else window_start
    records = cache.setdefault("issues", {})
    for issue in pager.all(repo, since, ["OPEN", "CLOSED"] if cursor else ["OPEN"]):
        num = str(issue["number"])
        if "pull_request" in issue or issue.get("state", "open") != "open":
            records.pop(num, None)
            continue
        records[num] = {
            "number": issue["number"],
            "html_url": issue["html_url"],
            "pain": summarize(issue.get("title")),
            "created_at": issue["created_at"],
            "updated_at": issue.get("updated_at") or issue["created_at"],
            "author": _hash((issue.get("user") or {}).get("login")),
            "commenters": sorted({_hash(c) for c in issue.get("commenters", [])} - {None}),
            "comments": int(issue.get("comments") or 0),
            "thumbs": int((issue.get("reactions") or {}).get("+1") or 0),
        }
    cache.update(version=CACHE_VERSION, cursor=(now - CURSOR_SKEW).strftime(_ISO))
    cache.setdefault("covered_from", window_start)


def _near(seen, key):
    """First id in ``seen`` whose text matches ``key``; the cheap upper bounds skip most full ratios."""
    sm = difflib.SequenceMatcher(None, "", key)
    for k, sid in seen:
        sm.set_seq1(k)
        if sm.real_quick_ratio() >= DEDUP_RATIO and sm.quick_ratio() >= DEDUP_RATIO and sm.ratio() >= DEDUP_RATIO:
            return sid
    return None


def rows_from_cache(repo, cache, window_start, window_days):
    recs = [r for r in cache.get("issues", {}).values() if r["updated_at"] >= window_start]
    recs.sort(key=lambda r: (r["created_at"], r["number"]))
    rows, seen = [], []  # seen: (normalized pain, canonical id)
    for rec in recs:
        pain = rec["pain"]
        key = _norm(pain)
        dedup_of = _near(seen, key)
        sid = f"sig_{rec['created_at'][:10].replace('-', '')}_{rec['number'] % 10000:04d}"
        if dedup_of is None:
            seen.append((key, sid))
        people = set(rec["commenters"]) | ({rec["author"]} if rec["author"] else set())
        rows.append({
            "id": sid,
            "source": f"github:{repo}",
            "links": [rec["html_url"]],
            "pain": pain,
            "mentions": {"count": 1 + rec["thumbs"] + rec["comments"], "people": max(len(people), 1), "window_days": window_days},
            "layer": "real",
            "testable": {"ok": False, "task": None},
            "dedup_of": dedup_of,
        })
    return rows


def build_signals(repo, fetch, window_days=DEFAULT_WINDOW_DAYS, now=None, cache_dir=None,
                  full=False, min_remaining=DEFAULT_MIN_REMAINING, stats=None, budget=None):
    """Read issues through ``fetch(repo, since, states, after)`` (see gh_fetch) and return signal rows.

    With ``cache_dir`` the run is incremental and the cache is saved only on success.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    window_start = (now - dt.timedelta(days=window_days)).strftime(_ISO)
    path = _cache_file(cache_dir, repo) if cache_dir else None
    cache = None if (full or path is None) else _load(path)
    if cache is None or cache.get("covered_from", window_start) > window_start:
        cache = {}
    pager = _Pager(fetch, min_remaining, budget)
    sync(repo, pager, cache, window_start, now)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        tmp.replace(path)
    if stats is not None:
        stats.update(calls=pager.calls, remaining=pager.remaining)
    return rows_from_cache(repo, cache, window_start, window_days)


def main(argv=None, fetch=gh_fetch, out=sys.stdout, err=sys.stderr, now=None, budget=gh_budget):
    ap = argparse.ArgumentParser(description="Emit signal rows from GitHub issues.")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="owner/name")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--full", action="store_true", help="ignore the cursor and rebuild the cache")
    ap.add_argument("--min-remaining", type=int, default=DEFAULT_MIN_REMAINING, help="stop below this many API points left")
    ap.add_argument("--cache-dir", default=None, help="default: $HARNESS_RSI_DATA/cache/github_issues")
    args = ap.parse_args(argv)
    if args.window_days < 1 or not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
        ap.error("need --repo owner/name and --window-days >= 1")
    stats = {}
    try:
        rows = build_signals(args.repo, fetch, args.window_days, now=now, cache_dir=args.cache_dir or default_cache_dir(),
                             full=args.full, min_remaining=args.min_remaining, stats=stats, budget=budget)
    except RateLimitLow as exc:
        err.write(f"github_issues: stopped, {exc}. Cache and cursor unchanged; try again after the limit resets.\n")
        return 3
    json.dump(rows, out, indent=2, ensure_ascii=False)
    out.write("\n")
    err.write(f"github_issues: {len(rows)} rows, {stats['calls']} API calls, {stats['remaining']} left\n")
    return 0
