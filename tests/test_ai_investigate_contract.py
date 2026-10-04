import unittest

from iceman.ai.contracts import AIIntent, TrustedAction


class AIInvestigateContractTests(unittest.TestCase):
    def test_investigation_is_trusted_action(self):
        intent = AIIntent(
            action=TrustedAction.INVESTIGATE_APPLICATION_SIGNAL,
            target="Openai",
            rationale="Investigate the repeated DOWN signal.",
            confidence="high",
        )
        self.assertEqual(
            intent.action,
            TrustedAction.INVESTIGATE_APPLICATION_SIGNAL,
        )
        self.assertEqual(intent.target, "Openai")


if __name__ == "__main__":
    unittest.main()
