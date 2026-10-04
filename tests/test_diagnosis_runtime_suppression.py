import unittest
from unittest.mock import patch

from iceman.diagnostics import diagnose_docker_container


class RuntimeSuppressionTests(unittest.TestCase):
    @patch("iceman.diagnostics.run_command")
    def test_runtime_error_marks_logs_unreliable_and_suppresses_exit_hint(
        self,
        mock_run,
    ):
        inspect_json = """[
          {
            "Id": "abcdef1234567890",
            "Config": {
              "Image": "demo",
              "Labels": {
                "com.docker.compose.service": "backend"
              }
            },
            "State": {
              "Status": "exited",
              "Running": false,
              "ExitCode": 127,
              "Error": "OCI runtime create failed: error mounting docker.sock to /var/run/docker.sock: not a directory"
            },
            "RestartCount": 0,
            "NetworkSettings": {"Ports": {}},
            "Mounts": []
          }
        ]"""

        class Result:
            def __init__(self, stdout="", stderr="", returncode=0):
                self.stdout = stdout
                self.stderr = stderr
                self.returncode = returncode

        mock_run.side_effect = [
            Result(stdout=inspect_json),
            Result(stdout="Cannot find module old-history"),
        ]

        with patch(
            "iceman.diagnostics.inspect_host_docker_socket",
            return_value=(True, "socket ok"),
        ):
            diagnosis = diagnose_docker_container("demo")

        titles = {finding.title for finding in diagnosis.findings}
        self.assertFalse(diagnosis.application_logs_reliable)
        self.assertIn("Docker runtime error", titles)
        self.assertIn("Exit code interpretation suppressed", titles)
        self.assertIn("Application logs may be stale", titles)
        self.assertNotIn("Missing Node.js module", titles)


if __name__ == "__main__":
    unittest.main()
