import json
import tempfile
import unittest
from pathlib import Path

from iceman.workspace_files import (
    WorkspaceFileRequest,
    WorkspaceTemplate,
    build_workspace_file_plan,
)


class WorkspaceDockerTests(unittest.TestCase):
    def test_node_component_dockerfile_is_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = root / "backend"
            backend.mkdir()
            (backend / "package.json").write_text(
                json.dumps({
                    "scripts": {"start": "node server.js"}
                }),
                encoding="utf-8",
            )
            (backend / "package-lock.json").write_text(
                "{}",
                encoding="utf-8",
            )
            (backend / "server.js").write_text(
                "app.listen(process.env.PORT || 5000);",
                encoding="utf-8",
            )

            plan = build_workspace_file_plan(
                root,
                WorkspaceFileRequest(
                    template=WorkspaceTemplate.DOCKERFILE,
                    component="backend",
                ),
            )

            self.assertTrue(plan.safe_to_apply)
            paths = [op.relative_path for op in plan.operations]
            self.assertEqual(
                paths,
                ["backend/Dockerfile", "backend/.dockerignore"],
            )
            dockerfile = plan.operations[0].content
            self.assertIn("FROM node:20-alpine", dockerfile)
            self.assertIn("EXPOSE 5000", dockerfile)
            self.assertIn('CMD ["npm", "start"]', dockerfile)

    def test_dockerfile_refuses_to_guess_missing_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"scripts": {"test": "echo test"}}),
                encoding="utf-8",
            )
            plan = build_workspace_file_plan(
                root,
                WorkspaceFileRequest(
                    template=WorkspaceTemplate.DOCKERFILE,
                ),
            )
            self.assertFalse(plan.safe_to_apply)
            self.assertIn("production entrypoint", " ".join(plan.notes))


if __name__ == "__main__":
    unittest.main()
