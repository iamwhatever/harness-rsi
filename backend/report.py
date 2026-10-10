"""The weekly industry catch-up report, and the web side's routes (``/report``, ``/topics``).

A report is made after every round (``round_job``) and by the owner's Make catch-up report button
(``POST /report/make``, which first reads fast-rising repos for the topics, ``adapters.trending``).
It is built from the signal list only, no agent: the top 5 web trends (web pages first, then rising
repos), new repos and releases, what each top trend may mean for us (the card that picks it up, or
none yet), the pages the scout could not reach, and every link. It is written to
``$HARNESS_RSI_DATA/reports/<date>.md`` with a ``.json`` twin the Home card reads; a later report the
same day replaces it. ``slack`` is a plain-text draft for the owner to copy: nothing here posts.

Signal text is untrusted data: it is shown and written as text, one line per item, links https only.
"""

import asyncio
import datetime as dt
import json
import re
import time

try:  # see settings.py: a subpackage in the gateway, top-level in tests and the CLI
    from .. import adapters
    from ..adapters import trending as _trending  # noqa: F401 - binds adapters.trending
except ImportError:
    import adapters.trending

from . import round_job, store, topics

DIR, TOP, LIST_MAX = "reports", 5, 10
CLOCK = lambda: dt.datetime.now().astimezone()  # noqa: E731 - local time; tests pass a fake
#: The Make catch-up report job; ``task`` is the running asyncio task, the rest is shown on Home.
JOB = {"task": None, "running": False, "started_at": None, "finished_at": None, "error": ""}
_REPO_RE = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+$")
_LINE_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _line(s):
    """Untrusted text as one plain line."""
    return _LINE_RE.sub(" ", str(s)).strip()


def kind(row):
    """``web`` (a page the scout read), ``repo``, ``release`` or ``issue`` (``adapters.trending``)."""
    if not row["source"].startswith("trend:github:"):
        return "web"
    link = row["links"][0]
    return "repo" if _REPO_RE.match(link) else "release" if "/releases/" in link else "issue"


def _item(row, cards):
    card = next((p for p in cards if row["id"] in p.get("signal_ids", [])), None)
    return {"id": row["id"], "pain": _line(row["pain"]), "link": row["links"][0], "source": row["source"],
            "people": row["mentions"]["people"], "count": row["mentions"]["count"], "kind": kind(row),
            "card": {"id": card["id"], "pain": _line(card["pain"])} if card else None}


def build(signals, proposals, reach, conf, made_at, how, rnd=None):
    """The report as data plus its Markdown and Slack draft. ``how`` is ``round`` or ``button``."""
    ext = [r for r in store.by_heat(signals) if r["layer"] == "external" and not r["dedup_of"]]
    items = [_item(r, proposals) for r in ext]
    top = items[:TOP]  # by_heat already puts web pages before repos
    by = lambda k: [i for i in items if i["kind"] == k][:LIST_MAX]  # noqa: E731
    doc = {"date": made_at.date().isoformat(), "made_at": made_at.isoformat(timespec="seconds"), "how": how, "round": rnd,
           "topics": conf["topics"], "trends": top, "repos": by("repo"), "releases": by("release"),
           "not_reachable": [u for u in reach if isinstance(u, str) and u.startswith("https://")][:LIST_MAX],
           "counts": {"external": len(items), "web": sum(i["kind"] == "web" for i in items), "repos": sum(i["kind"] == "repo" for i in items)}}
    return {**doc, "markdown": markdown(doc), "slack": slack(doc)}


def _meaning(i):
    return f"card {i['card']['id']} picks this up: {i['card']['pain']}" if i["card"] else "no card picks this up yet; the next round's reviewers weigh it"


def markdown(d):
    made = f"round {d['round']}" if d["how"] == "round" else "the Make catch-up report button"
    out = [f"# Industry catch-up, {d['date']}", "", f"Made {d['made_at']} by {made}. Topics: {', '.join(d['topics'])}.",
           f"From {d['counts']['external']} external signal(s): {d['counts']['web']} web page(s), {d['counts']['repos']} rising repo(s).", "",
           "## Top 5 trends", ""]
    out += [f"{n}. {i['pain']} ({i['people']} people, {i['count']} mentions) <{i['link']}>" for n, i in enumerate(d["trends"], 1)] or ["None yet: run a round."]
    out += ["", "## New repos and releases", ""]
    out += [f"- {i['pain']} <{i['link']}>" for i in d["repos"] + d["releases"]] or ["None found for these topics."]
    out += ["", "## What it may mean for us", ""]
    out += [f"- {i['pain']}: {_meaning(i)}." for i in d["trends"]] or ["Nothing to weigh yet."]
    if d["not_reachable"]:
        out += ["", "## Not reachable (login or paywall)", ""] + [f"- <{u}>" for u in d["not_reachable"]]
    links = list(dict.fromkeys(i["link"] for i in d["trends"] + d["repos"] + d["releases"]))
    out += ["", "## Links", ""] + ([f"- <{u}>" for u in links] or ["None."])
    return "\n".join(out) + "\n"


