import tempfile
import unittest
from pathlib import Path

from iceman.linux_actions import LinuxAction
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


class LinuxReasonerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = SessionState(Path(self.tmp.name))

    def test_where_am_i(self):
        decision = reason_request("where am I?", self.state)
        self.assertEqual(decision.action, ReasoningAction.EXECUTE_LINUX_ACTION)
        self.assertEqual(decision.linux_action, LinuxAction.PWD)

    def test_list_files(self):
        decision = reason_request("show me the files", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.LIST)

    def test_list_hidden_files(self):
        decision = reason_request("show hidden files", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.LIST_ALL)

    def test_go_back(self):
        decision = reason_request("go back one folder", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.CHANGE_DIR)
        self.assertEqual(decision.path, "..")

    def test_go_into_directory(self):
        decision = reason_request("go into backend", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.CHANGE_DIR)
        self.assertEqual(decision.path, "backend")

    def test_create_directory(self):
        decision = reason_request("create a folder called configs", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.MAKE_DIR)
        self.assertEqual(decision.path, "configs")

    def test_create_file(self):
        decision = reason_request("create an empty file called app.log", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.CREATE_FILE)
        self.assertEqual(decision.path, "app.log")

    def test_make_executable(self):
        decision = reason_request("make deploy.sh executable", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.MAKE_EXECUTABLE)
        self.assertEqual(decision.path, "deploy.sh")

    def test_permissions_typo_is_repaired(self):
        decision = reason_request("show permions for deploy.sh", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.SHOW_PERMISSIONS)
        self.assertTrue(decision.requires_clarification)
        self.assertIn("permissions", decision.interpreted_request)

    def test_port_owner(self):
        decision = reason_request("show what is using port 5000", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.PORT_OWNER)
        self.assertEqual(decision.port, 5000)

    def test_find_files(self):
        decision = reason_request("find files matching *.yaml", self.state)
        self.assertEqual(decision.linux_action, LinuxAction.FIND_FILES)
        self.assertEqual(decision.pattern, "*.yaml")

    def test_docker_images_now_has_trusted_action(self):
        from iceman.devops_actions import DevOpsAction
        decision = reason_request("show me the docker images", self.state)
        self.assertEqual(decision.devops_action, DevOpsAction.DOCKER_IMAGES)


if __name__ == "__main__":
    unittest.main()
