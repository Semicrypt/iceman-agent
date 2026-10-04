import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.devops_actions import DevOpsAction, DevOpsRequest, build_devops_plan
from iceman.presentation import execute_devops_plan_with_confirmation


class DevOpsConfirmationTests(unittest.TestCase):
    def _tmp_k8s_workspace(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        k8s = root / "k8s"
        k8s.mkdir()
        (k8s / "backend-deployment.yaml").write_text(
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: backend\n"
            "spec:\n  template:\n    spec:\n      containers:\n"
            "      - name: backend\n        image: example/backend:1.0\n",
            encoding="utf-8",
        )
        return root

    def test_read_only_devops_action_requires_operator_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_devops_plan(
                Path(tmp),
                DevOpsRequest(action=DevOpsAction.K8S_PODS),
            )

            with patch("typer.confirm", return_value=False) as confirm, patch(
                "iceman.presentation.execute_devops_plan"
            ) as execute:
                result = execute_devops_plan_with_confirmation(plan)

        confirm.assert_called_once_with("Execute the exact command(s) shown above?")
        execute.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.error, "Cancelled by operator.")

    @patch("iceman.devops_actions._context_namespace", return_value="default")
    @patch("iceman.devops_actions._available_k8s_contexts", return_value=["minikube"])
    @patch("iceman.devops_actions._current_k8s_context", return_value="minikube")
    def test_inferred_kubernetes_target_requires_target_confirmation_before_command_confirmation(self, current, available, namespace):
        k8s = self._tmp_k8s_workspace()
        plan = build_devops_plan(
            k8s,
            DevOpsRequest(action=DevOpsAction.K8S_DEPLOY, component="backend"),
        )
        with patch("typer.confirm", side_effect=[True, False]) as confirm, patch(
            "iceman.presentation.execute_devops_plan"
        ) as execute:
            result = execute_devops_plan_with_confirmation(plan)
        self.assertEqual(confirm.call_count, 2)
        self.assertEqual(
            confirm.call_args_list[0].args[0],
            "Deploy to current Kubernetes context 'minikube'?",
        )
        self.assertEqual(
            confirm.call_args_list[1].args[0],
            "Execute the exact command(s) shown above?",
        )
        execute.assert_not_called()
        self.assertFalse(result.success)

    def test_state_changing_devops_action_uses_same_exact_command_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_devops_plan(
                Path(tmp),
                DevOpsRequest(
                    action=DevOpsAction.K8S_SCALE,
                    component="backend",
                    replicas=3,
                ),
            )

            with patch("typer.confirm", return_value=False) as confirm, patch(
                "iceman.presentation.execute_devops_plan"
            ) as execute:
                result = execute_devops_plan_with_confirmation(plan)

        confirm.assert_called_once_with("Execute the exact command(s) shown above?")
        execute.assert_not_called()
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
