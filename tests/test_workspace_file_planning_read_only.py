import tempfile
import unittest
from pathlib import Path

from iceman.workspace_files import (
    WorkspaceFileRequest,
    WorkspaceTemplate,
    build_workspace_file_plan,
)


class WorkspaceFilePlanningReadOnlyTests(unittest.TestCase):
    def test_kubernetes_planning_does_not_create_k8s_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_workspace_file_plan(
                root,
                WorkspaceFileRequest(
                    template=WorkspaceTemplate.KUBERNETES_APP,
                    component="backend",
                    port=5000,
                ),
            )
            self.assertTrue(plan.safe_to_apply)
            self.assertFalse((root / "k8s").exists())

    def test_terraform_planning_does_not_create_terraform_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_workspace_file_plan(
                root,
                WorkspaceFileRequest(
                    template=WorkspaceTemplate.TERRAFORM_AWS_STARTER,
                ),
            )
            self.assertTrue(plan.safe_to_apply)
            self.assertFalse((root / "terraform").exists())


if __name__ == "__main__":
    unittest.main()
