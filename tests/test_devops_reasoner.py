import unittest
from pathlib import Path

from iceman.devops_actions import DevOpsAction
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


class DevOpsReasonerTests(unittest.TestCase):
    def setUp(self):
        self.state = SessionState(Path("."))

    def test_docker_build_natural_language(self):
        decision = reason_request("build the backend", self.state)
        self.assertEqual(decision.action, ReasoningAction.EXECUTE_DEVOPS_ACTION)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_BUILD)
        self.assertEqual(decision.component, "backend")

    def test_docker_run_natural_language(self):
        self.state.last_devops_component = "backend"
        self.state.last_devops_image = "iceman-test-backend:latest"
        decision = reason_request("run it on port 5050", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_RUN)
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.image, "iceman-test-backend:latest")
        self.assertEqual(decision.port, 5050)

    def test_kubernetes_deploy_natural_language(self):
        decision = reason_request("deploy backend to minikube", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_DEPLOY)
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.context, "minikube")


    def test_kubernetes_platform_word_is_not_treated_as_context(self):
        decision = reason_request("deploy backend to kubernetes", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_DEPLOY)
        self.assertEqual(decision.component, "backend")
        self.assertIsNone(decision.context)

    def test_k8s_platform_word_is_not_treated_as_context(self):
        decision = reason_request("deploy backend to k8s", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_DEPLOY)
        self.assertIsNone(decision.context)

    def test_kubernetes_scale_natural_language(self):
        decision = reason_request("scale backend to 5 replicas", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_SCALE)
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.replicas, 5)

    def test_terraform_validate_natural_language(self):
        decision = reason_request("validate my terraform", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.TERRAFORM_VALIDATE)

    def test_terraform_plan_natural_language(self):
        decision = reason_request("show me what terraform will create", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.TERRAFORM_PLAN)


if __name__ == "__main__":
    unittest.main()
