from types import SimpleNamespace
import subprocess
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from iceman import __version__, cli, shell
from iceman.ai.contracts import AIIntent, AIReasoningResult, TrustedAction
from iceman.git_actions import GitAction
from iceman.session import SessionState

runner = CliRunner()


@pytest.fixture(autouse=True)
def local_workspace(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ICEMAN_AI_PROVIDER", "local")


@pytest.mark.parametrize(
    "args,prompt",
    [
        (["analyze", "plan"], "analyze plan"),
        (["git", "push"], "git push"),
        (["stage", "everything"], "stage everything"),
        (["show", "git", "status"], "show git status"),
        (["git", "status"], "git status"),
        (["status"], "status"),
        (["ask", "status"], "status"),
        (["git push"], "git push"),
        (["ask", "git", "push"], "git push"),
        (["ask", "stage everything"], "stage everything"),
        (["ask", "diagnose", "pod", "backend"], "diagnose pod backend"),
        (["commit", "my", "changes", "with", "message", "fix: keep spaces"],
         "commit my changes with message fix: keep spaces"),
    ],
)
def test_requests_run_once_without_entering_shell(monkeypatch, tmp_path, args, prompt):
    request = Mock(return_value=0)
    interactive = Mock(side_effect=AssertionError("One-shot entered the shell"))
    monkeypatch.setattr(cli, "run_request", request)
    monkeypatch.setattr(cli, "start_shell", interactive)
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    request.assert_called_once_with(prompt, project_path=tmp_path)
    interactive.assert_not_called()


@pytest.mark.parametrize("prog_name", ["ice", "iceman"])
def test_no_arguments_still_start_interactive_shell(monkeypatch, prog_name):
    interactive = Mock()
    monkeypatch.setattr(cli, "start_shell", interactive)
    result = runner.invoke(cli.app, [], prog_name=prog_name)
    assert result.exit_code == 0
    interactive.assert_called_once_with()


@pytest.mark.parametrize("args", [["--version"], ["-V"], ["version"], ["--help"], ["ask", "--help"]])
def test_information_flags_never_start_shell_or_reasoner(monkeypatch, args):
    forbidden = Mock(side_effect=AssertionError("Unexpected request or shell"))
    monkeypatch.setattr(cli, "start_shell", forbidden)
    monkeypatch.setattr(cli, "run_request", forbidden)
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    forbidden.assert_not_called()
    if "help" not in " ".join(args):
        assert __version__ in result.output


@pytest.mark.parametrize(
    "args",
    [ ["--yes"], ["--not-an-option"], ["git", "push", "--yes"],
      ["ask", "stage everything", "-y"], ["status", "--yes"],
      ["ask", "status", "-y"], ["ask"], ["inspect", "--not-an-option"] ],
)
def test_invalid_flags_and_missing_ask_are_usage_errors(monkeypatch, args):
    forbidden = Mock(side_effect=AssertionError("Unexpected request"))
    monkeypatch.setattr(cli, "run_request", forbidden)
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 2, result.output
    forbidden.assert_not_called()


def test_legacy_inspect_still_uses_explicit_path(monkeypatch, tmp_path):
    inspect = Mock(return_value=object())
    display = Mock()
    monkeypatch.setattr(cli, "inspect_project", inspect)
    monkeypatch.setattr(cli, "show_inspection", display)
    result = runner.invoke(cli.app, ["inspect", str(tmp_path)])
    assert result.exit_code == 0
    inspect.assert_called_once_with(tmp_path)
    display.assert_called_once_with(inspect.return_value)


@pytest.mark.parametrize("code", [1, 2, 7, 130])
def test_one_shot_propagates_exit_status(monkeypatch, code):
    monkeypatch.setattr(cli, "run_request", lambda *a, **k: code)
    assert runner.invoke(cli.app, ["stage", "everything"]).exit_code == code


def git(workspace, *args):
    return subprocess.run(
        ["git", *args], cwd=workspace, capture_output=True, text=True, check=True,
    ).stdout


@pytest.fixture
def git_workspace(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "hello.txt").write_text("hello\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("answer", ["n\n", ""])
def test_staging_denial_or_eof_leaves_index_untouched(git_workspace, answer):
    result = runner.invoke(cli.app, ["stage", "everything"], input=answer)
    assert result.exit_code == 1, result.output
    assert "git add -A" in result.output
    assert git(git_workspace, "diff", "--cached", "--name-only") == ""


def test_staging_uses_real_safety_review_and_explicit_approval(git_workspace):
    result = runner.invoke(cli.app, ["stage", "everything"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Pre-commit safety scan passed" in result.output
    assert git(git_workspace, "diff", "--cached", "--name-only").strip() == "hello.txt"


def test_sensitive_file_blocks_staging_even_with_yes_input(git_workspace):
    (git_workspace / ".env").write_text("EXAMPLE=value\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["stage", "everything"], input="y\n")
    assert result.exit_code == 1, result.output
    assert ".env" in result.output
    assert "Execute the exact" not in result.output
    assert git(git_workspace, "diff", "--cached", "--name-only") == ""


def test_git_push_builds_plan_but_does_not_execute_on_decline(git_workspace, monkeypatch):
    git(git_workspace, "-c", "user.name=Iceman Test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-qm", "fixture")
    git(git_workspace, "remote", "add", "origin", str(git_workspace / "unused.git"))
    executor = Mock(side_effect=AssertionError("Push executed without approval"))
    monkeypatch.setattr("iceman.presentation.execute_git_plan", executor)
    result = runner.invoke(cli.app, ["git", "push"], input="n\n")
    assert result.exit_code == 1
    assert "git push" in result.output
    executor.assert_not_called()


def test_failed_trusted_action_does_not_fall_back(git_workspace, monkeypatch):
    monkeypatch.setattr(shell, "execute_git_plan_with_confirmation",
                        lambda plan: SimpleNamespace(success=False))
    fallback = Mock(side_effect=AssertionError("Failed action fell back"))
    monkeypatch.setattr(shell, "make_plan", fallback)
    result = runner.invoke(cli.app, ["stage", "everything"])
    assert result.exit_code == 1
    fallback.assert_not_called()


@pytest.mark.parametrize("answer", ["n\n", ""])
def test_terraform_analysis_keeps_command_preview_and_approval(tmp_path, monkeypatch, answer):
    (tmp_path / "main.tf").write_text('terraform { required_version = ">= 1.6.0" }\n')
    (tmp_path / ".terraform").mkdir()
    executor = Mock(side_effect=AssertionError("Terraform executed without approval"))
    monkeypatch.setattr("iceman.presentation.execute_devops_plan", executor)
    result = runner.invoke(cli.app, ["analyze", "plan"], input=answer)
    assert result.exit_code == 1, result.output
    assert "terraform_explain_plan" in result.output
    assert "Execute the exact" in result.output
    executor.assert_not_called()


def test_terraform_analysis_rejects_missing_configuration():
    result = runner.invoke(cli.app, ["analyze", "plan"])
    assert result.exit_code == 1
    assert "No Terraform configuration was found" in result.output


def test_unknown_request_never_executes_or_enters_shell(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Unknown prompt executed"))
    monkeypatch.setattr(shell, "execute_with_confirmation", forbidden)
    monkeypatch.setattr(cli, "start_shell", forbidden)
    result = runner.invoke(cli.app, ["frobnicate", "the", "moon"])
    assert result.exit_code == 1, result.output
    forbidden.assert_not_called()


def provider_for(**fields):
    return SimpleNamespace(reason=lambda raw, state: AIReasoningResult(
        provider="test",
        intent=AIIntent(rationale="Test trusted route", **fields),
    ))


@pytest.mark.parametrize("answer", ["n\n", ""])
def test_correction_must_be_confirmed_before_dispatch(monkeypatch, answer):
    provider = provider_for(
        action=TrustedAction.EXECUTE_GIT_ACTION, git_action=GitAction.STAGE_ALL,
        requires_clarification=True, interpreted_request="stage everything",
    )
    monkeypatch.setattr(shell, "get_reasoning_provider", lambda: provider)
    dispatch = Mock(side_effect=AssertionError("Unconfirmed interpretation dispatched"))
    monkeypatch.setattr(shell, "_dispatch_trusted_intent", dispatch)
    result = runner.invoke(cli.app, ["stgae", "everything"], input=answer)
    assert result.exit_code == 1
    dispatch.assert_not_called()


def test_clarification_without_interpretation_fails_closed(monkeypatch):
    provider = provider_for(action=TrustedAction.EXECUTE_GIT_ACTION,
                            git_action=GitAction.STAGE_ALL, requires_clarification=True)
    monkeypatch.setattr(shell, "get_reasoning_provider", lambda: provider)
    dispatch = Mock(side_effect=AssertionError("Missing clarification dispatched"))
    monkeypatch.setattr(shell, "_dispatch_trusted_intent", dispatch)
    result = runner.invoke(cli.app, ["stage", "everything"], input="y\n")
    assert result.exit_code == 1
    dispatch.assert_not_called()


def test_confirmed_correction_still_requires_execution_approval(git_workspace, monkeypatch):
    provider = provider_for(
        action=TrustedAction.EXECUTE_GIT_ACTION, git_action=GitAction.STAGE_ALL,
        requires_clarification=True, interpreted_request="stage everything",
    )
    monkeypatch.setattr(shell, "get_reasoning_provider", lambda: provider)
    result = runner.invoke(cli.app, ["stgae", "everything"], input="y\nn\n")
    assert result.exit_code == 1
    assert "Do you mean" in result.output
    assert "Execute the exact Git/GitHub" in result.output
    assert git(git_workspace, "diff", "--cached", "--name-only") == ""


def test_shell_passes_existing_context_to_shared_runner(monkeypatch, tmp_path):
    requests = Mock(return_value=0)
    answers = iter(["stage everything", "git push", "exit"])
    monkeypatch.setattr(shell, "run_request", requests)
    monkeypatch.setattr(shell.console, "input", lambda *a, **k: next(answers))
    shell.start_shell(tmp_path)
    assert requests.call_count == 2
    first, second = requests.call_args_list
    assert first.args == ("stage everything",)
    assert second.args == ("git push",)
    assert first.kwargs["state"] is second.kwargs["state"]
    assert first.kwargs["provider"] is second.kwargs["provider"]
    assert first.kwargs["state"].workspace == tmp_path


@pytest.mark.parametrize("prompt", ["apply file plan", "rollback file changes", "apply option 1"])
def test_one_shot_context_is_fresh(prompt):
    result = runner.invoke(cli.app, ["ask", prompt])
    assert result.exit_code == 1, result.output


def test_provider_failure_keeps_local_safety_gates(git_workspace, monkeypatch):
    from iceman.ai.local_provider import LocalReasoningProvider
    from iceman.ai.provider import SafeFallbackProvider

    primary = SimpleNamespace(name="unavailable", reason=Mock(side_effect=RuntimeError("offline")))
    provider = SafeFallbackProvider(primary, LocalReasoningProvider())
    monkeypatch.setattr(shell, "get_reasoning_provider", lambda: provider)
    result = runner.invoke(cli.app, ["stage", "everything"], input="n\n")
    assert result.exit_code == 1
    assert "git add -A" in result.output
    assert git(git_workspace, "diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize("answer", ["0\n", ""])
def test_ambiguous_resource_cancellation_never_dispatches(monkeypatch, answer):
    provider = provider_for(
        action=TrustedAction.EXECUTE_GIT_ACTION, git_action=GitAction.STATUS,
        clarification_candidates=["backend-a", "backend-b"],
        clarification_field="component",
    )
    monkeypatch.setattr(shell, "get_reasoning_provider", lambda: provider)
    dispatch = Mock(side_effect=AssertionError("Unresolved candidate dispatched"))
    monkeypatch.setattr(shell, "_dispatch_trusted_intent", dispatch)
    result = runner.invoke(cli.app, ["show", "git", "status"], input=answer)
    assert result.exit_code == 1
    dispatch.assert_not_called()


@pytest.mark.parametrize("answer", ["n\n", ""])
def test_bounded_fallback_still_needs_approval(monkeypatch, answer):
    provider = provider_for(action=TrustedAction.FALLBACK)
    monkeypatch.setattr(shell, "get_reasoning_provider", lambda: provider)
    executor = Mock(side_effect=AssertionError("Fallback ran without approval"))
    monkeypatch.setattr("iceman.presentation.run_command_streaming", executor)
    result = runner.invoke(cli.app, ["who", "am", "I", "on", "aws"], input=answer)
    assert result.exit_code == 1
    assert "aws sts get-caller-identity" in result.output
    executor.assert_not_called()


def test_workspace_file_request_cancellation_does_not_write(tmp_path):
    result = runner.invoke(cli.app, ["create", "a", ".gitignore"], input="n\n")
    assert result.exit_code == 1, result.output
    assert not (tmp_path / ".gitignore").exists()


def test_linux_request_cancellation_does_not_create_directory(tmp_path):
    result = runner.invoke(cli.app, ["create", "a", "folder", "called", "configs"], input="n\n")
    assert result.exit_code == 1, result.output
    assert not (tmp_path / "configs").exists()


@pytest.mark.parametrize("prog_name", ["ice", "iceman"])
@pytest.mark.parametrize("args", [["status"], ["ask", "status"], [" STATUS "]])
def test_status_shows_fresh_session_outside_git_without_provider_or_commands(
    monkeypatch, tmp_path, prog_name, args,
):
    forbidden = Mock(side_effect=AssertionError("Session status requested external work"))
    display = Mock(wraps=shell.show_session_state)
    monkeypatch.setattr(cli, "start_shell", forbidden)
    monkeypatch.setattr(shell, "get_reasoning_provider", forbidden)
    monkeypatch.setattr(shell, "make_plan", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(shell, "show_session_state", display)

    result = runner.invoke(cli.app, args, prog_name=prog_name)

    assert result.exit_code == 0, result.output
    assert "Iceman Session" in result.output
    assert "Workspace" in result.output
    assert "Execute the exact" not in result.output
    assert "fall back" not in result.output
    display.assert_called_once()
    state = display.call_args.args[0]
    assert state.workspace == tmp_path
    assert state.last_target is None
    assert state.history == []
    forbidden.assert_not_called()


def test_status_keeps_supplied_session_and_cannot_be_reinterpreted(monkeypatch, tmp_path):
    state = SessionState(tmp_path)
    state.last_target = "backend"
    state.add_event("test", "existing context")
    provider = SimpleNamespace(reason=Mock(side_effect=AssertionError("Status was reinterpreted")))
    display = Mock()
    monkeypatch.setattr(shell, "show_session_state", display)

    assert shell.run_request("status", state=state, provider=provider) == 0

    display.assert_called_once_with(state)
    assert state.last_target == "backend"
    assert state.history[0].summary == "existing context"
    provider.reason.assert_not_called()


def test_interactive_status_still_uses_existing_session(monkeypatch, tmp_path):
    state = SessionState(tmp_path)
    state.last_target = "backend"
    answers = iter(["status", "exit"])
    display = Mock()
    one_shot = Mock(side_effect=AssertionError("Interactive status used fresh context"))
    monkeypatch.setattr(shell, "SessionState", lambda workspace: state)
    monkeypatch.setattr(shell.console, "input", lambda *a, **k: next(answers))
    monkeypatch.setattr(shell, "show_session_state", display)
    monkeypatch.setattr(shell, "run_request", one_shot)

    result = runner.invoke(cli.app, [], prog_name="iceman")

    assert result.exit_code == 0, result.output
    display.assert_called_once_with(state)
    assert state.last_target == "backend"
    one_shot.assert_not_called()


@pytest.mark.parametrize("args", [["git", "status"], ["show", "git", "status"]])
@pytest.mark.parametrize("answer", ["n\n", ""])
def test_git_status_still_requires_approval(git_workspace, monkeypatch, args, answer):
    executor = Mock(side_effect=AssertionError("Git status executed without approval"))
    monkeypatch.setattr("iceman.presentation.execute_git_plan", executor)

    result = runner.invoke(cli.app, args, input=answer)

    assert result.exit_code == 1, result.output
    assert "git status --short --branch" in result.output
    assert "Execute the exact Git/GitHub" in result.output
    executor.assert_not_called()


def test_git_status_executes_after_approval_without_changing_worktree(git_workspace):
    before = git(git_workspace, "status", "--porcelain")

    result = runner.invoke(cli.app, ["git", "status"], input="y\n")

    assert result.exit_code == 0, result.output
    assert "git status --short --branch" in result.output
    assert "Execute the exact Git/GitHub" in result.output
    assert "hello.txt" in result.output
    assert "Git/GitHub workflow completed successfully" in result.output
    assert git(git_workspace, "status", "--porcelain") == before
