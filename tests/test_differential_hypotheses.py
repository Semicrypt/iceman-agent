import tempfile
import unittest
from pathlib import Path

from iceman.http_context import (
    HttpContextSnapshot,
    assess_differential_hypotheses,
    differential_confidence_basis,
)
from iceman.probing import (
    EvidenceStability,
    ExecutionContextProbe,
    HttpEvidenceSample,
    ProbeStep,
    RootCauseInvestigation,
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


def pass_step(name):
    return ProbeStep(
        name=name,
        success=True,
        detail=f"{name} passed",
    )


class DifferentialHypothesisTests(unittest.TestCase):
    def build_investigation(self, root):
        return RootCauseInvestigation(
            subject="Openai",
            workspace=root,
            resolution=TargetResolution(
                subject="Openai",
                resolved=True,
            ),
            url="https://openai.com/",
            steps=[
                pass_step("DNS"),
                pass_step("TCP"),
                pass_step("TLS"),
            ],
            container_probe=ExecutionContextProbe(
                context="application-container",
                steps=[
                    pass_step("DNS"),
                    pass_step("TCP"),
                    pass_step("TLS"),
                ],
            ),
            session_host_stability=stability(
                "host",
                [403, 403, 200, 200],
            ),
            session_source_stability=stability(
                "source",
                [403, 403, 403, 403],
            ),
        )

    def test_lower_layers_redirect_proxy_tls_alpn_are_constrained(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = self.build_investigation(Path(tmp))
            host = HttpContextSnapshot(
                context="host",
                client="urllib",
                final_url="https://openai.com/",
                tls_version="TLSv1.3",
                alpn_protocol="http/1.1",
                proxy_env_present=[],
                request_headers={
                    "user-agent": "Iceman/0.7.4 context-differential",
                    "accept": "*/*",
                },
            )
            app = HttpContextSnapshot(
                context="application-container",
                client="axios",
                final_url="https://openai.com/",
                tls_version="TLSv1.3",
                alpn_protocol="http/1.1",
                proxy_env_present=[],
                request_headers={
                    "user-agent": "axios/1.19.0",
                    "accept": "application/json, text/plain, */*",
                },
            )

            assessments = assess_differential_hypotheses(
                inv,
                host,
                app,
            )

        mapping = {
            item.hypothesis: item.status
            for item in assessments
        }
        self.assertEqual(
            mapping["Basic DNS/TCP/TLS reachability failure"],
            "ruled_down",
        )
        self.assertEqual(
            mapping["Different final redirect destination"],
            "unsupported",
        )
        self.assertEqual(
            mapping["Simple proxy-environment mismatch"],
            "unsupported",
        )
        self.assertEqual(
            mapping["TLS-version mismatch"],
            "unsupported",
        )
        self.assertEqual(
            mapping["ALPN/protocol-negotiation mismatch"],
            "unsupported",
        )
        self.assertEqual(
            mapping["HTTP request-characteristic treatment"],
            "plausible",
        )
        self.assertEqual(
            mapping["User-Agent is the root cause"],
            "not_established",
        )
        self.assertEqual(
            mapping["Specific WAF/bot rule"],
            "not_established",
        )

    def test_high_confidence_is_about_failure_layer_not_specific_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = self.build_investigation(Path(tmp))
            host = HttpContextSnapshot(
                context="host",
                client="urllib",
                final_url="https://openai.com/",
                tls_version="TLSv1.3",
                alpn_protocol="http/1.1",
                proxy_env_present=[],
            )
            app = HttpContextSnapshot(
                context="application-container",
                client="axios",
                final_url="https://openai.com/",
                tls_version="TLSv1.3",
                alpn_protocol="http/1.1",
                proxy_env_present=[],
            )
            assessments = assess_differential_hypotheses(
                inv,
                host,
                app,
            )
            confidence, basis = differential_confidence_basis(
                inv,
                assessments,
            )

        self.assertEqual(confidence, "high")
        self.assertIn("failure layer/context", basis)
        self.assertIn(
            "specific remote rejection mechanism remains lower",
            basis,
        )


if __name__ == "__main__":
    unittest.main()
