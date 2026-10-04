import unittest
from pathlib import Path

from iceman.application_remediation import build_application_remediation_advice
from iceman.probing import (
    ApplicationCheckProfile,
    RootCauseInvestigation,
    SignalEmitter,
    TargetCandidate,
    TargetResolution,
)


class ApplicationRemediationAdviceTests(unittest.TestCase):
    def test_http_policy_failure_gets_three_bounded_options(self):
        candidate = TargetCandidate(
            subject="Openai",
            url="https://openai.com/",
            source_file="PostgreSQL services.url (matched by services.name)",
            line_number=None,
            score=100,
            context="Openai -> https://openai.com/",
            source_kind="database",
        )
        inv = RootCauseInvestigation(
            subject="Openai",
            workspace=Path("."),
            resolution=TargetResolution(
                subject="Openai",
                resolved=True,
                selected=candidate,
                confidence="high",
            ),
            url="https://openai.com/",
            likely_cause="HTTP 403 fails the monitor policy.",
            confidence="high",
            signal_emitter=SignalEmitter("backend", "be123", 4),
            check_profile=ApplicationCheckProfile(
                source_file="backend/monitor.js",
                line_number=5,
                client="axios",
                method="GET",
                timeout_ms=10000,
                headers={},
                omitted_header_names=[],
                follow_redirects=True,
                accepted_status_min=200,
                accepted_status_max=299,
                success_policy="Axios default HTTP 2xx",
                confidence="high",
                fidelity="high",
            ),
        )

        advice = build_application_remediation_advice(inv)

        self.assertEqual(len(advice.options), 3)
        self.assertEqual(advice.options[0].action, "replace_target")
        self.assertTrue(advice.options[0].recommended)
        self.assertTrue(advice.options[0].automatically_applicable)
        self.assertFalse(advice.options[1].automatically_applicable)
        self.assertFalse(advice.options[2].automatically_applicable)


if __name__ == "__main__":
    unittest.main()
