import tempfile
import unittest
from pathlib import Path

from iceman.devops_actions import (
    DevOpsAction,
    DevOpsPlan,
    DevOpsRequest,
    DevOpsStep,
    DevOpsStepResult,
    reason_docker_execution,
)
from iceman.runner import CommandResult


class DockerReasonedDiagnosisV094Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def plan(self, action, *, component="backend", port=None, notes=None):
        return DevOpsPlan(
            request=DevOpsRequest(action, component=component, port=port),
            workspace=self.root,
            title="test",
            notes=list(notes or []),
            inferred_component=component,
        )

    def state_result(self, text="running|true|0|0|none|\n"):
        return DevOpsStepResult(
            DevOpsStep("Inspect runtime state for backend", "docker inspect"),
            CommandResult("docker inspect", 0, text, ""),
        )

    def ports_result(self, text="5000/tcp -> 0.0.0.0:5001\n5000/tcp -> [::]:5001\n"):
        return DevOpsStepResult(
            DevOpsStep("Show published ports for backend", "docker port backend"),
            CommandResult("docker port backend", 0, text, ""),
        )

    def probe_result(self, text="curl_rc=0|HTTP 200\n"):
        return DevOpsStepResult(
            DevOpsStep("Probe localhost:5001 over HTTP", "curl"),
            CommandResult("curl", 0, text, ""),
        )

    def test_connectivity_explains_port_mismatch_and_endpoint(self):
        plan = self.plan(
            DevOpsAction.DOCKER_CONNECTIVITY,
            port=5000,
            notes=[
                "Requested host port 5000 is currently published by Docker container 'minerva-backend'."
            ],
        )
        diagnosis, evidence, next_actions = reason_docker_execution(
            plan,
            [self.state_result(), self.ports_result(), self.probe_result()],
        )
        self.assertTrue(any("reachable locally" in line for line in diagnosis))
        self.assertTrue(any("port mismatch" in line.lower() for line in diagnosis))
        self.assertTrue(any("minerva-backend" in line for line in evidence))
        self.assertTrue(any("http://localhost:5001/" in line for line in next_actions))

    def test_health_explains_runtime_is_not_readiness_without_healthcheck(self):
        plan = self.plan(DevOpsAction.DOCKER_HEALTH)
        diagnosis, evidence, next_actions = reason_docker_execution(
            plan, [self.state_result()]
        )
        text = " ".join(diagnosis).lower()
        self.assertIn("no docker healthcheck", text)
        self.assertIn("cannot prove application readiness", text)
        self.assertTrue(any("healthcheck" in line.lower() for line in next_actions))
        self.assertTrue(any("status=running" in line for line in evidence))

    def test_diagnose_prioritizes_dependency_refusal(self):
        plan = self.plan(DevOpsAction.DOCKER_DIAGNOSE)
        logs = DevOpsStepResult(
            DevOpsStep("Read recent logs for backend", "docker logs"),
            CommandResult(
                "docker logs",
                0,
                "Error: connect ECONNREFUSED 127.0.0.1:5432\n",
                "",
            ),
        )
        diagnosis, evidence, next_actions = reason_docker_execution(
            plan, [self.state_result(), logs]
        )
        self.assertTrue(any("refused dependency connection" in line for line in diagnosis))
        self.assertTrue(any("127.0.0.1:5432" in line for line in evidence))
        self.assertTrue(any("localhost refers to that same container" in line for line in next_actions))

    def test_diagnose_explains_port_bind_failure(self):
        plan = self.plan(DevOpsAction.DOCKER_DIAGNOSE)
        logs = DevOpsStepResult(
            DevOpsStep("Read recent logs for backend", "docker logs"),
            CommandResult("docker logs", 0, "Error: listen EADDRINUSE: address already in use 0.0.0.0:5000\n", ""),
        )
        diagnosis, evidence, next_actions = reason_docker_execution(
            plan, [self.state_result("exited|false|1|0|none|\n"), logs]
        )
        self.assertTrue(any("already in use" in line for line in diagnosis))
        self.assertTrue(any("EADDRINUSE" in line for line in evidence))
        self.assertTrue(any("existing listener" in line for line in next_actions))

    def test_diagnose_unknown_failure_does_not_invent_root_cause(self):
        plan = self.plan(DevOpsAction.DOCKER_DIAGNOSE)
        logs = DevOpsStepResult(
            DevOpsStep("Read recent logs for backend", "docker logs"),
            CommandResult("docker logs", 0, "application said something unusual\n", ""),
        )
        diagnosis, _, next_actions = reason_docker_execution(
            plan, [self.state_result(), logs]
        )
        self.assertTrue(any("No common high-signal failure pattern" in line for line in diagnosis))
        self.assertTrue(any("full application logs" in line for line in next_actions))


if __name__ == "__main__":
    unittest.main()
