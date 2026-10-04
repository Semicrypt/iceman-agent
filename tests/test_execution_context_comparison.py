import unittest
from pathlib import Path

from iceman.probing import (
    ExecutionContextProbe,
    ProbeStep,
    RootCauseInvestigation,
    SignalEmitter,
    TargetResolution,
    compare_execution_contexts,
)


def investigation_with_statuses(host_status, container_status):
    inv = RootCauseInvestigation(
        subject="Openai",
        workspace=Path("."),
        resolution=TargetResolution(
            subject="Openai",
            resolved=True,
        ),
        steps=[
            ProbeStep(
                name="HTTP",
                success=200 <= host_status < 400,
                detail=f"HTTP {host_status}",
                status_code=host_status,
            )
        ],
        signal_emitter=SignalEmitter(
            compose_service="backend",
            container_id="be123",
            matches=4,
        ),
        container_probe=ExecutionContextProbe(
            context="application-container",
            compose_service="backend",
            container_id="be123",
            steps=[
                ProbeStep(
                    name="HTTP",
                    success=200 <= container_status < 400,
                    detail=f"HTTP {container_status}",
                    status_code=container_status,
                )
            ],
        ),
    )
    return inv


class ExecutionContextComparisonTests(unittest.TestCase):
    def test_same_403_rejects_network_context_hypothesis(self):
        inv = investigation_with_statuses(403, 403)

        compare_execution_contexts(inv)

        self.assertIn("both receive HTTP 403", inv.context_comparison)
        self.assertIn("WAF", inv.likely_cause)
        self.assertEqual(inv.confidence, "high")

    def test_host_200_container_403_is_context_specific(self):
        inv = investigation_with_statuses(200, 403)

        compare_execution_contexts(inv)

        self.assertIn("Host receives HTTP 200", inv.context_comparison)
        self.assertIn(
            "execution-context specific",
            inv.likely_cause,
        )
        self.assertEqual(inv.confidence, "high")

    def test_both_200_points_back_to_check_logic_or_transient_state(self):
        inv = investigation_with_statuses(200, 200)

        compare_execution_contexts(inv)

        self.assertIn("both receive HTTP 200", inv.context_comparison)
        self.assertIn("check criteria", inv.likely_cause)
        self.assertEqual(inv.confidence, "medium")


if __name__ == "__main__":
    unittest.main()
