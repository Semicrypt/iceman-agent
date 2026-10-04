import unittest
from pathlib import Path

from iceman.ai.contracts import (
    AIIntent,
    AIReasoningResult,
    TrustedAction,
)
from iceman.ai.provider import SafeFallbackProvider
from iceman.session import SessionState


class BrokenProvider:
    name = "broken"

    def reason(self, prompt, state):
        raise RuntimeError("provider unavailable")


class FallbackProvider:
    name = "local"

    def reason(self, prompt, state):
        return AIReasoningResult(
            intent=AIIntent(
                action=TrustedAction.SESSION_STATUS,
                rationale="Show session.",
                confidence="high",
            ),
            provider="local",
        )


class SafeFallbackTests(unittest.TestCase):
    def test_external_failure_uses_local_reasoner(self):
        provider = SafeFallbackProvider(
            primary=BrokenProvider(),
            fallback=FallbackProvider(),
        )

        result = provider.reason(
            "show context",
            SessionState(Path(".")),
        )

        self.assertTrue(result.used_fallback)
        self.assertEqual(result.provider, "local")
        self.assertIn("RuntimeError", result.error)


if __name__ == "__main__":
    unittest.main()
