from pathlib import Path

from iceman.devops_actions import DevOpsAction
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


def _decision(prompt: str, workspace: Path):
    state = SessionState(workspace=workspace)
    return reason_request(prompt, state)


def test_start_minikube_routes_to_trusted_action(tmp_path):
    decision = _decision("start minikube", tmp_path)

    assert decision.action == ReasoningAction.EXECUTE_DEVOPS_ACTION
    assert decision.devops_action == DevOpsAction.K8S_MINIKUBE_START


def test_start_minikube_natural_variants(tmp_path):
    requests = (
        "start my minikube",
        "start k8s minikube",
        "start kubernetes minikube",
        "start the minikube cluster",
        "launch minikube",
        "bring up minikube",
    )

    for prompt in requests:
        decision = _decision(prompt, tmp_path)

        assert decision.action == ReasoningAction.EXECUTE_DEVOPS_ACTION
        assert decision.devops_action == DevOpsAction.K8S_MINIKUBE_START
