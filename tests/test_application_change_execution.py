import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.application_remediation import (
    ApplicationChangePlan,
    execute_application_change,
)
from iceman.runner import CommandResult


def result(command, stdout="", stderr="", returncode=0):
    return CommandResult(
        command=command,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


class ApplicationChangeExecutionTests(unittest.TestCase):
    @patch("iceman.application_remediation.run_command")
    def test_successful_mutation_requires_exact_readback(self, run_command):
        run_command.side_effect = [
            result("mutate", stdout="DO\n"),
            result("verify", stdout="1\n"),
        ]
        plan = ApplicationChangePlan(
            subject="Openai",
            option_number=1,
            title="Replace target",
            cause="403",
            workspace=Path("."),
            safe_to_apply=True,
            mutation_command="mutate",
            verification_command="verify",
            rollback_command="rollback",
        )

        outcome = execute_application_change(plan)

        self.assertTrue(outcome.success)
        self.assertTrue(outcome.verification_passed)
        self.assertEqual(run_command.call_count, 2)

    @patch("iceman.application_remediation.run_command")
    def test_failed_readback_attempts_bounded_rollback(self, run_command):
        run_command.side_effect = [
            result("mutate", stdout="DO\n"),
            result("verify", stdout="0\n"),
            result("rollback", stdout="DO\n"),
        ]
        plan = ApplicationChangePlan(
            subject="Openai",
            option_number=1,
            title="Replace target",
            cause="403",
            workspace=Path("."),
            safe_to_apply=True,
            mutation_command="mutate",
            verification_command="verify",
            rollback_command="rollback",
        )

        outcome = execute_application_change(plan)

        self.assertFalse(outcome.success)
        self.assertTrue(outcome.mutation_applied)
        self.assertIn("rollback completed", outcome.message)
        self.assertEqual(run_command.call_count, 3)


if __name__ == "__main__":
    unittest.main()
