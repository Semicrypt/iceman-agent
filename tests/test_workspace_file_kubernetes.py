import tempfile
import unittest
from pathlib import Path

from iceman.workspace_files import (
    WorkspaceFileRequest,
    WorkspaceTemplate,
    build_workspace_file_plan,
)


class WorkspaceKubernetesTests(unittest.TestCase):
    def test_deployment_and_service_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_workspace_file_plan(
                Path(tmp),
                WorkspaceFileRequest(
                    template=WorkspaceTemplate.KUBERNETES_APP,
                    component="backend",
                    port=5000,
                    replicas=3,
                    image="example/backend:1.0",
                ),
            )

        self.assertTrue(plan.safe_to_apply)
        self.assertEqual(len(plan.operations), 2)
        deployment = plan.operations[0].content
        service = plan.operations[1].content
        self.assertIn("kind: Deployment", deployment)
        self.assertIn("replicas: 3", deployment)
        self.assertIn("image: example/backend:1.0", deployment)
        self.assertIn("containerPort: 5000", deployment)
        self.assertIn("kind: Service", service)
        self.assertIn("targetPort: 5000", service)

    def test_missing_kubernetes_port_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_workspace_file_plan(
                Path(tmp),
                WorkspaceFileRequest(
                    template=WorkspaceTemplate.KUBERNETES_APP,
                    component="backend",
                ),
            )
        self.assertFalse(plan.safe_to_apply)
        self.assertIn("explicit port", " ".join(plan.notes))

    def test_image_with_shell_syntax_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_workspace_file_plan(
                Path(tmp),
                WorkspaceFileRequest(
                    template=WorkspaceTemplate.KUBERNETES_APP,
                    component="backend",
                    port=5000,
                    image="repo/app:1.0;rm",
                ),
            )
        self.assertFalse(plan.safe_to_apply)


if __name__ == "__main__":
    unittest.main()
