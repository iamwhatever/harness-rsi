"""Harness RSI app page: opt-in manifest, fake data = fixtures, board behaviour (node)."""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Fake host modules for board_check.mjs: elements become plain objects.
FAKES = {
    ("react", "."): "export const createElement = (type, props, ...c) => ({ type, key: props?.key,"
    " props: { ...(props || {}), children: c.length === 1 ? c[0] : c } })\n"
    "export const useState = (v) => [v, () => {}]\nexport const useEffect = () => {}\n"
    "export const useCallback = (f) => f, useMemo = (f) => f()\n",
    ("@kirocrew/app-sdk", "."): "export const useAppApi = () => null\n",
    ("@kirocrew/app-sdk", "./ui"): "import { createElement as h } from 'react'\n"
    "const pass = (tag) => ({ children, primary, variant, ...rest }) => h(tag, rest, children)\n"
    "export const Card = pass('div'), Btn = pass('button'), Badge = pass('span')\n"
    "export const PageHeader = ({ title }) => h('div', null, title), EmptyState = PageHeader\n",
}


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_manifest_is_opt_in_with_no_automatic_actions():
    m = load("app.json")
    assert m["name"] == "harness-rsi" and m["version"] and m["displayName"] and m["description"]
    assert m["defaultEnabled"] is False
    for key in ("crons", "agents", "mcpServers", "skills"):
        assert not m.get(key), key
    assert m["backend"] == {"hooks": {"routes": "backend.routes:register_routes"}}  # no startup hook, no loop
    perms = m["permissions"]
    assert perms["api"] == ["/api/apps/harness-rsi", "/api/apps/harness-rsi/*", "/api/apps/slack-radar/signals"]
    assert not any(perms[k] for k in ("events", "mcpTools", "storage", "cron", "spawn"))
    assert (ROOT / "ui" / m["ui"]["entry"]).is_file()
    assert [p["route"] for p in m["ui"]["pages"]] == ["/apps/harness-rsi"]


def test_fake_data_is_a_copy_of_the_fixtures():
    lines = (ROOT / "ui" / "fake-data.mjs").read_text(encoding="utf-8").splitlines()
    for name in ("proposals", "signals"):
        line = next(x for x in lines if x.startswith(f"export const {name} = "))
        assert json.loads(line.split(" = ", 1)[1]) == load(f"fixtures/{name}.json")


def test_board_renders_every_proposal_and_writes_decisions(tmp_path):
    node = shutil.which("node")
    assert node, "node is required (it ships on the CI runner)"
    for (name, sub), body in FAKES.items():
        pkg = tmp_path / "node_modules" / name
        pkg.mkdir(parents=True, exist_ok=True)
        file = "index.mjs" if sub == "." else f"{sub[2:]}.mjs"
        (pkg / file).write_text(body, encoding="utf-8")
        exports = {s: ("./index.mjs" if s == "." else f"./{s[2:]}.mjs") for (n, s) in FAKES if n == name}
        (pkg / "package.json").write_text(json.dumps({"type": "module", "exports": exports}))
    shutil.copytree(ROOT / "ui", tmp_path / "ui", ignore=shutil.ignore_patterns("screenshots"))
    shutil.copytree(ROOT / "fixtures", tmp_path / "fixtures")
    shutil.copy(Path(__file__).with_name("board_check.mjs"), tmp_path)
    r = subprocess.run([node, "board_check.mjs"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "board ok" in r.stdout, r.stderr
