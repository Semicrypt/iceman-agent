import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.application_remediation import prepare_application_change_plan
from iceman.probing import (
    ApplicationCheckProfile,
    ApplicationCheckReplay,
    RootCauseInvestigation,
    SignalEmitter,
    TargetCandidate,
    TargetResolution,
)


def make_investigation(root: Path):
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
        likely_cause="HTTP 403 fails HTTP 2xx.",
        confidence="high",
        signal_emitter=SignalEmitter("backend", "be123", 4),
        check_profile=ApplicationCheckProfile(
            source_file="backend/monitor.service.js",
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
    )


class ApplicationChangePlanTests(unittest.TestCase):
    def test_option_one_requires_explicit_replacement_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = prepare_application_change_plan(
                make_investigation(root),
                1,
                None,
                root,
            )

        self.assertFalse(plan.safe_to_apply)
        self.assertIn("requires an explicit replacement URL", plan.note)

    @patch("iceman.application_remediation._postgres_container")
    @patch("iceman.probing.replay_application_check")
    @patch("iceman.application_remediation.socket.getaddrinfo", return_value=[])
    def test_passing_replacement_builds_guarded_database_plan(
        self,
        getaddrinfo,
        replay_application_check,
        postgres_container,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            replay_application_check.return_value = ApplicationCheckReplay(
                compose_service="backend",
                container_id="be123",
                client="axios",
                runtime="axios/1.19.0 on v20.20.2",
                status_code=200,
                latency_ms=200,
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

            plan = prepare_application_change_plan(
                make_investigation(root),
                1,
                "https://example.com/health",
                root,
            )

        self.assertTrue(plan.safe_to_apply)
        self.assertIn("expected exactly one matching row", plan.mutation_command)
        self.assertIn("https://openai.com/", plan.mutation_command)
        self.assertIn("https://example.com/health", plan.mutation_command)
        self.assertIsNotNone(plan.rollback_command)

    @patch("iceman.probing.replay_application_check")
    @patch("iceman.application_remediation.socket.getaddrinfo", return_value=[])
    def test_failed_preflight_blocks_database_write(
        self,
        getaddrinfo,
        replay_application_check,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            replay_application_check.return_value = ApplicationCheckReplay(
                compose_service="backend",
                container_id="be123",
                client="axios",
                status_code=403,
                accepted_by_policy=False,
                fidelity="high",
            )

            plan = prepare_application_change_plan(
                make_investigation(root),
                1,
                "https://example.com/health",
                root,
            )

        self.assertFalse(plan.safe_to_apply)
        self.assertIsNone(plan.mutation_command)
        self.assertIn("does not pass", plan.note)


if __name__ == "__main__":
    unittest.main()
