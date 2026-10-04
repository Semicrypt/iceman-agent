import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.application_remediation import prepare_application_change_plan
from iceman.probing import (
    ApplicationCheckProfile,
    RootCauseInvestigation,
    SignalEmitter,
    TargetCandidate,
    TargetResolution,
)


class ApplicationChangeUrlSafetyTests(unittest.TestCase):
    def test_metadata_replacement_is_blocked(self):
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
                    success_policy="HTTP 2xx",
                    confidence="high",
                    fidelity="high",
                ),
            )

            plan = prepare_application_change_plan(
                inv,
                1,
                "http://169.254.169.254/latest/meta-data/",
                root,
            )

        self.assertFalse(plan.safe_to_apply)
        self.assertIn("metadata", plan.note.lower())


if __name__ == "__main__":
    unittest.main()
