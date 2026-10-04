import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.application_remediation import (
    build_application_remediation_advice,
    prepare_application_change_plan,
)
from iceman.probing import (
    ApplicationCheckProfile,
    ApplicationCheckReplay,
    ExecutionContextProbe,
    ProbeStep,
    RootCauseInvestigation,
    SignalEmitter,
    TargetCandidate,
    TargetResolution,
)


def base_investigation(root: Path, host_status: int, container_status: int):
    candidate = TargetCandidate(
        subject="Openai",
        url="https://openai.com/",
        source_file="PostgreSQL services.url (matched by services.name)",
        line_number=None,
        score=100,
        context="",
        source_kind="database",
    )
    return RootCauseInvestigation(
        subject="Openai",
        workspace=root,
        resolution=TargetResolution(
            subject="Openai",
            resolved=True,
            selected=candidate,
            confidence="high",
        ),
        url="https://openai.com/",
        likely_cause="HTTP response differs by execution context.",
        confidence="high",
        steps=[
            ProbeStep(
                name="HTTP",
                success=200 <= host_status < 400,
                detail=f"HTTP {host_status}",
                status_code=host_status,
            )
        ],
        signal_emitter=SignalEmitter("backend", "be123", 11),
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
        check_profile=ApplicationCheckProfile(
            source_file="backend/src/services/monitor.service.js",
            line_number=30,
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
        check_replay=ApplicationCheckReplay(
            compose_service="backend",
            container_id="be123",
            client="axios",
            status_code=container_status,
            accepted_by_policy=200 <= container_status <= 299,
            fidelity="high",
        ),
    )


class ContextAwareRemediationRankingTests(unittest.TestCase):
    def test_host_200_backend_403_prioritizes_context_investigation(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = base_investigation(Path(tmp), 200, 403)
            advice = build_application_remediation_advice(inv)

        self.assertEqual(advice.options[0].action, "investigate_context")
        self.assertTrue(advice.options[0].recommended)
        self.assertEqual(advice.options[1].action, "replace_target")
        self.assertFalse(advice.options[1].recommended)
        self.assertIn("host HTTP 200", advice.ranking_basis)
        self.assertIn("HTTP 403", advice.ranking_basis)

    @patch("iceman.application_remediation._postgres_container")
    @patch("iceman.probing.replay_application_check")
    @patch("iceman.application_remediation.socket.getaddrinfo", return_value=[])
    def test_replacement_write_moves_to_option_two(
        self,
        getaddrinfo,
        replay_application_check,
        postgres_container,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inv = base_investigation(root, 200, 403)

            replay_application_check.return_value = ApplicationCheckReplay(
                compose_service="backend",
                container_id="be123",
                client="axios",
                status_code=200,
                accepted_by_policy=True,
                fidelity="high",
            )
            postgres_container.return_value = (
                "pg123",
                {
                    "POSTGRES_USER": "minerva",
                    "POSTGRES_DB": "hybrid_monitor",
                },
            )

            option_one = prepare_application_change_plan(
                inv,
                1,
                None,
                root,
            )
            option_two = prepare_application_change_plan(
                inv,
                2,
                "https://example.com/health",
                root,
            )

        self.assertFalse(option_one.safe_to_apply)
        self.assertIn("not a bounded automatic database write", option_one.note)

        self.assertTrue(option_two.safe_to_apply)
        self.assertEqual(option_two.option_number, 2)
        self.assertEqual(option_two.title, "Replace the monitored target URL")
        self.assertIn("https://example.com/health", option_two.mutation_command)

    def test_both_healthy_prioritizes_check_logic_investigation(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = base_investigation(Path(tmp), 200, 200)
            advice = build_application_remediation_advice(inv)

        self.assertEqual(
            advice.options[0].action,
            "investigate_check_logic",
        )
        self.assertTrue(advice.options[0].recommended)
        self.assertEqual(advice.options[1].action, "replace_target")


if __name__ == "__main__":
    unittest.main()
