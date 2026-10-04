import tempfile
import unittest
from pathlib import Path

from iceman.devops_actions import (
    DevOpsAction,
    DevOpsPlan,
    DevOpsRequest,
    DevOpsStep,
    execute_devops_plan,
)
from iceman.linux_actions import (
    LinuxAction,
    LinuxPlan,
    LinuxRequest,
    LinuxStep,
    execute_linux_plan,
)
from iceman.runner import CommandResult, run_command_streaming


class StreamingRunnerTests(unittest.TestCase):
    def test_streams_stdout_and_stderr_and_captures_them(self):
        events = []
        command = (
            "python -c 'import sys; print(\"out\", flush=True); "
            "print(\"err\", file=sys.stderr, flush=True)'"
        )
        result = run_command_streaming(
            command,
            on_output=lambda stream, line: events.append((stream, line.strip())),
            heartbeat_interval=0.05,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("out", result.stdout)
        self.assertIn("err", result.stderr)
        self.assertIn(("stdout", "out"), events)
        self.assertIn(("stderr", "err"), events)
        self.assertGreaterEqual(result.duration_seconds, 0)
        self.assertFalse(result.interrupted)

    def test_emits_heartbeat_for_silent_process(self):
        heartbeats = []
        result = run_command_streaming(
            "python -c 'import time; time.sleep(0.16); print(\"done\")'",
            on_heartbeat=lambda elapsed, silent: heartbeats.append((elapsed, silent)),
            heartbeat_interval=0.04,
        )
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(len(heartbeats), 1)
        self.assertTrue(all(elapsed >= 0 for elapsed, _ in heartbeats))

    def test_streaming_runner_honors_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            result = run_command_streaming("pwd", cwd=target)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(Path(result.stdout.strip()), target)


class WorkflowProgressHookTests(unittest.TestCase):
    def test_devops_executor_uses_step_runner_with_position(self):
        workspace = Path("/tmp")
        request = DevOpsRequest(action=DevOpsAction.K8S_PODS)
        plan = DevOpsPlan(
            request=request,
            workspace=workspace,
            title="test",
            steps=[
                DevOpsStep("first", "echo first"),
                DevOpsStep("second", "echo second"),
            ],
        )
        calls = []

        def runner(step, index, total):
            calls.append((step.label, index, total))
            return CommandResult(step.command, 0, step.label, "", 0.1)

        result = execute_devops_plan(plan, step_runner=runner)
        self.assertTrue(result.success)
        self.assertEqual(calls, [("first", 1, 2), ("second", 2, 2)])
        self.assertEqual(len(result.step_results), 2)
        self.assertGreaterEqual(result.duration_seconds, 0)

    def test_devops_executor_stops_after_interrupted_step(self):
        workspace = Path("/tmp")
        request = DevOpsRequest(action=DevOpsAction.K8S_PODS)
        plan = DevOpsPlan(
            request=request,
            workspace=workspace,
            title="test",
            steps=[
                DevOpsStep("first", "echo first"),
                DevOpsStep("second", "echo second", True),
                DevOpsStep("third", "echo third", True),
            ],
        )
        calls = []

        def runner(step, index, total):
            calls.append(step.label)
            if index == 2:
                return CommandResult(step.command, 130, "", "", 1.2, True)
            return CommandResult(step.command, 0, "ok", "", 0.1)

        result = execute_devops_plan(plan, step_runner=runner)
        self.assertFalse(result.success)
        self.assertTrue(result.interrupted)
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(len(result.step_results), 2)
        self.assertIn("interrupted", result.error.lower())

    def test_linux_executor_uses_step_runner(self):
        workspace = Path("/tmp")
        request = LinuxRequest(action=LinuxAction.LIST)
        plan = LinuxPlan(
            request=request,
            workspace=workspace,
            title="test",
            steps=[LinuxStep("list", "ls")],
        )
        calls = []

        def runner(step, index, total):
            calls.append((index, total, step.label))
            return CommandResult(step.command, 0, "ok", "", 0.01)

        result = execute_linux_plan(plan, step_runner=runner)
        self.assertTrue(result.success)
        self.assertEqual(calls, [(1, 1, "list")])

    def test_linux_executor_stops_on_interruption(self):
        workspace = Path("/tmp")
        request = LinuxRequest(action=LinuxAction.LIST)
        plan = LinuxPlan(
            request=request,
            workspace=workspace,
            title="test",
            steps=[LinuxStep("list", "ls"), LinuxStep("later", "pwd", True)],
        )
        calls = []

        def runner(step, index, total):
            calls.append(step.label)
            return CommandResult(step.command, 130, "", "", 0.2, True)

        result = execute_linux_plan(plan, step_runner=runner)
        self.assertFalse(result.success)
        self.assertTrue(result.interrupted)
        self.assertEqual(calls, ["list"])


if __name__ == "__main__":
    unittest.main()
