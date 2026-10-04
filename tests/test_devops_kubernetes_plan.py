import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.devops_actions import (
    DevOpsAction,
    DevOpsRequest,
    build_devops_plan,
)
from iceman.runner import CommandResult


class KubernetesActionPlanTests(unittest.TestCase):
    def _manifests(self, root: Path):
        k8s = root / "k8s"
        k8s.mkdir()
        (k8s / "backend-deployment.yaml").write_text(
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: backend\n"
            "spec:\n  template:\n    spec:\n      containers:\n"
            "        - name: backend\n          image: iceman-test-backend:0.1\n",
            encoding="utf-8",
        )
        (k8s / "backend-service.yaml").write_text(
            "apiVersion: v1\nkind: Service\nmetadata:\n  name: backend\n",
            encoding="utf-8",
        )

    @patch("iceman.devops_actions._context_namespace", return_value="default")
    @patch("iceman.devops_actions._available_k8s_contexts", return_value=["minikube"])
    @patch("iceman.devops_actions.run_command")
    @patch("iceman.devops_actions._current_k8s_context", return_value="minikube")
    def test_deploy_to_minikube_loads_local_image_and_applies(self, current, run, available, namespace):
        run.return_value = CommandResult(
            command="docker image inspect",
            returncode=0,
            stdout="[]",
            stderr="",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._manifests(root)
            plan = build_devops_plan(
                root,
                DevOpsRequest(
                    action=DevOpsAction.K8S_DEPLOY,
                    component="backend",
                    context="minikube",
                ),
            )
        commands = [step.command for step in plan.steps]
        self.assertTrue(plan.safe_to_execute)
        self.assertTrue(plan.requires_approval)
        self.assertTrue(any("minikube image load" in cmd for cmd in commands))
        self.assertTrue(any("apply -f" in cmd for cmd in commands))
        self.assertTrue(any("rollout status deployment/backend" in cmd for cmd in commands))

    def test_show_pods_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_devops_plan(
                Path(tmp),
                DevOpsRequest(action=DevOpsAction.K8S_PODS),
            )
        self.assertTrue(plan.safe_to_execute)
        self.assertFalse(plan.requires_approval)
        self.assertEqual(plan.steps[0].command, "kubectl get pods -o wide")


if __name__ == "__main__":
    unittest.main()