def slack(d):
    """A short plain-text draft for the owner to paste; nothing posts it."""
    out = [f"Industry catch-up, {d['date']} ({', '.join(d['topics'])})"]
    out += [f"{n}. {i['pain']} {i['link']}" for n, i in enumerate(d["trends"], 1)] or ["No web trends yet."]
    if d["repos"]:
        out += ["New repos: " + "; ".join(f"{i['pain']} {i['link']}" for i in d["repos"][:3])]
    if d["releases"]:
        out += ["Releases: " + "; ".join(f"{i['pain']} {i['link']}" for i in d["releases"][:3])]
    return "\n".join(out)


def _reach():
    try:
        doc = json.loads((store.data_dir() / "web_reach.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return doc.get("not_reachable", []) if isinstance(doc, dict) and isinstance(doc.get("not_reachable"), list) else []


def write(how, rnd=None, now=None):
    """Build from the data dir and write ``reports/<date>.md`` and ``.json``; returns the report."""
    doc = build(store.read_signals(), store.read_proposals(), _reach(), topics.read(), now or CLOCK(), how, rnd)
    path = store.data_dir() / DIR
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{doc['date']}.md").write_text(doc["markdown"], encoding="utf-8")
    (path / f"{doc['date']}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


def latest():
    """The newest report, or None."""
    for p in sorted((store.data_dir() / DIR).glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json"), reverse=True):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("trends"), list):
            return doc
    return None


def trending_rows():
    """Rising repos for the owner's topics, or ``([], note)``: a round source like ``routes.github_rows``."""
    try:
        rows, notes = adapters.trending.collect(topics.read()["topics"])
    except Exception as exc:  # noqa: BLE001 - a trending failure must not stop the round
        return [], f"trending: {type(exc).__name__}"
    return rows, "; ".join(notes)


def after_round(rnd):
    """Round ``rnd`` ended well: its catch-up report."""
    return write("round", rnd)


def _make():
    rows, note = trending_rows()
    if rows:
        store.refresh([rows])
    write("button")
    return note


async def _job():
    try:
        JOB.update(error=await asyncio.to_thread(_make))
    except Exception as exc:  # noqa: BLE001 - the job must always report and release
        JOB.update(error=f"report: {type(exc).__name__}")
    finally:
        JOB.update(running=False, finished_at=time.time(), task=None)


def view():
    return {k: v for k, v in JOB.items() if k != "task"}


def routes(AppRoute, owner, err, save):
    """``GET /report``, ``POST /report/make``, ``GET``/``POST /topics``; ``routes.py`` passes its owner check and helpers."""
    async def get(request, ctx):
        return _json({"ok": True, "report": await asyncio.to_thread(latest), "job": view()})

    async def make(request, ctx):
        if not owner(request):
            return err(403, "owner_only", "only the dashboard owner can make a report")
        if round_job.STATE["running"]:
            return err(409, "round_running", "a round is running; it makes a report when it ends")
        if JOB["running"]:
            return err(409, "report_running", "a report is already being made")
        JOB.update(running=True, started_at=time.time(), finished_at=None, error="")
        JOB["task"] = asyncio.get_running_loop().create_task(_job())
        return _json({"ok": True, "job": view()}, status=202)

    async def topics_get(request, ctx):
        return _json({"ok": True, "topics": await asyncio.to_thread(topics.read)})

    async def topics_post(request, ctx):
        return await save(request, topics, "topics", dict)

    return [AppRoute(method="GET", path="/report", handler=get), AppRoute(method="POST", path="/report/make", handler=make),
            AppRoute(method="GET", path="/topics", handler=topics_get), AppRoute(method="POST", path="/topics", handler=topics_post)]


def _json(body, status=200):
    from aiohttp import web
    return web.json_response(body, status=status)
