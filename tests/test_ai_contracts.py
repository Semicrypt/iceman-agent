import unittest

from pydantic import ValidationError

from iceman.ai.contracts import AIIntent, TrustedAction


class AIContractTests(unittest.TestCase):
    def test_valid_trusted_intent(self):
        intent = AIIntent(
            action=TrustedAction.DIAGNOSE_CONTAINER,
            target="minerva-frontend",
            rationale="The user asked to investigate a specific container.",
            confidence="high",
        )
        self.assertEqual(intent.target, "minerva-frontend")

    def test_contract_rejects_arbitrary_command_field(self):
        with self.assertRaises(ValidationError):
            AIIntent.model_validate(
                {
                    "action": "diagnose_container",
                    "target": "demo",
                    "rationale": "test",
                    "confidence": "high",
                    "command": "rm -rf /",
                }
            )

    def test_contract_rejects_unknown_action(self):
        with self.assertRaises(ValidationError):
            AIIntent.model_validate(
                {
                    "action": "run_shell",
                    "target": None,
                    "rationale": "test",
                    "confidence": "high",
                }
            )


if __name__ == "__main__":
    unittest.main()
