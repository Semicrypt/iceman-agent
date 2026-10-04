import unittest
from pathlib import Path

from iceman.probing import (
    RootCauseInvestigation,
    TargetResolution,
)
from iceman.reasoner import explain_context
from iceman.session import SessionState


class ProbeExplanationTests(unittest.TestCase):
    def test_last_investigation_is_explained_first(self):
        state = SessionState(Path("."))
        investigation = RootCauseInvestigation(
            subject="Openai",
            workspace=Path("."),
            resolution=TargetResolution(
                subject="Openai",
                resolved=False,
            ),
            likely_cause="DNS/name-resolution failure.",
            confidence="high",
        )
        state.remember_investigation(investigation)

        lines = explain_context(state)
        joined = "\n".join(lines)

        self.assertIn("Latest investigation: Openai", joined)
        self.assertIn("DNS/name-resolution failure", joined)
        self.assertIn("Confidence: high", joined)


if __name__ == "__main__":
    unittest.main()
