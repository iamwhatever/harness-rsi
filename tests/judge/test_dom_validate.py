"""The dom_assert check kind and `python -m judge.validate`. The browser test runs only where Playwright can launch."""

import copy
import functools
import importlib.util
import http.server
import json
import pathlib
import sys
import threading
import urllib.error
import urllib.request

import pytest
from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from judge import dom, validate  # noqa: E402

EXAM_SCHEMA = Draft202012Validator(json.loads((ROOT / "schemas/exam.schema.json").read_text()))
BASE = json.loads((ROOT / "fixtures/exams.json").read_text())[0]
CHECK = {"kind": "dom_assert", "root": "dist", "path": "/chat", "selector": "#out", "text": "hello · auto",
         "setup": [{"route": "/api/hello", "json": {"word": "hello · auto"}}, {"storage": {"k": "v"}}, {"wait_for": "#out"}]}
SPA = """<!doctype html><div id="out"></div><script>
fetch('/api/hello').then(r => r.json()).then(d => { document.getElementById('out').textContent = d.word })
</script>"""


def exam(**check):
    return {**copy.deepcopy(BASE), "id": "exam_dom", "check": {**copy.deepcopy(CHECK), **check}}


@pytest.mark.parametrize("change,valid", [
    ({}, True),
    ({"regex": "^hello"}, False),  # text and regex together
    ({"setup": [{"route": "/not-api", "json": {}}]}, False),
    ({"setup": [{"click": "#a", "fill": ["#b", "x"]}]}, False),  # one op per step
    ({"path": "chat"}, False),
])
def test_dom_assert_schema(change, valid):
    assert EXAM_SCHEMA.is_valid(exam(**change)) is valid


def test_spa_server_falls_back_to_index_and_serves_no_api(tmp_path):
    (tmp_path / "index.html").write_text("INDEX")
    (tmp_path / "app.js").write_text("JS")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(dom._Spa, directory=str(tmp_path)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    get = lambda p: urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}{p}").read()  # noqa: E731
    try:
        assert (get("/"), get("/deep/link"), get("/app.js"), get("/%2e%2e/%2e%2e/etc/passwd")) == (b"INDEX", b"INDEX", b"JS", b"INDEX")
        with pytest.raises(urllib.error.HTTPError, match="404"):
            get("/api/anything")
    finally:
        srv.shutdown()
        srv.server_close()
    assert dom._match([{"route": "/api/chat/*"}], "/api/chat/slots") and not dom._match([{"route": "/api/chat"}], "/api/chats")


def test_cannot_run_without_playwright_or_spa(tmp_path, monkeypatch):
    assert dom.run(CHECK, {"workdir": tmp_path}) in ((None, "no built SPA: dist/index.html missing"),
                                                     (None, "playwright not installed (pip install playwright)"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    assert dom.run(CHECK, {"workdir": tmp_path}) == (None, "playwright not installed (pip install playwright)")


def test_a_driver_that_cannot_start_is_an_error_not_a_crash(tmp_path, monkeypatch):
    """A Playwright whose bundled node cannot start (e.g. too old a libc) raises on enter."""
    import types

    class Dead:
        def __enter__(self):
            raise Exception("Connection.init: Connection closed while reading from the driver")

        def __exit__(self, *exc):
            return False

    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("<p>x</p>")
    fake = types.ModuleType("playwright.sync_api")
    fake.Error, fake.sync_playwright = Exception, Dead
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)
    assert dom.run(CHECK, {"workdir": tmp_path}) == (
        None, "playwright driver cannot start: Connection.init: Connection closed while reading from the driver")


def test_validate_refuses_what_cannot_run(tmp_path, capsys):
    (tmp_path / "e.json").write_text(json.dumps(exam()))
    assert validate.main([str(tmp_path / "e.json"), "--workdir", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    (tmp_path / "e.json").write_text(json.dumps(exam(regex="x")))
    assert validate.main([str(tmp_path / "e.json"), "--workdir", str(tmp_path)]) == 2
    ok = {**copy.deepcopy(BASE), "check": {"kind": "exit_code", "cmd": [sys.executable, "-c", "pass"], "expect": 1}}
    (tmp_path / "e.json").write_text(json.dumps(ok))
    assert validate.main([str(tmp_path / "e.json"), "--workdir", str(tmp_path)]) == 0  # runnable, even though it fails
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["status"] == "fail"


def _browser_ok():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch().close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _browser_ok(), reason="needs Playwright with a launchable Chromium")
def test_dom_assert_in_a_real_browser(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/index.html").write_text(SPA)
    ctx = {"workdir": tmp_path}
    assert dom.run(CHECK, ctx)[0] is True
    assert dom.run({**CHECK, "text": "hello · default", "timeout_s": 2}, ctx)[0] is False
    assert dom.run({**CHECK, "regex": r"^hello · (auto|x)$"}, ctx)[0] is True
    missing = dom.run({**CHECK, "selector": "#nope", "timeout_s": 1}, ctx)
    assert missing[0] is False and missing[1].startswith("#nope")


def test_question_setter_writes_only_exams_that_can_run(tmp_path):
    spec = importlib.util.spec_from_file_location("run_round", ROOT / "crew" / "run_round.py")
    rr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rr)
    sig = json.loads((ROOT / "fixtures/signals.json").read_text())[0]
    runs = {**copy.deepcopy(BASE), "id": "exam_runs", "origin": [sig["id"]],
            "check": {"kind": "exit_code", "cmd": [sys.executable, "-c", "pass"], "expect": 0}}
    stuck = {**exam(), "origin": [sig["id"]]}  # dom_assert with no built SPA in the workdir
    reply = json.dumps([runs, stuck])
    refused = []
    kept = rr.write_exams(lambda name, msg: reply, [sig], 4, rr.validate_checker(tmp_path), refused)
    assert [e["id"] for e in kept] == ["exam_runs"]
    assert [r["id"] for r in refused] == ["exam_dom"]
    assert refused[0]["detail"] in ("no built SPA: dist/index.html missing", "playwright not installed (pip install playwright)")
