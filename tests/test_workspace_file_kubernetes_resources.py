import tempfile
import unittest
from pathlib import Path

from iceman.workspace_files import (
    WorkspaceFileRequest,
    WorkspaceTemplate,
    apply_workspace_file_plan,
    build_workspace_file_plan,
    rollback_workspace_file_result,
    unified_diff_for_operation,
)


DEPLOYMENT = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
spec:
  template:
    spec:
      containers:
        - name: backend
          image: example/backend:1.0
          ports:
            - containerPort: 5000
"""


class KubernetesResourceAdvisorFileTests(unittest.TestCase):
    def _workspace(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        k8s = root / "k8s"
        k8s.mkdir()
        path = k8s / "backend-deployment.yaml"
        path.write_text(DEPLOYMENT, encoding="utf-8")
        return root, path

    def test_starter_resources_are_previewed_as_bounded_diff(self):
        root, _ = self._workspace()
        plan = build_workspace_file_plan(
            root,
            WorkspaceFileRequest(
                template=WorkspaceTemplate.KUBERNETES_RESOURCES,
                component="backend",
            ),
        )
        self.assertTrue(plan.safe_to_apply)
        self.assertEqual(len(plan.operations), 1)
        content = plan.operations[0].content
        self.assertIn("resources:", content)
        self.assertIn("cpu: 100m", content)
        self.assertIn("memory: 128Mi", content)
        self.assertIn("cpu: 500m", content)
        self.assertIn("memory: 512Mi", content)
        diff = unified_diff_for_operation(plan.operations[0])
        self.assertIn("+          resources:", diff)

    def test_apply_and_rollback_restore_original_manifest(self):
        root, path = self._workspace()
        plan = build_workspace_file_plan(
            root,
            WorkspaceFileRequest(
                template=WorkspaceTemplate.KUBERNETES_RESOURCES,
                component="backend",
            ),
        )
        result = apply_workspace_file_plan(plan)
        self.assertTrue(result.success)
        self.assertIn("resources:", path.read_text(encoding="utf-8"))
        rollback = rollback_workspace_file_result(result)
        self.assertTrue(rollback.success)
        self.assertEqual(path.read_text(encoding="utf-8"), DEPLOYMENT)

    def test_multi_container_manifest_is_not_modified_automatically(self):
        root, path = self._workspace()
        path.write_text(
            DEPLOYMENT.replace(
                "          ports:\n",
                "        - name: sidecar\n          image: example/sidecar:1.0\n          ports:\n",
            ),
            encoding="utf-8",
        )
        plan = build_workspace_file_plan(
            root,
            WorkspaceFileRequest(
                template=WorkspaceTemplate.KUBERNETES_RESOURCES,
                component="backend",
            ),
        )
        self.assertFalse(plan.safe_to_apply)
        self.assertIn("Multi-container", " ".join(plan.notes))


if __name__ == "__main__":
    unittest.main()
