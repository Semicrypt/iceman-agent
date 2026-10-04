import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.advisor import advise_devops_plan
from iceman.devops_actions import DevOpsAction, DevOpsRequest, build_devops_plan
from iceman.session import SessionState
from iceman.shell import _maybe_apply_guided_devops_advice


DEPLOYMENT = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
spec:
  replicas: 2
  selector:
    matchLabels:
      app: backend
  template:
    metadata:
      labels:
        app: backend
    spec:
      containers:
        - name: backend
          image: example/backend:1.0
          ports:
            - containerPort: 5000
          readinessProbe:
            tcpSocket:
              port: 5000
          livenessProbe:
            tcpSocket:
              port: 5000
"""


class AdvisorGuidedFixTests(unittest.TestCase):
    def test_kubernetes_deploy_guided_fix_writes_then_rechecks_advisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            k8s = root / "k8s"
            k8s.mkdir()
            path = k8s / "backend-deployment.yaml"
            path.write_text(DEPLOYMENT, encoding="utf-8")
            (k8s / "backend-service.yaml").write_text(
                "apiVersion: v1\nkind: Service\nmetadata:\n  name: backend\nspec:\n  selector:\n    app: backend\n",
                encoding="utf-8",
            )
            request = DevOpsRequest(
                action=DevOpsAction.K8S_DEPLOY,
                component="backend",
                context="minikube",
            )
            state = SessionState(root)
            with patch("iceman.devops_actions._current_k8s_context", return_value="minikube"), \
                 patch("iceman.devops_actions._available_k8s_contexts", return_value=["minikube"]), \
                 patch("iceman.devops_actions._context_namespace", return_value="default"):
                plan = build_devops_plan(root, request)
                report = advise_devops_plan(plan)
                self.assertTrue(any(x.fix_id == "k8s_starter_resources" for x in report.findings))
                with patch("typer.confirm", side_effect=[True, True]), \
                     patch("iceman.shell.show_workspace_file_plan"):
                    refreshed_plan, refreshed_report = _maybe_apply_guided_devops_advice(
                        plan, report, request, state
                    )

            self.assertTrue(state.last_workspace_file_apply is not None)
            self.assertTrue(state.last_workspace_file_apply.success)
            self.assertIn("resources:", path.read_text(encoding="utf-8"))
            self.assertFalse(any(x.fix_id == "k8s_starter_resources" for x in refreshed_report.findings))
            self.assertEqual(refreshed_plan.target_context, "minikube")


if __name__ == "__main__":
    unittest.main()
