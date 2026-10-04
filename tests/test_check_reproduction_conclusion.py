import unittest
from pathlib import Path

from iceman.probing import (
    ApplicationCheckProfile,
    ApplicationCheckReplay,
    RootCauseInvestigation,
    TargetResolution,
    apply_source_reproduction_conclusion,
)


class CheckReproductionConclusionTests(unittest.TestCase):
    def test_403_plus_2xx_policy_explains_down(self):
        profile = ApplicationCheckProfile(
            source_file="backend/monitor.js",
            line_number=5,
            client="axios",
            method="GET",
            timeout_ms=5000,
            headers={},
            omitted_header_names=[],
            follow_redirects=True,
            accepted_status_min=200,
            accepted_status_max=299,
            success_policy="Axios default HTTP 2xx",
            confidence="high",
            fidelity="high",
        )
        replay = ApplicationCheckReplay(
            compose_service="backend",
            container_id="be123",
            client="axios",
            status_code=403,
            accepted_by_policy=False,
            fidelity="high",
        )
        investigation = RootCauseInvestigation(
            subject="Openai",
            workspace=Path("."),
            resolution=TargetResolution(
                subject="Openai",
                resolved=True,
            ),
            check_profile=profile,
            check_replay=replay,
        )

        apply_source_reproduction_conclusion(investigation)

        self.assertIn("HTTP 403", investigation.reproduction_conclusion)
        self.assertIn("DOWN classification", investigation.reproduction_conclusion)
        self.assertIn("fails its discovered", investigation.likely_cause)
        self.assertEqual(investigation.confidence, "high")

    def test_passing_replay_means_status_alone_does_not_explain_down(self):
        profile = ApplicationCheckProfile(
            source_file="backend/monitor.js",
            line_number=5,
            client="fetch",
            method="GET",
            timeout_ms=5000,
            headers={},
            omitted_header_names=[],
            follow_redirects=True,
            accepted_status_min=200,
            accepted_status_max=299,
            success_policy="HTTP 2xx",
            confidence="high",
            fidelity="high",
        )
        replay = ApplicationCheckReplay(
            compose_service="backend",
            container_id="be123",
            client="fetch",
            status_code=200,
            accepted_by_policy=True,
            fidelity="high",
        )
        investigation = RootCauseInvestigation(
            subject="Openai",
            workspace=Path("."),
            resolution=TargetResolution(
                subject="Openai",
                resolved=True,
            ),
            check_profile=profile,
            check_replay=replay,
        )

        apply_source_reproduction_conclusion(investigation)

        self.assertIn("passes", investigation.reproduction_conclusion)
        self.assertIn("not explained by HTTP status alone", investigation.reproduction_conclusion)
        self.assertEqual(investigation.confidence, "medium")


if __name__ == "__main__":
    unittest.main()
