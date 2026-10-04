import tempfile
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


class RemediationActionStabilityTests(unittest.TestCase):
    def test_generic_case_keeps_replace_target_as_option_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = TargetCandidate(
                subject="Openai",
                url="https://openai.com/",
                source_file="PostgreSQL services.url (matched by services.name)",
                line_number=None,
                score=100,
                context="",
                source_kind="database",
            )
            inv = RootCauseInvestigation(
                subject="Openai",
                workspace=root,
                resolution=TargetResolution(
                    subject="Openai",
                    resolved=True,
                    selected=candidate,
                ),
                url="https://openai.com/",
                signal_emitter=SignalEmitter("backend", "be", 1),
                check_profile=ApplicationCheckProfile(
                    source_file="backend/monitor.js",
                    line_number=1,
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

        self.assertEqual(advice.options[0].action, "replace_target")
        self.assertTrue(advice.options[0].recommended)


if __name__ == "__main__":
    unittest.main()
