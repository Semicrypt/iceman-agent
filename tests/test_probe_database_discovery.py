import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.probing import discover_postgres_target
from iceman.runner import CommandResult


def command_result(command, stdout="", stderr="", returncode=0):
    return CommandResult(
        command=command,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


class ProbeDatabaseDiscoveryTests(unittest.TestCase):
    @patch("iceman.probing.run_command")
    def test_exact_database_target_is_resolved(self, run_command):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake(command, cwd=None):
                if command == "docker compose config --services":
                    return command_result(command, "backend\npostgres\n")
                if command == "docker compose ps -q backend":
                    return command_result(command, "backend123\n")
                if command.startswith("docker inspect backend123") and ".Config.Image" in command:
                    return command_result(command, "minerva-backend:local\n")
                if command.startswith("docker inspect backend123") and ".Config.Env" in command:
                    return command_result(command, "NODE_ENV=production\n")
                if command == "docker compose ps -q postgres":
                    return command_result(command, "pg123\n")
                if command.startswith("docker inspect pg123") and ".Config.Image" in command:
                    return command_result(command, "postgres:17-alpine\n")
                if command.startswith("docker inspect pg123") and ".Config.Env" in command:
                    return command_result(
                        command,
                        "POSTGRES_USER=minerva\nPOSTGRES_DB=hybrid_monitor\n",
                    )
                if "information_schema.columns" in command:
                    return command_result(
                        command,
                        "services|name\nservices|url\nservices|status\n",
                    )
                if 'FROM "services"' in command:
                    return command_result(
                        command,
                        "Openai|https://api.openai.com/v1/models\n",
                    )
                return command_result(command, returncode=1)

            run_command.side_effect = fake

            resolution = discover_postgres_target(root, "Openai")

            self.assertTrue(resolution.resolved)
            self.assertEqual(resolution.confidence, "high")
            self.assertEqual(
                resolution.selected.url,
                "https://api.openai.com/v1/models",
            )
            self.assertEqual(
                resolution.selected.source_kind,
                "database",
            )
            self.assertIn(
                "PostgreSQL services.url",
                resolution.selected.source_file,
            )

    @patch("iceman.probing.run_command")
    def test_database_non_url_value_is_rejected(self, run_command):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake(command, cwd=None):
                if command == "docker compose config --services":
                    return command_result(command, "postgres\n")
                if command == "docker compose ps -q postgres":
                    return command_result(command, "pg123\n")
                if command.startswith("docker inspect pg123") and ".Config.Image" in command:
                    return command_result(command, "postgres:17-alpine\n")
                if command.startswith("docker inspect pg123") and ".Config.Env" in command:
                    return command_result(
                        command,
                        "POSTGRES_USER=minerva\nPOSTGRES_DB=hybrid_monitor\n",
                    )
                if "information_schema.columns" in command:
                    return command_result(
                        command,
                        "services|name\nservices|url\n",
                    )
                if 'FROM "services"' in command:
                    return command_result(
                        command,
                        "Openai|not-a-url\n",
                    )
                return command_result(command, returncode=1)

            run_command.side_effect = fake

            resolution = discover_postgres_target(root, "Openai")
            self.assertFalse(resolution.resolved)


if __name__ == "__main__":
    unittest.main()
