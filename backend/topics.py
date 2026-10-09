"""What the web side of a round looks for: topics, trusted sites and optional X handles.

They steer what the trend scout searches and what ``adapters.trending`` asks GitHub for, so
they live in the gateway's vault (agent tools can neither read nor write it); the only writer
is the owner-only ``POST /topics`` route. Outside a gateway the vault is absent and they read
as the defaults. No credential is kept here: X and paywalled pages are read as public pages only.
"""

import json
import re

from . import settings

VAULT_NAME = "harness-rsi.topics"
DEFAULTS = {"topics": ["coding agent", "MCP", "agent harness"], "sites": [], "x_handles": []}
MAX_TOPICS, MAX_SITES, MAX_HANDLES, MAX_TOPIC_LEN = 10, 20, 20, 60
_TOPIC_RE = re.compile(r"^[\w][\w .+#/-]*$")  # words, spaces and a few joiners; no quotes or search syntax
_SITE_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def _site(s):
    """A bare host from what the owner typed: no scheme, path or trailing dot."""
    s = str(s).strip().lower()
    s = re.sub(r"^https?://", "", s).split("/", 1)[0].rstrip(".")
    return s[4:] if s.startswith("www.") else s


def _list(value, norm):
    return list(dict.fromkeys(x for x in (norm(v) for v in value) if x)) if isinstance(value, list) else None


def validate(patch, current):
    """``(conf, errors)``: ``patch`` over ``current``; any error refuses the write."""
    new = {**current, **{k: patch[k] for k in DEFAULTS if k in patch}}
    topics = _list(new["topics"], lambda s: " ".join(str(s).split()))
    sites = _list(new["sites"], _site)
    handles = _list(new["x_handles"], lambda s: str(s).strip().lstrip("@"))
    errors = []
    if topics is None or not 1 <= len(topics) <= MAX_TOPICS or not all(len(t) <= MAX_TOPIC_LEN and _TOPIC_RE.match(t) for t in topics):
        errors.append(f"topics must be 1-{MAX_TOPICS} short phrases (letters, digits, spaces; up to {MAX_TOPIC_LEN} characters)")
    if sites is None or len(sites) > MAX_SITES or not all(_SITE_RE.match(s) for s in sites):
        errors.append(f"sites must be up to {MAX_SITES} site names like example.com")
    if handles is None or len(handles) > MAX_HANDLES or not all(_HANDLE_RE.match(x) for x in handles):
        errors.append(f"x_handles must be up to {MAX_HANDLES} X handles like @example")
    return {"topics": topics or [], "sites": sites or [], "x_handles": handles or []}, errors


def read():
    """The owner's topics; no vault (tests, a bare CLI) or a bad value reads as the defaults."""
    try:
        secret = settings._vault().get(VAULT_NAME)
        new, errors = validate(json.loads(secret.reveal()) if secret is not None else {}, DEFAULTS)
    except Exception:  # noqa: BLE001 - unreadable reads as the defaults
        return {k: list(v) for k, v in DEFAULTS.items()}
    return {k: list(v) for k, v in DEFAULTS.items()} if errors else new


def write(conf):
    settings._vault().set_sync(VAULT_NAME, json.dumps({k: conf[k] for k in DEFAULTS}))


def brief(conf):
    """The scout's task lines for these topics: what to search, where, and what is out of reach."""
    lines = ["Topics (search each one on the open web):"] + [f"- {t}" for t in conf["topics"]]
    if conf["sites"]:
        lines += ["Trusted sites (search each topic on each site, e.g. `site:example.com <topic>`):"] + [f"- {s}" for s in conf["sites"]]
    if conf["x_handles"]:
        lines += ["X accounts (public profile pages only, https://x.com/<handle>):"] + [f"- @{h}" for h in conf["x_handles"]]
    return "\n".join(lines) + "\n"
