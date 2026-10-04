import unittest
from pydantic import ValidationError

from iceman.ai.contracts import AIIntent, TrustedAction
from iceman.workspace_files import WorkspaceTemplate


class WorkspaceFileAIContractTests(unittest.TestCase):
    def test_workspace_template_is_typed(self):
        intent = AIIntent(
            action=TrustedAction.PLAN_WORKSPACE_FILES,
            file_template=WorkspaceTemplate.KUBERNETES_APP,
            component="backend",
            port=5000,
            replicas=3,
            rationale="Generate trusted Kubernetes manifests.",
        )
        self.assertEqual(
            intent.file_template,
            WorkspaceTemplate.KUBERNETES_APP,
        )

    def test_arbitrary_file_content_is_rejected(self):
        with self.assertRaises(ValidationError):
            AIIntent(
                action=TrustedAction.PLAN_WORKSPACE_FILES,
                file_template=WorkspaceTemplate.GITIGNORE,
                rationale="Bad payload.",
                file_content="rm -rf /",
            )

    def test_arbitrary_shell_command_still_rejected(self):
        with self.assertRaises(ValidationError):
            AIIntent(
                action=TrustedAction.PLAN_WORKSPACE_FILES,
                file_template=WorkspaceTemplate.DOCKERFILE,
                rationale="Bad payload.",
                command="cat > Dockerfile",
            )


if __name__ == "__main__":
    unittest.main()
