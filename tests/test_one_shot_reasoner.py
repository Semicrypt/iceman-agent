import pytest

from iceman.ai.contracts import TrustedAction
from iceman.ai.local_provider import LocalReasoningProvider
from iceman.devops_actions import DevOpsAction
from iceman.git_actions import GitAction
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


@pytest.mark.parametrize(
    "prompt,action,trusted_action,devops_action,git_action",
    [
        ("status", ReasoningAction.SESSION,
         TrustedAction.SESSION_STATUS, None, None),
        ("git status", ReasoningAction.EXECUTE_GIT_ACTION,
         TrustedAction.EXECUTE_GIT_ACTION, None, GitAction.STATUS),
        ("show git status", ReasoningAction.EXECUTE_GIT_ACTION,
         TrustedAction.EXECUTE_GIT_ACTION, None, GitAction.STATUS),
        ("analyze plan", ReasoningAction.EXECUTE_DEVOPS_ACTION,
         TrustedAction.EXECUTE_DEVOPS_ACTION, DevOpsAction.TERRAFORM_EXPLAIN_PLAN, None),
        ("analyze the plan", ReasoningAction.EXECUTE_DEVOPS_ACTION,
         TrustedAction.EXECUTE_DEVOPS_ACTION, DevOpsAction.TERRAFORM_EXPLAIN_PLAN, None),
        ("git push", ReasoningAction.EXECUTE_GIT_ACTION,
         TrustedAction.EXECUTE_GIT_ACTION, None, GitAction.PUSH),
        ("stage everything", ReasoningAction.EXECUTE_GIT_ACTION,
         TrustedAction.EXECUTE_GIT_ACTION, None, GitAction.STAGE_ALL),
    ],
)
def test_one_shot_requests_keep_typed_intents(
    tmp_path, prompt, action, trusted_action, devops_action, git_action,
):
    state = SessionState(tmp_path)
    decision = reason_request(prompt, state)
    assert decision.action is action
    assert decision.devops_action is devops_action
    assert decision.git_action is git_action
    assert not decision.requires_clarification

    result = LocalReasoningProvider().reason(prompt, state)
    assert result.provider == "local"
    assert result.intent.action is trusted_action
    assert result.intent.devops_action is devops_action
    assert result.intent.git_action is git_action
    assert not result.intent.requires_clarification


@pytest.mark.parametrize("prompt", ["status backend", "status and git push"])
def test_status_shortcut_does_not_capture_other_requests(tmp_path, prompt):
    state = SessionState(tmp_path)
    assert reason_request(prompt, state).action is not ReasoningAction.SESSION
    assert LocalReasoningProvider().reason(prompt, state).intent.action is not TrustedAction.SESSION_STATUS


def test_plan_shorthand_does_not_capture_other_plan_objects(tmp_path):
    state = SessionState(tmp_path)
    prompt = "analyze deployment plan"
    assert reason_request(prompt, state).devops_action is not DevOpsAction.TERRAFORM_EXPLAIN_PLAN
    assert LocalReasoningProvider().reason(prompt, state).intent.devops_action is not DevOpsAction.TERRAFORM_EXPLAIN_PLAN


def test_typo_correction_still_requires_clarification(tmp_path):
    state = SessionState(tmp_path)
    prompt = "comit my chnages with message 'feat: test'"
    assert reason_request(prompt, state).requires_clarification
    assert LocalReasoningProvider().reason(prompt, state).intent.requires_clarification
