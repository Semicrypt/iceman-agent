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


def investigation(root):
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
        ),
        url="https://openai.com/",
        signal_emitter=SignalEmitter("backend", "be123", 4),
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


def replay(status):
    return ApplicationCheckReplay(
        compose_service="backend",
        container_id="be123",
        client="axios",
        status_code=status,
        accepted_by_policy=200 <= status <= 299,
        fidelity="high",
    )


class MultiSamplePreflightTests(unittest.TestCase):
    @patch("iceman.application_remediation._postgres_container")
    @patch("iceman.probing.time.sleep")
    @patch("iceman.probing.replay_application_check")
    @patch("iceman.application_remediation.socket.getaddrinfo", return_value=[])
    def test_three_successes_are_required(
        self,
        getaddrinfo,
        replay_application_check,
        sleep,
        postgres_container,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            replay_application_check.side_effect = [
                replay(200),
                replay(200),
                replay(200),
            ]
            postgres_container.return_value = (
                "pg",
                {
                    "POSTGRES_USER": "u",
                    "POSTGRES_DB": "d",
                },
            )

            plan = prepare_application_change_plan(
                investigation(root),
                1,
                "https://example.com/health",
                root,
            )

        self.assertTrue(plan.safe_to_apply)
        self.assertEqual(
            plan.preflight_stability.classification,
            "stable_success",
        )
        self.assertEqual(len(plan.preflight_samples), 3)

    @patch("iceman.probing.time.sleep")
    @patch("iceman.probing.replay_application_check")
    @patch("iceman.application_remediation.socket.getaddrinfo", return_value=[])
    def test_one_failure_blocks_write(
        self,
        getaddrinfo,
        replay_application_check,
        sleep,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            replay_application_check.side_effect = [
                replay(200),
                replay(403),
                replay(200),
            ]

            plan = prepare_application_change_plan(
                investigation(root),
                1,
                "https://example.com/health",
                root,
            )

        self.assertFalse(plan.safe_to_apply)
        self.assertEqual(
            plan.preflight_stability.classification,
            "unstable_mixed",
        )
        self.assertIn("not stable-success", plan.note)


if __name__ == "__main__":
    unittest.main()
