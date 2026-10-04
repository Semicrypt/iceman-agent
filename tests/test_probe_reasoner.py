import unittest
from pathlib import Path

from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


class ProbeReasonerTests(unittest.TestCase):
    def test_investigate_down_subject(self):
        state = SessionState(Path("."))
        decision = reason_request(
            "investigate why Openai is down",
            state,
        )
        self.assertEqual(
            decision.action,
            ReasoningAction.INVESTIGATE,
        )
        self.assertEqual(decision.target, "openai")

    def test_why_is_down_routes_to_investigation(self):
        state = SessionState(Path("."))
        decision = reason_request(
            "why is Broken Website down?",
            state,
        )
        self.assertEqual(
            decision.action,
            ReasoningAction.INVESTIGATE,
        )
        self.assertEqual(decision.target, "broken website")


if __name__ == "__main__":
    unittest.main()
