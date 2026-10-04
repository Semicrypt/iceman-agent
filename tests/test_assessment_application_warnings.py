import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.assessment import assess_project
from iceman.diagnostics import ApplicationSignal, DockerDiagnosis
from iceman.runner import CommandResult


def result(command, stdout="", stderr="", code=0):
    return CommandResult(
        command=command,
        returncode=code,
        stdout=stdout,
        stderr=stderr,
    )


class AssessmentApplicationWarningTests(unittest.TestCase):
    def test_app_warning_does_not_make_runtime_deployment_unhealthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docker-compose.yml").write_text(
                "services:\n  backend:\n    image: demo\n",
                encoding="utf-8",
            )

            def fake_run(command, cwd=None):
                if command == "docker compose config --services":
                    return result(command, "backend\n")
                if command == "docker compose ps -a -q backend":
                    return result(command, "abc123\n")
                return result(command)

            diagnosis = DockerDiagnosis(
                target="abc123",
                found=True,
                status="running",
                running=True,
                exit_code=0,
                restart_count=0,
                compose_service="backend",
                application_signals=[
                    ApplicationSignal("Openai", "DOWN", 4),
                    ApplicationSignal("Broken Website", "DOWN", 4),
                ],
            )

            with patch("iceman.assessment.run_command", side_effect=fake_run):
                with patch(
                    "iceman.assessment.diagnose_docker_container",
                    return_value=diagnosis,
                ):
                    assessment = assess_project(root)

            self.assertTrue(assessment.healthy)
            self.assertEqual(assessment.application_warning_count, 2)
            self.assertEqual(assessment.high_count, 0)
            self.assertEqual(assessment.medium_count, 0)


if __name__ == "__main__":
    unittest.main()
