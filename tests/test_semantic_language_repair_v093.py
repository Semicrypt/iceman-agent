import tempfile
import unittest
from pathlib import Path

from iceman.devops_actions import DevOpsAction
from iceman.linux_actions import LinuxAction
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


class SemanticLanguageRepairV093Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "backend").mkdir()
        (self.root / "k8s").mkdir()
        (self.root / "terraform").mkdir()
        (self.root / "deploy.sh").write_text("", encoding="utf-8")
        (self.root / "k8s" / "backend-deployment.yaml").write_text(
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: backend\n",
            encoding="utf-8",
        )
        self.state = SessionState(self.root)

    def test_multi_error_connectivity_is_repaired_before_routing(self):
        decision = reason_request(
            "why cant i reac the backen on pot 5000",
            self.state,
        )
        self.assertEqual(decision.action, ReasoningAction.EXECUTE_DEVOPS_ACTION)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_CONNECTIVITY)
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.port, 5000)
        self.assertTrue(decision.requires_clarification)
        self.assertEqual(
            decision.interpreted_request,
            "why can't i reach the backend on port 5000",
        )

    def test_colloquial_connectivity_with_bad_port_spelling(self):
        decision = reason_request("backend no dey open pot 5000", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_CONNECTIVITY)
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.port, 5000)
        self.assertEqual(
            decision.interpreted_request,
            "why can't I reach backend on port 5000",
        )

    def test_broken_kubernetes_show_pods_phrase(self):
        decision = reason_request("chec kubernete pod", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_PODS)
        self.assertEqual(decision.interpreted_request, "show pods")
        self.assertTrue(decision.requires_clarification)

    def test_broken_terraform_plan_phrase(self):
        decision = reason_request("terrafor plan no working", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.TERRAFORM_PLAN)
        self.assertEqual(decision.interpreted_request, "why terraform plan failing")
        self.assertTrue(decision.requires_clarification)

    def test_broken_linux_go_back_phrase(self):
        decision = reason_request("go bak one folde", self.state)
        self.assertEqual(decision.action, ReasoningAction.EXECUTE_LINUX_ACTION)
        self.assertEqual(decision.linux_action, LinuxAction.CHANGE_DIR)
        self.assertEqual(decision.path, "..")
        self.assertTrue(decision.requires_clarification)

    def test_broken_permission_phrase(self):
        decision = reason_request("giv deploy.sh execut permision", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.MAKE_EXECUTABLE)
        self.assertEqual(decision.path, "deploy.sh")
        self.assertEqual(decision.interpreted_request, "make deploy.sh executable")
        self.assertTrue(decision.requires_clarification)

    def test_multiple_scale_errors_repair_then_ground_entity(self):
        decision = reason_request("mak backen 5 replca", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.K8S_SCALE)
        self.assertEqual(decision.component, "backend")
        self.assertEqual(decision.replicas, 5)
        self.assertEqual(decision.interpreted_request, "scale backend to 5 replicas")
        self.assertTrue(decision.requires_clarification)

    def test_broken_container_listing_phrase(self):
        decision = reason_request("shw me the contaners", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_LIST)
        self.assertEqual(decision.interpreted_request, "show me the containers")
        self.assertTrue(decision.requires_clarification)

    def test_creation_destination_still_not_fuzzy_changed(self):
        decision = reason_request("create a folder called backen", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.MAKE_DIR)
        self.assertEqual(decision.path, "backen")
        self.assertFalse(decision.requires_clarification)

    def test_destructive_typo_is_not_promoted(self):
        decision = reason_request("delet backen deployment", self.state)
        self.assertEqual(decision.action, ReasoningAction.FALLBACK)


if __name__ == "__main__":
    unittest.main()
