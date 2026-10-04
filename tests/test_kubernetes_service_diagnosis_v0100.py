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
    reason_k8s_service_execution,
    summarize_k8s_service_execution,
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


def service_plan(root: Path) -> DevOpsPlan:
    plan = DevOpsPlan(
        request=DevOpsRequest(
            action=DevOpsAction.K8S_DIAGNOSE_SERVICE,
            component="backend",
        ),
        workspace=root,
        title="Kubernetes Service diagnosis",
    )
    plan.inferred_component = "backend"
    plan.target_context = "minikube"
    plan.target_namespace = "default"
    return plan


def diagnosis_results(
    *,
    selector: str = "app=backend",
    pod_lines: str = "",
    endpoint_lines: str = "",
    service_type: str = "ClusterIP",
) -> list[DevOpsStepResult]:
    selector_raw = "map[app:backend]" if selector else "map[]"

    return [
        step_result(
            "Inspect service routing for backend",
            f"SERVICE|backend|{service_type}|10.96.0.10|{selector_raw}\n"
            "PORT||80|8080|TCP\n",
        ),
        step_result(
            "Inspect EndpointSlices for backend",
            endpoint_lines,
        ),
        step_result(
            "Inspect pods matching service selector for backend",
            f"SELECTOR|{selector}\n{pod_lines}",
        ),
    ]


class KubernetesServiceDiagnosisV0100Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = SessionState(self.root)

    def test_reasoner_routes_explicit_service_diagnosis(self):
        decision = reason_request(
            "diagnose service backend",
            self.state,
        )

        self.assertEqual(
            decision.action,
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
        )
        self.assertEqual(
            decision.devops_action,
            DevOpsAction.K8S_DIAGNOSE_SERVICE,
        )
        self.assertEqual(decision.component, "backend")

    def test_shell_does_not_consume_explicit_service_as_legacy_docker(self):
        self.assertIsNone(
            _legacy_docker_diagnose_target(
                "diagnose service backend"
            )
        )
        self.assertIsNone(
            _legacy_docker_diagnose_target(
                "diagnose kubernetes service backend"
            )
        )

    def test_service_plan_is_context_scoped_and_read_only(self):
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
                    DevOpsAction.K8S_DIAGNOSE_SERVICE,
                    component="backend",
                ),
            )

        self.assertTrue(plan.safe_to_execute)
        self.assertFalse(plan.requires_approval)
        self.assertEqual(plan.target_context, "minikube")
        self.assertEqual(plan.target_namespace, "default")

        commands = [step.command for step in plan.steps]

        self.assertEqual(len(commands), 4)
        self.assertTrue(
            all(
                "--context minikube" in command
                and "--namespace default" in command
                for command in commands
            )
        )
        self.assertTrue(
            any(
                "endpointslices.discovery.k8s.io" in command
                for command in commands
            )
        )
        self.assertTrue(
            any("get pods -l" in command for command in commands)
        )

        joined = " ".join(commands).lower()
        for forbidden in (
            "kubectl describe",
            "kubectl delete",
            "rollout restart",
            "kubectl patch",
            "kubectl scale",
        ):
            self.assertNotIn(forbidden, joined)

    def test_selector_matching_no_pods_is_classified(self):
        plan = service_plan(self.root)
        results = diagnosis_results()

        diagnosis, evidence, next_actions = (
            reason_k8s_service_execution(plan, results)
        )

        joined = " ".join(diagnosis).lower()
        self.assertIn("matches no pods", joined)
        self.assertIn("no endpointslice backends", joined)
        self.assertTrue(
            any("app=backend" in item for item in evidence)
        )
        self.assertTrue(next_actions)

    def test_matching_pods_but_none_ready_is_classified(self):
        plan = service_plan(self.root)
        results = diagnosis_results(
            pod_lines="POD|backend-abc|Running|False\n",
            endpoint_lines=(
                "SLICE|backend-xyz\n"
                "ENDPOINT|10.244.0.7|false|false|false\n"
            ),
        )

        diagnosis, evidence, _ = reason_k8s_service_execution(
            plan,
            results,
        )

        joined = " ".join(diagnosis).lower()
        self.assertIn("none of the matching pods are ready", joined)
        self.assertIn("no ready backend endpoints", joined)
        self.assertTrue(
            any("selector matched 1 pod" in item.lower() for item in evidence)
        )

    def test_ready_matching_pod_but_no_endpoint_is_classified(self):
        plan = service_plan(self.root)
        results = diagnosis_results(
            pod_lines="POD|backend-abc|Running|True\n",
            endpoint_lines="SLICE|backend-xyz\n",
        )

        diagnosis, _, next_actions = reason_k8s_service_execution(
            plan,
            results,
        )

        self.assertTrue(
            any(
                "ready matching pods but no ready endpointslice" in item.lower()
                for item in diagnosis
            )
        )
        self.assertTrue(next_actions)

    def test_ready_endpoint_is_reported_as_healthy_routing_evidence(self):
        plan = service_plan(self.root)
        results = diagnosis_results(
            pod_lines="POD|backend-abc|Running|True\n",
            endpoint_lines=(
                "SLICE|backend-xyz\n"
                "ENDPOINT|10.244.0.7|true|true|false\n"
            ),
        )

        summary = summarize_k8s_service_execution(plan, results)
        diagnosis, evidence, next_actions = (
            reason_k8s_service_execution(plan, results)
        )

        self.assertTrue(
            any("ready_endpoints=1" in item for item in summary)
        )
        self.assertTrue(
            any("1 ready backend endpoint" in item.lower() for item in diagnosis)
        )
        self.assertTrue(
            any("ready_endpoints=1" in item for item in evidence)
        )
        self.assertTrue(
            any("connectivity" in item.lower() for item in next_actions)
        )

    def test_service_without_selector_is_not_called_selector_mismatch(self):
        plan = service_plan(self.root)
        results = diagnosis_results(selector="")

        diagnosis, evidence, next_actions = (
            reason_k8s_service_execution(plan, results)
        )

        joined = " ".join(diagnosis).lower()
        self.assertIn("has no pod selector", joined)
        self.assertNotIn("matches no pods", joined)
        self.assertTrue(evidence)
        self.assertTrue(next_actions)


if __name__ == "__main__":
    unittest.main()
