"""Publishing a round's exams into the shared bank: validated, idempotent, rejects filed with a reason."""

import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location("run_round_publish", ROOT / "crew" / "run_round.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def exam(name, task="Do the thing", check=None, origin="sig_20261001_0001"):
    return {"id": f"exam_{name}", "layer": 2, "origin": [origin], "visibility": "hidden", "task": task,
            "check": check or {"kind": "exit_code", "cmd": ["true"], "expect": 0}, "created_round": 4,
            "used_rounds": []}


def round_dir(tmp, exams, signals=(), props=()):
    d = tmp / "rounds" / "round-4"
    (d / "exams" / "hidden").mkdir(parents=True)
    for x in exams:
        (d / "exams" / "hidden" / f"{x['id']}.json").write_text(json.dumps(x))
    (d / "signals.jsonl").write_text("".join(json.dumps(s) + "\n" for s in signals))
    (d / "proposals.json").write_text(json.dumps(list(props)))
    return d


class FakeClassifier:
    """Status by exam id; every call is recorded with the context it got."""

    def __init__(self, status):
        self.status, self.calls = status, []

    def __call__(self, row, context):
        self.calls.append((row["id"], list(context)))
        return self.status.get(row["id"], "runnable"), "fake detail"


def test_publish_files_each_exam_by_its_verdict_and_keeps_the_round_copy(tmp_path):
    mod, bank = load(), tmp_path / "bank"
    src = round_dir(tmp_path, [exam("ok"), exam("bad"), exam("later")])
    out = mod.publish_round(src, bank, FakeClassifier({"exam_bad": "missing-module", "exam_later": "held"}))
    assert {k: out[k] for k in ("published", "rejected", "held", "skipped")} == {
        "published": 1, "rejected": 1, "held": 1, "skipped": 0}
    assert [p.name for p in (bank / "hidden").iterdir()] == ["exam_ok.json"]
    assert (bank / "rejected" / "exam_bad.json").is_file()
    reason = json.loads((bank / "rejected" / "reasons.jsonl").read_text())
    assert reason["exam_id"] == "exam_bad" and reason["reason"] == "missing-module"
    assert len(list((src / "exams" / "hidden").glob("*.json"))) == 3  # the record stays


def test_publish_is_idempotent(tmp_path):
    mod, bank = load(), tmp_path / "bank"
    src = round_dir(tmp_path, [exam("ok"), exam("bad")])
    mod.publish_round(src, bank, FakeClassifier({"exam_bad": "refused"}))
    again = FakeClassifier({})
    out = mod.publish_round(src, bank, again)
    assert out["skipped"] == 2 and out["published"] == 0 and again.calls == []
    assert all("already in the bank" in n for n in out["notes"])
    assert len((bank / "rejected" / "reasons.jsonl").read_text().splitlines()) == 1


def test_classifier_sees_signal_and_proposal_pain(tmp_path):
    mod = load()
    sig = {"id": "sig_20261001_0001", "pain": "Security module redacts Docs links"}
    prop = {"id": "prop_x", "pain": "Fix URL redaction false positives", "exam_ids": ["exam_ok"]}
    fake = FakeClassifier({})
    mod.publish_round(round_dir(tmp_path, [exam("ok")], [sig], [prop]), tmp_path / "bank", fake)
    assert fake.calls == [("exam_ok", ["Security module redacts Docs links", "Fix URL redaction false positives"])]


def test_a_round_written_into_the_bank_is_not_republished(tmp_path):
    mod = load()
    (tmp_path / "exams" / "hidden").mkdir(parents=True)
    out = mod.publish_round(tmp_path, tmp_path / "exams", FakeClassifier({}))
    assert out["published"] == 0 and out["notes"] == ["round dir is the bank"]


def test_bank_classifier_refuses_a_security_file_assert(tmp_path):
    mod = load()
    (tmp_path / "out.txt").write_text("https://docs.example/d/1")
    row = exam("link", "Link survives", {"kind": "file_assert", "path": "out.txt", "contains": "docs.example"})
    classify = mod.bank_classifier(tmp_path)
    assert classify(row, [])[0] == "runnable"
    assert classify(row, ["URL redaction false positives"])[0] == "needs-behaviour-check"
    assert classify(exam("cmd", check={"kind": "exit_code", "cmd": ["no-such-cmd-xyz"], "expect": 0}),
                    [])[0] == "missing-command"


def test_a_round_publishes_its_exams_when_it_ends(tmp_path):
    spec = importlib.util.spec_from_file_location("rr_fakes", ROOT / "tests" / "crew" / "test_run_round.py")
    fakes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fakes)
    mod, bank, fake = load(), tmp_path / "bank", FakeClassifier({})
    data = tmp_path / "round"
    data.mkdir()
    result = mod.run_round(agent=fakes.FakeCrew(), collectors=[lambda: fakes.SIGNALS], data=data, rnd=2,
                           day="20260930", save_mock=lambda slug, title, page: slug, bank=bank, classify_bank=fake)
    assert result["published"]["published"] == len(result["exams"]) > 0
    assert sorted(p.stem for p in (bank / "hidden").glob("*.json")) == sorted(x["id"] for x in result["exams"])
