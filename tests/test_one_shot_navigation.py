from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from iceman import cli, shell
from iceman.ai.contracts import AIIntent, AIReasoningResult, TrustedAction
from iceman.linux_actions import LinuxAction
from iceman.session import SessionState


runner = CliRunner()


@pytest.fixture(autouse=True)
def local_workspace(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ICEMAN_AI_PROVIDER", "local")
    (tmp_path / "Works").mkdir()


@pytest.mark.parametrize("prog_name", ["ice", "iceman"])
@pytest.mark.parametrize(
    "prompt",
    [
        "change folder to Works",
        "change directory to Works",
        "change to Works",
        "go into Works",
        "go to Works",
        "enter Works",
        "go back",
        "go back one folder",
        "go up one directory",
        "cd Works",
        "cd",
    ],
)
def test_one_shot_navigation_explains_terminal_limit_without_changing_workspace(
    monkeypatch, tmp_path, prog_name, prompt,
):
    state = SessionState(tmp_path)
    monkeypatch.setattr(shell, "SessionState", lambda workspace: state)
    forbidden = Mock(side_effect=AssertionError("One-shot changed workspace or executed"))
    monkeypatch.setattr(shell, "build_linux_plan", forbidden)
    monkeypatch.setattr(shell, "execute_linux_plan_with_confirmation", forbidden)
    monkeypatch.setattr(shell, "make_plan", forbidden)
    monkeypatch.setattr(state, "change_workspace", forbidden)

    result = runner.invoke(cli.app, prompt.split(), prog_name=prog_name, input="y\n")

    assert result.exit_code == 1, result.output
    assert "cannot change your terminal's working directory" in result.output
    assert "Use cd in your terminal" in result.output
    assert "start iceman" in result.output
    assert "Active Iceman workspace changed" not in result.output
    assert "Execute the exact" not in result.output
    assert state.workspace == tmp_path
    assert state.history == []
    assert Path.cwd() == tmp_path
    forbidden.assert_not_called()


def test_ask_navigation_leaves_next_one_shot_in_callers_directory(monkeypatch, tmp_path):
    display = Mock()
    monkeypatch.setattr(shell, "show_session_state", display)

    navigation = runner.invoke(cli.app, ["ask", "change folder to Works"])
    status = runner.invoke(cli.app, ["status"])

    assert navigation.exit_code == 1, navigation.output
    assert status.exit_code == 0, status.output
    assert display.call_args.args[0].workspace == tmp_path
    assert Path.cwd() == tmp_path


@pytest.mark.parametrize("path", ["Works", "Works; touch sentinel", "$(touch sentinel)"])
def test_one_shot_blocks_provider_selected_navigation_without_constructing_a_plan(
    monkeypatch, tmp_path, path,
):
    state = SessionState(tmp_path)
    provider = SimpleNamespace(reason=Mock(return_value=AIReasoningResult(
        provider="test",
        intent=AIIntent(
            action=TrustedAction.EXECUTE_LINUX_ACTION,
            rationale="Switch workspace",
            linux_action=LinuxAction.CHANGE_DIR,
            path=path,
        ),
    )))
    monkeypatch.setattr(shell, "SessionState", lambda workspace: state)
    forbidden = Mock(side_effect=AssertionError("Navigation was dispatched"))
    monkeypatch.setattr(shell, "_dispatch_trusted_intent", forbidden)
    monkeypatch.setattr(shell, "make_plan", forbidden)

    assert shell.run_request("switch my workspace", provider=provider) == 1

    assert state.workspace == tmp_path
    assert not (tmp_path / "sentinel").exists()
    forbidden.assert_not_called()


@pytest.mark.parametrize("prompt", ["cd Works", "change folder to Works", "go into Works"])
def test_interactive_navigation_keeps_workspace_for_later_requests(
    monkeypatch, tmp_path, prompt,
):
    state = SessionState(tmp_path)
    answers = iter([prompt, "status", "exit"])
    display = Mock()
    monkeypatch.setattr(shell, "SessionState", lambda workspace: state)
    monkeypatch.setattr(shell.console, "input", lambda *a, **k: next(answers))
    monkeypatch.setattr(shell, "show_session_state", display)

    result = runner.invoke(cli.app, [], prog_name="iceman", input="y\n")

    assert result.exit_code == 0, result.output
    assert state.workspace == tmp_path / "Works"
    display.assert_called_once_with(state)
    assert Path.cwd() == tmp_path
    assert "cannot change your terminal" not in result.output


@pytest.mark.parametrize("answer", ["n\n", ""])
def test_interactive_natural_navigation_still_requires_approval(
    monkeypatch, tmp_path, answer,
):
    state = SessionState(tmp_path)
    answers = iter(["change folder to Works", "exit"])
    monkeypatch.setattr(shell, "SessionState", lambda workspace: state)
    monkeypatch.setattr(shell.console, "input", lambda *a, **k: next(answers))

    result = runner.invoke(cli.app, [], prog_name="iceman", input=answer)

    assert result.exit_code == 0, result.output
    assert "Execute the exact" in result.output
    assert state.workspace == tmp_path
    assert Path.cwd() == tmp_path
