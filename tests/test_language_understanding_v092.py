import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.conversation import interpret_prompt
from iceman.devops_actions import DevOpsAction
from iceman.language_understanding import resolve_entity
from iceman.linux_actions import LinuxAction
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


class RobustLanguageUnderstandingV092Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "backend").mkdir()
        (self.root / "k8s").mkdir()
        (self.root / "terraform").mkdir()
        (self.root / "k8s" / "backend-deployment.yaml").write_text(
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: backend\n",
            encoding="utf-8",
        )
        self.state = SessionState(self.root)

    def test_user_regression_typo_corrects_real_container_component(self):
        decision = reason_request("why cant i reach the backen on port 5000", self.state)
        self.assertEqual(decision.action, ReasoningAction.EXECUTE_DEVOPS_ACTION)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_CONNECTIVITY)
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.port, 5000)
        self.assertTrue(decision.requires_clarification)
        self.assertEqual(
            decision.interpreted_request,
            "why can't i reach the backend on port 5000",
        )

    def test_bad_grammar_connectivity_is_repaired(self):
        decision = reason_request("why backend no open", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_CONNECTIVITY)
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.interpreted_request, "why can't I reach backend")
        self.assertTrue(decision.requires_clarification)

    def test_bad_scale_grammar_is_repaired(self):
        decision = reason_request("mak backend 5 replica", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_SCALE)
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.replicas, 5)
        self.assertTrue(decision.requires_clarification)

    def test_kubernetes_component_typo_is_grounded_to_manifest(self):
        decision = reason_request("deploy backen to kubernete", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_DEPLOY)
        self.assertEqual(decision.component, "backend")
        self.assertIsNone(decision.context)
        self.assertTrue(decision.requires_clarification)
        self.assertEqual(decision.interpreted_request, "deploy backend to kubernetes")

    def test_linux_permission_bad_grammar_is_repaired(self):
        (self.root / "deploy.sh").write_text("", encoding="utf-8")
        decision = reason_request("giv deploy.sh execute permision", self.state)
        self.assertEqual(decision.action, ReasoningAction.EXECUTE_LINUX_ACTION)
        self.assertEqual(decision.linux_action, LinuxAction.MAKE_EXECUTABLE)
        self.assertEqual(decision.path, "deploy.sh")
        self.assertTrue(decision.requires_clarification)

    def test_go_back_bad_spelling_is_repaired(self):
        decision = reason_request("go bak to project folder", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.CHANGE_DIR)
        self.assertEqual(decision.path, "..")
        self.assertTrue(decision.requires_clarification)

    def test_terraform_plan_failure_bad_spelling_routes_plan(self):
        decision = reason_request("why terrafor plan failing", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.TERRAFORM_PLAN)
        self.assertTrue(decision.requires_clarification)

    def test_ambiguous_entity_returns_real_candidate_choices(self):
        (self.root / "backups").mkdir()
        decision = reason_request("why cant i reach back on port 5000", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_CONNECTIVITY)
        self.assertTrue(decision.requires_clarification)
        self.assertEqual(decision.clarification_field, "component")
        self.assertIn("backend", decision.clarification_candidates)
        self.assertIn("backups", decision.clarification_candidates)

    def test_entity_match_does_not_invent_when_similarity_is_low(self):
        resolution = resolve_entity("totallydifferent", ["backend", "frontend"], kind="container")
        self.assertIsNone(resolution.resolved)
        self.assertEqual(resolution.candidates, ())

    def test_creation_name_is_not_fuzzy_rewritten(self):
        decision = reason_request("create a folder called backen", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.MAKE_DIR)
        self.assertEqual(decision.path, "backen")
        self.assertFalse(decision.requires_clarification)

    def test_destructive_request_still_is_not_promoted(self):
        decision = reason_request("delete backen deployment", self.state)
        self.assertEqual(decision.action, ReasoningAction.FALLBACK)

    @patch("iceman.language_understanding._docker_container_names", return_value=["backend", "minerva-backend"])
    def test_runtime_only_docker_name_can_be_repaired(self, _names):
        isolated = Path(self.tmp.name) / "empty"
        isolated.mkdir()
        state = SessionState(isolated)
        decision = reason_request("check if minerva-backen is healthy", state)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_HEALTH)
        self.assertEqual(decision.component, "minerva-backend")
        self.assertTrue(decision.requires_clarification)


if __name__ == "__main__":
    unittest.main()
