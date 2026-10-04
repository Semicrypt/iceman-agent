import tempfile
import unittest
from pathlib import Path

from iceman.workspace_files import (
    WorkspaceFileRequest,
    WorkspaceTemplate,
    build_workspace_file_plan,
    _safe_target,
)


class WorkspaceFilePathTests(unittest.TestCase):
    def test_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _safe_target(Path(tmp), "../escape.txt")

    def test_absolute_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _safe_target(Path(tmp), "/tmp/escape.txt")

    def test_gitignore_stays_in_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_workspace_file_plan(
                root,
                WorkspaceFileRequest(
                    template=WorkspaceTemplate.GITIGNORE,
                ),
            )
            self.assertTrue(plan.safe_to_apply)
            self.assertEqual(
                [op.relative_path for op in plan.operations],
                [".gitignore"],
            )


if __name__ == "__main__":
    unittest.main()
