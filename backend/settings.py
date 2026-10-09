"""Slack and GitHub collection settings, split by authority.

* ``command`` + ``args`` choose what the gateway SPAWNS, so they live in the gateway's
  vault (agent tools can neither read nor write it) with ``workspace_url`` (the link host),
  ``server`` (the name of the Slack connector they were copied from, or "" for a hand-typed
  command) and ``repos`` (the GitHub ``owner/name`` list a refresh and a round read); the
  only writer is the owner-only ``POST /settings`` route. Outside a gateway the vault is
  absent and they read as their defaults.
* ``channels`` and ``window_days`` only narrow what is read, so they live in the app data
  dir as ``slack-settings.json``.

The Slack connector picker: :func:`slack_servers` lists the stdio MCP servers whose name
contains "slack" in the owner's ``mcp.json`` files (read-only). A ``POST /settings`` body
``{"pick": NAME}`` copies that server's ``command`` and ``args`` into the vault at that
moment; its ``env`` is never read. The vault copy is what runs afterwards, so a later edit
of ``mcp.json`` changes nothing until the owner picks again.

An empty command means Slack collection is off; an empty repo list means GitHub is off.
Responses carry :func:`public`: whether a command is set and the connector's name, never
the command or its args.
"""

import json
import os
import re
from pathlib import Path

try:  # see routes.py: a subpackage in the gateway, top-level in tests and the CLI
    from ..adapters.github_issues.adapter import DEFAULT_REPOS, REPO_RE
    from ..adapters.slack import CHANNEL_RE, DEFAULT_CHANNELS, DEFAULT_WINDOW_DAYS, check_argv
except ImportError:
    from adapters.github_issues.adapter import DEFAULT_REPOS, REPO_RE
    from adapters.slack import CHANNEL_RE, DEFAULT_CHANNELS, DEFAULT_WINDOW_DAYS, check_argv

from . import store

VAULT_NAME, FILE, FETCH_FILE = "harness-rsi.slack-mcp", "slack-settings.json", "github-fetch.json"
VAULT_KEYS = ("command", "args", "workspace_url", "server", "repos")
LOCAL_KEYS = ("channels", "window_days")
MAX_CHANNELS, MAX_WINDOW, MAX_REPOS = 20, 90, 10
_WORKSPACE_RE = re.compile(r"^(https://[a-z0-9-]{1,63}(\.enterprise)?\.slack\.com)?$")
DEFAULTS = {"command": "", "args": [], "channels": list(DEFAULT_CHANNELS), "window_days": DEFAULT_WINDOW_DAYS,
            "workspace_url": "", "server": "", "repos": list(DEFAULT_REPOS)}


def _vault():
    from kiro_crew.config.loader import config_dir
    from kiro_crew.secrets import SecretVault
    return SecretVault(config_dir())


def _json(text):
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def mcp_files():
    """``[(source, path)]``: the owner's user-level and workspace-level ``mcp.json``; workspace wins on a name."""
    home = Path(os.environ.get("HOME") or Path.home())
    crew = Path(os.environ.get("KIROCREW_HOME") or home / ".kiro" / "crew")
    paths = [("user", home / ".kiro" / "settings" / "mcp.json")]
    for ws in dict.fromkeys([crew / "workspace", Path.cwd()]):
        paths.append(("workspace", ws / ".kiro" / "settings" / "mcp.json"))
    return paths


def slack_servers():
    """The Slack connectors already configured: ``[{name, command, args, source}]``, sorted by name.

    A server counts when its name contains "slack", it has a ``command`` (stdio, not a URL)
    and it is not disabled. Only ``command`` and ``args`` are read; ``env`` is never touched.
    """
    found = {}
    for source, path in mcp_files():
        try:
            servers = _json(path.read_text(encoding="utf-8")).get("mcpServers")
        except OSError:
            continue
        for name, conf in (servers if isinstance(servers, dict) else {}).items():
            if not (isinstance(name, str) and "slack" in name.lower() and isinstance(conf, dict)):
                continue
            command, args = conf.get("command"), conf.get("args", [])
            if conf.get("disabled") is True or not isinstance(command, str) or not command.strip():
                continue
            found[name] = {"name": name, "command": command.strip(), "args": args if isinstance(args, list) else None,
                           "source": source}
    return [found[k] for k in sorted(found)]


def public_servers(servers):
    """What a response may show of :func:`slack_servers`: name and source, never the command or args."""
    return [{"name": s["name"], "source": s["source"],
             "usable": isinstance(s["args"], list) and not check_argv(s["command"], s["args"])} for s in servers]


