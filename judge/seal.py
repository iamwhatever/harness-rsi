"""The sealed store: exam and prompt files encrypted at rest, ledger rows signed.

Agents share the user's account, so the data dir alone keeps nothing from them. Two keys
live in the gateway vault (``SecretVault``; its ``.vault`` leaf is closed to agent tools):

* exam files (``exams/**``, ``rounds/*/exams/**``), the owner's applied prompts
  (``prompts/*.md``) and the setter's A/B replies are AES-GCM ciphertext: ``MAGIC`` then
  base64 of nonce + ciphertext, bound to the file name. The judge, ``validate`` and
  ``regress`` decrypt in their own process; ``cat`` shows ciphertext.
* every ``outcomes.jsonl`` row and every ``regress/<sha>.json`` run carries ``sig``, an
  HMAC-SHA256 over its canonical JSON and its place. A reader drops an unsigned or bad row
  and reports how many it dropped; any drop voids the round that reads the ledger.

Once ``migrate`` has run, the vault marks the store sealed and a plaintext file in a sealed
place is refused, so a planted plaintext exam cannot stand in for a sealed one. Before that,
plaintext still reads (old data dirs and the repo's fixtures). No vault (a bare CLI, CI)
means no key: writes and signed reads fail closed, plaintext reads still work.

``python -m judge.seal migrate`` seals an existing data dir once; it is idempotent, keeps a
plaintext backup under ``seal-backup/<stamp>/`` and deletes nothing (an unparsable ledger line
moves to the backup only). On a sealed store it changes nothing, so a row forged after sealing
is never signed by a rerun.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import shutil
import sys
import tempfile
import threading
import time

MAGIC = b"HRSI-SEAL1:"
KEY_NAME, STATE_NAME = "harness-rsi.seal-key", "harness-rsi.seal-state"
OUTCOMES, REGRESS = "outcomes", "regress"
_LOCK = threading.Lock()
_CACHE = {}


class SealError(Exception):
    """No key, a bad ciphertext, or plaintext where the sealed store allows none."""


def data_dir():
    return pathlib.Path(os.environ.get("HARNESS_RSI_DATA") or pathlib.Path.home() / ".kiro/crew/harness-rsi-data").expanduser()


def _vault():
    from kiro_crew.config.loader import config_dir
    from kiro_crew.secrets import SecretVault
    return SecretVault(config_dir())


def _load():
    """``{"enc", "mac", "sealed"}`` from the vault; the key is created on first use."""
    try:
        vault = _vault()
        got = vault.get(KEY_NAME)
        if got is None:
            make = getattr(vault, "_set_if_absent_sync", None) or vault.set_sync  # first writer wins
            make(KEY_NAME, base64.b64encode(secrets.token_bytes(32)).decode())
            got = vault.get(KEY_NAME)
        root = base64.b64decode(got.reveal())
        state = vault.get(STATE_NAME)
    except Exception as exc:  # noqa: BLE001 - no vault here: nothing can be sealed or verified
        raise SealError(f"no seal key ({type(exc).__name__}): run inside the gateway") from exc
    sub = lambda what: hmac.new(root, what, hashlib.sha256).digest()  # noqa: E731
    return {"enc": sub(b"harness-rsi exam encryption"), "mac": sub(b"harness-rsi ledger signing"),
            "sealed": state is not None and state.reveal() == "sealed"}


def keys():
    with _LOCK:
        if not _CACHE:
            _CACHE.update(_load())
        return dict(_CACHE)


def _mark_sealed():
    _vault().set_sync(STATE_NAME, "sealed")
    with _LOCK:
        _CACHE.clear()


def _sealed():
    try:
        return keys()["sealed"]
    except SealError:
        return False


def _in_data(path):
    try:
        pathlib.Path(path).resolve().relative_to(data_dir().resolve())
        return True
    except ValueError:
        return False


def is_sealed(raw):
    return raw.startswith(MAGIC)


def encrypt(text, name):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    body = AESGCM(keys()["enc"]).encrypt(nonce, text.encode("utf-8"), name.encode("utf-8"))
    return MAGIC + base64.b64encode(nonce + body) + b"\n"


def decrypt(raw, name):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        blob = base64.b64decode(raw[len(MAGIC):].strip(), validate=True)
        return AESGCM(keys()["enc"]).decrypt(blob[:12], blob[12:], name.encode("utf-8")).decode("utf-8")
    except (InvalidTag, ValueError) as exc:
        raise SealError(f"{name}: bad sealed file") from exc


def read_text(path):
    """A sealed file's text; a plaintext one as it is, unless the store is sealed and it sits in the data dir."""
    path = pathlib.Path(path)
    raw = path.read_bytes()
    if is_sealed(raw):
        return decrypt(raw, path.name)
    if _in_data(path) and _sealed():
        raise SealError(f"{path.name}: plaintext in the sealed store")
    return raw.decode("utf-8")


