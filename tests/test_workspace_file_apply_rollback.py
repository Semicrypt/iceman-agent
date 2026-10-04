import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.workspace_files import (
    WorkspaceFileRequest,
    WorkspaceTemplate,
    apply_workspace_file_plan,
    build_workspace_file_plan,
    rollback_workspace_file_result,
)


class WorkspaceFileApplyRollbackTests(unittest.TestCase):
    def test_create_and_rollback_new_file(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            root = Path(tmp)
            with patch("pathlib.Path.home", return_value=Path(home)):
                plan = build_workspace_file_plan(
                    root,
                    WorkspaceFileRequest(
                        template=WorkspaceTemplate.GITIGNORE,
                    ),
                )
                result = apply_workspace_file_plan(plan)
                self.assertTrue(result.success)
                self.assertTrue((root / ".gitignore").exists())

                rollback = rollback_workspace_file_result(result)
                self.assertTrue(rollback.success)
                self.assertFalse((root / ".gitignore").exists())

    def test_update_is_backed_up_and_restored(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            root = Path(tmp)
            original = "custom-entry\n"
            (root / ".gitignore").write_text(
                original,
                encoding="utf-8",
            )

            with patch("pathlib.Path.home", return_value=Path(home)):
                plan = build_workspace_file_plan(
                    root,
                    WorkspaceFileRequest(
                        template=WorkspaceTemplate.GITIGNORE,
                    ),
                )
                result = apply_workspace_file_plan(plan)
                self.assertTrue(result.success)
                self.assertNotEqual(
                    (root / ".gitignore").read_text(encoding="utf-8"),
                    original,
                )

                rollback = rollback_workspace_file_result(result)
                self.assertTrue(rollback.success)
                self.assertEqual(
                    (root / ".gitignore").read_text(encoding="utf-8"),
                    original,
                )


if __name__ == "__main__":
    unittest.main()
