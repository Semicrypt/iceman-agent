import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.conversation import interpret_prompt
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


class DockerLanguageV091Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = SessionState(self.root)

    def assert_connectivity(self, text, component="backend", port=None):
        decision = reason_request(text, self.state)
        self.assertEqual(decision.action, ReasoningAction.EXECUTE_DEVOPS_ACTION)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_CONNECTIVITY)
        self.assertEqual(decision.component, component)
        self.assertEqual(decision.port, port)

    def test_exact_regression_phrase_keeps_component_and_port(self):
        self.assert_connectivity("why can't I reach backend on port 5000", port=5000)

    def test_cannot_contractionless_variant(self):
        self.assert_connectivity("why cannot I access backend on port 5000", port=5000)

    def test_plain_user_statement_routes_connectivity(self):
        self.assert_connectivity("I can't connect to backend")

    def test_isnt_reachable_routes_connectivity(self):
        self.assert_connectivity("backend isn't reachable")

    def test_isnt_opening_routes_connectivity(self):
        self.assert_connectivity("backend isn't opening")

    def test_not_responding_routes_connectivity(self):
        self.assert_connectivity("why is backend not responding")

    def test_connectivity_failing_routes_connectivity(self):
        self.assert_connectivity("check why backend connectivity is failing")

    def test_port_only_phrase_stays_port_action(self):
        decision = reason_request("why can't I reach port 5000", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_PORTS)
        self.assertEqual(decision.port, 5000)
        self.assertIsNone(decision.component)

    def test_port_isnt_working_routes_port_action(self):
        decision = reason_request("port 5000 isn't working", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_PORTS)
        self.assertEqual(decision.port, 5000)

    def test_error_log_wording_variant(self):
        decision = reason_request("check relevant errors in backend logs", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_DIAGNOSE)
        self.assertEqual(decision.component, "backend")

    @patch("iceman.devops_actions._socket_port_free", return_value=False)
    @patch("iceman.devops_actions._docker_port_owners", return_value={5000: "minerva-backend"})
    @patch("iceman.devops_actions._docker_container_brief", return_value={"running": True, "status": "running", "image": "x", "host_ports": {5001}})
    @patch("iceman.devops_actions._docker_container_exists", return_value=True)
    def test_connectivity_plan_is_component_aware_and_bounded(self, _exists, _brief, _owners, _free):
        plan = build_devops_plan(
            self.root,
            DevOpsRequest(DevOpsAction.DOCKER_CONNECTIVITY, component="backend", port=5000),
        )
        self.assertTrue(plan.safe_to_execute)
        self.assertFalse(plan.requires_approval)
        commands = "\n".join(step.command for step in plan.steps)
        self.assertIn("docker inspect --format", commands)
        self.assertIn("docker port backend", commands)
        self.assertIn("http://127.0.0.1:5001/", commands)
        self.assertNotIn("http://backend", commands)
        notes = " ".join(plan.notes)
        self.assertIn("minerva-backend", notes)
        self.assertIn("5001", notes)

    @patch("iceman.devops_actions._docker_port_owners", return_value={})
    @patch("iceman.devops_actions._docker_container_brief", return_value={"running": True, "status": "running", "image": "x", "host_ports": {5001, 5002}})
    @patch("iceman.devops_actions._docker_container_exists", return_value=True)
    def test_multiple_published_ports_do_not_guess_http_probe(self, _exists, _brief, _owners):
        plan = build_devops_plan(
            self.root,
            DevOpsRequest(DevOpsAction.DOCKER_CONNECTIVITY, component="backend"),
        )
        commands = "\n".join(step.command for step in plan.steps)
        self.assertNotIn("curl ", commands)

    def test_connectivity_summary_explains_host_mapping_and_endpoint(self):
        state_step = DevOpsStep("Inspect runtime state for backend", "docker inspect")
        port_step = DevOpsStep("Show published ports for backend", "docker port backend")
        probe_step = DevOpsStep("Probe localhost:5001 over HTTP", "curl")
        results = [
            DevOpsStepResult(state_step, CommandResult("docker inspect", 0, "running|true|0|0|none|\n", "")),
            DevOpsStepResult(port_step, CommandResult("docker port backend", 0, "5000/tcp -> 0.0.0.0:5001\n5000/tcp -> [::]:5001\n", "")),
            DevOpsStepResult(probe_step, CommandResult("curl", 0, "curl_rc=0|HTTP 200\n", "")),
        ]
        summary = summarize_docker_execution(DevOpsAction.DOCKER_CONNECTIVITY, results)
        text = " ".join(summary)
        self.assertIn("container 5000 -> host 5001", text)
        self.assertIn("http://localhost:5001/", text)
        self.assertIn("HTTP 200", text)

    def test_repeat_context_reconstructs_connectivity_request(self):
        # A remembered trusted plan can safely power a conversational "again".
        with patch("iceman.devops_actions._docker_container_exists", return_value=True), \
             patch("iceman.devops_actions._docker_container_brief", return_value={"running": True, "status": "running", "image": "x", "host_ports": set()}), \
             patch("iceman.devops_actions._docker_port_owners", return_value={}):
            plan = build_devops_plan(
                self.root,
                DevOpsRequest(DevOpsAction.DOCKER_CONNECTIVITY, component="backend", port=5000),
            )
        self.state.remember_devops_plan(plan)
        interpreted = interpret_prompt("do that again", self.state)
        self.assertEqual(interpreted.normalized, "why can't I reach backend on port 5000")
        self.assertTrue(interpreted.requires_clarification)


if __name__ == "__main__":
    unittest.main()
