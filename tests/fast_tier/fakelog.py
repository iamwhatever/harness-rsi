"""Write fake KiroCrew session crew logs for the fast-tier tests."""

import hashlib
import json


def write_unit(root, slot, entries, segment="log.jsonl"):
    """Write ``entries`` ``[(type, data, time)]`` as one session log for ``slot`` under ``root``."""
    unit = root / f"{slot.replace(':', '_')}-{hashlib.sha256(slot.encode()).hexdigest()[:8]}"
    unit.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "session", "version": 1, "id": slot, "createdAt": 0}]
    lines += [{"type": t, "seq": i, "time": when, "src": "gateway", "data": data}
              for i, (t, data, when) in enumerate(entries, 1)]
    (unit / segment).write_text("".join(json.dumps(x) + "\n" for x in lines) + "not json\n", encoding="utf-8")
    return unit


def opened(agent, parent=None, when=1):
    data = {"agent": agent, "slot": "", "model": "", "cwd": "", "owner": "o", "resumed": False}
    if parent:
        data["parent"] = {"slot": parent}
    return ("session/opened", data, when)


def tree(root):
    """lead -> lane -> worker, plus an unrelated session and a lane-less orphan."""
    write_unit(root, "lead", [opened("rsi-lead"),
                              ("turn/completed", {"turn": 1, "stop_reason": "end_turn", "credits": 1.5}, 10),
                              ("tool/called", {"turn": 1, "call_id": "a", "name": "shell", "server": "", "kind": ""}, 10)])
    write_unit(root, "lane", [opened("kirocrew-conductor", "lead"),
                              ("turn/completed", {"turn": 1, "stop_reason": "end_turn", "credits": 2.25}, 20),
                              ("work/recorded", {"slot": "lane", "actor": "conductor", "action": "create"}, 20),
                              ("tool/called", {"turn": 1, "call_id": "b", "name": "shell", "server": "", "kind": ""}, 200),
                              ("approval/requested", {"turn": 1, "approval_id": "x", "tool": "shell"}, 200)])
    write_unit(root, "worker", [opened("kirocrew-worker", "lane"),
                                ("turn/completed", {"turn": 1, "stop_reason": "end_turn", "credits": 0.25}, 30),
                                ("turn/completed", {"turn": 2, "stop_reason": "failed"}, 31),
                                ("approval/requested", {"turn": 2, "approval_id": "y", "tool": "shell"}, 300),
                                ("approval/decided", {"turn": 2, "approval_id": "y", "decision": "rejected",
                                                      "by": "host", "cause": "timeout"}, 900),
                                ("work/recorded", {"slot": "lane", "actor": "worker", "action": "report",
                                                   "status": "progress"}, 31),
                                ("work/recorded", {"slot": "lane", "actor": "worker", "action": "report",
                                                   "status": "blocked"}, 901)])
    write_unit(root, "other", [opened("kirocrew"),
                               ("turn/completed", {"turn": 1, "stop_reason": "end_turn", "credits": 9.0}, 5)])
    return root
