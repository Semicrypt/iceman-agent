import tempfile
import unittest
from pathlib import Path

from iceman.http_context import (
    HttpContextSnapshot,
    build_differential_findings,
)
from iceman.probing import (
    HttpEvidenceSample,
    RootCauseInvestigation,
    TargetResolution,
    summarize_http_evidence,
)


def stability(context, statuses):
    return summarize_http_evidence(
        context,
        [
            HttpEvidenceSample(
                attempt=index + 1,
                status_code=status,
                accepted=200 <= status < 400,
                latency_ms=1,
                detail=f"HTTP {status}",
            )
            for index, status in enumerate(statuses)
        ],
    )


class HttpContextDifferentialTests(unittest.TestCase):
    def test_mixed_host_and_stable_app_failure_is_explained_without_bypass(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = RootCauseInvestigation(
                subject="Openai",
                workspace=Path(tmp),
                resolution=TargetResolution(
                    subject="Openai",
                    resolved=True,
                ),
                url="https://openai.com/",
                session_host_stability=stability(
                    "host",
                    [403, 403, 403, 403, 200, 200],
                ),
                session_source_stability=stability(
                    "source",
                    [403, 403, 403, 403, 403, 403],
                ),
            )

            host = HttpContextSnapshot(
                context="host",
                client="Python urllib",
                status_code=200,
                final_url="https://openai.com/",
                request_headers={
                    "user-agent": "Iceman/0.7.5 context-differential",
                },
                response_headers={"server": "cloudflare"},
                proxy_env_present=[],
            )
            application = HttpContextSnapshot(
                context="application-container",
                client="axios",
                status_code=403,
                final_url="https://openai.com/",
                request_headers={
                    "user-agent": "axios/1.19.0",
                },
                response_headers={"server": "cloudflare"},
                proxy_env_present=[],
                selected_remote_address="104.18.33.45",
                address_family="IPv4",
            )

            findings = build_differential_findings(
                inv,
                host,
                application,
            )

        categories = [finding.category for finding in findings]
        self.assertIn("stability", categories)
        self.assertIn("request", categories)
        self.assertIn("response", categories)
        ua_finding = next(
            item for item in findings
            if item.category == "request"
        )
        self.assertIn("not proof", ua_finding.interpretation)
        self.assertNotIn("bypass", ua_finding.observation.lower())


if __name__ == "__main__":
    unittest.main()
