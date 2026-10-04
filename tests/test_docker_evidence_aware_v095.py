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
    build_devops_plan,
    reason_docker_execution,
    summarize_docker_execution,
)
from iceman.runner import CommandResult


class DockerEvidenceAwareV095Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def plan(self, component="broken-backend"):
        return DevOpsPlan(
            request=DevOpsRequest(DevOpsAction.DOCKER_DIAGNOSE, component=component),
            workspace=self.root,
            title="test",
            inferred_component=component,
        )

    def state(self):
        return DevOpsStepResult(
            DevOpsStep("Inspect runtime state for broken-backend", "docker inspect"),
            CommandResult("docker inspect", 0, "exited|false|1|0|none|\n", ""),
        )

    def startup(self, text="node|missing.js\n"):
        return DevOpsStepResult(
            DevOpsStep("Inspect startup hint for broken-backend", "docker inspect"),
            CommandResult("docker inspect", 0, text, ""),
        )

    def logs(self, text):
        return DevOpsStepResult(
            DevOpsStep("Read recent logs for broken-backend", "docker logs"),
            CommandResult("docker logs", 0, text, ""),
        )

    @patch("iceman.devops_actions._docker_container_exists", return_value=True)
    def test_diagnose_plan_inspects_only_bounded_startup_hint(self, _exists):
        plan = build_devops_plan(
            self.root,
            DevOpsRequest(DevOpsAction.DOCKER_DIAGNOSE, component="broken-backend"),
        )
        commands = "\n".join(step.command for step in plan.steps)
        self.assertIn("{{.Path}}", commands)
        self.assertIn("index .Args 0", commands)
        self.assertNotIn("{{json .Args}}", commands)
        self.assertEqual(len(plan.steps), 4)

    def test_missing_node_entry_file_is_not_misclassified_as_dependency(self):
        log = """node:internal/modules/cjs/loader:1210\n  throw err;\nError: Cannot find module '/app/missing.js'\n  code: 'MODULE_NOT_FOUND',\n  requireStack: []\n"""
        diagnosis, evidence, actions = reason_docker_execution(
            self.plan(), [self.state(), self.startup(), self.logs(log)]
        )
        joined = " ".join(diagnosis).lower()
        self.assertIn("startup command", joined)
        self.assertIn("missing.js", joined)
        self.assertNotIn("runtime package/module dependency", joined)
        self.assertTrue(any("node missing.js" in line for line in evidence))
        self.assertTrue(any("Log excerpt:" in line and "missing.js" in line for line in evidence))
        self.assertTrue(any("startup command" in line.lower() for line in actions))

    def test_bare_package_is_classified_as_dependency(self):
        log = "Error: Cannot find module 'express'\ncode: 'MODULE_NOT_FOUND'\n"
        diagnosis, evidence, actions = reason_docker_execution(
            self.plan(), [self.state(), self.startup("node|server.js\n"), self.logs(log)]
        )
        self.assertTrue(any("runtime package/module dependency" in line for line in diagnosis))
        self.assertTrue(any("express" in line for line in evidence))
        self.assertTrue(any("declared and installed" in line for line in actions))

    def test_relative_local_module_is_classified_as_local_file(self):
        log = "Error: Cannot find module './config/local.js'\ncode: 'MODULE_NOT_FOUND'\nRequire stack:\n- /app/server.js\n"
        diagnosis, evidence, actions = reason_docker_execution(
            self.plan(), [self.state(), self.startup("node|server.js\n"), self.logs(log)]
        )
        self.assertTrue(any("local module/file" in line for line in diagnosis))
        self.assertTrue(any("./config/local.js" in line for line in evidence))
        self.assertTrue(any("COPY/build-context" in line for line in actions))

    def test_summary_uses_precise_entry_file_language(self):
        log = "Error: Cannot find module '/app/missing.js'\ncode: 'MODULE_NOT_FOUND'\nrequireStack: []\n"
        summary = summarize_docker_execution(
            DevOpsAction.DOCKER_DIAGNOSE,
            [self.state(), self.startup(), self.logs(log)],
        )
        self.assertTrue(any("missing startup/entry file" in line for line in summary))
        self.assertFalse(any("verify dependency installation" in line.lower() for line in summary))

    def test_unknown_module_target_does_not_invent_package_fix(self):
        log = "Error: MODULE_NOT_FOUND\n"
        diagnosis, evidence, actions = reason_docker_execution(
            self.plan(), [self.state(), self.startup(), self.logs(log)]
        )
        self.assertTrue(any("did not expose a target" in line for line in diagnosis))
        self.assertTrue(any("MODULE_NOT_FOUND" in line for line in evidence))
        self.assertFalse(any("dependency is declared" in line for line in actions))


if __name__ == "__main__":
    unittest.main()
