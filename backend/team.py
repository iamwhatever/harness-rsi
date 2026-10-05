"""Team view: self-reported work-ledger snapshots from the lead and its lanes, folded into one tree.

The lead and each lane conductor call the app MCP tool ``rsi_report_ledger`` at the end of a
patrol cycle with the JSON of their own ``work_ledger_read compact=true``. :func:`ingest` checks
it against a fixed shape, caps it, keeps only the columns the page shows, and writes one file per
reporter under ``<data dir>/team/``. :func:`fold` turns those files into lead -> lanes -> workers.

Everything here is SELF-REPORTED: the tool cannot see which session called it, so a snapshot is
only what its reporter claimed. Approvals and credits are not in a work ledger and are not shown.
Stdlib only: the MCP server runs this module outside the gateway.
"""

import calendar
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

ROLES = ("lead", "lane")
STATES = ("open", "accepted", "rejected", "abandoned")
STATUSES = ("progress", "done", "blocked", "question")
VERDICTS = ("pass", "fail", "pending", "refused", "error")
MAX_BYTES = 256 * 1024
MAX_ITEMS = 200
MAX_REPORTERS = 64
STALE_MINUTES = 90
#: Why an item needs the owner, most urgent first.
NEEDS = ("question", "blocked", "merge")
TEXT_CAPS = {"title": 300, "summary": 500, "goal": 500}
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_ITEM_RE = re.compile(r"^it_[0-9a-f]{8}$")
_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+(Z|[+-]\d{2}:?\d{2})?$")


class Refused(ValueError):
    """The snapshot does not fit the shape; the message says which field."""


def data_dir():
    return Path(os.environ.get("HARNESS_RSI_DATA") or Path.home() / ".kiro/crew/harness-rsi-data").expanduser()


def _stamp(now):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


def _key(v, field):
    if not (isinstance(v, str) and _KEY_RE.match(v)):
        raise Refused(f"{field}: expected a session key")
    return v


def _opt_key(v, field):
    return None if v in (None, "") else _key(v, field)


def _text(v, field):
    if v is None:
        return ""
    if not isinstance(v, str):
        raise Refused(f"{field}: expected text")
    return v[:TEXT_CAPS[field.rsplit(".", 1)[-1]]]


def _choice(v, allowed, field, optional=True):
    if v is None and optional:
        return None
    if v not in allowed:
        raise Refused(f"{field}: expected one of {', '.join(allowed)}")
    return v


def _int(v, field, lo, hi, optional=True):
    if v is None and optional:
        return None
    if not (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi):
        raise Refused(f"{field}: expected an integer {lo}-{hi}")
    return v


def _when(v, field):
    if v in (None, ""):
        return None
    if not (isinstance(v, str) and len(v) <= 40 and _STAMP_RE.match(v)):
        raise Refused(f"{field}: expected an ISO-8601 stamp")
    return v


def _item(raw, i):
    f = f"items[{i}]"
    if not isinstance(raw, dict):
        raise Refused(f"{f}: expected an object")
    if not (isinstance(raw.get("item_id"), str) and _ITEM_RE.match(raw["item_id"])):
        raise Refused(f"{f}.item_id: expected it_<8 hex>")
    return {
        "item_id": raw["item_id"],
        "title": _text(raw.get("title"), f"{f}.title"),
        "state": _choice(raw.get("state"), STATES, f"{f}.state", optional=False),
        "status": _choice(raw.get("status"), STATUSES, f"{f}.status"),
        "summary": _text(raw.get("summary"), f"{f}.summary"),
        "verdict": _choice(raw.get("verdict"), VERDICTS, f"{f}.verdict"),
        "pr": _int(raw.get("pr"), f"{f}.pr", 1, 10**7),
        "worker_session_key": _opt_key(raw.get("worker_session_key"), f"{f}.worker_session_key"),
        "last_report_at": _when(raw.get("last_report_at"), f"{f}.last_report_at"),
        "orphaned": raw.get("orphaned") is True,
        "stale": raw.get("stale") is True,
    }


def validate(args):
    """The tool's arguments -> the record to store, or :class:`Refused`. Unknown fields are dropped."""
    if not isinstance(args, dict):
        raise Refused("arguments: expected an object")
    snap = args.get("snapshot")
    if isinstance(snap, str):
        if len(snap.encode("utf-8")) > MAX_BYTES:
            raise Refused(f"snapshot: over {MAX_BYTES} bytes")
        try:
            snap = json.loads(snap)
        except ValueError:
            raise Refused("snapshot: not JSON") from None
    if not isinstance(snap, dict):
        raise Refused("snapshot: expected the JSON of work_ledger_read compact=true")
    if len(json.dumps(snap).encode("utf-8")) > MAX_BYTES:
        raise Refused(f"snapshot: over {MAX_BYTES} bytes")
    conductor = snap.get("conductor") if isinstance(snap.get("conductor"), dict) else {}
    items = snap.get("items")
    if not isinstance(items, list):
        raise Refused("snapshot.items: expected a list")
    if len(items) > MAX_ITEMS:
        raise Refused(f"snapshot.items: over {MAX_ITEMS} items")
    slot = _opt_key(conductor.get("slot_key"), "snapshot.conductor.slot_key")
    reporter = _opt_key(args.get("reporter_session_key"), "reporter_session_key") or slot
    if reporter is None:
        raise Refused("reporter_session_key: missing, and the snapshot has no conductor.slot_key")
    if slot and slot != reporter:
        raise Refused("reporter_session_key: differs from the snapshot's conductor.slot_key")
    return {
        "reporter": reporter,
        "role": _choice(args.get("role"), ROLES, "role", optional=False),
        "round": _int(args.get("round"), "round", 0, 9999),
        "goal": _text(conductor.get("goal"), "conductor.goal"),
        "items": [_item(x, i) for i, x in enumerate(items)],
    }


