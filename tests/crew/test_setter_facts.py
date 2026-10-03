"""The question setter's facts about the exam checkout, and its validate -> repair loop."""

import importlib.util
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "crew"))
import target_facts as tf  # noqa: E402

sys.path.insert(0, str(ROOT))
from judge import audit  # noqa: E402


def load():
    spec = importlib.util.spec_from_file_location("run_round", ROOT / "crew" / "run_round.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def checkout(tmp):
    """A tiny KiroCrew-shaped repo: one product module, one test module, one UI hook."""
    (tmp / "src/kiro_crew/security").mkdir(parents=True)
    (tmp / "src/kiro_crew/__init__.py").write_text("")
    (tmp / "src/kiro_crew/security/__init__.py").write_text("")
    (tmp / "src/kiro_crew/security/redaction.py").write_text(
        "def redact_credentials(text) -> str:\n    return text.replace('SECRET', '[REDACTED]').replace('docs', '[R]')\n\n"
        "def _private():\n    pass\n\nclass CredentialMatch:\n    pass\n")
    (tmp / "src/kiro_crew/security/tests").mkdir()
    (tmp / "src/kiro_crew/security/tests/test_redaction_links.py").write_text("def test_redaction_links(): pass\n")
    (tmp / "src/kiro_crew/scheduler.py").write_text("def next_cron_tick(spec, now):\n    return now\n")
    (tmp / "website/src/components").mkdir(parents=True)
    (tmp / "website/src/components/Chip.tsx").write_text('<span data-testid="composer-model-chip">x</span>')
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    return tmp


SIG = {"id": "sig_20261001_0001", "source": "github:o/r", "links": ["https://example.com/1"],
       "pain": "Redaction mangles shared document links as if they were credentials",
       "mentions": {"count": 3, "people": 2, "window_days": 7}, "layer": "real",
       "testable": {"ok": True, "task": "Benign links survive redaction"}, "dedup_of": None}


def exam(eid, cmd):
    return {"id": eid, "layer": 2, "origin": [SIG["id"]], "visibility": "hidden", "task": "Links survive",
            "check": {"kind": "exit_code", "cmd": cmd, "expect": 0}, "created_round": 0, "used_rounds": []}


def test_modules_list_public_product_names_only(tmp_path):
    mods = tf.modules(checkout(tmp_path))
    assert mods["kiro_crew.security.redaction"] == ["redact_credentials(text) -> str", "CredentialMatch"]
    assert "kiro_crew.security" in mods and not any("tests" in m for m in mods)


def test_facts_point_each_signal_at_its_real_code(tmp_path):
    text = tf.facts(checkout(tmp_path), [SIG, {**SIG, "id": "sig_20261001_0002", "dedup_of": SIG["id"]}],
                    ["turn_count", "steer_rate"])
    assert f"{SIG['id']}: kiro_crew.security.redaction\n" in text
    assert "\nkiro_crew.security.redaction: redact_credentials(text) -> str, CredentialMatch\n" in text
    assert "scheduler" not in text and "sig_20261001_0002" not in text  # a dup gets no exam, so no facts
    assert "metric_threshold may name ONLY these): turn_count steer_rate" in text
    assert "sys.path.insert(0, 'src')" in text and "SPA: not built" in text


def test_a_signal_with_no_real_code_says_so(tmp_path):
    text = tf.facts(checkout(tmp_path), [{**SIG, "pain": "Zebra quokka", "testable": {"ok": True, "task": "x"}}], [])
    assert "(no close match: prefer writing no exam over guessing)" in text


def test_examples_take_one_regression_exam_per_kind_and_never_a_hidden_one(tmp_path):
    for n, kind in enumerate(["exit_code", "exit_code", "dom_assert", "file_assert"]):
        vis = "hidden" if kind == "file_assert" else "regression"
        (tmp_path / f"exam_{n}.json").write_text(json.dumps({"id": f"exam_{n}", "visibility": vis, "check": {"kind": kind}}) + " " * n)
    assert [x["id"] for x in tf.examples(tmp_path)] == ["exam_0", "exam_2"]


def test_refused_exams_go_back_with_reasons_and_come_back_runnable(tmp_path):
    rr, work = load(), checkout(tmp_path)
    good = ["python3", "-c", "import sys; sys.path.insert(0, 'src'); "
            "from kiro_crew.security.redaction import redact_credentials as r; sys.exit(r('see docs') != 'see docs')"]
    replies = [json.dumps([exam("exam_links", ["python3", "-m", "harness.tests.links"]),
                           exam("exam_fake_import", ["python3", "-c", "import sys; sys.path.insert(0, 'src'); "
                                                     "from kiro_crew.nowhere import x"])]),
               json.dumps([exam("exam_links", good), exam("exam_sneaked_in", good)])]
    calls = []

    def agent(name, msg):
        calls.append(msg)
        return replies[len(calls) - 1] if len(calls) <= len(replies) else "[]"
    refused = []
    kept = rr.write_exams(agent, [SIG], 7, rr.validate_checker(work), refused, facts="FACTS-BLOCK")
    assert [e["id"] for e in kept] == ["exam_links"]  # a repair may not add an exam it was not sent
    assert "FACTS-BLOCK" in calls[0] and "FACTS-BLOCK" in calls[1]
    assert "missing-module" in calls[1] and "No module named 'kiro_crew.nowhere'" in calls[1]
    assert len(calls) == 2 and [r["id"] for r in refused] == ["exam_fake_import"]


def test_repairs_stop_at_the_cap(tmp_path):
    rr, calls = load(), []
    bad = json.dumps([exam("exam_x", ["no-such-cmd-r72"])])
    refused = []
    rr.write_exams(lambda n, m: calls.append(m) or bad, [SIG], 7, rr.validate_checker(tmp_path), refused, repairs=2)
    assert len(calls) == 3 and refused == [{"id": "exam_x", "detail": "missing-command: missing-command"}]


def test_held_exams_are_not_sent_back(tmp_path):
    rr, calls = load(), []
    dom = {**exam("exam_dom", ["true"]), "check": {"kind": "dom_assert", "root": "dist", "path": "/",
                                                    "selector": "#a", "text": "b"}}
    rr.write_exams(lambda n, m: calls.append(m) or json.dumps([dom]), [SIG], 7, rr.validate_checker(tmp_path), [])
    assert len(calls) == 1


def test_an_exam_that_passes_where_the_pain_is_real_goes_back(tmp_path):
    rr, work, calls = load(), checkout(tmp_path), []
    blind = exam("exam_blind", ["python3", "-c", "import sys; sys.path.insert(0, 'src'); "
                                "from kiro_crew.security.redaction import redact_credentials as r; "
                                "sys.exit(r('a SECRET') == 'a SECRET')"])
    refused = []
    rr.write_exams(lambda n, m: calls.append(m) or json.dumps([blind]), [SIG], 7, rr.validate_checker(work),
                   refused, repairs=1)
    assert len(calls) == 2 and "passes-unfixed" in calls[1] and refused[0]["detail"].startswith("passes-unfixed")
    assert rr.bank_classifier(work)(blind, [])[0] == "runnable"  # publishing later, after a fix, is fine


def test_a_missing_checkout_gives_no_facts(tmp_path):
    assert tf.facts(tmp_path / "gone", [SIG], ["turn_count"]) == ""


def test_a_fail_that_broke_on_the_exams_own_code_is_not_runnable(tmp_path):
    def run(src):
        exam = {"id": "exam_x", "layer": 2, "origin": ["sig_20260101_0001"], "visibility": "hidden", "task": "t",
                "check": {"kind": "exit_code", "cmd": [sys.executable, "-c", src], "expect": 0},
                "created_round": 0, "used_rounds": []}
        return audit.classify(exam, tmp_path, {}, None, 30)
    assert run("import no_such_mod_r72") == ("missing-module", "exit 1, expected 0: ModuleNotFoundError: "
                                             "No module named 'no_such_mod_r72'")
    assert run("import json; json.no_such_fn_r72()")[0] == "missing-module"
    assert run("len(5)")[0] == run("undefined_r72")[0] == "broken-check"
    assert run("assert 1 == 2, 'benign link redacted'") == ("runnable", "exit 1, expected 0: AssertionError: "
                                                             "benign link redacted")
