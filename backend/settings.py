"""Slack collection settings, split by authority.

* ``command`` + ``args`` choose what the gateway SPAWNS, so they live in the gateway's
  vault (agent tools can neither read nor write it) with ``workspace_url`` (the link host);
  the only writer is the owner-only ``POST /settings`` route. Outside a gateway the vault
  is absent and they read as empty.
* ``channels`` and ``window_days`` only narrow what is read, so they live in the app data
  dir as ``slack-settings.json``.

An empty command means Slack collection is off.
"""

import json
import re

try:  # see routes.py: a subpackage in the gateway, top-level in tests and the CLI
    from ..adapters.slack import CHANNEL_RE, DEFAULT_CHANNELS, DEFAULT_WINDOW_DAYS, check_argv
except ImportError:
    from adapters.slack import CHANNEL_RE, DEFAULT_CHANNELS, DEFAULT_WINDOW_DAYS, check_argv

from . import store

VAULT_NAME, FILE = "harness-rsi.slack-mcp", "slack-settings.json"
VAULT_KEYS, LOCAL_KEYS = ("command", "args", "workspace_url"), ("channels", "window_days")
MAX_CHANNELS, MAX_WINDOW = 20, 90
_WORKSPACE_RE = re.compile(r"^(https://[a-z0-9-]{1,63}(\.enterprise)?\.slack\.com)?$")
DEFAULTS = {"command": "", "args": [], "channels": list(DEFAULT_CHANNELS), "window_days": DEFAULT_WINDOW_DAYS,
            "workspace_url": ""}


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


def validate(patch, current):
    """``(settings, errors)``: ``patch`` over ``current``; any error refuses the write."""
    new = {**current, **{k: patch[k] for k in DEFAULTS if k in patch}}
    new["command"] = str(new["command"] or "").strip()
    new["channels"] = list(dict.fromkeys(str(c).strip().upper() for c in new["channels"])) \
        if isinstance(new["channels"], list) else [""]
    new["workspace_url"] = str(new["workspace_url"] or "").strip().rstrip("/").lower()
    n, errors = new["window_days"], []
    if not isinstance(new["args"], list) or (new["command"] and check_argv(new["command"], new["args"])):
        errors.append("command and args must be plain words (no spaces or shell syntax)")
    if not all(CHANNEL_RE.match(c) for c in new["channels"]) or len(new["channels"]) > MAX_CHANNELS:
        errors.append(f"channels must be up to {MAX_CHANNELS} Slack channel ids like C0123ABCD")
    if not (isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= MAX_WINDOW):
        errors.append(f"window_days must be 1-{MAX_WINDOW}")
    if not _WORKSPACE_RE.match(new["workspace_url"]):
        errors.append("workspace_url must look like https://yourteam.slack.com")
    return {**new, "args": new["args"] if new["command"] else []}, errors


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
        new, errors = validate({k: src[k] for k in keys if k in src}, out)
        out = out if errors else new
    return out


def write(settings):
    """Command, args and link host to the vault; channels and window to the data dir."""
    _vault().set_sync(VAULT_NAME, json.dumps({k: settings[k] for k in VAULT_KEYS}))
    path = store.data_dir() / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: settings[k] for k in LOCAL_KEYS}, indent=1) + "\n", encoding="utf-8")
