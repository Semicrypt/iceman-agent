import unittest
from unittest.mock import patch

from iceman.ai.contracts import AIIntent, AIReasoningResult, TrustedAction
from iceman.presentation import show_ai_route


class AIPresentationLabelTests(unittest.TestCase):
    def test_normal_route_hides_provider_and_model(self):
        result = AIReasoningResult(
            intent=AIIntent(
                action=TrustedAction.ASSESS_PROJECT,
                rationale="Check the whole project.",
                confidence="high",
            ),
            provider="openai",
            model="gpt-5.6-luna",
        )

        with patch("iceman.presentation.console.print") as mock_print:
            show_ai_route(result)

        rendered = " ".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("Advanced Intelligence", rendered)
        self.assertNotIn("openai", rendered.lower())
        self.assertNotIn("gpt-5.6-luna", rendered.lower())


if __name__ == "__main__":
    unittest.main()
