import tempfile
import unittest
from pathlib import Path

from iceman.http_context import (
    HttpContextSnapshot,
    build_differential_findings,
)
from iceman.probing import RootCauseInvestigation, TargetResolution


class HttpTransportCorrelationTests(unittest.TestCase):
    def test_application_selected_ip_is_correlated_with_container_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = RootCauseInvestigation(
                subject="Openai",
                workspace=Path(tmp),
                resolution=TargetResolution(
                    subject="Openai",
                    resolved=True,
                ),
                url="https://openai.com/",
            )
            host = HttpContextSnapshot(
                context="host",
                client="urllib",
                tls_version="TLSv1.3",
                alpn_protocol="http/1.1",
            )
            application = HttpContextSnapshot(
                context="application-container",
                client="axios",
                resolved_addresses=[
                    "104.18.33.45",
                    "172.64.154.211",
                ],
                selected_remote_address="172.64.154.211",
                address_family="IPv4",
                tls_version="TLSv1.3",
                alpn_protocol="http/1.1",
            )

            findings = build_differential_findings(
                inv,
                host,
                application,
            )

        correlation = [
            item for item in findings
            if item.category == "transport-correlation"
        ]
        self.assertEqual(len(correlation), 1)
        self.assertIn("is present in", correlation[0].observation)

    def test_matching_tls_and_alpn_are_reported_as_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = RootCauseInvestigation(
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
                tls_version="TLSv1.3",
                alpn_protocol="http/1.1",
            )
            app = HttpContextSnapshot(
                context="application-container",
                client="axios",
                tls_version="TLSv1.3",
                alpn_protocol="http/1.1",
            )
            findings = build_differential_findings(inv, host, app)

        transport_text = " ".join(
            item.observation
            for item in findings
            if item.category == "transport"
        )
        self.assertIn("TLSv1.3", transport_text)
        self.assertIn("http/1.1", transport_text)


if __name__ == "__main__":
    unittest.main()
