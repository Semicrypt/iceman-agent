import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.http_context import capture_application_context
from iceman.probing import ApplicationCheckProfile, SignalEmitter
from iceman.runner import CommandResult


def result(command, stdout="", stderr="", returncode=0):
    return CommandResult(
        command=command,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


class HttpContextApplicationCaptureTests(unittest.TestCase):
    @patch(
        "iceman.http_context._container_metadata",
        return_value=(["HTTPS_PROXY"], ["default"]),
    )
    @patch("iceman.http_context.run_command")
    def test_safe_container_context_is_parsed(
        self,
        run_command,
        metadata,
    ):
        payload = {
            "status": 403,
            "latency_ms": 500,
            "runtime": "axios/1.19.0 on v20.20.2",
            "final_url": "https://openai.com/",
            "redirect_count": 0,
            "request_headers": {
                "user-agent": "axios/1.19.0",
                "accept": "application/json, text/plain, */*",
            },
            "response_headers": {
                "server": "cloudflare",
                "cf-ray": "abc-XYZ",
                "content-type": "text/html",
            },
            "resolved_addresses": [
                {"address": "104.18.33.45", "family": "IPv4"},
                {"address": "172.64.154.211", "family": "IPv4"},
            ],
            "selected_remote_address": "104.18.33.45",
            "address_family": "IPv4",
            "tls_version": "TLSv1.3",
            "alpn_protocol": "http/1.1",
            "transport_observation_source": (
                "live https.request secureConnect event"
            ),
            "transport_event": "secureConnect",
            "dns_servers": ["127.0.0.11"],
            "proxy_env_present": ["HTTPS_PROXY"],
        }
        run_command.return_value = result(
            "docker exec ...",
            json.dumps(payload) + "\n",
        )

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = capture_application_context(
                Path(tmp),
                SignalEmitter("backend", "be123", 4),
                "https://openai.com/",
                ApplicationCheckProfile(
                    source_file="backend/monitor.js",
                    line_number=1,
                    client="axios",
                    method="GET",
                    timeout_ms=10000,
                    headers={},
                    omitted_header_names=["Authorization"],
                    follow_redirects=True,
                    accepted_status_min=200,
                    accepted_status_max=299,
                    success_policy="HTTP 2xx",
                    confidence="high",
                    fidelity="high",
                ),
            )

        self.assertEqual(snapshot.status_code, 403)
        self.assertEqual(snapshot.runtime, "axios/1.19.0 on v20.20.2")
        self.assertEqual(snapshot.request_headers["user-agent"], "axios/1.19.0")
        self.assertEqual(snapshot.response_headers["server"], "cloudflare")
        self.assertEqual(snapshot.proxy_env_present, ["HTTPS_PROXY"])
        self.assertEqual(snapshot.container_networks, ["default"])
        self.assertEqual(
            snapshot.resolved_addresses,
            ["104.18.33.45", "172.64.154.211"],
        )
        self.assertEqual(snapshot.selected_remote_address, "104.18.33.45")
        self.assertEqual(snapshot.address_family, "IPv4")
        self.assertEqual(snapshot.tls_version, "TLSv1.3")
        self.assertEqual(snapshot.alpn_protocol, "http/1.1")
        self.assertIn(
            "live https.request",
            snapshot.transport_observation_source,
        )
        self.assertEqual(snapshot.transport_event, "secureConnect")
        self.assertEqual(snapshot.dns_servers, ["127.0.0.11"])
        self.assertIn("Authorization", snapshot.omitted_header_names)


if __name__ == "__main__":
    unittest.main()
