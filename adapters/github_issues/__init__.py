"""GitHub issues -> signal rows. Run: python -m adapters.github_issues --repo owner/name"""

from .adapter import build_signals, gh_fetch, main, summarize

__all__ = ["build_signals", "gh_fetch", "main", "summarize"]
