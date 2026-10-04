from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from iceman.devops_actions import (
    DevOpsAction,
    DevOpsPlan,
    DevOpsRequest,
    build_devops_plan,
    reason_k8s_service_connectivity_execution,
    reason_k8s_service_execution,
)
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


def _decision(prompt: str):
    state = SessionState(Path("."))
    return reason_request(prompt, state)


def _build_connectivity_plan(port=None):
    with (
        patch(
            "iceman.devops_actions._current_k8s_context",
            return_value="minikube",
        ),
        patch(
            "iceman.devops_actions._available_k8s_contexts",
            return_value=["minikube"],
        ),
        patch(
            "iceman.devops_actions._context_namespace",
            return_value="default",
        ),
    ):
        return build_devops_plan(
            Path("."),
            DevOpsRequest(
                action=DevOpsAction.K8S_SERVICE_CONNECTIVITY,
                component="backend",
                port=port,
            ),
        )


def _reasoning_plan(port=None):
    return DevOpsPlan(
        request=DevOpsRequest(
            action=DevOpsAction.K8S_SERVICE_CONNECTIVITY,
            component="backend",
            port=port,
        ),
        workspace=Path("."),
        title="test",
        inferred_component="backend",
        target_context="minikube",
        target_namespace="default",
    )


def _step(label: str, stdout: str):
    return SimpleNamespace(
        step=SimpleNamespace(label=label),
        result=SimpleNamespace(
            stdout=stdout,
            stderr="",
            returncode=0,
            interrupted=False,
        ),
    )


def _healthy_steps(probe: str):
    return [
        _step(
            "Inspect service routing for backend",
            "SERVICE|backend|ClusterIP|10.96.0.20|{'app': 'backend'}\n"
            "PORT|http|80|8080|TCP\n",
        ),
        _step(
            "Inspect EndpointSlices for backend",
            "SLICE|backend-abc\n"
            "ENDPOINT|10.244.0.20|true|true|false\n",
        ),
        _step(
            "Inspect pods matching service selector for backend",
            "SELECTOR|app=backend\n"
            "POD|backend-abc|Running|True\n",
        ),
        _step(
            "Probe Service-selected backend TCP connectivity for backend",
            probe,
        ),
    ]


def test_reasoner_routes_service_connectivity():
    decision = _decision("check service backend connectivity")

    assert decision.action == ReasoningAction.EXECUTE_DEVOPS_ACTION
    assert decision.devops_action == DevOpsAction.K8S_SERVICE_CONNECTIVITY
    assert decision.component == "backend"
    assert decision.port is None


def test_reasoner_routes_explicit_service_connectivity_port():
    decision = _decision("check service backend connectivity on port 80")

    assert decision.devops_action == DevOpsAction.K8S_SERVICE_CONNECTIVITY
    assert decision.component == "backend"
    assert decision.port == 80


def test_connectivity_plan_is_scoped_read_only_and_non_mutating():
    plan = _build_connectivity_plan()

    assert plan.safe_to_execute
    assert plan.target_context == "minikube"
    assert plan.target_namespace == "default"
    assert len(plan.steps) == 5
    assert all(not step.changes_state for step in plan.steps)

    command = plan.steps[-1].command
    assert "kubectl --context minikube --namespace default" in command
    assert "port-forward --address 127.0.0.1" in command
    assert "kubectl run" not in command
    assert "kubectl create" not in command
    assert "kubectl delete" not in command
    assert "kubectl patch" not in command
    assert "kubectl scale" not in command
    assert "rollout restart" not in command


def test_connectivity_plan_rejects_invalid_port():
    plan = _build_connectivity_plan(port=70000)

    assert not plan.safe_to_execute
    assert plan.steps == []


def test_successful_probe_reports_backend_listener_reachable():
    plan = _reasoning_plan()
    steps = _healthy_steps(
        "CONNECTIVITY|SUCCESS|reason=tcp-connected|"
        "service_port=80|target_port=8080|local_port=41001\n"
    )

    base_diagnosis, _, _ = reason_k8s_service_execution(plan, steps)
    diagnosis, evidence, next_actions = (
        reason_k8s_service_connectivity_execution(plan, steps)
    )

    assert any("ready backend" in item.lower() for item in base_diagnosis)
    assert any("accepted a bounded TCP connection" in item for item in diagnosis)
    assert any("Service port 80 -> targetPort 8080" in item for item in evidence)
    assert any("bypasses kube-proxy" in item for item in next_actions)


def test_target_port_failure_identifies_listener_mismatch_candidate():
    plan = _reasoning_plan()
    steps = _healthy_steps(
        "CONNECTIVITY|FAIL|reason=target-port-connect-failed|"
        "service_port=80|target_port=9999|local_port=41002\n"
    )

    diagnosis, evidence, next_actions = (
        reason_k8s_service_connectivity_execution(plan, steps)
    )

    assert any("targetPort/application-listener mismatch" in item for item in diagnosis)
    assert any("status=FAIL" in item for item in evidence)
    assert any("actual listening port" in item for item in next_actions)


def test_multiple_tcp_ports_are_not_guessed():
    plan = _reasoning_plan()
    steps = _healthy_steps(
        "CONNECTIVITY|SKIPPED|reason=multiple-tcp-service-ports|count=2\n"
    )

    diagnosis, evidence, next_actions = (
        reason_k8s_service_connectivity_execution(plan, steps)
    )

    assert any("exposes 2 TCP ports" in item for item in diagnosis)
    assert any("multiple-tcp-service-ports" in item for item in evidence)
    assert any("connectivity on port" in item for item in next_actions)


def test_no_ready_endpoints_does_not_invent_listener_failure():
    plan = _reasoning_plan()
    steps = [
        _step(
            "Inspect service routing for backend",
            "SERVICE|backend|ClusterIP|10.96.0.20|{'app': 'backend'}\n"
            "PORT|http|80|8080|TCP\n",
        ),
        _step(
            "Inspect EndpointSlices for backend",
            "SLICE|backend-abc\n"
            "ENDPOINT|10.244.0.20|false|false|false\n",
        ),
        _step(
            "Inspect pods matching service selector for backend",
            "SELECTOR|app=backend\n"
            "POD|backend-abc|Running|False\n",
        ),
        _step(
            "Probe Service-selected backend TCP connectivity for backend",
            "CONNECTIVITY|SKIPPED|reason=no-ready-endpoints|"
            "service_port=80|target_port=8080\n",
        ),
    ]

    base_diagnosis, _, _ = reason_k8s_service_execution(plan, steps)
    diagnosis, evidence, next_actions = (
        reason_k8s_service_connectivity_execution(plan, steps)
    )

    assert any("none of the matching Pods are Ready" in item for item in base_diagnosis)
    assert not any("listener mismatch" in item for item in diagnosis)
    assert any("no-ready-endpoints" in item for item in evidence)
    assert any("Pod-readiness" in item for item in next_actions)
