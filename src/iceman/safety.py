from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyResult:
    allowed: bool
    reason: str | None = None


_BLOCKED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(^|\s)rm\s+(-[^\s]*r[^\s]*f|-rf|-fr)\b", "recursive forced deletion"),
    (r"(^|\s)mkfs(\.|\s)", "filesystem formatting"),
    (r"(^|\s)(shutdown|reboot|poweroff|halt)\b", "system shutdown/reboot"),
    (r"(^|\s)terraform\s+destroy\b", "Terraform destroy"),
    (r"(^|\s)terraform\s+apply\b", "Terraform apply is not exposed by the current trusted workflow"),
    (r"(^|\s)kubectl\s+delete\b", "Kubernetes deletion"),
    (r"(^|\s)docker\s+system\s+prune\b", "Docker system pruning"),
    (r"(^|\s)aws\s+.*\b(delete|terminate|remove|deregister)\b", "potentially destructive AWS operation"),
    (r"(^|\s)git\s+push\b.*(?:--force(?:-with-lease)?|-f)\b", "Git force-push is not exposed by the current trusted workflow"),
    (r"(^|\s)git\s+reset\s+--hard\b", "destructive Git hard reset"),
    (r"(^|\s)git\s+clean\s+[^\n]*-[^\s]*f", "destructive Git clean"),
    (r"(^|\s)git\s+branch\s+-D\b", "forced Git branch deletion"),
    (r"(^|\s)sudo\b", "privileged execution is disabled"),
)


def check_command(command: str) -> SafetyResult:
    normalized = command.strip().lower()

    if not normalized:
        return SafetyResult(False, "empty command")

    # The runner does not use a shell, so shell operators are not interpreted.
    for pattern, reason in _BLOCKED_PATTERNS:
        if re.search(pattern, normalized):
            return SafetyResult(False, reason)

    return SafetyResult(True)
