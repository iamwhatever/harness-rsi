"""Outcome ledger and auto-score on a real local git origin and a fake ``gh``: link, score, merge, regress."""

import io
import json
import pathlib
import re
import subprocess
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import autoscore, ledger, store  # noqa: E402

PROPOSALS = json.loads((ROOT / "fixtures/proposals.json").read_text(encoding="utf-8"))
EXAM = {"id": "exam_bg_task_survives_close", "layer": 2, "origin": ["sig_20260101_0001"], "visibility": "hidden",
        "task": "The fix file exists.", "check": {"kind": "exit_code", "cmd": ["test", "-f", "fixed.txt"], "expect": 0},
        "created_round": 1, "used_rounds": []}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class Origin:
    """A local "GitHub": a repo with main and refs/pull/N/head, and the PR objects ``gh`` would return."""

    def __init__(self, tmp):
        self.dir, self.prs = tmp / "origin", {}
        self.dir.mkdir()
        git(self.dir, "init", "-q", "-b", "main")
        git(self.dir, "config", "user.email", "t@example.com")
        git(self.dir, "config", "user.name", "t")
        (self.dir / "README").write_text("base\n", encoding="utf-8")
        git(self.dir, "add", ".")
        git(self.dir, "commit", "-qm", "base")
        self.base = git(self.dir, "rev-parse", "HEAD")
        git(self.dir, "checkout", "-qb", "feat")
        (self.dir / "fixed.txt").write_text("fix\n", encoding="utf-8")
        git(self.dir, "add", ".")
        git(self.dir, "commit", "-qm", "fix")
        self.head = git(self.dir, "rev-parse", "HEAD")
        git(self.dir, "checkout", "-q", "main")

    def pr(self, n, body, fork=False):
        git(self.dir, "update-ref", f"refs/pull/{n}/head", self.head)
        self.prs[n] = {"number": n, "title": "fix(x): a fix", "body": body, "state": "OPEN", "isCrossRepository": fork,
                       "baseRefOid": self.base, "headRefOid": self.head, "mergeCommit": None}

    def merge(self, n):
        git(self.dir, "merge", "-q", "--squash", "feat")
        git(self.dir, "commit", "-qm", "squash")
        self.prs[n].update(state="MERGED", mergeCommit={"oid": git(self.dir, "rev-parse", "HEAD")})
        return self.prs[n]["mergeCommit"]["oid"]

    def gh(self, argv, **_):
        """``gh api graphql``: the recent list holds every PR; aliases answer the numbers asked for."""
        q = argv[-1]
        assert argv[:3] == ["gh", "api", "graphql"] and "mutation" not in q  # read-only
        repo = {"recent": {"nodes": list(self.prs.values())}}
        repo |= {f"p{n}": self.prs.get(int(n)) for n in re.findall(r"p(\d+): pullRequest", q)}
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"data": {"repository": repo}}), stderr="")


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("HARNESS_RSI_DATA", str(data))
    (data / "exams" / "hidden").mkdir(parents=True)
    (data / "exams" / "hidden" / f"{EXAM['id']}.json").write_text(json.dumps(EXAM), encoding="utf-8")
    (data / "proposals.json").write_text(json.dumps(PROPOSALS), encoding="utf-8")
    store.append_decision("prop_bg_tasks", "do")
    origin = Origin(tmp_path)
    kc = tmp_path / "kc"
    subprocess.run(["git", "clone", "-q", str(origin.dir), str(kc)], check=True)
    return types.SimpleNamespace(data=data, origin=origin, kc=str(kc), tick=lambda **kw: autoscore.tick(str(kc), run=origin.gh, budget=lambda: 5000, **kw))


def lines(env):
    return (env.data / ledger.FILE).read_text(encoding="utf-8").splitlines()


