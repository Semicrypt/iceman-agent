import tempfile
import unittest
from pathlib import Path

from iceman.advisor import AdviceLevel, advise_devops_plan, advise_linux_plan
from iceman.devops_actions import DevOpsAction, DevOpsRequest, build_devops_plan
from iceman.linux_actions import LinuxAction, LinuxRequest, build_linux_plan


class AdvisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_docker_advisor_flags_root_and_missing_healthcheck(self):
        backend = self.root / "backend"
        backend.mkdir()
        (backend / "Dockerfile").write_text("FROM node:20-alpine\nEXPOSE 5000\nCMD [\"node\", \"server.js\"]\n")
        (backend / "package.json").write_text('{"name":"demo"}')
        plan = build_devops_plan(self.root, DevOpsRequest(DevOpsAction.DOCKER_BUILD, component="backend"))
        report = advise_devops_plan(plan)
        text = " ".join(x.message for x in report.findings)
        self.assertIn("non-root USER", text)
        self.assertIn("HEALTHCHECK", text)

    def test_kubernetes_advisor_flags_resources_and_namespace(self):
        k8s = self.root / "k8s"
        k8s.mkdir()
        (k8s / "app.yaml").write_text("""apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    spec:
      containers:
      - name: api
        image: example/api:latest
""")
        plan = build_devops_plan(self.root, DevOpsRequest(DevOpsAction.K8S_VALIDATE))
        report = advise_devops_plan(plan)
        levels = {x.level for x in report.findings}
        text = " ".join(x.message for x in report.findings)
        self.assertIn(AdviceLevel.WARNING, levels)
        self.assertIn(":latest", text)
        self.assertIn("namespace", text)
        resource_finding = next(
            item for item in report.findings
            if "CPU/memory requests or limits" in item.message
        )
        self.assertEqual(resource_finding.fix_id, "k8s_starter_resources")
        self.assertIn("starter CPU/memory", resource_finding.fix_prompt or "")


    def test_kubernetes_advisor_calls_out_inferred_context(self):
        k8s = self.root / "k8s"
        k8s.mkdir()
        (k8s / "app.yaml").write_text("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\nspec:\n  template:\n    spec:\n      containers:\n      - name: api\n        image: api:1.0\n")
        from unittest.mock import patch
        with patch("iceman.devops_actions._current_k8s_context", return_value="minikube"), \
             patch("iceman.devops_actions._available_k8s_contexts", return_value=["minikube"]), \
             patch("iceman.devops_actions._context_namespace", return_value="default"):
            plan = build_devops_plan(self.root, DevOpsRequest(DevOpsAction.K8S_DEPLOY, component="api"))
        report = advise_devops_plan(plan)
        self.assertTrue(any("resolved the deployment target" in x.message for x in report.findings))

    def test_kubernetes_advisor_warns_for_remote_context(self):
        k8s = self.root / "k8s"
        k8s.mkdir()
        (k8s / "app.yaml").write_text("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\nspec:\n  template:\n    spec:\n      containers:\n      - name: api\n        image: api:1.0\n")
        from unittest.mock import patch
        with patch("iceman.devops_actions._current_k8s_context", return_value="minikube"), \
             patch("iceman.devops_actions._available_k8s_contexts", return_value=["minikube", "prod-cluster"]), \
             patch("iceman.devops_actions._context_namespace", return_value="prod"):
            plan = build_devops_plan(self.root, DevOpsRequest(DevOpsAction.K8S_DEPLOY, component="api", context="prod-cluster"))
        report = advise_devops_plan(plan)
        self.assertTrue(any(x.level == AdviceLevel.WARNING and "does not look like" in x.message for x in report.findings))

    def test_terraform_advisor_recommends_remote_state(self):
        tf = self.root / "terraform"
        tf.mkdir()
        (tf / "main.tf").write_text('terraform { required_version = ">= 1.6" }\n')
        plan = build_devops_plan(self.root, DevOpsRequest(DevOpsAction.TERRAFORM_VALIDATE))
        report = advise_devops_plan(plan)
        self.assertTrue(any("remote Terraform backend" in x.message for x in report.findings))

    def test_linux_chmod_advisor_reinforces_least_privilege(self):
        script = self.root / "deploy.sh"
        script.write_text("echo ok\n")
        plan = build_linux_plan(self.root, LinuxRequest(LinuxAction.MAKE_EXECUTABLE, path="deploy.sh"))
        report = advise_linux_plan(plan)
        self.assertTrue(any("chmod +x" in x.message for x in report.findings))


if __name__ == "__main__":
    unittest.main()
