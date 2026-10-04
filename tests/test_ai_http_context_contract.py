import unittest
from pydantic import ValidationError

from iceman.ai.contracts import AIIntent, TrustedAction


class AIHttpContextContractTests(unittest.TestCase):
    def test_http_context_action_is_structured(self):
        intent = AIIntent(
            action=TrustedAction.ANALYZE_HTTP_CONTEXT,
            option_number=1,
            rationale="Investigate the recommended read-only context option.",
            confidence="high",
        )
        self.assertEqual(intent.option_number, 1)

    def test_http_context_action_still_rejects_shell_command(self):
        with self.assertRaises(ValidationError):
            AIIntent(
                action=TrustedAction.ANALYZE_HTTP_CONTEXT,
                rationale="Bad payload.",
                command="curl -A browser ...",
            )


if __name__ == "__main__":
    unittest.main()
