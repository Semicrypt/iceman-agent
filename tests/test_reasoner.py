import unittest
from pathlib import Path

from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


class ReasonerTests(unittest.TestCase):
    def setUp(self):
        self.state = SessionState(Path("."))

    def test_check_everything(self):
        decision = reason_request("check everything", self.state)
        self.assertEqual(decision.action, ReasoningAction.ASSESS)

    def test_priority(self):
        decision = reason_request("what should I fix first?", self.state)
        self.assertEqual(decision.action, ReasoningAction.PRIORITIZE)

    def test_why_target(self):
        decision = reason_request(
            "why is minerva-frontend broken?",
            self.state,
        )
        self.assertEqual(decision.action, ReasoningAction.DIAGNOSE)
        self.assertEqual(decision.target, "minerva-frontend")

    def test_explain_followup(self):
        decision = reason_request("what caused that?", self.state)
        self.assertEqual(decision.action, ReasoningAction.EXPLAIN)


if __name__ == "__main__":
    unittest.main()
