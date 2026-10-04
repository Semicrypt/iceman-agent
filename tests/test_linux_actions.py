import tempfile
import unittest
from pathlib import Path

from iceman.linux_actions import LinuxAction, LinuxRequest, build_linux_plan, execute_linux_plan


class LinuxActionPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_create_file_is_workspace_bounded(self):
        plan = build_linux_plan(self.root, LinuxRequest(LinuxAction.CREATE_FILE, path="note.txt"))
        self.assertTrue(plan.safe_to_execute)
        self.assertIn("touch", plan.steps[0].command)
        result = execute_linux_plan(plan)
        self.assertTrue(result.success)
        self.assertTrue((self.root / "note.txt").exists())

    def test_mutation_cannot_escape_workspace(self):
        plan = build_linux_plan(self.root, LinuxRequest(LinuxAction.CREATE_FILE, path="../escape.txt"))
        self.assertFalse(plan.safe_to_execute)
        self.assertIn("restricted", plan.notes[0])

    def test_change_dir_can_move_to_parent_and_persists_as_plan_state(self):
        child = self.root / "child"
        child.mkdir()
        plan = build_linux_plan(child, LinuxRequest(LinuxAction.CHANGE_DIR, path=".."))
        self.assertTrue(plan.safe_to_execute)
        self.assertEqual(plan.new_workspace, self.root.resolve())
        result = execute_linux_plan(plan)
        self.assertTrue(result.success)

    def test_make_executable_uses_minimal_permission(self):
        script = self.root / "deploy.sh"
        script.write_text("#!/bin/sh\necho ok\n")
        plan = build_linux_plan(self.root, LinuxRequest(LinuxAction.MAKE_EXECUTABLE, path="deploy.sh"))
        self.assertTrue(plan.safe_to_execute)
        self.assertIn("chmod +x", plan.steps[0].command)
        self.assertNotIn("777", plan.steps[0].command)

    def test_find_pattern_rejects_shell_payload(self):
        plan = build_linux_plan(self.root, LinuxRequest(LinuxAction.FIND_FILES, pattern="*.yaml;rm"))
        self.assertFalse(plan.safe_to_execute)

    def test_copy_paths_stay_in_workspace(self):
        (self.root / "a.txt").write_text("a")
        (self.root / "dest").mkdir()
        plan = build_linux_plan(self.root, LinuxRequest(LinuxAction.COPY, path="a.txt", destination="dest"))
        self.assertTrue(plan.safe_to_execute)
        result = execute_linux_plan(plan)
        self.assertTrue(result.success)
        self.assertTrue((self.root / "dest" / "a.txt").exists())


if __name__ == "__main__":
    unittest.main()
