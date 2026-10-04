import unittest
from pathlib import Path

from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


class HttpContextReasonerTests(unittest.TestCase):
    def test_go_deeper_routes_to_http_context_analysis(self):
        decision = reason_request(
            "go deeper on the backend rejection",
            SessionState(Path(".")),
        )
        self.assertEqual(
            decision.action,
            ReasoningAction.ANALYZE_HTTP_CONTEXT,
        )
        self.assertIsNone(decision.option_number)

    def test_investigate_option_preserves_number(self):
        decision = reason_request(
            "investigate option 1",
            SessionState(Path(".")),
        )
        self.assertEqual(
            decision.action,
            ReasoningAction.ANALYZE_HTTP_CONTEXT,
        )
        self.assertEqual(decision.option_number, 1)


if __name__ == "__main__":
    unittest.main()
