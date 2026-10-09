"""Every test gets a fixed seal key in place of the gateway vault (there is none in CI), and no test calls real ``gh``."""

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import outcome_checks  # noqa: E402
from judge import seal  # noqa: E402

TEST_KEYS = {"enc": hashlib.sha256(b"test enc").digest(), "mac": hashlib.sha256(b"test mac").digest()}


class FakeVault:
    """The two seal entries the vault would hold; ``sealed`` flips when migrate marks the store."""

    def __init__(self):
        self.sealed = False


@pytest.fixture(autouse=True)
def seal_keys(monkeypatch):
    vault = FakeVault()
    monkeypatch.setattr(seal, "_load", lambda: {**TEST_KEYS, "sealed": vault.sealed})

    def mark():
        vault.sealed = True
        seal._CACHE.clear()
    monkeypatch.setattr(seal, "_mark_sealed", mark)
    seal._CACHE.clear()
    yield vault
    seal._CACHE.clear()


@pytest.fixture(autouse=True)
def no_gh_trending(monkeypatch):
    """``adapters.trending`` reads GitHub through ``gh``; a test that wants rows passes its own fetch."""
    from adapters import trending

    def off(path, params=None):
        raise OSError("gh is off in tests")
    monkeypatch.setattr(trending, "gh_fetch", off)


@pytest.fixture(autouse=True)
def no_gh_checks(monkeypatch):
    """``GET /outcomes`` reads PR checks through ``gh``; a test that wants them passes its own runner."""
    monkeypatch.setattr(outcome_checks, "RUN", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "gh is off in tests"))