def read_json(path):
    return json.loads(read_text(path))


def write_text(path, text):
    """Encrypt ``text`` to ``path`` atomically; SealError when there is no key."""
    path = pathlib.Path(path)
    blob = encrypt(text, path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, path)


def _canon(domain, row):
    body = json.dumps({k: v for k, v in row.items() if k != "sig"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{domain}\n{body}".encode("utf-8")


def sign(domain, row):
    """``row`` with its ``sig``; ``domain`` names the ledger (and for a regress run, its sha)."""
    return {**row, "sig": hmac.new(keys()["mac"], _canon(domain, row), hashlib.sha256).hexdigest()}


def verify(domain, row):
    """``row`` without ``sig`` when the signature holds, else None (no key verifies nothing)."""
    if not (isinstance(row, dict) and isinstance(row.get("sig"), str)):
        return None
    try:
        want = hmac.new(keys()["mac"], _canon(domain, row), hashlib.sha256).hexdigest()
    except SealError:
        return None
    return {k: v for k, v in row.items() if k != "sig"} if hmac.compare_digest(want, row["sig"]) else None


def signed_lines(path, domain):
    """``(rows, bad)``: the verified rows of a JSONL ledger and how many lines failed; a missing file is ``([], 0)``."""
    try:
        lines = [x for x in pathlib.Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    except FileNotFoundError:
        return [], 0
    rows = []
    for line in lines:
        try:
            rows.append(verify(domain, json.loads(line)))
        except ValueError:
            rows.append(None)
    return [r for r in rows if r is not None], sum(r is None for r in rows)


def regress_domain(path):
    return f"{REGRESS}:{pathlib.Path(path).stem}"


def read_run(path):
    """One verified regress run, else None."""
    try:
        return verify(regress_domain(path), json.loads(pathlib.Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def write_run(path, run):
    path = pathlib.Path(path)
    path.write_text(json.dumps(sign(regress_domain(path), run), indent=2) + "\n", encoding="utf-8")


def sealed_files(data):
    """Every file that belongs encrypted: exams, saved round exams, applied prompts, setter A/B replies."""
    data = pathlib.Path(data)
    found = [*data.glob("exams/**/*.json"), *data.glob("rounds/*/exams/**/*.json"), *data.glob("prompts/*.md"),
             *data.glob("ab/cache/setter/**/*.json")]
    return sorted(p for p in found if p.is_file())


def migrate(data=None, now=time.time):
    """Encrypt every plaintext sealed file and sign every unsigned ledger row, once; back up first, delete nothing."""
    data = pathlib.Path(data or data_dir())
    if keys()["sealed"]:  # fails before touching anything when there is no key
        return {"encrypted": 0, "signed_rows": 0, "signed_runs": 0, "backup": None, "note": "already sealed"}
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now()))
    backup = data / "seal-backup" / stamp
    out = {"encrypted": 0, "signed_rows": 0, "signed_runs": 0, "unreadable_rows": 0, "backup": None}

    def keep(path):
        dest = backup / path.relative_to(data)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
        out["backup"] = str(backup.relative_to(data))

    for path in sealed_files(data):
        if not is_sealed(raw := path.read_bytes()):
            keep(path)
            write_text(path, raw.decode("utf-8"))
            out["encrypted"] += 1
    ledger = data / "outcomes.jsonl"
    if ledger.is_file():
        lines, rows, changed = ledger.read_text(encoding="utf-8").splitlines(), [], 0
        for line in (x for x in lines if x.strip()):
            try:
                row = json.loads(line)
            except ValueError:  # kept only in the backup: a line no reader could use
                changed, out["unreadable_rows"] = changed + 1, out["unreadable_rows"] + 1
                continue
            if verify(OUTCOMES, row) is None:
                row, changed = sign(OUTCOMES, row), changed + 1
            rows.append(json.dumps(row, ensure_ascii=False))
        if changed:
            keep(ledger)
            fd, tmp = tempfile.mkstemp(dir=data, prefix=".outcomes.")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(r + "\n" for r in rows)
            os.replace(tmp, ledger)
            out["signed_rows"] = changed - out["unreadable_rows"]
    for path in sorted((data / "regress").glob("*.json")):
        if read_run(path) is None:
            keep(path)
            write_run(path, {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k != "sig"})
            out["signed_runs"] += 1
    _mark_sealed()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m judge.seal", description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["migrate"])
    ap.parse_args(argv)
    try:
        print(json.dumps(migrate(), indent=1))
    except SealError as exc:
        print(f"seal: error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
