from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from iceman import cli, shell
from iceman.ai.contracts import AIIntent, AIReasoningResult, TrustedAction
from iceman.ai.context import build_reasoning_context
from iceman.git_actions import GitAction, GitPlan, GitRequest, GitStep, GitExecutionResult
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState

runner = CliRunner()


@pytest.fixture(autouse=True)
def local_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ICEMAN_AI_PROVIDER", "local")
    (tmp_path / "Dockerfile").write_text("FROM python:3\nUSER root\n")


@pytest.mark.parametrize("words", [
    ["what", "do", "you", "think", "about", "this", "project?"],
    ["what", "do", "you", "think", "i", "should", "improve?"],
    ["any", "good", "suggestion?"],
    ["what", "do", "you", "think", "is", "missing?"],
    ["reveiw", "this", "proejct", "for", "securty", "vulnerabilities"],
])
def test_one_shot_reviews_without_key_or_subprocess(words, monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Static review launched a process"))
    monkeypatch.setattr("subprocess.run", forbidden)
    result = runner.invoke(cli.app, words)
    assert result.exit_code == 0, result.output
    assert "Docker" in result.output
    assert "project_review" not in result.output
    forbidden.assert_not_called()


def test_interactive_followups_and_workspace_isolation(tmp_path):
    state = SessionState(tmp_path)
    provider = SimpleNamespace(reason=Mock(side_effect=AssertionError("High confidence review called AI")))
    assert shell.run_request("review this project", state=state, provider=provider) == 0
    for prompt in ["why?", "go deeper", "what else?", "show me the evidence", "what should I do first?", "fix the highest risk"]:
        assert shell.run_request(prompt, state=state, provider=provider) == 0
    other = tmp_path / "other"
    other.mkdir()
    state.change_workspace(other)
    assert state.last_project_review is None
    assert shell.run_request("why?", state=state, provider=provider) == 1


def test_unsupported_request_naturally_refuses_without_execution(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Unsupported request executed"))
    monkeypatch.setattr(shell, "execute_with_confirmation", forbidden)
    result = runner.invoke(cli.app, ["bake", "me", "a", "cake"])
    assert result.exit_code == 1
    assert "not programmed" in result.output
    forbidden.assert_not_called()


@pytest.mark.parametrize("prompt", [
    "stage everything and sing a song",
    "stage all, commit as 'example' and push --force",
    "stage all, commit as 'example' and push then terraform destroy",
    "terraform aply", "git push --force", "kubectl delete pods",
])
def test_unsafe_chain_never_reaches_provider_or_dispatch(tmp_path, monkeypatch, prompt):
    forbidden = Mock(side_effect=AssertionError("Unsafe chain advanced"))
    monkeypatch.setattr(shell, "_dispatch_trusted_intent", forbidden)
    assert shell.run_request(prompt, state=SessionState(tmp_path), provider=SimpleNamespace(reason=forbidden)) == 1
    forbidden.assert_not_called()


@pytest.mark.parametrize("approval", [True, False])
def test_combined_workflow_has_exactly_one_approval(tmp_path, monkeypatch, approval):
    plan = GitPlan(GitRequest(GitAction.COMMIT_PUSH, message="example"), tmp_path, "combined",
                   steps=[GitStep("Stage all approved changes", "git add -A", True)])
    monkeypatch.setattr(shell, "build_git_plan", lambda *args: plan)
    monkeypatch.setattr(shell, "advise_git_plan", lambda *args: SimpleNamespace(advisories=[]))
    monkeypatch.setattr(shell, "show_advisor_report", lambda *args: None)
    confirm = Mock(return_value=approval)
    monkeypatch.setattr("typer.confirm", confirm)
    executor = Mock(return_value=GitExecutionResult(plan, True))
    monkeypatch.setattr("iceman.presentation.execute_git_plan", executor)
    result = runner.invoke(cli.app, ["stage", "everything,", "commit", "with", "the", "message", "example", "and", "push", "to", "github"])
    assert result.exit_code == (0 if approval else 1), result.output
    assert confirm.call_count == 1
    assert executor.call_count == int(approval)


def test_low_confidence_provider_mutation_is_not_executed(tmp_path, monkeypatch):
    result = AIReasoningResult(provider="test", intent=AIIntent(action=TrustedAction.EXECUTE_GIT_ACTION, git_action=GitAction.STAGE_ALL, rationale="uncertain", confidence="low"))
    forbidden = Mock(side_effect=AssertionError("Low confidence mutation"))
    monkeypatch.setattr(shell, "_dispatch_trusted_intent", forbidden)
    assert shell.run_request("do that thing", state=SessionState(tmp_path), provider=SimpleNamespace(reason=lambda *args: result)) == 1
    forbidden.assert_not_called()


def test_safe_review_context_contains_no_secret(tmp_path):
    secret = "confidential_credential_123456"
    (tmp_path / "config.py").write_text(f'API_KEY = "{secret}"\n')
    state = SessionState(tmp_path)
    shell.run_request("review this project", state=state)
    assert secret not in build_reasoning_context(state)


def test_typo_correction_does_not_rewrite_commit_message(tmp_path):
    decision = reason_request("comit my chnages with message 'dockre  gti stgae'", SessionState(tmp_path))
    assert decision.action == ReasoningAction.EXECUTE_GIT_ACTION
    assert decision.requires_clarification
    assert decision.commit_message == "dockre  gti stgae"


def test_commit_message_is_data_for_command_safety():
    from iceman.safety import check_command
    assert check_command("git commit -m 'document terraform destroy'").allowed
    assert not check_command("terraform destroy").allowed
