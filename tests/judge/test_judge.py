"""Judge CLI tests on fixtures/exams.json and fake workdirs; every output is checked against judge.schema.json."""

import copy
import hashlib
import io
import json
import pathlib
import struct
import subprocess
import sys
import zlib

import pytest
from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from judge.__main__ import main  # noqa: E402

EXAMS = json.loads((ROOT / "fixtures/exams.json").read_text())
EX = {e["id"]: e for e in EXAMS}
SCHEMA = Draft202012Validator(json.loads((ROOT / "schemas/judge.schema.json").read_text()))
stub_ratio = 0.0


def stub(baseline, current):
    return stub_ratio


def png(path, pixels, width):
    chunk = lambda k, d: struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d))  # noqa: E731
    rows = b"".join(b"\x00" + bytes(pixels[i : i + width * 3]) for i in range(0, len(pixels), width * 3))
    head = struct.pack(">IIBBBBB", width, len(pixels) // 3 // width, 8, 2, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def run(tmp, exams, suites, metrics=None, extra=()):
    (tmp / "exams").mkdir(exist_ok=True)
    (tmp / "exams/bg_task.py").write_text("raise SystemExit(0)")
    (tmp / "exams/set.json").write_text(json.dumps(exams))
    argv = ["--exams", str(tmp / "exams"), "--workdir", str(tmp), *extra]
    if metrics is not None:
        (tmp / "m.json").write_text(json.dumps(metrics))
        argv += ["--metrics", str(tmp / "m.json")]
    inp, out = {"pr": 1, "repo": "example-org/example-repo", "suites": suites}, io.StringIO()
    code = main(argv, io.StringIO(json.dumps(inp)), out)
    doc = json.loads(out.getvalue())
    assert [e.message for e in SCHEMA.iter_errors({"input": inp, "output": doc})] == []
    assert code == (0 if doc["verdict"] == "pass" else 1)
    return doc


def one(exam_id, **check):
    exam = copy.deepcopy(EX[exam_id])
    exam["check"].update(check)
    return [exam]


BG = {"cmd": [sys.executable, "exams/bg_task.py"]}


@pytest.mark.parametrize(
    "exam_id,check,ok",
    [
        ("exam_bg_task_survives_close", {**BG, "expect": 0}, True),
        ("exam_bg_task_survives_close", {**BG, "expect": 3}, False),
        ("exam_undo_three_files", {"sha256": hashlib.sha256(b"hello").hexdigest()}, True),
        ("exam_undo_three_files", {"sha256": "0" * 64}, False),
        ("exam_undo_three_files", {"path": "workspace/missing.txt"}, False),
    ],
)
def test_exit_code_and_file_assert(tmp_path, exam_id, check, ok):
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace/a.txt").write_bytes(b"hello")
    doc = run(tmp_path, one(exam_id, **check), [EX[exam_id]["visibility"]])
    assert doc["evidence"][0]["ok"] is ok and doc["verdict"] == ("pass" if ok else "fail")


@pytest.mark.parametrize("value,samples,ok", [(1600, range(1000, 2000, 10), True), (1000, range(1000, 2000, 10), False), (1600, [1], False)])
def test_metric_threshold(tmp_path, value, samples, ok):
    doc = run(tmp_path, one("exam_first_token_p50", value=value), ["metrics"], {"current": {"first_token_ms": list(samples)}})
    assert doc["evidence"][0]["ok"] is ok  # op lt on p50 = 1490; one sample is fewer than 100 runs


@pytest.mark.parametrize("ratio,ok", [(0.01, True), (0.02, False)])
def test_screenshot_diff_stub_comparer(tmp_path, ratio, ok):
    global stub_ratio
    stub_ratio = ratio
    png(tmp_path / "baseline/board.png", [0] * 3, 1)
    png(tmp_path / "shots/board.png", [0] * 3, 1)
    doc = run(tmp_path, [EX["exam_board_unchanged"]], ["regression"], extra=["--comparer", f"{__name__}:stub"])
    assert doc["evidence"][0]["ok"] is ok


@pytest.mark.parametrize("last,ok", [([10, 20, 30, 0, 0, 0], True), ([11, 20, 30, 0, 0, 0], False)])
def test_screenshot_diff_builtin_png(tmp_path, last, ok):
    png(tmp_path / "baseline/board.png", [10, 20, 30, 40, 50, 60] * 50, 10)
    png(tmp_path / "shots/board.png", [10, 20, 30, 40, 50, 60] * 49 + last, 10)
    doc = run(tmp_path, [EX["exam_board_unchanged"]], ["regression"])
    assert doc["evidence"][0]["ok"] is ok  # 1 of 100 pixels changed passes max 0.01; 2 fails


@pytest.mark.parametrize("base_rate,ok", [([0.9] * 100, False), ([0.8, 1.0] * 50, True)])
def test_paired_metrics(tmp_path, base_rate, ok):
    metrics = {
        "current": {"first_token_ms": [1000.0] * 100, "success_rate": [0.89] * 100},
        "baseline": {"first_token_ms": [1500.0] * 100, "success_rate": base_rate},
    }
    doc = run(tmp_path, [EX["exam_first_token_p50"]], ["metrics"], metrics)
    assert doc["scores"]["metrics"] == 1.0  # the latency exam alone passes
    assert doc["paired_metrics"] == [{"a": "first_token_ms", "b": "success_rate", "delta_a": -500.0, "delta_b": -0.01, "ok": ok}]
    assert doc["verdict"] == ("pass" if ok else "fail")  # a noisy baseline swallows the -0.01 move


def test_reused_hidden_exam_is_refused(tmp_path):
    exam = one("exam_bg_task_survives_close", **BG)[0]
    exam["used_rounds"] = [1]
    doc = run(tmp_path, [exam], ["hidden"], extra=["--round", "2"])
    assert doc["verdict"] == "fail" and doc["evidence"][0]["detail"].startswith("refused")
    assert run(tmp_path, [exam], ["hidden"], extra=["--round", "1"])["verdict"] == "pass"  # same-round rerun


def test_full_fixture_set_skips_retired(tmp_path):
    doc = run(tmp_path, EXAMS, ["new", "hidden", "regression", "metrics"], extra=["--round", "1"])
    assert "exam_error_plain_sentence" not in {e["exam_id"] for e in doc["evidence"]} and doc["verdict"] == "fail"


def test_missing_exams_dir_is_a_clear_error(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "judge", "--exams", str(tmp_path / "nope")],
        input='{"pr": 1, "repo": "a/b", "suites": ["hidden"]}', capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 2 and proc.stdout == "" and "Traceback" not in proc.stderr
    assert proc.stderr.startswith("judge: error: exams dir not found")


def test_empty_requested_suite_fails_the_verdict(tmp_path):
    passing = one("exam_bg_task_survives_close", **BG)  # a hidden exam that passes on its own
    doc = run(tmp_path, passing, ["hidden", "regression"])  # no exam is in the regression suite
    assert doc["scores"] == {"hidden": 1.0, "regression": 0.0} and doc["verdict"] == "fail"
