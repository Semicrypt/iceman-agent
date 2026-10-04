import tempfile
import unittest
from pathlib import Path

from iceman.devops_actions import DevOpsAction, DevOpsRequest, build_devops_plan
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


class ConversationalDevOpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = SessionState(self.root)

    def _remember_plan(self, action: DevOpsAction, **kwargs):
        plan = build_devops_plan(
            self.root,
            DevOpsRequest(action=action, **kwargs),
        )
        self.state.remember_devops_plan(plan)
        return plan

    def test_scale_grammar_without_to_or_replicas_is_accepted(self):
        decision = reason_request("scale backend 4", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_SCALE)
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.replicas, 4)

    def test_initialize_my_terraform_is_accepted(self):
        decision = reason_request("initialize my terraform", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.TERRAFORM_INIT)

    def test_terraform_typo_is_repaired_but_requires_clarification(self):
        decision = reason_request("validate my terrafor", self.state)
        self.assertEqual(decision.action, ReasoningAction.EXECUTE_DEVOPS_ACTION)
        self.assertEqual(decision.devops_action, DevOpsAction.TERRAFORM_VALIDATE)
        self.assertEqual(decision.interpreted_request, "validate my terraform")
        self.assertTrue(decision.requires_clarification)

    def test_deployment_typo_is_repaired(self):
        decision = reason_request("show me the deploymet", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_DEPLOYMENTS)
        self.assertTrue(decision.requires_clarification)
        self.assertEqual(decision.interpreted_request, "show me the deployment")

    def test_polite_grammar_filler_is_accepted(self):
        decision = reason_request("please show me pods", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_PODS)
        self.assertFalse(decision.requires_clarification)

    def test_common_show_typo_is_repaired(self):
        decision = reason_request("shwo pods", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_PODS)
        self.assertTrue(decision.requires_clarification)

    def test_again_typo_is_repaired(self):
        decision = reason_request("show pods agian", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_PODS)
        self.assertTrue(decision.requires_clarification)

    def test_show_pods_again_is_understood_without_context_ambiguity(self):
        decision = reason_request("show pods again", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_PODS)
        self.assertFalse(decision.requires_clarification)

    def test_show_them_again_reuses_previous_trusted_action(self):
        self._remember_plan(DevOpsAction.K8S_PODS)
        decision = reason_request("show them again", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_PODS)
        self.assertEqual(decision.interpreted_request, "show pods")
        self.assertTrue(decision.requires_clarification)

    def test_make_it_three_instead_reuses_scale_component(self):
        self._remember_plan(
            DevOpsAction.K8S_SCALE,
            component="backend",
            replicas=5,
        )
        decision = reason_request("make it 3 instead", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_SCALE)
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.replicas, 3)
        self.assertEqual(
            decision.interpreted_request,
            "scale backend to 3 replicas",
        )
        self.assertTrue(decision.requires_clarification)

    def test_what_about_services_is_understood(self):
        decision = reason_request("what about services?", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_SERVICES)

    def test_destructive_request_is_not_fuzzy_promoted(self):
        decision = reason_request("destroy terrafor", self.state)
        self.assertEqual(decision.action, ReasoningAction.FALLBACK)


if __name__ == "__main__":
    unittest.main()
