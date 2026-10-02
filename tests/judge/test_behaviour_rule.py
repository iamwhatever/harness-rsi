"""The behaviour rule: a security exam that only reads a file is refused before it runs."""

import pytest

from judge import behaviour, validate


def exam(task, check):
    return {"id": "exam_x", "layer": 2, "origin": ["sig_20261001_0001"], "visibility": "hidden", "task": task,
            "check": check, "created_round": 4, "used_rounds": []}


FILE = {"kind": "file_assert", "path": "out.txt", "contains": "https://docs.example/d/1"}
RUN = {"kind": "exit_code", "cmd": ["true"], "expect": 0}


@pytest.mark.parametrize("task", ["Confirm the link survives the redaction pass.", "Auth token is refused",
                                  "No secret leaks into the log", "Security gate passes benign text"])
def test_security_file_assert_is_refused(tmp_path, task):
    (tmp_path / "out.txt").write_text("https://docs.example/d/1")  # the file is there: only the rule refuses
    code, report = validate.validate(exam(task, FILE), tmp_path)
    assert code == 1 and report["status"] == "refused" and "behaviour check" in report["detail"]


def test_context_alone_makes_an_exam_security_scoped(tmp_path):
    (tmp_path / "out.txt").write_text("https://docs.example/d/1")
    plain = exam("The link is kept intact.", FILE)
    assert validate.validate(plain, tmp_path)[0] == 0
    code, _ = validate.validate(plain, tmp_path, context=["Security module redacts legitimate URLs"])
    assert code == 1


def test_behaviour_check_and_plain_file_assert_still_run(tmp_path):
    (tmp_path / "out.txt").write_text("https://docs.example/d/1")
    assert validate.validate(exam("Redaction keeps benign links", RUN), tmp_path) == (
        0, {"exam_id": "exam_x", "status": "pass", "detail": "exit 0, expected 0"})
    assert validate.validate(exam("The chip names the default model", FILE), tmp_path)[1]["status"] == "pass"


def test_refusal_ignores_malformed_rows():
    assert behaviour.refusal({"check": "file_assert", "task": "secret"}) is None
    assert behaviour.refusal("not an exam") is None
