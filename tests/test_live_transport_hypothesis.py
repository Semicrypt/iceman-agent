import tempfile
import unittest
from pathlib import Path

from iceman.http_context import (
    HttpContextSnapshot,
    assess_differential_hypotheses,
)
from iceman.probing import (
    RootCauseInvestigation,
    TargetResolution,
)


class LiveTransportHypothesisTests(unittest.TestCase):
    def test_selected_address_matching_dns_is_not_unrelated_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            investigation = RootCauseInvestigation(
                subject="Openai",
                workspace=Path(tmp),
                resolution=TargetResolution(
                    subject="Openai",
                    resolved=True,
                ),
            )
            host = HttpContextSnapshot(
                context="host",
                client="urllib",
            )
            app = HttpContextSnapshot(
                context="application-container",
                client="axios",
                resolved_addresses=[
                    "104.18.33.45",
                    "172.64.154.211",
                ],
                selected_remote_address="172.64.154.211",
            )

            assessments = assess_differential_hypotheses(
                investigation,
                host,
                app,
            )

        matching = [
            item
            for item in assessments
            if item.hypothesis
            == "Application request reached an unrelated remote address"
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].status, "unsupported")
        self.assertIn("172.64.154.211", matching[0].evidence)


if __name__ == "__main__":
    unittest.main()
