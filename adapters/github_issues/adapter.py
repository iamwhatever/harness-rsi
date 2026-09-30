"""Turn GitHub issues into signal rows (schemas/signal.schema.json).

Only summaries and counts leave this module: the pain line comes from the
issue title, people is a count of distinct authors, and no login, email or
issue body is ever written out.
"""

import argparse
import datetime as dt
import difflib
import json
import re
import subprocess
import sys

DEFAULT_REPO = "kirodotdev/KiroCrew"
DEFAULT_WINDOW_DAYS = 14
DEDUP_RATIO = 0.85
PAIN_MAX = 200

_PREFIX_RE = re.compile(r"^\s*(\[[^\]]{1,30}\]\s*|[a-z]+(\([^)]{0,40}\))?!?:\s*)+", re.I)
_EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
_HANDLE_RE = re.compile(r"(?<![\w/])@[\w-]+")
_URL_RE = re.compile(r"https?://\S+")


def gh_fetch(path, params):
    """Call `gh api` for a GET endpoint and return the decoded JSON list."""
    cmd = ["gh", "api", "-X", "GET", "--paginate", path]
    for key, value in params.items():
        cmd += ["-f", f"{key}={value}"]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    # --paginate concatenates one JSON array per page.
    rows, dec, pos = [], json.JSONDecoder(), 0
    while pos < len(out):
        while pos < len(out) and out[pos].isspace():
            pos += 1
        if pos >= len(out):
            break
        page, pos = dec.raw_decode(out, pos)
        rows.extend(page if isinstance(page, list) else [page])
    return rows


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


def _signal_id(issue):
    day = issue["created_at"][:10].replace("-", "")
    return f"sig_{day}_{issue['number'] % 10000:04d}"


def _people(issue, comments):
    logins = {(issue.get("user") or {}).get("login")}
    logins |= {(c.get("user") or {}).get("login") for c in comments}
    logins.discard(None)
    return max(len(logins), 1)


def build_signals(repo, fetch, window_days=DEFAULT_WINDOW_DAYS, now=None):
    """Read issues through ``fetch(path, params)`` and return signal rows."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    issues = fetch(f"repos/{repo}/issues", {"state": "open", "since": since, "per_page": 100})
    issues = [i for i in issues if "pull_request" not in i]
    issues.sort(key=lambda i: (i["created_at"], i["number"]))

    rows, seen = [], []  # seen: (normalized pain, canonical id)
    for issue in issues:
        n_comments = int(issue.get("comments") or 0)
        comments = fetch(f"repos/{repo}/issues/{issue['number']}/comments", {"per_page": 100}) if n_comments else []
        thumbs = int((issue.get("reactions") or {}).get("+1") or 0)
        pain = summarize(issue.get("title"))
        key = _norm(pain)
        dedup_of = next(
            (sid for k, sid in seen if difflib.SequenceMatcher(None, k, key).ratio() >= DEDUP_RATIO),
            None,
        )
        sid = _signal_id(issue)
        if dedup_of is None:
            seen.append((key, sid))
        rows.append({
            "id": sid,
            "source": f"github:{repo}",
            "links": [issue["html_url"]],
            "pain": pain,
            "mentions": {
                "count": 1 + thumbs + n_comments,
                "people": _people(issue, comments),
                "window_days": window_days,
            },
            "layer": "real",
            "testable": {"ok": False, "task": None},
            "dedup_of": dedup_of,
        })
    return rows


def main(argv=None, fetch=gh_fetch, out=sys.stdout):
    ap = argparse.ArgumentParser(description="Emit signal rows from GitHub issues.")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="owner/name")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    args = ap.parse_args(argv)
    if args.window_days < 1 or not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
        ap.error("need --repo owner/name and --window-days >= 1")
    json.dump(build_signals(args.repo, fetch, args.window_days), out, indent=2, ensure_ascii=False)
    out.write("\n")
    return 0
