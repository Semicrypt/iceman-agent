import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.assessment import assess_project
from iceman.diagnostics import DockerDiagnosis
from iceman.runner import CommandResult


def result(command, stdout="", stderr="", code=0):
    return CommandResult(
        command=command,
        returncode=code,
        stdout=stdout,
        stderr=stderr,
    )


class AssessmentTests(unittest.TestCase):
    def test_running_compose_service_is_not_an_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docker-compose.yml").write_text(
                "services:\n  backend:\n    image: demo\n",
                encoding="utf-8",
            )

            def fake_run(command, cwd=None):
                if command == "git status --porcelain":
                    return result(command)
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
            )

            with patch("iceman.assessment.run_command", side_effect=fake_run):
                with patch(
                    "iceman.assessment.diagnose_docker_container",
                    return_value=diagnosis,
                ):
                    assessment = assess_project(root)

            self.assertEqual(len(assessment.services), 1)
            self.assertEqual(assessment.high_count, 0)
            self.assertEqual(assessment.medium_count, 0)

    def test_failed_service_becomes_high_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docker-compose.yml").write_text(
                "services:\n  frontend:\n    image: demo\n",
                encoding="utf-8",
            )

            def fake_run(command, cwd=None):
                if command == "docker compose config --services":
                    return result(command, "frontend\n")
                if command == "docker compose ps -a -q frontend":
                    return result(command, "deadbeef\n")
                return result(command)

            diagnosis = DockerDiagnosis(
                target="deadbeef",
                found=True,
                status="restarting",
                running=True,
                exit_code=1,
                restart_count=8,
                compose_service="frontend",
            )

            with patch("iceman.assessment.run_command", side_effect=fake_run):
                with patch(
                    "iceman.assessment.diagnose_docker_container",
                    return_value=diagnosis,
                ):
                    assessment = assess_project(root)

            self.assertEqual(assessment.high_count, 1)
            self.assertIsNotNone(assessment.top_issue)
            self.assertEqual(assessment.top_issue.target, "deadbeef")


if __name__ == "__main__":
    unittest.main()
