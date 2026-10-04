import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.http_context import (
    HttpContextSnapshot,
    analyze_http_context_differential,
)
from iceman.probing import (
    ApplicationCheckProfile,
    HttpEvidenceSample,
    RootCauseInvestigation,
    SignalEmitter,
    TargetResolution,
    summarize_http_evidence,
)


def stability(context, statuses):
    return summarize_http_evidence(
        context,
        [
            HttpEvidenceSample(
                attempt=i + 1,
                status_code=status,
                accepted=200 <= status < 400,
                latency_ms=1,
                detail=f"HTTP {status}",
            )
            for i, status in enumerate(statuses)
        ],
    )


class HttpContextAnalysisIntegrationTests(unittest.TestCase):
    @patch("iceman.http_context.capture_application_context")
    @patch("iceman.http_context.capture_host_context")
    def test_conclusion_uses_session_stability(
        self,
        host_capture,
        app_capture,
    ):
        host_capture.return_value = HttpContextSnapshot(
            context="host",
            client="urllib",
            status_code=200,
            sample_statuses=[403, 200, 200],
            final_url="https://openai.com/",
            request_headers={"user-agent": "Iceman"},
        )
        app_capture.return_value = HttpContextSnapshot(
            context="application-container",
            client="axios",
            status_code=403,
            sample_statuses=[403],
            final_url="https://openai.com/",
            request_headers={"user-agent": "axios/1.19.0"},
        )

        with tempfile.TemporaryDirectory() as tmp:
            inv = RootCauseInvestigation(
                subject="Openai",
                workspace=Path(tmp),
                resolution=TargetResolution(
                    subject="Openai",
                    resolved=True,
                ),
                url="https://openai.com/",
                signal_emitter=SignalEmitter("backend", "be123", 6),
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
                session_host_stability=stability(
                    "host",
                    [403, 403, 403, 403, 200, 200],
                ),
                session_source_stability=stability(
                    "source",
                    [403, 403, 403, 403, 403, 403],
                ),
            )

            result = analyze_http_context_differential(inv)

        self.assertEqual(result.confidence, "high")
        self.assertIn("failure layer/context", result.confidence_basis)
        self.assertGreaterEqual(len(result.hypotheses), 3)
        self.assertIn(
            "host access succeeds at least intermittently",
            result.conclusion,
        )
        self.assertIn(
            "does not attempt WAF/bot bypasses",
            result.safety_note,
        )


if __name__ == "__main__":
    unittest.main()
