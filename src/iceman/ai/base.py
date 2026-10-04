from __future__ import annotations

from typing import Protocol

from iceman.ai.contracts import AIReasoningResult
from iceman.session import SessionState


class ReasoningProvider(Protocol):
    name: str

    def reason(
        self,
        prompt: str,
        state: SessionState,
    ) -> AIReasoningResult:
        ...
