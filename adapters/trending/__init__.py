"""Fast-rising open-source repos for the owner's topics, as external signal rows.

Deterministic, no agent: for each topic the GitHub search API lists repos created in the last
``window_days``, most stars first; a repo's pace is its stars per day since it was created. The
fastest ``max_repos`` (forks and archived repos left out) become one row each, and each also gives
its recent releases and its most-discussed new issues as rows. Every row is ``layer: external``
with source ``trend:github:<owner/repo>``.

Only names, counts, dates and links leave this module: no description, README, issue title or
release note is copied (that text is the repo author's, and untrusted). A release tag is kept
only when it is a plain version word. The run stops cleanly when the API budget is low and says
so in its notes; reads go through ``gh api`` (read-only).
"""

import datetime as dt
import json
import re
import subprocess

DEFAULT_WINDOW_DAYS = 30
PER_TOPIC = 5  # search hits kept per topic
MAX_REPOS = 10
RELEASES, ISSUES = 2, 2  # rows per repo
MIN_CORE, MIN_SEARCH = 100, 3  # stop below these API points
PAIN_MAX = 200
_TAG_RE = re.compile(r"^[\w.+-]{1,40}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_ISO = "%Y-%m-%dT%H:%M:%SZ"


class RateLimitLow(Exception):
    """The API budget is below the floor."""


def gh_fetch(path, params=None):
    """One read-only REST call through ``gh api``; JSON in, JSON out."""
    cmd = ["gh", "api", "-X", "GET", path] + [a for k, v in (params or {}).items() for a in ("-f", f"{k}={v}")]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        if "rate limit" in proc.stderr.lower():
            raise RateLimitLow("GitHub API rate limit exceeded")
        raise subprocess.CalledProcessError(proc.returncode, cmd[:4], proc.stdout, proc.stderr)
    return json.loads(proc.stdout)


def _when(s):
    return dt.datetime.strptime(s, _ISO).replace(tzinfo=dt.timezone.utc).date()


class _Budget:
    """API points left, read once and counted down, so a run never digs below the floor."""

    def __init__(self, fetch):
        res = fetch("rate_limit").get("resources", {})
        self.core, self.search = res.get("core", {}).get("remaining", 0), res.get("search", {}).get("remaining", 0)

    def take(self, kind):
        left = getattr(self, kind)
        if left <= (MIN_SEARCH if kind == "search" else MIN_CORE):
            raise RateLimitLow(f"GitHub {kind} API budget is low ({left} left)")
        setattr(self, kind, left - 1)


def _row(day, n, full, link, pain, count, days):
    return {"id": f"sig_{day}_{n:04d}", "source": f"trend:github:{full}", "links": [link], "pain": pain[:PAIN_MAX],
            "mentions": {"count": max(1, count), "people": 1, "window_days": max(1, days)},
            "layer": "external", "testable": {"ok": False, "task": None}, "dedup_of": None}


def rising(topics, fetch, budget, today, window_days=DEFAULT_WINDOW_DAYS, per_topic=PER_TOPIC, max_repos=MAX_REPOS):
    """The fastest-rising repos over all topics: ``[{full, url, stars, age, topic}]``, fastest first."""
    since, found = (today - dt.timedelta(days=window_days)).isoformat(), {}
    for topic in topics:
        budget.take("search")
        q = f'"{topic}" in:name,description,topics created:>={since} fork:false archived:false'
        page = fetch("search/repositories", {"q": q, "sort": "stars", "order": "desc", "per_page": per_topic})
        for item in page.get("items", [])[:per_topic]:
            full = item.get("full_name", "")
            if not _NAME_RE.match(full) or item.get("fork") or item.get("archived") or full in found:
                continue
            age = max(1, (today - _when(item["created_at"])).days)
            stars = int(item.get("stargazers_count") or 0)
            found[full] = {"full": full, "url": f"https://github.com/{full}", "stars": stars, "age": age, "topic": topic}
    return sorted(found.values(), key=lambda r: (-r["stars"] / r["age"], r["full"]))[:max_repos]


def _releases(repo, fetch, budget, today, window_days):
    budget.take("core")
    out = []
    for rel in fetch(f"repos/{repo['full']}/releases", {"per_page": 5}):
        if rel.get("draft") or not rel.get("published_at") or not str(rel.get("html_url", "")).startswith("https://github.com/"):
            continue
        days = (today - _when(rel["published_at"])).days
        if days <= window_days:
            tag = rel.get("tag_name") or ""
            out.append((rel["html_url"], f"{repo['full']} shipped release {tag}" if _TAG_RE.match(tag)
                        else f"{repo['full']} shipped a new release", 1, days or 1))
    return out[:RELEASES]


def _issues(repo, fetch, budget, today, window_days):
    budget.take("search")
    since = (today - dt.timedelta(days=window_days)).isoformat()
    page = fetch("search/issues", {"q": f"repo:{repo['full']} is:issue created:>={since}", "sort": "comments",
                                   "order": "desc", "per_page": ISSUES})
    out = []
    for it in page.get("items", [])[:ISSUES]:
        n = int(it.get("comments") or 0)
        if n and str(it.get("html_url", "")).startswith("https://github.com/"):
            days = max(1, (today - _when(it["created_at"])).days)
            out.append((it["html_url"], f"New issue on {repo['full']} drew {n} comments in {days} days", n, days))
    return out


def collect(topics, fetch=None, today=None, window_days=DEFAULT_WINDOW_DAYS, max_repos=MAX_REPOS):
    """``(rows, notes)``. Rows for each rising repo, then its releases and issues; a note says what was cut short."""
    today, fetch = today or dt.datetime.now(dt.timezone.utc).date(), fetch or gh_fetch
    day, rows, notes = today.strftime("%Y%m%d"), [], []
    try:
        budget = _Budget(fetch)
        repos = rising(topics, fetch, budget, today, window_days, max_repos=max_repos)
    except (RateLimitLow, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError, KeyError) as exc:
        return [], [f"trending: {exc if isinstance(exc, RateLimitLow) else type(exc).__name__}"]
    for repo in repos:
        rows.append(_row(day, len(rows) + 1, repo["full"], repo["url"],
                         f"Fast-rising {repo['topic']} repo {repo['full']}: {repo['stars']} stars in {repo['age']} days",
                         repo["stars"], repo["age"]))
    for repo in repos:
        try:
            extra = _releases(repo, fetch, budget, today, window_days) + _issues(repo, fetch, budget, today, window_days)
        except RateLimitLow as exc:
            notes.append(f"trending: {exc}; releases and issues stop at {repo['full']}")
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError, KeyError) as exc:
            notes.append(f"trending: {repo['full']} skipped ({type(exc).__name__})")
            continue
        for link, pain, count, days in extra:
            rows.append(_row(day, len(rows) + 1, repo["full"], link, pain, count, days))
    return rows, notes
