import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from iceman.probing import investigate_target


class FakeResponse:
    status = 200

    def __init__(self):
        self._url = "https://example.com/health"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return b"ok"

    def geturl(self):
        return self._url


class ProbeLayerTests(unittest.TestCase):
    @patch("iceman.probing.time.sleep")
    @patch("iceman.probing._docker_compose_services", return_value=[])
    @patch("iceman.probing.urllib.request.urlopen")
    @patch("iceman.probing.ssl.create_default_context")
    @patch("iceman.probing.socket.create_connection")
    @patch("iceman.probing.socket.getaddrinfo")
    def test_successful_https_probe(
        self,
        getaddrinfo,
        create_connection,
        create_default_context,
        urlopen,
        compose_services,
        sleep,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "checks.js").write_text(
                """
const check = {
  name: "Openai",
  url: "https://example.com/health"
};
""",
                encoding="utf-8",
            )

            getaddrinfo.return_value = [
                (
                    2,
                    1,
                    6,
                    "",
                    ("93.184.216.34", 443),
                )
            ]

            raw_sock = MagicMock()
            create_connection.return_value = raw_sock

            tls_sock = MagicMock()
            tls_sock.version.return_value = "TLSv1.3"
            tls_sock.__enter__.return_value = tls_sock
            tls_sock.__exit__.return_value = False

            context = MagicMock()
            context.wrap_socket.return_value = tls_sock
            create_default_context.return_value = context

            urlopen.return_value = FakeResponse()

            result = investigate_target(root, "Openai")

            self.assertEqual(result.confidence, "medium")
            self.assertTrue(result.healthy_endpoint)
            self.assertEqual(
                [step.name for step in result.steps],
                ["DNS", "TCP", "TLS", "HTTP"],
            )
            self.assertTrue(all(step.success for step in result.steps))

    @patch("iceman.probing._docker_compose_services", return_value=[])
    @patch("iceman.probing.socket.getaddrinfo")
    def test_dns_failure_stops_probe(
        self,
        getaddrinfo,
        compose_services,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "checks.js").write_text(
                """
const check = {
  name: "Broken",
  url: "https://does-not-exist.invalid"
};
""",
                encoding="utf-8",
            )

            import socket
            getaddrinfo.side_effect = socket.gaierror(-2, "Name not known")

            result = investigate_target(root, "Broken")

            self.assertEqual(result.confidence, "high")
            self.assertIn("DNS", result.likely_cause)
            self.assertEqual(len(result.steps), 1)
            self.assertFalse(result.steps[0].success)


if __name__ == "__main__":
    unittest.main()
