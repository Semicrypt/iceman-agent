import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.devops_actions import (
    DevOpsAction,
    DevOpsPlan,
    DevOpsRequest,
    DevOpsStep,
    DevOpsStepResult,
    build_devops_plan,
    execute_devops_plan,
    reason_k8s_pod_execution,
)
from iceman.reasoner import reason_request
from iceman.runner import CommandResult
from iceman.session import SessionState


def result(command: str, stdout: str = "", returncode: int = 0):
    return CommandResult(
        command=command,
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


def pod_result(
    root: Path,
    status: str,
    *,
    events: str = "",
    conditions: str = "",
):
    request = DevOpsRequest(
        action=DevOpsAction.K8S_DIAGNOSE_POD,
        component="api-abc",
    )
    plan = DevOpsPlan(
        request=request,
        workspace=root,
        title="Kubernetes pod diagnosis",
    )
    plan.inferred_component = "api-abc"
    plan.target_context = "minikube"
    plan.target_namespace = "default"

    steps = [
        DevOpsStep("Inspect pod status for api-abc", "status"),
        DevOpsStep("Inspect pod conditions for api-abc", "conditions"),
        DevOpsStep("Read recent pod events for api-abc", "events"),
        DevOpsStep("Read bounded current logs for api-abc", "current"),
        DevOpsStep("Read bounded previous logs for api-abc", "previous"),
    ]

    step_results = [
        DevOpsStepResult(steps[0], result("status", status)),
        DevOpsStepResult(steps[1], result("conditions", conditions)),
        DevOpsStepResult(steps[2], result("events", events)),
        DevOpsStepResult(
            steps[3],
            result("current", "ICEMAN_LOGS_RC=0\n"),
        ),
        DevOpsStepResult(
            steps[4],
            result("previous", "ICEMAN_LOGS_RC=0\n"),
        ),
    ]

    return plan, step_results


class KubernetesPodDiagnosisV0100Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = SessionState(self.root)

    def test_reasoner_routes_explicit_pod_diagnosis(self):
        decision = reason_request("diagnose pod api-abc", self.state)

        self.assertEqual(
            decision.devops_action,
            DevOpsAction.K8S_DIAGNOSE_POD,
        )
        self.assertEqual(decision.component, "api-abc")

    def test_ambiguous_crashing_name_keeps_existing_docker_route(self):
        decision = reason_request("why is api-abc crashing", self.state)

        self.assertEqual(
            decision.devops_action,
            DevOpsAction.DOCKER_DIAGNOSE,
        )

    @patch(
        "iceman.devops_actions._context_namespace",
        return_value="default",
    )
    @patch(
        "iceman.devops_actions._available_k8s_contexts",
        return_value=["minikube"],
    )
    @patch(
        "iceman.devops_actions._current_k8s_context",
        return_value="minikube",
    )
    def test_plan_is_read_only_context_scoped_and_bounded(
        self,
        current,
        available,
        namespace,
    ):
        plan = build_devops_plan(
            self.root,
            DevOpsRequest(
                action=DevOpsAction.K8S_DIAGNOSE_POD,
                component="api-abc",
            ),
        )

        self.assertTrue(plan.safe_to_execute)
        self.assertFalse(plan.requires_approval)
        self.assertEqual(plan.target_context, "minikube")
        self.assertEqual(plan.target_namespace, "default")
        self.assertTrue(all(not step.changes_state for step in plan.steps))

        commands = [step.command for step in plan.steps]
        self.assertTrue(
            all(
                "--context minikube --namespace default" in command
                for command in commands
            )
        )
        self.assertTrue(any("get pod api-abc -o name" in c for c in commands))
        self.assertTrue(any("get events" in c for c in commands))
        self.assertTrue(any("--tail=100" in c for c in commands))
        self.assertTrue(any("--previous" in c for c in commands))
        self.assertFalse(any("delete " in c for c in commands))
        self.assertFalse(any("rollout restart" in c for c in commands))
        self.assertFalse(any(" describe " in c for c in commands))

    def test_crashloopbackoff_is_classified(self):
        plan, steps = pod_result(
            self.root,
            (
                "POD|api-abc|Running|\n"
                "CONTAINER|api|false|7|CrashLoopBackOff||||1\n"
            ),
        )

        diagnosis, evidence, next_actions = reason_k8s_pod_execution(
            plan,
            steps,
        )

        self.assertTrue(any("CrashLoopBackOff" in x for x in diagnosis))
        self.assertTrue(any("restarts=7" in x for x in evidence))
        self.assertTrue(any("previous" in x.lower() for x in next_actions))

    def test_image_pull_failure_is_classified(self):
        plan, steps = pod_result(
            self.root,
            (
                "POD|api-abc|Pending|\n"
                "CONTAINER|api|false|0|ImagePullBackOff||||\n"
            ),
            events="Warning Failed Failed to pull image\n",
        )

        diagnosis, _, next_actions = reason_k8s_pod_execution(plan, steps)

        self.assertTrue(any("ImagePullBackOff" in x for x in diagnosis))
        self.assertTrue(any("image" in x.lower() for x in next_actions))

    def test_failed_scheduling_pending_is_classified(self):
        plan, steps = pod_result(
            self.root,
            "POD|api-abc|Pending|\n",
            events=(
                "Warning FailedScheduling "
                "0/1 nodes are available: 1 Insufficient memory\n"
            ),
        )

        diagnosis, evidence, next_actions = reason_k8s_pod_execution(
            plan,
            steps,
        )

        self.assertTrue(any("scheduler" in x.lower() for x in diagnosis))
        self.assertTrue(any("FailedScheduling" in x for x in evidence))
        self.assertTrue(any("resource requests" in x for x in next_actions))

    def test_oomkilled_is_classified_with_exit_code(self):
        plan, steps = pod_result(
            self.root,
            (
                "POD|api-abc|Running|\n"
                "CONTAINER|api|true|3||||OOMKilled|137\n"
            ),
        )

        diagnosis, evidence, next_actions = reason_k8s_pod_execution(
            plan,
            steps,
        )

        self.assertTrue(any("OOMKilled" in x for x in diagnosis))
        self.assertTrue(any("exit=137" in x for x in evidence))
        self.assertTrue(any("memory" in x.lower() for x in next_actions))

    def test_probe_failures_are_classified(self):
        plan, steps = pod_result(
            self.root,
            "POD|api-abc|Running|\nCONTAINER|api|true|1||||Error|1\n",
            events=(
                "Warning Unhealthy Readiness probe failed: connection refused\n"
                "Warning Unhealthy Liveness probe failed: HTTP probe failed\n"
            ),
        )

        diagnosis, evidence, next_actions = reason_k8s_pod_execution(
            plan,
            steps,
        )

        text = " ".join(diagnosis)
        self.assertIn("readiness-probe", text)
        self.assertIn("liveness-probe", text)
        self.assertTrue(any("readiness probe" in x.lower() for x in evidence))
        self.assertTrue(any("probe" in x.lower() for x in next_actions))

    def test_execute_devops_plan_attaches_kubernetes_diagnosis(self):
        request = DevOpsRequest(
            action=DevOpsAction.K8S_DIAGNOSE_POD,
            component="api-abc",
        )
        plan = DevOpsPlan(
            request=request,
            workspace=self.root,
            title="Kubernetes pod diagnosis",
            steps=[
                DevOpsStep("Inspect pod status for api-abc", "status"),
                DevOpsStep("Inspect pod conditions for api-abc", "conditions"),
                DevOpsStep("Read recent pod events for api-abc", "events"),
            ],
        )
        plan.inferred_component = "api-abc"
        plan.target_context = "minikube"
        plan.target_namespace = "default"

        outputs = {
            "status": (
                "POD|api-abc|Running|\n"
                "CONTAINER|api|false|4|CrashLoopBackOff||||1\n"
            ),
            "conditions": "",
            "events": "",
        }

        def runner(step, index, total):
            return result(step.command, outputs[step.command])

        execution = execute_devops_plan(plan, step_runner=runner)

        self.assertTrue(execution.success)
        self.assertTrue(
            any("CrashLoopBackOff" in x for x in execution.diagnosis)
        )
        self.assertTrue(
            any("phase=Running" in x for x in execution.summary)
        )


if __name__ == "__main__":
    unittest.main()
