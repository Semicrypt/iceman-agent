import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.probing import discover_signal_emitter
from iceman.runner import CommandResult


def result(command, stdout="", stderr="", returncode=0):
    return CommandResult(
        command=command,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


class ExecutionContextEmitterTests(unittest.TestCase):
    @patch("iceman.probing.run_command")
    def test_backend_is_identified_as_signal_emitter(self, run_command):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake(command, cwd=None):
                if command == "docker compose config --services":
                    return result(command, "postgres\nbackend\nfrontend\n")
                if command == "docker compose ps -q postgres":
                    return result(command, "pg123\n")
                if command == "docker compose ps -q backend":
                    return result(command, "be123\n")
                if command == "docker compose ps -q frontend":
                    return result(command, "fe123\n")
                if command.startswith("docker logs --tail 250 pg123"):
                    return result(command, "database ready\n")
                if command.startswith("docker logs --tail 250 be123"):
                    return result(
                        command,
                        "❌ Openai | DOWN\n❌ Openai | DOWN\n",
                    )
                if command.startswith("docker logs --tail 250 fe123"):
                    return result(command, "nginx ready\n")
                return result(command, returncode=1)

            run_command.side_effect = fake

            emitter = discover_signal_emitter(root, "Openai")

            self.assertIsNotNone(emitter)
            self.assertEqual(emitter.compose_service, "backend")
            self.assertEqual(emitter.container_id, "be123")
            self.assertEqual(emitter.matches, 2)


if __name__ == "__main__":
    unittest.main()
