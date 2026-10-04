import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.probing import (
    ApplicationCheckProfile,
    SignalEmitter,
    replay_application_check,
)
from iceman.runner import CommandResult


def result(command, stdout="", stderr="", returncode=0):
    return CommandResult(
        command=command,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


class CheckReplayTests(unittest.TestCase):
    @patch("iceman.probing.run_command")
    def test_axios_403_fails_2xx_policy(self, run_command):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            emitter = SignalEmitter(
                compose_service="backend",
                container_id="be123",
                matches=4,
            )
            profile = ApplicationCheckProfile(
                source_file="backend/monitor.js",
                line_number=10,
                client="axios",
                method="GET",
                timeout_ms=5000,
                headers={},
                omitted_header_names=[],
                follow_redirects=True,
                accepted_status_min=200,
                accepted_status_max=299,
                success_policy="Axios default HTTP 2xx",
                confidence="high",
                fidelity="high",
            )

            run_command.return_value = result(
                "docker exec ...",
                (
                    '{"status":403,"latency_ms":123,'
                    '"runtime":"axios/1.7.0 on v20.20.2"}\n'
                ),
            )

            replay = replay_application_check(
                root,
                emitter,
                "https://openai.com/",
                profile,
            )

            self.assertEqual(replay.status_code, 403)
            self.assertFalse(replay.accepted_by_policy)
            self.assertEqual(replay.fidelity, "high")
            self.assertIn("axios/", replay.runtime)

    @patch("iceman.probing.run_command")
    def test_non_read_only_method_is_not_replayed(self, run_command):
        with tempfile.TemporaryDirectory() as tmp:
            profile = ApplicationCheckProfile(
                source_file="backend/monitor.js",
                line_number=10,
                client="axios",
                method="POST",
                timeout_ms=5000,
                headers={},
                omitted_header_names=[],
                follow_redirects=True,
                accepted_status_min=200,
                accepted_status_max=299,
                success_policy="HTTP 2xx",
                confidence="high",
                fidelity="high",
            )
            emitter = SignalEmitter("backend", "be123", 1)

            replay = replay_application_check(
                Path(tmp),
                emitter,
                "https://example.com/",
                profile,
            )

            self.assertIsNone(replay.status_code)
            self.assertIn("disabled for POST", replay.note)
            run_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
