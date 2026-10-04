import unittest
from pathlib import Path

from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState
from iceman.workspace_files import WorkspaceTemplate


class WorkspaceFileReasonerTests(unittest.TestCase):
    def test_dockerfile_request(self):
        decision = reason_request(
            "create a Dockerfile for backend port 5000",
            SessionState(Path(".")),
        )
        self.assertEqual(
            decision.action,
            ReasoningAction.PLAN_WORKSPACE_FILES,
        )
        self.assertEqual(
            decision.file_template,
            WorkspaceTemplate.DOCKERFILE,
        )
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.port, 5000)

    def test_kubernetes_request(self):
        decision = reason_request(
            "create Kubernetes deployment and service for backend port 5000 replicas 3 image repo/backend:1.2",
            SessionState(Path(".")),
        )
        self.assertEqual(
            decision.file_template,
            WorkspaceTemplate.KUBERNETES_APP,
        )
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.port, 5000)
        self.assertEqual(decision.replicas, 3)
        self.assertEqual(decision.image, "repo/backend:1.2")

    def test_kubernetes_resource_request(self):
        decision = reason_request(
            "add kubernetes resource requests and limits for backend",
            SessionState(Path(".")),
        )
        self.assertEqual(
            decision.file_template,
            WorkspaceTemplate.KUBERNETES_RESOURCES,
        )
        self.assertEqual(decision.component, "backend")

    def test_terraform_aws_request(self):
        decision = reason_request(
            "create Terraform starter for AWS",
            SessionState(Path(".")),
        )
        self.assertEqual(
            decision.file_template,
            WorkspaceTemplate.TERRAFORM_AWS_STARTER,
        )

    def test_rollback_request(self):
        decision = reason_request(
            "rollback file changes",
            SessionState(Path(".")),
        )
        self.assertEqual(
            decision.action,
            ReasoningAction.ROLLBACK_WORKSPACE_FILES,
        )


if __name__ == "__main__":
    unittest.main()
