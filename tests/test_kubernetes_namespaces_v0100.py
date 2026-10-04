import tempfile
from pathlib import Path
from unittest.mock import patch

from iceman.devops_actions import (
    DevOpsAction,
    DevOpsRequest,
    build_devops_plan,
)
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


def test_show_namespaces_routes_to_trusted_action():
    with tempfile.TemporaryDirectory() as tmp:
        state = SessionState(Path(tmp))
        decision = reason_request("show namespaces", state)

    assert decision.action == ReasoningAction.EXECUTE_DEVOPS_ACTION
    assert decision.devops_action == DevOpsAction.K8S_NAMESPACES


def test_list_ns_alias_routes_to_trusted_action():
    with tempfile.TemporaryDirectory() as tmp:
        state = SessionState(Path(tmp))
        decision = reason_request("list ns", state)

    assert decision.devops_action == DevOpsAction.K8S_NAMESPACES


@patch(
    "iceman.devops_actions._available_k8s_contexts",
    return_value=["minikube"],
)
@patch(
    "iceman.devops_actions._current_k8s_context",
    return_value="minikube",
)
def test_namespace_plan_is_read_only_and_context_scoped(current, available):
    with tempfile.TemporaryDirectory() as tmp:
        plan = build_devops_plan(
            Path(tmp),
            DevOpsRequest(action=DevOpsAction.K8S_NAMESPACES),
        )

    assert plan.safe_to_execute
    assert not plan.requires_approval
    assert plan.target_context == "minikube"
    assert len(plan.steps) == 1
    assert (
        plan.steps[0].command
        == "kubectl --context minikube get namespaces -o wide"
    )
    assert not plan.steps[0].changes_state


@patch(
    "iceman.devops_actions._available_k8s_contexts",
    return_value=[],
)
@patch(
    "iceman.devops_actions._current_k8s_context",
    return_value=None,
)
def test_namespace_listing_requires_active_context(current, available):
    with tempfile.TemporaryDirectory() as tmp:
        plan = build_devops_plan(
            Path(tmp),
            DevOpsRequest(action=DevOpsAction.K8S_NAMESPACES),
        )

    assert not plan.safe_to_execute
    assert plan.steps == []
    assert any(
        "No active Kubernetes context" in note
        for note in plan.notes
    )
