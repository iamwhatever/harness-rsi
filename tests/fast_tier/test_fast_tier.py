"""tools/fast_tier: team fold, trust restart reader, break probe and trust preflight on fake inputs."""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools" / "fast_tier"), str(ROOT / "tests" / "fast_tier")]
import break_probe  # noqa: E402
import crewlog  # noqa: E402
import fakelog  # noqa: E402
import team_fold  # noqa: E402
import trust_preflight  # noqa: E402
import trust_restart_check  # noqa: E402


def test_subtree_follows_parent_edges_and_skips_others(tmp_path):
    units = crewlog.load_units(fakelog.tree(tmp_path))
    assert crewlog.subtree(units, "lead") == [("lead", 0), ("lane", 1), ("worker", 2)]


def test_adoption_moves_a_session_and_later_segments_are_read(tmp_path):
    fakelog.tree(tmp_path)
    unit = next(p for p in tmp_path.iterdir() if p.name.startswith("worker-"))
    later = [{"type": "session", "version": 1, "id": "worker", "createdAt": 0},
             {"type": "session/adopted", "seq": 8, "time": 50, "src": "gateway", "data": {"parent": {"slot": "lead"}}}]
    (unit / "log.8.jsonl").write_text("".join(json.dumps(x) + "\n" for x in later), encoding="utf-8")
    units = crewlog.load_units(tmp_path)
    assert dict(crewlog.subtree(units, "lead")) == {"lead": 0, "lane": 1, "worker": 1}


def test_team_fold_sums_credits_declines_and_work(tmp_path):
    team = team_fold.fold_team(crewlog.load_units(fakelog.tree(tmp_path)), "lead")
    t = team["total"]
    assert (t["sessions"], t["turns"], t["credits"], t["approvals"]) == (3, 4, 4.0, 2)
    assert dict(t["host_declines"]) == {"timeout": 1}
    assert dict(t["work_status"]) == {"progress": 1, "blocked": 1}
    assert dict(t["work_action"]) == {"create": 1, "report": 2}
    md = team_fold.markdown(team)
    assert "| credits | 4.0 |" in md and "timeout 1" in md


def test_team_fold_missing_root_exits_1(tmp_path, capsys):
    assert team_fold.main(["nobody", "--root", str(fakelog.tree(tmp_path))]) == 1
    assert "No crew log" in capsys.readouterr().out


def test_trust_posture_after_restart(tmp_path):
    units = crewlog.load_units(fakelog.tree(tmp_path))
    rows = {r["slot"]: r["posture"] for r in trust_restart_check.report(units, "lead", 100)}
    assert rows == {"lead": "idle", "lane": "asked", "worker": "declined"}
    rows = {r["slot"]: r["posture"] for r in trust_restart_check.report(units, "lead", 0)}
    assert rows["lead"] == "no prompt"
    assert trust_restart_check.parse_when("1970-01-01T00:00:01Z") == 1000


def test_trust_restart_prints_checklist(tmp_path, capsys):
    assert trust_restart_check.main(["lead", "--root", str(fakelog.tree(tmp_path)), "--since", "100"]) == 0
    out = capsys.readouterr().out
    assert "| lane | 1 | kirocrew-conductor | asked |" in out.replace("  lane", "lane")
    assert "restart the gateway" in out


def _data(tmp_path, hidden_text):
    data = tmp_path / "real"
    (data / "exams" / "hidden").mkdir(parents=True)
    (data / "outcomes.jsonl").write_text("", encoding="utf-8")
    if hidden_text is not None:
        (data / "exams" / "hidden" / "exam_x.json").write_text(hidden_text, encoding="utf-8")
    return data


def test_break_probe_rejects_forged_scores_and_never_touches_the_source(tmp_path, capsys):
    data = _data(tmp_path, json.dumps({"id": "exam_x", "check": {"kind": "exit_code"}}))
    before = {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    results = {name: verdict for name, verdict, _ in break_probe.run(data)}
    assert results == {"forge-write": "BROKEN", "forge-accepted": "SEALED", "hidden-read": "BROKEN"}
    assert {p: p.read_bytes() for p in data.rglob("*") if p.is_file()} == before
    assert break_probe.main(["--data", str(data)]) == 1
    assert "exit_code" not in capsys.readouterr().out


def test_break_probe_reads_sealed_for_encrypted_exams(tmp_path):
    results = {name: verdict for name, verdict, _ in break_probe.run(_data(tmp_path, "\x00ciphertext"))}
    assert results["hidden-read"] == "SEALED"
    results = {name: verdict for name, verdict, _ in break_probe.run(_data(tmp_path / "b", None))}
    assert results["hidden-read"] == "SKIP"


def test_preflight_lists_uncovered_tools(tmp_path, capsys):
    spec = {"name": "c", "tools": ["@kirocrew-dashboard", "@kirocrew-work", "@kirocrew-core", "execute_bash"],
            "allowedTools": ["shell", "@kirocrew-work", "@kirocrew-dashboard/session_*"]}
    path = tmp_path / "c.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    missing, absent = trust_preflight.check(spec, trust_preflight.FLOWS["conductor"])
    assert missing == ["@kirocrew-core/monitor_start"] and absent == []
    assert trust_preflight.main([str(path), "--tool", "@other/x"]) == 1
    assert "@kirocrew-core/monitor_start, @other/x | @other/x |" in capsys.readouterr().out
    spec["allowedTools"].append("*")
    path.write_text(json.dumps(spec), encoding="utf-8")
    assert trust_preflight.main([str(path)]) == 0


def test_preflight_resolves_agent_names(tmp_path):
    (tmp_path / "w.json").write_text(json.dumps({"allowedTools": ["@kirocrew-work/work_report"]}), encoding="utf-8")
    assert trust_preflight.main(["w", "--flow", "worker", "--agents-dir", str(tmp_path)]) == 1
