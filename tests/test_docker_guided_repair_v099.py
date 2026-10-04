import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.ai.local_provider import LocalReasoningProvider
from iceman.devops_actions import (
    DevOpsAction,
    DevOpsExecutionResult,
    DevOpsPlan,
    DevOpsRequest,
    DevOpsStep,
    DevOpsStepResult,
    build_guided_docker_repair_plan,
    reason_docker_execution,
    summarize_docker_execution,
)
from iceman.reasoner import reason_request
from iceman.runner import CommandResult
from iceman.session import SessionState


def result(command: str, stdout: str = "", stderr: str = "", returncode: int = 0):
    return CommandResult(
        command=command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def previous_missing_entry_result(root: Path) -> DevOpsExecutionResult:
    request = DevOpsRequest(
        action=DevOpsAction.DOCKER_DIAGNOSE,
        component="broken-backend",
    )
    plan = DevOpsPlan(
        request=request,
        workspace=root,
        title="Docker diagnosis",
    )
    plan.inferred_component = "broken-backend"

    startup_step = DevOpsStep(
        "Inspect safe startup command for broken-backend",
        "docker inspect startup",
        stream_output=False,
    )
    log_step = DevOpsStep(
        "Read recent logs for broken-backend",
        "docker logs",
        stream_output=False,
    )

    return DevOpsExecutionResult(
        plan=plan,
        success=True,
        step_results=[
            DevOpsStepResult(
                startup_step,
                result(
                    "docker inspect startup",
                    "docker-entrypoint.sh|node|missing.js\n",
                ),
            ),
            DevOpsStepResult(
                log_step,
                result(
                    "docker logs",
                    "Error: Cannot find module '/app/missing.js'\n"
                    "code: 'MODULE_NOT_FOUND'\n"
                    "requireStack: []\n",
                ),
            ),
        ],
        diagnosis=[
            "The startup command references a Node.js entry file/module that is "
            "not present in the container: /app/missing.js."
        ],
        evidence=[
            "Safe startup reconstruction: node missing.js."
        ],
    )


class DockerGuidedRepairV099Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_local_provider_preserves_explicit_startup_target(self):
        state = SessionState(self.root)

        result = LocalReasoningProvider().reason(
            "fix broken-backend using server.js",
            state,
        )

        self.assertEqual(
            result.intent.devops_action,
            DevOpsAction.DOCKER_GUIDED_REPAIR,
        )
        self.assertEqual(result.intent.component, "broken-backend")
        self.assertEqual(result.intent.startup_target, "server.js")

    def test_reasoner_routes_fix_with_explicit_target(self):
        state = SessionState(self.root)

        decision = reason_request(
            "fix broken-backend using server.js",
            state,
        )

        self.assertEqual(
            decision.devops_action,
            DevOpsAction.DOCKER_GUIDED_REPAIR,
        )
        self.assertEqual(decision.component, "broken-backend")
        self.assertEqual(decision.startup_target, "server.js")

    def test_reasoner_routes_fix_without_guessing_target(self):
        state = SessionState(self.root)

        decision = reason_request("fix broken-backend", state)

        self.assertEqual(
            decision.devops_action,
            DevOpsAction.DOCKER_GUIDED_REPAIR,
        )
        self.assertIsNone(decision.startup_target)

    def test_missing_explicit_target_is_safe_refusal(self):
        previous = previous_missing_entry_result(self.root)

        plan = build_guided_docker_repair_plan(
            self.root,
            DevOpsRequest(
                action=DevOpsAction.DOCKER_GUIDED_REPAIR,
                component="broken-backend",
            ),
            previous,
        )

        self.assertFalse(plan.safe_to_execute)
        joined = " ".join(plan.notes).lower()
        self.assertIn("will not guess", joined)
        self.assertIn("server.js", joined)
        self.assertFalse(plan.steps)

    def test_repair_target_must_match_previous_diagnosis(self):
        previous = previous_missing_entry_result(self.root)

        plan = build_guided_docker_repair_plan(
            self.root,
            DevOpsRequest(
                action=DevOpsAction.DOCKER_GUIDED_REPAIR,
                component="other-backend",
                startup_target="server.js",
            ),
            previous,
        )

        self.assertFalse(plan.safe_to_execute)
        self.assertTrue(
            any("not 'other-backend'" in note for note in plan.notes)
        )

    def test_explicit_target_builds_bounded_repair_and_real_verification(self):
        previous = previous_missing_entry_result(self.root)

        with patch(
            "iceman.devops_actions._docker_container_exists",
            side_effect=lambda workspace, name: name == "broken-backend",
        ), patch(
            "iceman.devops_actions._docker_container_image",
            return_value="broken-image:latest",
        ), patch(
            "iceman.devops_actions._standalone_recreate_recipe",
            return_value=(["-p", "8080:3000"], ["8080:3000"]),
        ):
            plan = build_guided_docker_repair_plan(
                self.root,
                DevOpsRequest(
                    action=DevOpsAction.DOCKER_GUIDED_REPAIR,
                    component="broken-backend",
                    startup_target="server.js",
                ),
                previous,
            )

        self.assertTrue(plan.safe_to_execute)

        change_steps = [step for step in plan.steps if step.changes_state]
        commands = [step.command for step in plan.steps]

        self.assertTrue(
            any(
                step.label.startswith("Start corrected container")
                and "broken-image:latest" in step.command
                and "node server.js" in step.command
                for step in change_steps
            )
        )
        self.assertTrue(
            any(
                "Verify corrected container" in step.label
                and "sleep 2" in step.command
                and ".State.Running" in step.command
                for step in plan.steps
            )
        )
        self.assertFalse(any("--force" in command for command in commands))
        self.assertTrue(
            any("iceman-backup" in step.command for step in change_steps)
        )

    def test_repair_refuses_same_known_broken_target(self):
        previous = previous_missing_entry_result(self.root)

        plan = build_guided_docker_repair_plan(
            self.root,
            DevOpsRequest(
                action=DevOpsAction.DOCKER_GUIDED_REPAIR,
                component="broken-backend",
                startup_target="missing.js",
            ),
            previous,
        )

        self.assertFalse(plan.safe_to_execute)
        self.assertTrue(
            any("same target" in note.lower() for note in plan.notes)
        )

    def test_guided_repair_reporting_prefers_post_repair_state(self):
        request = DevOpsRequest(
            action=DevOpsAction.DOCKER_GUIDED_REPAIR,
            component="broken-backend",
            startup_target="server.js",
        )
        plan = DevOpsPlan(
            request=request,
            workspace=self.root,
            title="Guided Docker remediation",
        )
        plan.inferred_component = "broken-backend"

        pre = DevOpsStep(
            "Inspect current runtime state for broken-backend",
            "docker inspect pre",
        )
        post = DevOpsStep(
            "Inspect repaired runtime state for broken-backend",
            "docker inspect post",
        )
        logs = DevOpsStep(
            "Read bounded post-repair logs for broken-backend",
            "docker logs --tail 40 broken-backend",
        )

        step_results = [
            DevOpsStepResult(
                pre,
                result(
                    "docker inspect pre",
                    "exited|false|1|0|none|\n",
                ),
            ),
            DevOpsStepResult(
                post,
                result(
                    "docker inspect post",
                    "running|true|0|0|none|\n",
                ),
            ),
            DevOpsStepResult(
                logs,
                result(
                    "docker logs --tail 40 broken-backend",
                    "ICEMAN_FIXTURE_READY\n",
                ),
            ),
        ]

        summary = summarize_docker_execution(
            DevOpsAction.DOCKER_GUIDED_REPAIR,
            step_results,
        )
        diagnosis, evidence, _ = reason_docker_execution(
            plan,
            step_results,
        )

        summary_text = " ".join(summary)
        evidence_text = " ".join(evidence)

        self.assertIn("status=running", summary_text)
        self.assertIn("exit=0", summary_text)
        self.assertNotIn("Container is not running", summary_text)

        self.assertIn(
            "Pre-repair Docker state: status=exited, exit=1",
            evidence_text,
        )
        self.assertIn(
            "Post-repair Docker state: status=running, exit=0",
            evidence_text,
        )
        self.assertIn(
            "Replacement startup target explicitly supplied by operator: "
            "'server.js'",
            evidence_text,
        )
        self.assertIn(
            "Rollback backup preserved as "
            "'broken-backend-iceman-backup'",
            evidence_text,
        )
        self.assertEqual(diagnosis, [])


if __name__ == "__main__":
    unittest.main()
