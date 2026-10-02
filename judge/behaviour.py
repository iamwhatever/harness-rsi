"""Security exams must run the product code: a `file_assert` only reads a file and proves no behaviour.

An exam is security-scoped when its task, or any text it is checked with (its origin signals' pain,
the proposals naming it), mentions security, redaction, auth, credentials, secrets, passwords or exfil.
"""

import re

SECURITY = re.compile(r"\b(?:secur|redact|auth|credential|secret|password|exfil)", re.I)
STATIC_KINDS = {"file_assert"}


def refusal(exam, context=()):
    """Why `exam` is refused under the behaviour rule, or None when it may run."""
    check = exam.get("check") if isinstance(exam, dict) else None
    if not isinstance(check, dict) or check.get("kind") not in STATIC_KINDS:
        return None
    texts = [exam.get("task", ""), *context]
    if not any(SECURITY.search(t or "") for t in texts):
        return None
    return (f"security exam needs a behaviour check: {check['kind']} cannot test it; "
            "use exit_code running the product code on inputs")
