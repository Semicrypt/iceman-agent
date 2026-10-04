import unittest
from pathlib import Path

from iceman.diagnostics import ApplicationSignal, DockerDiagnosis, Finding
from iceman.reasoner import explain_context
from iceman.session import SessionState


class ApplicationExplanationTests(unittest.TestCase):
    def test_application_signal_outranks_running_info(self):
        state = SessionState(Path("."))
        diagnosis = DockerDiagnosis(
            target="minerva-backend",
            found=True,
            status="running",
            running=True,
            exit_code=0,
            application_signals=[
                ApplicationSignal(
                    subject="Openai",
                    state="DOWN",
                    count=4,
                ),
                ApplicationSignal(
                    subject="Broken Website",
                    state="DOWN",
                    count=4,
                ),
            ],
            findings=[
                Finding(
                    "info",
                    "Container is running",
                    "Docker reports the container as running.",
                )
            ],
        )
        state.remember_diagnosis(diagnosis)

        lines = explain_context(state)
        joined = "\n".join(lines)

        self.assertIn("Application warnings:", joined)
        self.assertIn("Openai: DOWN observed 4 time(s)", joined)
        self.assertIn("Broken Website: DOWN observed 4 time(s)", joined)
        self.assertIn("container itself may be healthy", joined)
        self.assertNotIn("Primary finding: Container is running", joined)


if __name__ == "__main__":
    unittest.main()
