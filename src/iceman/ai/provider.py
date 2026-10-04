from __future__ import annotations

import os
from dataclasses import dataclass

from iceman.ai.base import ReasoningProvider
from iceman.ai.contracts import AIReasoningResult
from iceman.ai.local_provider import LocalReasoningProvider
from iceman.session import SessionState


@dataclass(frozen=True)
class ProviderStatus:
    requested: str
    selected: str
    openai_key_present: bool
    model: str
    note: str


class SafeFallbackProvider:
    """
    Wrap an external provider and fall back to the local structured reasoner
    when the external provider is unavailable.

    This fallback changes only intent routing; it never bypasses safety gates.
    """

    def __init__(
        self,
        primary: ReasoningProvider,
        fallback: ReasoningProvider,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}+fallback"

    def reason(
        self,
        prompt: str,
        state: SessionState,
    ) -> AIReasoningResult:
        try:
            return self.primary.reason(prompt, state)
        except Exception as exc:
            result = self.fallback.reason(prompt, state)
            result.used_fallback = True
            result.error = f"{type(exc).__name__}: {exc}"
            return result


def provider_status() -> ProviderStatus:
    requested = os.getenv("ICEMAN_AI_PROVIDER", "auto").strip().lower()
    key_present = bool(os.getenv("OPENAI_API_KEY"))
    model = os.getenv("ICEMAN_OPENAI_MODEL", "gpt-5.6-luna")

    if requested not in {"auto", "local", "openai"}:
        return ProviderStatus(
            requested=requested,
            selected="local",
            openai_key_present=key_present,
            model=model,
            note=(
                "Unknown ICEMAN_AI_PROVIDER value; using local provider."
            ),
        )

    if requested == "local":
        return ProviderStatus(
            requested=requested,
            selected="local",
            openai_key_present=key_present,
            model=model,
            note="Local structured reasoner selected explicitly.",
        )

    if requested == "openai":
        if key_present:
            return ProviderStatus(
                requested=requested,
                selected="openai",
                openai_key_present=True,
                model=model,
                note="OpenAI provider selected explicitly.",
            )
        return ProviderStatus(
            requested=requested,
            selected="local",
            openai_key_present=False,
            model=model,
            note=(
                "OpenAI was requested but OPENAI_API_KEY is absent; "
                "using local provider."
            ),
        )

    # auto
    if key_present:
        return ProviderStatus(
            requested=requested,
            selected="openai",
            openai_key_present=True,
            model=model,
            note=(
                "Auto mode selected OpenAI; local reasoning remains the "
                "runtime fallback if the API call fails."
            ),
        )

    return ProviderStatus(
        requested=requested,
        selected="local",
        openai_key_present=False,
        model=model,
        note=(
            "Auto mode selected local reasoning because OPENAI_API_KEY "
            "is not configured."
        ),
    )


def get_reasoning_provider() -> ReasoningProvider:
    status = provider_status()
    local = LocalReasoningProvider()

    if status.selected != "openai":
        return local

    # Import lazily so local operation remains simple even if provider config
    # later changes.
    from iceman.ai.openai_provider import OpenAIReasoningProvider

    primary = OpenAIReasoningProvider(model=status.model)
    return SafeFallbackProvider(primary=primary, fallback=local)