def _repos(value):
    if not isinstance(value, list):
        return None
    return list({str(r).strip().lower(): str(r).strip() for r in value if str(r).strip()}.values())


def validate(patch, current, trusted=False):
    """``(settings, errors)``: ``patch`` over ``current``; any error refuses the write.

    ``trusted`` is the vault's own copy (``read``); a request body may not set ``server``
    itself, only ``pick`` a connector by name, which copies its command and args.
    """
    keys = DEFAULTS if trusted else [k for k in DEFAULTS if k != "server"]
    new, errors = {**current, **{k: patch[k] for k in keys if k in patch}}, []
    if not trusted and "pick" in patch:
        pick = str(patch["pick"] or "")
        match = next((s for s in slack_servers() if s["name"] == pick), None)
        if match is None:
            errors.append(f"no Slack connector named {pick!r} in your mcp.json")
        else:
            new.update(command=match["command"], args=match["args"], server=pick)
    elif not trusted and "command" in patch:
        new["server"] = ""  # typed by hand under Advanced
    new["command"] = str(new["command"] or "").strip()
    new["channels"] = list(dict.fromkeys(str(c).strip().upper() for c in new["channels"])) \
        if isinstance(new["channels"], list) else [""]
    new["workspace_url"] = str(new["workspace_url"] or "").strip().rstrip("/").lower()
    new["server"] = str(new["server"] or "") if new["command"] else ""
    n, repos = new["window_days"], _repos(new["repos"])
    if not isinstance(new["args"], list) or (new["command"] and check_argv(new["command"], new["args"])):
        errors.append("command and args must be plain words (no spaces or shell syntax)")
    if not all(CHANNEL_RE.match(c) for c in new["channels"]) or len(new["channels"]) > MAX_CHANNELS:
        errors.append(f"channels must be up to {MAX_CHANNELS} Slack channel ids like C0123ABCD")
    if not (isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= MAX_WINDOW):
        errors.append(f"window_days must be 1-{MAX_WINDOW}")
    if not _WORKSPACE_RE.match(new["workspace_url"]):
        errors.append("workspace_url must look like https://yourteam.slack.com")
    if repos is None or len(repos) > MAX_REPOS or not all(REPO_RE.match(r) for r in repos):
        errors.append(f"repos must be up to {MAX_REPOS} GitHub repos like owner/name")
    else:
        new["repos"] = repos
    return {**new, "args": new["args"] if new["command"] else []}, errors


def public(settings):
    """What a response may show: ``command_set`` and the connector name instead of the command and its args."""
    return {"command_set": bool(settings["command"]),
            **{k: settings[k] for k in ("server", "channels", "window_days", "workspace_url", "repos")}}


def view(settings):
    """``public`` plus the Slack connectors found (names only) and each repo's last GitHub read."""
    return {**public(settings), "servers": public_servers(slack_servers()), "fetch": fetch_state()}


def read():
    """Current settings; an invalid or missing part reads as its default."""
    try:
        secret = _vault().get(VAULT_NAME)
        vault = _json(secret.reveal()) if secret is not None else {}
    except Exception:  # noqa: BLE001 - no vault (bare CLI, tests) reads as Slack off
        vault = {}
    try:
        local = _json((store.data_dir() / FILE).read_text(encoding="utf-8"))
    except OSError:
        local = {}
    out = dict(DEFAULTS)
    for src, keys in ((vault, VAULT_KEYS), (local, LOCAL_KEYS)):
        new, errors = validate({k: src[k] for k in keys if k in src}, out, trusted=src is vault)
        out = out if errors else new
    return out


def write(settings):
    """Command, args, connector, link host and repos to the vault; channels and window to the data dir."""
    _vault().set_sync(VAULT_NAME, json.dumps({k: settings[k] for k in VAULT_KEYS}))
    path = store.data_dir() / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: settings[k] for k in LOCAL_KEYS}, indent=1) + "\n", encoding="utf-8")


def fetch_state():
    """``{repo: {at, rows, error}}``: each repo's last GitHub read, as the refresh or round left it."""
    try:
        state = _json((store.data_dir() / FETCH_FILE).read_text(encoding="utf-8"))
    except OSError:
        return {}
    return {k: v for k, v in state.items() if isinstance(k, str) and REPO_RE.match(k) and isinstance(v, dict)}


def record_fetch(repo, at, rows, error):
    """Keep ``repo``'s last read (rows is None on a failure) beside the others."""
    state = fetch_state()
    state[repo] = {"at": at, "rows": rows, "error": error}
    path = store.data_dir() / FETCH_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)
