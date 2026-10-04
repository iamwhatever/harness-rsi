"""Read-only access to KiroCrew session crew logs, for the fast-tier scripts.

Layout (KiroCrew ``docs/system-specs/modules/crew-log-core.md``)::

    <data home>/crew-log/sessions/<store name>/log.jsonl        segment from seq 1
    <data home>/crew-log/sessions/<store name>/log.<seq>.jsonl  later segments

Line 1 of each segment is the header ``{"type": "session", "id": <slot>, ...}``; every later
line is an entry ``{"type", "seq", "time", "src", "data"}``. The data home is ``$KIROCREW_HOME``
or ``~/.kiro/crew``. Inside a KiroCrew sandbox the ``crew-log`` dir is masked to an empty
directory, so run these scripts from a plain shell.

Nothing here writes. A damaged line is skipped, as KiroCrew's own reader does.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_SEGMENT_RE = re.compile(r"\Alog(?:\.([1-9][0-9]*))?\.jsonl\Z")


def default_root():
    home = os.environ.get("KIROCREW_HOME") or Path.home() / ".kiro" / "crew"
    return Path(home).expanduser() / "crew-log" / "sessions"


@dataclass
class Unit:
    slot: str
    entries: list = field(default_factory=list)

    def of(self, *types):
        return [e for e in self.entries if e.get("type") in types]


def _segments(unit_dir):
    found = []
    for path in unit_dir.iterdir():
        m = _SEGMENT_RE.match(path.name)
        if m and path.is_file():
            found.append((int(m.group(1) or 1), path))
    return [p for _, p in sorted(found)]


def _read_unit(unit_dir):
    slot, entries = None, []
    for path in _segments(unit_dir):
        with path.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                if i == 0:
                    if row.get("type") == "session" and isinstance(row.get("id"), str):
                        slot = slot or row["id"]
                    continue
                if isinstance(row.get("data"), dict):
                    entries.append(row)
    if slot is None:
        return None
    entries.sort(key=lambda e: (e.get("seq") or 0))
    return Unit(slot, entries)


def load_units(root=None):
    """``{slot: Unit}`` for every readable session crew log under ``root``.

    One slot can own several logs (a new ACP session after a restart); their entries merge, by time.
    """
    root = Path(root) if root else default_root()
    units = {}
    if not root.is_dir():
        return units
    for unit_dir in sorted(root.iterdir()):
        if not unit_dir.is_dir():
            continue
        unit = _read_unit(unit_dir)
        if unit is None:
            continue
        if unit.slot in units:
            merged = units[unit.slot].entries + unit.entries
            units[unit.slot].entries = sorted(merged, key=lambda e: (e.get("time") or 0, e.get("seq") or 0))
        else:
            units[unit.slot] = unit
    return units


def _parent_slot(data):
    parent = data.get("parent")
    return parent.get("slot") if isinstance(parent, dict) and isinstance(parent.get("slot"), str) else None


def parents(units):
    """``{slot: parent slot or None}``: the creating edge from ``session/opened``, then the newest
    ``session/adopted`` / ``session/released`` per slot, as KiroCrew's ``session_tree`` folds it."""
    out = {}
    for slot, unit in units.items():
        created = next((p for p in (_parent_slot(e["data"]) for e in unit.of("session/opened")) if p), None)
        moves = unit.of("session/adopted", "session/released")
        if moves:
            last = max(moves, key=lambda e: (e.get("time") or 0, e.get("seq") or 0))
            created = _parent_slot(last["data"]) if last["type"] == "session/adopted" else None
        out[slot] = created
    return out


def subtree(units, root_slot):
    """``[(slot, depth)]`` for ``root_slot`` and every descendant, parents before children; a cycle stops."""
    children = {}
    for slot, parent in parents(units).items():
        if parent and parent != slot:
            children.setdefault(parent, []).append(slot)
    order, seen, stack = [], set(), [(root_slot, 0)]
    while stack:
        slot, depth = stack.pop()
        if slot in seen:
            continue
        seen.add(slot)
        order.append((slot, depth))
        stack.extend((c, depth + 1) for c in sorted(children.get(slot, []), reverse=True))
    return order


def agent_of(unit):
    if unit is None:
        return ""
    opened = unit.of("session/opened")
    return opened[0]["data"].get("agent", "") if opened else ""
