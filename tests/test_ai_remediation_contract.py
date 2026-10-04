import unittest
from pydantic import ValidationError

from iceman.ai.contracts import AIIntent, TrustedAction


class AIRemediationContractTests(unittest.TestCase):
    def test_apply_option_has_structured_fields_not_command(self):
        intent = AIIntent(
            action=TrustedAction.APPLY_REMEDIATION_OPTION,
            option_number=1,
            replacement_url="https://example.com/health",
            rationale="Apply the user-selected bounded option.",
            confidence="high",
        )
        self.assertEqual(intent.option_number, 1)
        self.assertEqual(
            intent.replacement_url,
            "https://example.com/health",
        )

    def test_arbitrary_command_still_rejected(self):
        with self.assertRaises(ValidationError):
            AIIntent(
                action=TrustedAction.APPLY_REMEDIATION_OPTION,
                option_number=1,
                rationale="Bad payload.",
                command="psql UPDATE ...",
            )


if __name__ == "__main__":
    unittest.main()
