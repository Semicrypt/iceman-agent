import tempfile
import unittest
from pathlib import Path

from iceman.workspace_files import (
    WorkspaceFileRequest,
    WorkspaceTemplate,
    build_workspace_file_plan,
)


class WorkspaceTerraformTests(unittest.TestCase):
    def test_aws_scaffold_contains_no_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_workspace_file_plan(
                Path(tmp),
                WorkspaceFileRequest(
                    template=WorkspaceTemplate.TERRAFORM_AWS_STARTER,
                ),
            )
        self.assertTrue(plan.safe_to_apply)
        content = "\n".join(op.content for op in plan.operations)
        self.assertIn('provider "aws"', content)
        self.assertIn("var.aws_region", content)
        self.assertNotIn("aws_access_key_id", content.lower())
        self.assertNotIn("aws_secret_access_key", content.lower())

    def test_azure_scaffold_uses_azurerm(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_workspace_file_plan(
                Path(tmp),
                WorkspaceFileRequest(
                    template=WorkspaceTemplate.TERRAFORM_AZURE_STARTER,
                ),
            )
        self.assertTrue(plan.safe_to_apply)
        content = "\n".join(op.content for op in plan.operations)
        self.assertIn('provider "azurerm"', content)
        self.assertIn("features {}", content)


if __name__ == "__main__":
    unittest.main()
