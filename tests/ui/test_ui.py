"""Harness RSI app page: opt-in manifest, fake data = fixtures, board behaviour (node)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
# Fake host modules for the node checks (tests/ui/fakes): elements become plain objects.
FAKES = {("react", "."): "react.mjs", ("@kirocrew/app-sdk", "."): "app-sdk.mjs",
         ("@kirocrew/app-sdk", "./ui"): "app-sdk-ui.mjs", ("lucide-react", "."): "lucide-react.mjs"}


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_manifest_is_opt_in_with_no_automatic_actions():
    m = load("app.json")
    assert m["name"] == "harness-rsi" and m["version"] and m["displayName"] and m["description"]
    assert m["defaultEnabled"] is False
    assert not m.get("skills")
    # The only agents are the lead and lane conductors; the only MCP server is the stdio team tool (tests/backend/test_team.py).
    assert [a.rsplit("/", 1)[1] for a in m["agents"]] == ["rsi-lead.json", *(f"rsi-lane-{x}.json" for x in ("find", "propose", "exam", "build", "prompt"))]
    assert list(m["mcpServers"]) == ["team"] and "url" not in m["mcpServers"]["team"]
    assert [c["script"] for c in m["crons"]] == ["schedule_tick.py:run"]  # asks the backend; the owner's switches decide
    assert {c["id"]: c["defaultPriority"] for c in m["notifications"]["channels"]} == {"rounds": "default", "regressions": "critical"}
    assert m["backend"] == {"hooks": {"routes": "backend.routes:register_routes"}}  # no startup hook, no loop
    perms = m["permissions"]
    # no Slack Radar route; /api/chat only for opt-in auto-dispatch, which opens app-owned chats (backend.dispatch)
    assert perms["api"] == ["/api/apps/harness-rsi", "/api/apps/harness-rsi/*", "/api/chat"]
    assert not any(perms[k] for k in ("events", "mcpTools", "storage", "spawn")) and perms["cron"] is True
    # session control only so a dispatched chat can start in Trust, and only when the owner's trust_dispatched is on
    assert perms["sessionApproval"] is True
    assert (ROOT / "ui" / m["ui"]["entry"]).is_file()
    assert [p["route"] for p in m["ui"]["pages"]] == ["/apps/harness-rsi"]


def test_fake_data_is_a_copy_of_the_fixtures():
    lines = (ROOT / "ui" / "fake-data.mjs").read_text(encoding="utf-8").splitlines()
    for name in ("proposals", "signals", "outcomes", "team"):
        line = next(x for x in lines if x.startswith(f"export const {name} = "))
        assert json.loads(line.split(" = ", 1)[1]) == load(f"fixtures/{name}.json")


def run_check(tmp_path, script):
    """Run tests/ui/SCRIPT in node beside the fake host modules, a copy of ui/ and the fixtures."""
    node = shutil.which("node")
    assert node, "node is required (it ships on the CI runner)"
    for (name, sub), fake in FAKES.items():
        pkg = tmp_path / "node_modules" / name
        pkg.mkdir(parents=True, exist_ok=True)
        file = "index.mjs" if sub == "." else f"{sub[2:]}.mjs"
        shutil.copy(HERE / "fakes" / fake, pkg / file)
        exports = {s: ("./index.mjs" if s == "." else f"./{s[2:]}.mjs") for (n, s) in FAKES if n == name}
        (pkg / "package.json").write_text(json.dumps({"type": "module", "exports": exports}))
    if not (tmp_path / "ui").exists():
        shutil.copytree(ROOT / "ui", tmp_path / "ui", ignore=shutil.ignore_patterns("screenshots"))
        shutil.copytree(ROOT / "fixtures", tmp_path / "fixtures")
    shutil.copy(HERE / script, tmp_path)
    return subprocess.run([node, script], cwd=tmp_path, capture_output=True, text=True, timeout=60)


def test_board_renders_every_proposal_and_writes_decisions(tmp_path):
    r = run_check(tmp_path, "board_check.mjs")
    assert r.returncode == 0 and "board ok" in r.stdout, r.stderr


def test_demo_mode_boots_the_page_on_fixtures_and_every_string_is_in_the_table(tmp_path):
    r = run_check(tmp_path, "page_check.mjs")
    assert r.returncode == 0 and "page ok" in r.stdout, r.stderr


def test_no_motion_of_our_own():
    """Motion comes only from host components, which honour prefers-reduced-motion."""
    for f in ("index.mjs", "v2.mjs"):
        src = (ROOT / "ui" / f).read_text(encoding="utf-8")
        assert not any(w in src for w in ("animate-", "transition", "@keyframes", "animation")), f


def test_ui_names_only_the_lucide_icons_the_host_stub_exports():
    """The host's lucide-react stub names a short list (copied into fakes/lucide-react.mjs); other icons need the default export."""
    fake = (HERE / "fakes" / "lucide-react.mjs").read_text(encoding="utf-8")
    host = set(re.findall(r"\w+", re.search(r"export const \{(.*?)\}", fake, re.S).group(1)))
    assert {"Users", "Wand2", "Zap"} <= host and "History" not in host
    for f in sorted((ROOT / "ui").rglob("*.mjs")):
        if "screenshots" in f.parts:
            continue  # the screenshot rig loads the real npm package, not the host stub
        for names in re.findall(r"import\s*(?:\w+\s*,\s*)?\{([^}]*)\}\s*from\s*['\"]lucide-react['\"]", f.read_text(encoding="utf-8")):
            missing = {n.split(" as ")[0].strip() for n in names.split(",") if n.strip()} - host
            assert not missing, f"{f.name} imports {sorted(missing)} by name; read them from the default export"
