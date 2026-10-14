from pathlib import Path
import shlex
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from iceman import cli, presentation, shell
from iceman.linux_actions import LinuxAction


runner = CliRunner()


@pytest.fixture(autouse=True)
def local_workspace(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ICEMAN_AI_PROVIDER", "local")


@pytest.mark.parametrize(
    "args",
    [
        ["list", "my", "folders"],
        ["list", "folders"],
        ["list", "my", "files"],
        ["show", "files", "here"],
        ["list", "files"],
        ["show", "folders", "here"],
        ["list my folders"],
        ["ask", "list", "my", "files"],
    ],
)
def test_directory_listing_uses_trusted_read_only_plan(monkeypatch, tmp_path, args):
    (tmp_path / "example.txt").write_text("unchanged\n", encoding="utf-8")
    (tmp_path / "subfolder").mkdir()
    executor = Mock(wraps=presentation.execute_linux_plan)
    fallback = Mock(side_effect=AssertionError("Listing fell back to a command planner"))
    monkeypatch.setattr(presentation, "execute_linux_plan", executor)
    monkeypatch.setattr(shell, "make_plan", fallback)

    result = runner.invoke(cli.app, args, input="y\n")

    assert result.exit_code == 0, result.output
    assert "linux_list" in result.output
    assert "read-only" in result.output
    assert "Execute the exact command(s) shown above?" in result.output
    executor.assert_called_once()
    plan = executor.call_args.args[0]
    assert plan.request.action == LinuxAction.LIST
    assert plan.workspace == tmp_path
    assert plan.safe_to_execute
    assert plan.new_workspace is None
    assert len(plan.steps) == 1
    assert plan.steps[0].command == f"ls -lh -- {shlex.quote(str(tmp_path))}"
    assert not plan.steps[0].changes_state
    assert "example.txt" in result.output
    assert "subfolder" in result.output
    assert (tmp_path / "example.txt").read_text(encoding="utf-8") == "unchanged\n"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["example.txt", "subfolder"]
    assert Path.cwd() == tmp_path
    fallback.assert_not_called()


@pytest.mark.parametrize("answer", ["n\n", ""])
def test_directory_listing_needs_explicit_approval(monkeypatch, answer):
    executor = Mock(side_effect=AssertionError("Listing executed without approval"))
    monkeypatch.setattr(presentation, "execute_linux_plan", executor)

    result = runner.invoke(cli.app, ["list", "my", "folders"], input=answer)

    assert result.exit_code == 1, result.output
    assert "linux_list" in result.output
    assert "read-only" in result.output
    executor.assert_not_called()


@pytest.mark.parametrize(
    "args",
    [
        ["analyze", "my", "current", "folder", "and", "files"],
        ["analyze", "this", "folder"],
        ["inspect", "current", "folder"],
        ["analyze", "current", "directory"],
        ["inspect", "this", "folder"],
        ["inspect", "my", "current", "folder", "and", "files"],
        ["analyse", "this", "directory"],
        ["analyze my current folder and files"],
        ["inspect current folder"],
        ["ask", "inspect", "current", "folder"],
    ],
)
def test_folder_analysis_reuses_inspector_for_current_directory(monkeypatch, tmp_path, args):
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    inspect = Mock(wraps=shell.inspect_project)
    forbidden = Mock(side_effect=AssertionError("Inspection used a shell command or fallback"))
    monkeypatch.setattr(shell, "inspect_project", inspect)
    monkeypatch.setattr(shell, "make_plan", forbidden)
    monkeypatch.setattr(shell, "execute_with_confirmation", forbidden)
    monkeypatch.setattr(presentation, "execute_linux_plan", forbidden)
    monkeypatch.setattr(cli, "start_shell", forbidden)

    result = runner.invoke(cli.app, args)

    assert result.exit_code == 0, result.output
    inspect.assert_called_once_with(tmp_path)
    assert "FastAPI" in result.output
    assert "FastAPI" in result.output
    assert "Python" in result.output
    assert (tmp_path / "requirements.txt").read_text(encoding="utf-8") == "fastapi\n"
    assert [item.name for item in tmp_path.iterdir()] == ["requirements.txt"]
    assert Path.cwd() == tmp_path
    forbidden.assert_not_called()


@pytest.mark.parametrize("name", ["current folder", "this directory", "project"])
def test_inspect_preserves_single_explicit_path_argument(monkeypatch, tmp_path, name):
    target = tmp_path / name
    target.mkdir()
    inspect = Mock(return_value=object())
    display = Mock()
    request = Mock(side_effect=AssertionError("Explicit inspect path became a request"))
    monkeypatch.setattr(cli, "inspect_project", inspect)
    monkeypatch.setattr(cli, "show_inspection", display)
    monkeypatch.setattr(cli, "run_request", request)

    result = runner.invoke(cli.app, ["inspect", name])

    assert result.exit_code == 0, result.output
    inspect.assert_called_once_with(target)
    display.assert_called_once_with(inspect.return_value)
    request.assert_not_called()


@pytest.mark.parametrize(
    "args,code",
    [
        (["inspect", "--help"], 0),
        (["inspect", "current", "folder", "--help"], 0),
        (["inspect", "current", "folder", "--yes"], 2),
        (["inspect", "current", "folder", "and", "delete", "files"], 2),
        (["inspect", "missing directory"], 2),
    ],
)
def test_inspect_help_and_unmatched_arguments_keep_builtin_parser(monkeypatch, args, code):
    forbidden = Mock(side_effect=AssertionError("CLI usage unexpectedly dispatched a workflow"))
    monkeypatch.setattr(cli, "run_request", forbidden)
    monkeypatch.setattr(cli, "inspect_project", forbidden)

    result = runner.invoke(cli.app, args)

    assert result.exit_code == code, result.output
    forbidden.assert_not_called()
