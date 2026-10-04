import unittest
from unittest.mock import patch

from iceman.diagnostics import diagnose_docker_container


class ApplicationDiagnosisIntegrationTests(unittest.TestCase):
    @patch("iceman.diagnostics.run_command")
    def test_running_container_gets_application_warning(self, mock_run):
        inspect_json = """[
          {
            "Id": "abcdef1234567890",
            "Config": {
              "Image": "demo",
              "Labels": {}
            },
            "State": {
              "Status": "running",
              "Running": true,
              "ExitCode": 0,
              "Error": ""
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

        logs = """
❌ Openai | DOWN
❌ Openai | DOWN
❌ Broken Website | DOWN
❌ Broken Website | DOWN
"""

        mock_run.side_effect = [
            Result(stdout=inspect_json),
            Result(stdout=logs),
        ]

        diagnosis = diagnose_docker_container("demo")
        titles = [finding.title for finding in diagnosis.findings]

        self.assertEqual(diagnosis.status, "running")
        self.assertEqual(len(diagnosis.application_signals), 2)
        self.assertIn("Container is running", titles)
        self.assertIn("Repeated application-level failure signals", titles)


if __name__ == "__main__":
    unittest.main()
