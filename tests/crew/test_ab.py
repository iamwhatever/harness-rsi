"""crew/ab.py on a fake data dir: the setter hit rate, reviewer rates, credits and the noise-band verdict."""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "crew"), str(ROOT / "tests" / "crew")]
import ab  # noqa: E402
import prompts  # noqa: E402
from judge import seal  # noqa: E402
from test_run_round import FakeCrew  # noqa: E402

SETTER = "rsi-question-setter"
FIX_LINK = "https://github.com/kirodotdev/KiroCrew/issues/1"
SHA = {"base": "a" * 40, "head": "b" * 40}


def signal(i, links, day="20261001"):
    return {**FakeCrew.signal(i, "github:kirodotdev/KiroCrew", "real"), "id": f"sig_{day}_{i:04d}", "links": links}


def exam(sig, marker):
    return {"id": "exam_" + sig["id"][4:], "layer": 2, "origin": [sig["id"]], "visibility": "hidden",
            "task": "A plain check of the marker file.", "created_round": 3, "used_rounds": [],
            "check": {"kind": "exit_code", "cmd": ["test", "-f", marker], "expect": 0}}


def setup(tmp_path):
    """Round 3: signal 1 aims at the fixed pain (shares its link), signal 2 does not; one merged fix PR."""
    d = tmp_path / "rounds" / "round-3"
    (d / "exams" / "hidden").mkdir(parents=True)
    sigs = [signal(1, [FIX_LINK]), signal(2, ["https://example.com/other"])]
    (d / "signals.jsonl").write_text("".join(json.dumps(s) + "\n" for s in sigs))
    card = {**FakeCrew.prop(sigs[0]), "id": "prop_fixed"}
    (d / "proposals.json").write_text(json.dumps([card]))
    row = {"card_id": "prop_fixed", "pr": "kirodotdev/KiroCrew#7", "state": "merged", "base_sha": SHA["base"],
           "head_sha": SHA["head"], "decision": "do"}
    (tmp_path / "outcomes.jsonl").write_text(json.dumps(seal.sign(seal.OUTCOMES, row)) + "\n")
    trees = {k: tmp_path / k for k in SHA}
    for t in trees.values():
        t.mkdir()
    (trees["head"] / "fixed.txt").write_text("fixed")
    return sigs, (lambda fix: (trees["base"], trees["head"]))


def setter_reply(sigs, marker):
    return json.dumps([exam(sigs[0], marker), exam(sigs[1], "fixed.txt")])


def test_setter_hit_counts_only_exams_that_split_base_from_head(tmp_path):
    sigs, trees = setup(tmp_path)
    variant = prompts.effective(tmp_path)[SETTER] + "\nWrite sharper exams."
    replies = {prompts.version(prompts.effective(tmp_path)[SETTER]): "no.txt", prompts.version(variant): "fixed.txt"}
    calls = []

    def call(agent, text, message):
        calls.append(agent)
        return setter_reply(sigs, replies[prompts.version(text)]), 0.5

    out = ab.run(tmp_path, SETTER, variant, call, trees, [3, 4], 2)
    assert out["rounds"] == [3] and out["reps"] == 2 and calls == [SETTER] * 4
    hit = out["metrics"]["setter_hit_rate"]
    assert (hit["A"], hit["B"], hit["verdict"]) == (0.0, 1.0, "better")  # A's exam fails on head too
    assert out["failures"] == {"A": {"fails on head: exit 1": 2}, "B": {"hit": 2}}  # the off-target exam is not counted
    assert out["metrics"]["credits_per_turn"]["verdict"] == "same"
    ab.run(tmp_path, SETTER, variant, call, trees, [3], 2)
    assert len(calls) == 4  # replies are cached by prompt version


def test_a_target_signal_without_an_exam_is_a_miss(tmp_path):
    sigs, trees = setup(tmp_path)
    hit, why = ab.setter_sample("[]", sigs, 3, ab.fixes(tmp_path), trees)
    assert hit == 0.0 and why == {"no exam for signal": 1}


def test_verdict_reads_the_judge_noise_band_both_ways():
    assert ab.verdict("setter_hit_rate", [0.5, 0.5], [0.52, 0.53]) == "same"  # inside the 0.05 floor
    assert ab.verdict("setter_hit_rate", [0.2, 0.3], [0.8, 0.9]) == "better"
    assert ab.verdict("setter_hit_rate", [0.8, 0.9], [0.2, 0.3]) == "worse"
    assert ab.verdict("prior_art_fp_rate", [0.5, 0.5], [0.0, 0.0]) == "better"  # lower is better
    assert ab.verdict("credits_per_turn", [1.0, 1.0], [2.0, 2.0]) == "worse"
    assert ab.verdict("credits_per_turn", [], [1.0]) == "no data"


def test_reviewer_rates_from_decisions_and_merge_dates(tmp_path):
    sigs, _ = setup(tmp_path)
    done = signal(3, ["https://github.com/kirodotdev/KiroCrew/pull/9"])
    transcript = {"rounds": [{"round": n, "turns": [
        {"agent": a, "text": "x" if n == 1 else "```json\n" + json.dumps([FakeCrew.prop(s) for s in (sigs[0], sigs[1], done)]) + "\n```"}
        for a in ("rsi-reviewer-value", "rsi-reviewer-risk")]} for n in (1, 2)]}
    exams = [exam(s, "x") for s in (*sigs, done)]
    rates = ab.reviewer_sample(transcript, [*sigs, done], exams, ab.cards(tmp_path), {"9": "2026-09-01T00:00:00Z"})
    assert rates == {"adopt_rate": 1 / 3, "prior_art_fp_rate": 1 / 3}
    assert ab.reviewer_sample({"rounds": []}, sigs, exams, {}, {}) == {"adopt_rate": None, "prior_art_fp_rate": None}


def test_a_head_failure_names_its_error_class_not_the_exam(tmp_path):
    check = {"kind": "exit_code", "cmd": ["python3", "-c", "import no_such_module_here"], "expect": 0}
    assert ab.error_class(check, tmp_path) == "ModuleNotFoundError"
    assert ab.error_class({"kind": "exit_code", "cmd": ["false"], "expect": 0}, tmp_path) == "exit 1"
    assert ab.error_class({"kind": "file_assert"}, tmp_path) == "file_assert"
