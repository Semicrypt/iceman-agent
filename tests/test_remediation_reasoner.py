import unittest
from pathlib import Path

from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


class RemediationReasonerTests(unittest.TestCase):
    def test_how_should_i_fix_it_routes_to_recommendations(self):
        state = SessionState(Path("."))
        decision = reason_request("how should I fix it?", state)
        self.assertEqual(
            decision.action,
            ReasoningAction.RECOMMEND_REMEDIATION,
        )

    def test_apply_option_with_url_is_structured(self):
        state = SessionState(Path("."))
        decision = reason_request(
            "apply option 1 with https://example.com/health",
            state,
        )
        self.assertEqual(
            decision.action,
            ReasoningAction.APPLY_REMEDIATION_OPTION,
        )
        self.assertEqual(decision.option_number, 1)
        self.assertEqual(
            decision.replacement_url,
            "https://example.com/health",
        )

    def test_apply_option_without_url_preserves_missing_input(self):
        state = SessionState(Path("."))
        decision = reason_request("apply option 1", state)
        self.assertEqual(
            decision.action,
            ReasoningAction.APPLY_REMEDIATION_OPTION,
        )
        self.assertEqual(decision.option_number, 1)
        self.assertIsNone(decision.replacement_url)


if __name__ == "__main__":
    unittest.main()
