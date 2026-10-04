import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.devops_actions import DevOpsAction, DevOpsRequest, build_devops_plan
from iceman.runner import CommandResult


class KubernetesContextIntelligenceTests(unittest.TestCase):
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
    @patch("iceman.devops_actions._available_k8s_contexts", return_value=["minikube", "arn:aws:eks:eu-north-1:123:cluster/demo"])
    @patch("iceman.devops_actions._current_k8s_context", return_value="minikube")
    @patch("iceman.devops_actions.run_command")
    def test_generic_kubernetes_uses_current_context_without_switching(self, run, current, available, namespace):
        run.return_value = CommandResult("docker image inspect", 0, "[]", "")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._manifests(root)
            plan = build_devops_plan(
                root,
                DevOpsRequest(DevOpsAction.K8S_DEPLOY, component="backend", context="kubernetes"),
            )
        commands = [step.command for step in plan.steps]
        self.assertTrue(plan.safe_to_execute)
        self.assertEqual(plan.target_context, "minikube")
        self.assertTrue(plan.context_inferred)
        self.assertFalse(any("use-context" in cmd for cmd in commands))
        self.assertTrue(any("kubectl --context minikube cluster-info" == cmd for cmd in commands))
        self.assertTrue(any("kubectl --context minikube apply -f k8s/" == cmd for cmd in commands))

    @patch("iceman.devops_actions._available_k8s_contexts", return_value=["minikube"])
    @patch("iceman.devops_actions._current_k8s_context", return_value="minikube")
    def test_nonexistent_context_is_rejected_before_execution_plan(self, current, available):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._manifests(root)
            plan = build_devops_plan(
                root,
                DevOpsRequest(DevOpsAction.K8S_DEPLOY, component="backend", context="production"),
            )
        self.assertFalse(plan.safe_to_execute)
        self.assertEqual(plan.steps, [])
        self.assertTrue(any("was not found" in note for note in plan.notes))
        self.assertTrue(any("minikube" in note for note in plan.notes))

    @patch("iceman.devops_actions._context_namespace", return_value="prod")
    @patch("iceman.devops_actions._available_k8s_contexts", return_value=["minikube", "prod-cluster"])
    @patch("iceman.devops_actions._current_k8s_context", return_value="minikube")
    def test_explicit_other_context_does_not_change_active_kubeconfig(self, current, available, namespace):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._manifests(root)
            plan = build_devops_plan(
                root,
                DevOpsRequest(DevOpsAction.K8S_DEPLOY, component="backend", context="prod-cluster"),
            )
        commands = [step.command for step in plan.steps]
        self.assertTrue(plan.safe_to_execute)
        self.assertEqual(plan.target_context, "prod-cluster")
        self.assertEqual(plan.target_namespace, "prod")
        self.assertFalse(any("use-context" in cmd for cmd in commands))
        self.assertTrue(any(cmd.startswith("kubectl --context prod-cluster ") for cmd in commands))
        self.assertTrue(any("will NOT change your active" in note for note in plan.notes))

    @patch("iceman.devops_actions.run_command")
    def test_context_namespace_reads_redacted_kubeconfig_json(self, run):
        from iceman.devops_actions import _context_namespace
        run.return_value = CommandResult(
            "kubectl config view -o json",
            0,
            json.dumps({"contexts": [{"name": "demo", "context": {"namespace": "team-a"}}]}),
            "",
        )
        self.assertEqual(_context_namespace(Path("."), "demo"), "team-a")


if __name__ == "__main__":
    unittest.main()
