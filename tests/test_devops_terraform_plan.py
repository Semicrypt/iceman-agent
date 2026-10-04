import tempfile
import unittest
from pathlib import Path

from iceman.devops_actions import (
    DevOpsAction,
    DevOpsRequest,
    build_devops_plan,
    summarize_terraform_plan,
)


class TerraformActionPlanTests(unittest.TestCase):
    def _terraform(self, root: Path, initialized: bool):
        tf = root / "terraform"
        tf.mkdir()
        (tf / "main.tf").write_text(
            'terraform { required_version = ">= 1.6.0" }\n',
            encoding="utf-8",
        )
        if initialized:
            (tf / ".terraform").mkdir()

    def test_validate_initialized_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._terraform(root, initialized=True)
            plan = build_devops_plan(
                root,
                DevOpsRequest(action=DevOpsAction.TERRAFORM_VALIDATE),
            )
        self.assertTrue(plan.safe_to_execute)
        self.assertFalse(plan.requires_approval)
        commands = [step.command for step in plan.steps]
        self.assertTrue(any("fmt -check" in cmd for cmd in commands))
        self.assertTrue(any("validate -no-color" in cmd for cmd in commands))
        self.assertFalse(any(" init " in cmd for cmd in commands))

    def test_validate_uninitialized_includes_approved_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._terraform(root, initialized=False)
            plan = build_devops_plan(
                root,
                DevOpsRequest(action=DevOpsAction.TERRAFORM_VALIDATE),
            )
        self.assertTrue(plan.requires_approval)
        self.assertIn(" init ", plan.steps[0].command)

    def test_plan_summary(self):
        summary = summarize_terraform_plan(
            "Plan: 7 to add, 1 to change, 0 to destroy."
        )
        self.assertEqual(
            summary,
            ["Terraform plan summary: 7 to add, 1 to change, 0 to destroy."],
        )


if __name__ == "__main__":
    unittest.main()