def team_dir(data=None):
    """``<data>/team``, refused when it is a link or resolves anywhere else.

    The file name inside it is a hash of the reporter key, so no argument can name a path; this
    check keeps a planted link from turning a snapshot write into a write to the sealed exams,
    the signed ledger or anything else.
    """
    data = Path(data or data_dir())
    path = data / "team"
    if path.is_symlink() or path.resolve() != data.resolve() / "team":
        raise Refused("team dir is a link or resolves outside the data dir; refusing to write")
    return path


def _file(reporter):
    return hashlib.sha256(reporter.encode("utf-8")).hexdigest()[:24] + ".json"


def ingest(args, now=time.time, data=None):
    """Validate and store one snapshot (replacing that reporter's last one); returns a short receipt."""
    rec = {**validate(args), "received_at": _stamp(now())}
    folder = team_dir(data)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / _file(rec["reporter"])
    if target.is_symlink():
        raise Refused("snapshot file is a symlink; refusing to write through it")
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".snap.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False)
    os.replace(tmp, target)
    _prune(folder)
    return {"ok": True, "reporter": rec["reporter"], "role": rec["role"], "items": len(rec["items"]),
            "received_at": rec["received_at"]}


def _prune(folder):
    """Keep the newest :data:`MAX_REPORTERS` snapshots."""
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[MAX_REPORTERS:]:
        old.unlink(missing_ok=True)


def read_all(data=None):
    """Every stored snapshot that still fits the shape (a hand-edited or broken file is left out)."""
    try:
        folder = team_dir(data)
    except Refused:
        return []
    out = []
    for path in folder.glob("*.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            out.append({**validate({"snapshot": {"conductor": {"slot_key": rec["reporter"], "goal": rec.get("goal")},
                                                 "items": rec["items"]}, "role": rec["role"], "round": rec.get("round")}),
                        "received_at": _when(rec.get("received_at"), "received_at")})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def _age_minutes(stamp, now):
    try:
        return (now - calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))) / 60
    except (TypeError, ValueError):
        return None


def _reporter(rec, now, stale_minutes):
    age = _age_minutes(rec["received_at"], now)
    return {"key": rec["reporter"], "role": rec["role"], "round": rec["round"], "goal": rec["goal"],
            "received_at": rec["received_at"], "stale": age is None or age > stale_minutes}


def _needs(item, reporter):
    """Why the owner must act on this item, or None."""
    if item["state"] == "open" and item["status"] in ("question", "blocked"):
        return item["status"]
    if item["verdict"] == "pass" and item["pr"] and item["state"] in ("open", "accepted"):
        return "merge"
    return None


def fold(records, now=None, stale_minutes=STALE_MINUTES):
    """Snapshots -> ``{leads, loose_lanes, counts, needs_you, reporters}``.

    A lane hangs under the lead item whose ``worker_session_key`` is the lane's reporter key;
    a lane no lead names is a loose lane. Every other item's worker is a leaf. Counts are over
    open items by status, plus closed items by state.
    """
    now = time.time() if now is None else now
    leads = sorted((r for r in records if r["role"] == "lead"), key=lambda r: r["received_at"] or "", reverse=True)
    lanes = {r["reporter"]: r for r in records if r["role"] == "lane"}
    used, counts, needs = set(), {}, []

    def node(item, owner):
        why = _needs(item, owner)
        if why:
            needs.append({"why": why, "item_id": item["item_id"], "title": item["title"], "pr": item["pr"],
                          "session": item["worker_session_key"] or owner["key"], "reporter": owner["key"]})
        bucket = (item["status"] or "none") if item["state"] == "open" else item["state"]
        counts[bucket] = counts.get(bucket, 0) + 1
        return {**item, "needs": why}

    def lane_view(rec):
        me = _reporter(rec, now, stale_minutes)
        return {**me, "items": [node(i, me) for i in rec["items"]]}

    out_leads = []
    for rec in leads:
        me = _reporter(rec, now, stale_minutes)
        items = []
        for item in rec["items"]:
            row = node(item, me)
            lane = lanes.get(item["worker_session_key"])
            if lane and lane["reporter"] not in used:
                used.add(lane["reporter"])
                row["lane"] = lane_view(lane)
            items.append(row)
        out_leads.append({**me, "items": items})
    loose = [lane_view(r) for k, r in sorted(lanes.items()) if k not in used]
    reporters = [_reporter(r, now, stale_minutes) for r in records]
    needs.sort(key=lambda n: NEEDS.index(n["why"]))
    return {"leads": out_leads, "loose_lanes": loose, "counts": counts, "needs_you": needs,
            "reporters": len(reporters), "stale_reporters": sum(r["stale"] for r in reporters),
            "stale_minutes": stale_minutes}


def view(data=None, now=None):
    return fold(read_all(data), now=now)
