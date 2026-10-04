import unittest
from pydantic import ValidationError

from iceman.ai.contracts import AIIntent, TrustedAction
from iceman.devops_actions import DevOpsAction


class DevOpsAIContractTests(unittest.TestCase):
    def test_typed_devops_action(self):
        intent = AIIntent(
            action=TrustedAction.EXECUTE_DEVOPS_ACTION,
            devops_action=DevOpsAction.K8S_SCALE,
            component="backend",
            replicas=5,
            rationale="Scale backend through trusted Kubernetes action.",
        )
        self.assertEqual(intent.devops_action, DevOpsAction.K8S_SCALE)

    def test_shell_command_field_still_rejected(self):
        with self.assertRaises(ValidationError):
            AIIntent(
                action=TrustedAction.EXECUTE_DEVOPS_ACTION,
                devops_action=DevOpsAction.TERRAFORM_VALIDATE,
                rationale="Bad payload",
                command="terraform validate",
            )


if __name__ == "__main__":
    unittest.main()
