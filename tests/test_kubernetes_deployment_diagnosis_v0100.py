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
    reason_k8s_deployment_execution,
    summarize_k8s_deployment_execution,
)
from iceman.reasoner import ReasoningAction, reason_request
from iceman.runner import CommandResult
from iceman.session import SessionState
from iceman.shell import _legacy_docker_diagnose_target


def command_result(stdout: str = "") -> CommandResult:
    return CommandResult(
        command="test",
        returncode=0,
        stdout=stdout,
        stderr="",
    )


def step_result(label: str, stdout: str) -> DevOpsStepResult:
    return DevOpsStepResult(
        DevOpsStep(label, "test", stream_output=False),
        command_result(stdout),
    )


def deployment_plan(root: Path) -> DevOpsPlan:
    plan = DevOpsPlan(
        request=DevOpsRequest(
            action=DevOpsAction.K8S_DIAGNOSE_DEPLOYMENT,
            component="backend",
        ),
        workspace=root,
        title="Kubernetes Deployment diagnosis",
    )
    plan.inferred_component = "backend"
    plan.target_context = "minikube"
    plan.target_namespace = "default"
    return plan


def diagnosis_results(
    *,
    status: str = "DEPLOYMENT|backend|3|3|2|2|2|2|2|0\n",
    conditions: str = (
        "CONDITION|Available|True|MinimumReplicasAvailable|ok\n"
        "CONDITION|Progressing|True|NewReplicaSetAvailable|ok\n"
    ),
    pods: str = (
        "POD|backend-a|Running|True||0\n"
        "POD|backend-b|Running|True||0\n"
    ),
    replicasets: str = (
        "RS|backend-rs|2|2|2|2|Deployment:backend\n"
    ),
    events: str = "",
) -> list[DevOpsStepResult]:
    return [
        step_result(
            "Inspect deployment status for backend",
            status,
        ),
        step_result(
            "Inspect deployment conditions for backend",
            conditions,
        ),
        step_result(
            "Inspect ReplicaSets for deployment backend",
            "SELECTOR|app=backend\n" + replicasets,
        ),
        step_result(
            "Inspect pods for deployment backend",
            "SELECTOR|app=backend\n" + pods,
        ),
        step_result(
            "Read recent deployment events for backend",
            events,
        ),
    ]


