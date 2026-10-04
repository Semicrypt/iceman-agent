import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.advisor import AdviceLevel, advise_devops_plan
from iceman.devops_actions import (
    DevOpsAction,
    DevOpsRequest,
    DevOpsStep,
    DevOpsStepResult,
    build_devops_plan,
    summarize_docker_execution,
)
from iceman.reasoner import ReasoningAction, reason_request
from iceman.runner import CommandResult
from iceman.session import SessionState
from iceman.workspace_files import (
    WorkspaceFileRequest,
    WorkspaceTemplate,
    build_workspace_file_plan,
)


class DockerIntelligenceV090Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = self.root / "backend"
        self.backend.mkdir()
        (self.backend / "Dockerfile").write_text(
            'FROM node:20-alpine\nWORKDIR /app\nCOPY . .\nEXPOSE 5000\nCMD ["node", "server.js"]\n',
            encoding="utf-8",
        )
        (self.backend / "package.json").write_text(
            json.dumps({"name": "iceman-test-backend", "scripts": {"start": "node server.js"}}),
            encoding="utf-8",
        )

    def state(self):
        return SessionState(self.root)

    def test_reasoner_why_did_backend_stop(self):
        decision = reason_request("why did backend stop", self.state())
        self.assertEqual(decision.action, ReasoningAction.EXECUTE_DEVOPS_ACTION)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_DIAGNOSE)
        self.assertEqual(decision.component, "backend")

    def test_reasoner_restarting(self):
        decision = reason_request("why is backend restarting", self.state())
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_DIAGNOSE)

    def test_reasoner_health(self):
        decision = reason_request("check if backend is healthy", self.state())
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_HEALTH)

    def test_reasoner_important_errors(self):
        decision = reason_request("show important errors in backend logs", self.state())
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_DIAGNOSE)

    def test_reasoner_port_troubleshooting(self):
        decision = reason_request("why can't I reach port 5000", self.state())
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_PORTS)
        self.assertEqual(decision.port, 5000)

    def test_reasoner_security(self):
        decision = reason_request("check docker security for backend", self.state())
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_SECURITY)
        self.assertEqual(decision.component, "backend")

    def test_reasoner_optimize(self):
        decision = reason_request("make backend image smaller", self.state())
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_OPTIMIZE)
        self.assertEqual(decision.component, "backend")

    def test_reasoner_rebuild_restart(self):
        decision = reason_request("rebuild and restart backend", self.state())
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_REBUILD_RESTART)

    def test_reasoner_compose_status(self):
        decision = reason_request("show docker compose status", self.state())
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_COMPOSE_STATUS)

    def test_reasoner_compose_generation(self):
        decision = reason_request("create docker compose for backend port 5000", self.state())
        self.assertEqual(decision.action, ReasoningAction.PLAN_WORKSPACE_FILES)
        self.assertEqual(decision.file_template, WorkspaceTemplate.DOCKER_COMPOSE)
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.port, 5000)

    def test_compose_generation_is_bounded(self):
        plan = build_workspace_file_plan(
            self.root,
            WorkspaceFileRequest(
                template=WorkspaceTemplate.DOCKER_COMPOSE,
                component="backend",
                port=5000,
            ),
        )
        self.assertTrue(plan.safe_to_apply)
        self.assertEqual(len(plan.operations), 1)
        content = plan.operations[0].content
        self.assertIn("services:", content)
        self.assertIn("context: ./backend", content)
        self.assertIn('"5000:5000"', content)
        self.assertNotIn("password", content.lower())

    def test_compose_generation_refuses_existing_compose(self):
        (self.root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        plan = build_workspace_file_plan(
            self.root,
            WorkspaceFileRequest(
                template=WorkspaceTemplate.DOCKER_COMPOSE,
                component="backend",
                port=5000,
            ),
        )
        self.assertFalse(plan.safe_to_apply)
        self.assertTrue(any("will not replace" in note for note in plan.notes))

    @patch("iceman.devops_actions._docker_container_exists", return_value=True)
    def test_diagnose_plan_has_state_ports_logs(self, _exists):
        plan = build_devops_plan(
            self.root,
            DevOpsRequest(DevOpsAction.DOCKER_DIAGNOSE, component="backend"),
        )
        self.assertTrue(plan.safe_to_execute)
        self.assertFalse(plan.requires_approval)
        self.assertEqual(len(plan.steps), 4)
        commands = "\n".join(step.command for step in plan.steps)
        self.assertIn("docker inspect --format", commands)
        self.assertIn("docker port", commands)
        self.assertIn("docker logs --tail 120", commands)

    @patch("iceman.devops_actions._socket_port_free", return_value=False)
    @patch("iceman.devops_actions._docker_port_owners", return_value={5000: "minerva-backend"})
    def test_port_plan_identifies_docker_owner(self, _owners, _free):
        plan = build_devops_plan(
            self.root,
            DevOpsRequest(DevOpsAction.DOCKER_PORTS, port=5000),
        )
        self.assertTrue(plan.safe_to_execute)
        self.assertIn("minerva-backend", " ".join(plan.notes))

    def test_advisor_flags_full_context_without_dockerignore(self):
        plan = build_devops_plan(
            self.root,
            DevOpsRequest(DevOpsAction.DOCKER_BUILD, component="backend"),
        )
        report = advise_devops_plan(plan)
        messages = " ".join(item.message for item in report.findings)
        self.assertIn(".dockerignore", messages)
        self.assertIn("non-root USER", messages)
        self.assertIn("HEALTHCHECK", messages)

    def test_docker_runtime_summary_detects_restarts_and_unhealthy(self):
        step = DevOpsStep("Inspect runtime state for backend", "docker inspect")
        result = CommandResult(
            command="docker inspect",
            returncode=0,
            stdout="running|true|0|4|unhealthy|\n",
            stderr="",
        )
        summary = summarize_docker_execution(
            DevOpsAction.DOCKER_HEALTH,
            [DevOpsStepResult(step=step, result=result)],
        )
        text = " ".join(summary)
        self.assertIn("unhealthy", text.lower())
        self.assertIn("restarted 4", text.lower())

    def test_docker_diagnosis_summary_detects_econnrefused(self):
        state_step = DevOpsStep("Inspect runtime state for backend", "docker inspect")
        log_step = DevOpsStep("Read recent logs for backend", "docker logs")
        items = [
            DevOpsStepResult(
                state_step,
                CommandResult("docker inspect", 0, "running|true|0|0|none|\n", ""),
            ),
            DevOpsStepResult(
                log_step,
                CommandResult("docker logs", 0, "Error: connect ECONNREFUSED 127.0.0.1:5432\n", ""),
            ),
        ]
        summary = summarize_docker_execution(DevOpsAction.DOCKER_DIAGNOSE, items)
        self.assertTrue(any("ECONNREFUSED" in line for line in summary))

    def test_docker_images_has_trusted_plan(self):
        plan = build_devops_plan(self.root, DevOpsRequest(DevOpsAction.DOCKER_IMAGES))
        self.assertTrue(plan.safe_to_execute)
        self.assertIn("docker images", plan.steps[0].command)

    @patch("iceman.devops_actions._docker_container_brief")
    @patch("iceman.devops_actions._socket_port_free", return_value=True)
    @patch("iceman.devops_actions._docker_port_owners", return_value={})
    def test_run_existing_running_container_verifies_without_duplicate(self, _owners, _free, brief):
        brief.return_value = {"running": True, "status": "running", "image": "iceman-test-backend:latest", "host_ports": {5001}}
        plan = build_devops_plan(
            self.root,
            DevOpsRequest(DevOpsAction.DOCKER_RUN, component="backend", port=5001),
        )
        self.assertTrue(plan.safe_to_execute)
        self.assertFalse(plan.requires_approval)
        commands = "\n".join(step.command for step in plan.steps)
        self.assertNotIn("docker run -d", commands)
        self.assertIn("docker inspect --format", commands)
        self.assertTrue(any("already exists and is running" in note for note in plan.notes))

    @patch("iceman.devops_actions._docker_container_brief")
    @patch("iceman.devops_actions._socket_port_free", return_value=True)
    @patch("iceman.devops_actions._docker_port_owners", return_value={})
    def test_run_existing_stopped_container_uses_docker_start(self, _owners, _free, brief):
        brief.return_value = {"running": False, "status": "exited", "image": "iceman-test-backend:latest", "host_ports": {5001}}
        plan = build_devops_plan(
            self.root,
            DevOpsRequest(DevOpsAction.DOCKER_RUN, component="backend", port=5001),
        )
        self.assertTrue(plan.safe_to_execute)
        self.assertTrue(plan.requires_approval)
        self.assertTrue(plan.steps[0].command.startswith("docker start"))



if __name__ == "__main__":
    unittest.main()
