import unittest
from pathlib import Path

from iceman.diagnostics import DockerDiagnosis
from iceman.session import SessionState


class SessionTests(unittest.TestCase):
    def test_remembers_diagnosis(self):
        state = SessionState(Path("."))
        diagnosis = DockerDiagnosis(
            target="demo",
            found=True,
            status="running",
            exit_code=0,
        )
        state.remember_diagnosis(diagnosis)

        self.assertIs(state.last_diagnosis, diagnosis)
        self.assertEqual(state.last_target, "demo")
        self.assertTrue(state.history)

    def test_workspace_change_clears_workspace_bound_context(self):
        state = SessionState(Path("."))
        old_diagnosis = DockerDiagnosis(
            target="demo",
            found=True,
            status="running",
        )
        state.remember_diagnosis(old_diagnosis)
        state.change_workspace(Path("/tmp"))

        self.assertEqual(state.workspace, Path("/tmp"))
        self.assertIsNone(state.last_diagnosis)
        self.assertIsNone(state.last_target)


if __name__ == "__main__":
    unittest.main()