class KubernetesDeploymentDiagnosisV0100Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = SessionState(self.root)

    def test_reasoner_routes_explicit_deployment_diagnosis(self):
        decision = reason_request(
            "diagnose deployment backend",
            self.state,
        )

        self.assertEqual(
            decision.action,
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
        )
        self.assertEqual(
            decision.devops_action,
            DevOpsAction.K8S_DIAGNOSE_DEPLOYMENT,
        )
        self.assertEqual(decision.component, "backend")

    def test_shell_does_not_consume_deployment_as_legacy_docker(self):
        self.assertIsNone(
            _legacy_docker_diagnose_target(
                "diagnose deployment backend"
            )
        )
        self.assertIsNone(
            _legacy_docker_diagnose_target(
                "diagnose kubernetes deployment backend"
            )
        )

    def test_plan_is_context_scoped_and_read_only(self):
        with patch(
            "iceman.devops_actions._current_k8s_context",
            return_value="minikube",
        ), patch(
            "iceman.devops_actions._available_k8s_contexts",
            return_value=["minikube"],
        ), patch(
            "iceman.devops_actions._context_namespace",
            return_value="default",
        ):
            plan = build_devops_plan(
                self.root,
                DevOpsRequest(
                    DevOpsAction.K8S_DIAGNOSE_DEPLOYMENT,
                    component="backend",
                ),
            )

        self.assertTrue(plan.safe_to_execute)
        self.assertFalse(plan.requires_approval)
        self.assertEqual(plan.target_context, "minikube")
        self.assertEqual(plan.target_namespace, "default")
        self.assertEqual(len(plan.steps), 6)

        commands = [step.command for step in plan.steps]
        self.assertTrue(
            all(
                "--context minikube" in command
                and "--namespace default" in command
                for command in commands
            )
        )

        joined = " ".join(commands).lower()

        for forbidden in (
            "rollout restart",
            "kubectl scale",
            "kubectl patch",
            "kubectl delete",
            "kubectl apply",
        ):
            self.assertNotIn(forbidden, joined)

    def test_progress_deadline_exceeded_is_classified(self):
        plan = deployment_plan(self.root)
        results = diagnosis_results(
            status="DEPLOYMENT|backend|3|3|2|2|1|1|1|1\n",
            conditions=(
                "CONDITION|Available|False|MinimumReplicasUnavailable|no\n"
                "CONDITION|Progressing|False|ProgressDeadlineExceeded|timed out\n"
            ),
            pods=(
                "POD|backend-a|Running|True||0\n"
                "POD|backend-b|Pending|False|ImagePullBackOff|0\n"
            ),
        )

        diagnosis, evidence, actions = reason_k8s_deployment_execution(
            plan,
            results,
        )

        text = " ".join(diagnosis).lower()
        self.assertIn("exceeded its rollout progress deadline", text)
        self.assertIn("imagepullbackoff", text)
        self.assertTrue(
            any("ProgressDeadlineExceeded" in item for item in evidence)
        )
        self.assertTrue(actions)

    def test_replica_failure_is_classified(self):
        plan = deployment_plan(self.root)
        results = diagnosis_results(
            status="DEPLOYMENT|backend|2|2|2|0|0|0|0|2\n",
            conditions=(
                "CONDITION|Available|False|MinimumReplicasUnavailable|no\n"
                "CONDITION|ReplicaFailure|True|FailedCreate|quota denied\n"
            ),
            pods="",
            replicasets="RS|backend-rs|2|0|0|0|Deployment:backend\n",
            events="Warning FailedCreate quota denied\n",
        )

        diagnosis, evidence, _ = reason_k8s_deployment_execution(
            plan,
            results,
        )

        text = " ".join(diagnosis).lower()
        self.assertIn("replica creation failure", text)
        self.assertIn("no pods matching", text)
        self.assertTrue(
            any("FailedCreate" in item for item in evidence)
        )

    def test_incomplete_availability_is_classified(self):
        plan = deployment_plan(self.root)
        results = diagnosis_results(
            status="DEPLOYMENT|backend|3|3|3|3|3|2|2|1\n",
            pods=(
                "POD|backend-a|Running|True||0\n"
                "POD|backend-b|Running|True||0\n"
                "POD|backend-c|Running|False||0\n"
            ),
        )

        diagnosis, _, _ = reason_k8s_deployment_execution(
            plan,
            results,
        )

        self.assertTrue(
            any("not fully available" in item.lower() for item in diagnosis)
        )

    def test_latest_generation_not_observed_is_classified(self):
        plan = deployment_plan(self.root)
        results = diagnosis_results(
            status="DEPLOYMENT|backend|5|4|2|2|2|2|2|0\n",
        )

        diagnosis, _, _ = reason_k8s_deployment_execution(
            plan,
            results,
        )

        self.assertTrue(
            any(
                "has not yet observed the latest generation" in item.lower()
                for item in diagnosis
            )
        )

    def test_healthy_deployment_is_not_reported_broken(self):
        plan = deployment_plan(self.root)
        results = diagnosis_results()

        summary = summarize_k8s_deployment_execution(plan, results)
        diagnosis, evidence, actions = reason_k8s_deployment_execution(
            plan,
            results,
        )

        self.assertTrue(
            any("desired=2" in item and "available=2" in item for item in summary)
        )
        self.assertTrue(
            any("fully available" in item.lower() for item in diagnosis)
        )
        self.assertFalse(
            any("not fully available" in item.lower() for item in diagnosis)
        )
        self.assertTrue(evidence)
        self.assertTrue(actions)

    def test_normal_event_with_failed_resource_name_is_not_high_signal(self):
        plan = deployment_plan(self.root)
        results = diagnosis_results(
            events=(
                "Normal ScalingReplicaSet "
                "Scaled up replica set backend-failed-rs from 0 to 1\n"
            ),
        )

        _, evidence, _ = reason_k8s_deployment_execution(
            plan,
            results,
        )

        self.assertFalse(
            any(
                item.startswith("Recent deployment event:")
                for item in evidence
            )
        )

    def test_scaled_to_zero_is_not_availability_failure(self):
        plan = deployment_plan(self.root)
        results = diagnosis_results(
            status="DEPLOYMENT|backend|4|4|0|0|0|0|0|0\n",
            conditions=(
                "CONDITION|Available|True|MinimumReplicasAvailable|ok\n"
                "CONDITION|Progressing|True|NewReplicaSetAvailable|ok\n"
            ),
            pods="",
            replicasets="RS|backend-rs|0|0|0|0|Deployment:backend\n",
        )

        diagnosis, _, _ = reason_k8s_deployment_execution(
            plan,
            results,
        )

        text = " ".join(diagnosis).lower()
        self.assertIn("intentionally scaled to zero", text)
        self.assertNotIn("not fully available", text)


if __name__ == "__main__":
    unittest.main()
