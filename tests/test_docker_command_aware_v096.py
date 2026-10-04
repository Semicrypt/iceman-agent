import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.devops_actions import (
    DevOpsAction,
    DevOpsPlan,
    DevOpsRequest,
    DevOpsStep,
    DevOpsStepResult,
    reason_docker_execution,
)
from iceman.reasoner import ReasoningAction, reason_request
from iceman.runner import CommandResult
from iceman.session import SessionState


class DockerCommandAwareV096Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = SessionState(self.root)

    @patch(
        "iceman.language_understanding._docker_container_names",
        return_value=["broken-backend", "backend", "minerva-backend"],
    )
    def test_spaced_existing_container_name_is_normalized_before_routing(self, _names):
        decision = reason_request("why did broken backend stop", self.state)
        self.assertEqual(decision.action, ReasoningAction.EXECUTE_DEVOPS_ACTION)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_DIAGNOSE)
        self.assertEqual(decision.component, "broken-backend")
        self.assertTrue(decision.requires_clarification)
        self.assertEqual(decision.interpreted_request, "why did broken-backend stop")

    @patch(
        "iceman.language_understanding._docker_container_names",
        return_value=["minerva-backend"],
    )
    def test_underscore_and_space_variants_ground_to_real_name(self, _names):
        decision = reason_request("check if minerva backend is healthy", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_HEALTH)
        self.assertEqual(decision.component, "minerva-backend")
        self.assertTrue(decision.requires_clarification)

    def _plan(self):
        return DevOpsPlan(
            request=DevOpsRequest(DevOpsAction.DOCKER_DIAGNOSE, component="broken-backend"),
            workspace=self.root,
            title="test",
            inferred_component="broken-backend",
        )

    def _state_result(self):
        return DevOpsStepResult(
            DevOpsStep("Inspect runtime state for broken-backend", "docker inspect"),
            CommandResult("docker inspect", 0, "exited|false|1|0|none|\n", ""),
        )

    def _startup_result(self, text):
        return DevOpsStepResult(
            DevOpsStep("Inspect safe startup command for broken-backend", "docker inspect", stream_output=False),
            CommandResult("docker inspect", 0, text, ""),
        )

    def _logs_result(self, text):
        return DevOpsStepResult(
            DevOpsStep("Read recent logs for broken-backend", "docker logs", stream_output=False),
            CommandResult("docker logs", 0, text, ""),
        )

    def test_entrypoint_wrapper_reconstructs_node_command(self):
        log = "Error: Cannot find module '/app/missing.js'\ncode: 'MODULE_NOT_FOUND'\nrequireStack: []\n"
        diagnosis, evidence, actions = reason_docker_execution(
            self._plan(),
            [
                self._state_result(),
                self._startup_result("docker-entrypoint.sh|node|missing.js\n"),
                self._logs_result(log),
            ],
        )
        self.assertTrue(any("startup command" in line.lower() and "missing.js" in line for line in diagnosis))
        self.assertTrue(any("Safe startup reconstruction: node missing.js." == line for line in evidence))
        self.assertTrue(any("correct the container startup command" in line for line in actions))
        self.assertFalse(any("runtime package/module dependency" in line for line in diagnosis))

    def test_sensitive_second_argument_is_redacted_in_evidence(self):
        log = "Error: Cannot find module '/app/missing.js'\ncode: 'MODULE_NOT_FOUND'\nrequireStack: []\n"
        _, evidence, _ = reason_docker_execution(
            self._plan(),
            [
                self._state_result(),
                self._startup_result("docker-entrypoint.sh|node|--token=supersecret\n"),
                self._logs_result(log),
            ],
        )
        joined = " ".join(evidence)
        self.assertNotIn("supersecret", joined)


if __name__ == "__main__":
    unittest.main()