def test_detected_pr_is_scored_on_base_and_head_and_fork_code_never_runs(env):
    env.origin.pr(7, "Closes the card prop_bg_tasks: the fix file.")
    env.origin.pr(8, "prop_bg_tasks from a fork", fork=True)
    env.origin.pr(9, "names prop_nope, which is no card")
    assert {u["pr"]: (u["base"], u["head"]) for u in env.tick()["updated"]} == {
        "kirodotdev/KiroCrew#7": ("fail", "pass"), "kirodotdev/KiroCrew#8": (None, None)}
    row = ledger.get("prop_bg_tasks", "kirodotdev/KiroCrew#7")
    assert (row["decision"], row["link"], row["exam_ids"], row["note"]) == ("do", "detected", [EXAM["id"]], "")
    assert row["score"]["base"]["sha"] == env.origin.base and row["score"]["head"]["sha"] == env.origin.head
    assert row["score"]["metrics"] is None  # nothing measured for this PR
    fork = ledger.get("prop_bg_tasks", "kirodotdev/KiroCrew#8")
    assert fork["exam_ids"] == [EXAM["id"]] and fork["score"]["head"] is None and fork["note"] == "fork PR: not run"
    assert all(store.valid("outcome", json.loads(x)) for x in lines(env))
    assert "Closes the card" not in (env.data / ledger.FILE).read_text(encoding="utf-8")  # no PR body kept
    assert not git(env.kc, "worktree", "list").count("\n")  # every throwaway worktree is gone


def test_an_unchanged_pr_is_not_rescored_and_merge_promotes_and_regresses(env, monkeypatch):
    env.origin.pr(7, "prop_bg_tasks")
    env.tick()
    before, judge_at = len(lines(env)), autoscore.judge_at
    monkeypatch.setattr(autoscore, "judge_at", lambda *a: pytest.fail("rescored an unchanged head"))
    assert env.tick()["updated"] == [] and len(lines(env)) == before  # same head: no judge run, no row
    monkeypatch.setattr(autoscore, "judge_at", judge_at)
    sha = env.origin.merge(7)
    env.tick()
    row = ledger.get("prop_bg_tasks", "kirodotdev/KiroCrew#7")
    assert (row["state"], row["merged_sha"], row["promoted"]) == ("merged", sha, [EXAM["id"]])
    assert [(g["sha"], g["exams"], g["regressions"]) for g in row["regress"]] == [(sha, {EXAM["id"]: "pass"}, 0)]
    assert json.loads(next((env.data / "exams").rglob(f"{EXAM['id']}.json")).read_text())["visibility"] == "regression"
    assert (env.data / "regress" / f"{sha}.json").is_file()
    later = json.loads((env.data / "regress" / f"{sha}.json").read_text())
    later.update(sha="f" * 40, at="2999-01-01T00:00:00+00:00", exams={EXAM["id"]: False},
                 regressions=[{"kind": "exam", "exam_id": EXAM["id"]}])
    (env.data / "regress" / ("f" * 40 + ".json")).write_text(json.dumps(later))  # the daily regress, later
    env.tick()
    got = ledger.get("prop_bg_tasks", "kirodotdev/KiroCrew#7")["regress"]
    assert [(g["sha"][:1], g["exams"][EXAM["id"]], g["regressions"]) for g in got] == [(sha[:1], "pass", 0), ("f", "fail", 1)]


def test_backfill_cli_scores_one_pr_with_or_without_a_card_and_stops_on_the_rate_floor(env):
    env.origin.pr(7, "no card id in here")
    out = io.StringIO()
    argv = ["--kirocrew", env.kc, "--pr", "7"]
    assert autoscore.main(argv + ["--card", "prop_bg_tasks"], out, env.origin.gh, lambda: 5000) == 0
    assert ledger.get("prop_bg_tasks", "kirodotdev/KiroCrew#7")["link"] == "backfill"
    assert autoscore.main(argv, io.StringIO(), env.origin.gh, lambda: 5000) == 0
    assert ledger.get(None, "kirodotdev/KiroCrew#7")["note"] == "no card; no exam"
    n = len(lines(env))
    assert autoscore.main(argv, io.StringIO(), env.origin.gh, lambda: 10) == 3 and len(lines(env)) == n


def test_ledger_newest_row_wins_and_a_bad_row_is_refused(env):
    ledger.link("prop_bg_tasks", "o/r#1")
    assert ledger.link("prop_bg_tasks", "o/r#1", "detected")["link"] == "board"  # linked once
    ledger.put({**ledger.get("prop_bg_tasks", "o/r#1"), "note": "second"})
    assert [r["note"] for r in ledger.rows()] == ["second"]
    with pytest.raises(ValueError):
        ledger.put({**ledger.blank("o/r#2"), "body": "x"})
    (env.data / ledger.FILE).open("a").write("not json\n{}\n")
    assert len(ledger.rows()) == 1
