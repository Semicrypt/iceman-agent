import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.probing import SignalEmitter, probe_from_container
from iceman.runner import CommandResult


def result(command, stdout="", stderr="", returncode=0):
    return CommandResult(
        command=command,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


class ExecutionContextContainerProbeTests(unittest.TestCase):
    @patch("iceman.probing.run_command")
    def test_node_probe_parses_http_403(self, run_command):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            emitter = SignalEmitter(
                compose_service="backend",
                container_id="be123",
                matches=4,
            )

            payload = (
                '{"dns":{"success":true,"address":"1.2.3.4","latency_ms":5},'
                '"tcp":{"success":true,"latency_ms":20},'
                '"tls":{"success":true,"protocol":"TLSv1.3","latency_ms":30},'
                '"http":{"status":403,"latency_ms":80},"error":null}'
            )

            def fake(command, cwd=None):
                if "node --version" in command:
                    return result(command, "v20.20.2\n")
                if "node -e" in command:
                    return result(command, payload + "\n")
                return result(command, returncode=1)

            run_command.side_effect = fake

            probe = probe_from_container(
                root,
                emitter,
                "https://openai.com/",
            )

            self.assertEqual(probe.runtime, "node v20.20.2")
            self.assertEqual(probe.http_status, 403)
            self.assertEqual(
                [step.name for step in probe.steps],
                ["DNS", "TCP", "TLS", "HTTP"],
            )
            self.assertFalse(probe.steps[-1].success)


if __name__ == "__main__":
    unittest.main()
