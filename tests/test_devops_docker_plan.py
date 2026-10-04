import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.devops_actions import (
    DevOpsAction,
    DevOpsRequest,
    build_devops_plan,
)


class DockerActionPlanTests(unittest.TestCase):
    def _project(self, root: Path):
        backend = root / "backend"
        backend.mkdir()
        (backend / "Dockerfile").write_text(
            "FROM node:20-alpine\nEXPOSE 5000\nCMD [\"npm\", \"start\"]\n",
            encoding="utf-8",
        )
        (backend / "package.json").write_text(
            json.dumps({"name": "iceman-test-backend"}),
            encoding="utf-8",
        )

    def test_build_infers_package_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            plan = build_devops_plan(
                root,
                DevOpsRequest(
                    action=DevOpsAction.DOCKER_BUILD,
                    component="backend",
                ),
            )
        self.assertTrue(plan.safe_to_execute)
        self.assertTrue(plan.requires_approval)
        self.assertEqual(plan.inferred_image, "iceman-test-backend:latest")
        self.assertIn("docker build", plan.steps[0].command)
        self.assertIn("backend", plan.steps[0].command)

    @patch("iceman.devops_actions._socket_port_free")
    @patch("iceman.devops_actions._docker_port_owners")
    def test_run_detects_port_conflict_and_selects_next_free(self, owners, free):
        owners.return_value = {5000: "minerva-backend"}
        free.side_effect = lambda port: port == 5001
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            plan = build_devops_plan(
                root,
                DevOpsRequest(
                    action=DevOpsAction.DOCKER_RUN,
                    component="backend",
                    port=5000,
                ),
            )
        self.assertTrue(plan.safe_to_execute)
        self.assertIn("-p 5001:5000", plan.steps[0].command)
        self.assertIn("minerva-backend", " ".join(plan.notes))


if __name__ == "__main__":
    unittest.main()
