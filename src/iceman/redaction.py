from __future__ import annotations

import re

# Display/provider boundary, not a replacement for avoiding secret files.
_SECRET_VALUES = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.S),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"(?i)(?:https?|postgres(?:ql)?|mysql|redis)://[^\s/@:]+:[^\s/@]+@"),
)
_ASSIGNMENT = re.compile(
    r"""(?ix)(\b(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|accountkey|authorization)\b\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)"""
)


def redact_text(value: str) -> str:
    """Remove terminal controls and common secret forms before display or AI."""
    text = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|$))", "", str(value))
    text = "".join(c for c in text if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))
    for pattern in _SECRET_VALUES:
        text = pattern.sub("[REDACTED]", text)
    return _ASSIGNMENT.sub(lambda match: match.group(1) + "[REDACTED]", text)
