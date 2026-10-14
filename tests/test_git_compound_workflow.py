"""Compound Git safety plus an entirely local/bare-remote integration fixture."""
import re
import shlex
import shutil
import subprocess

import pytest

from iceman import git_actions as git
from iceman.runner import CommandResult


@pytest.fixture
def git_fixture(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("print('hello')\n")
    state = {"status": " M app.py\0", "staged": "staged before approval",
             "head": "a" * 40, "branch": "main",
             "remote": "remote.origin.url /harmless/local/remote.git\n", "top": str(root),
             "destination": "/harmless/local/remote.git\n"}
    reads = []

    def fake_run(workspace, command):
        reads.append(command)
        outputs = {
            "git rev-parse --is-inside-work-tree": "true\n",
            "git rev-parse --show-toplevel": state["top"] + "\n",
            "git branch --show-current": state["branch"] + "\n",
            "git remote": "origin\n",
            "git status --porcelain=v1 -z --untracked-files=all": state["status"],
            "git diff --cached --binary": state["staged"],
            "git rev-parse --verify HEAD": state["head"] + "\n",
            "git config --get-regexp '^remote\\.origin\\.'": state["remote"],
            "git remote get-url --push --all origin": state["destination"],
        }
        return CommandResult(command, 0, outputs.get(command, ""), "")
    monkeypatch.setattr(git, "_run", fake_run)
    return root, state, reads


def make_plan(root, message="example"):
    return git.build_git_plan(root, git.GitRequest(git.GitAction.COMMIT_PUSH, message=message))


def test_one_combined_plan_has_explicit_scope_and_destination(git_fixture):
    root, _, reads = git_fixture
    plan = make_plan(root)
    assert plan.safe_to_execute and plan.requires_approval and plan.preflight_fingerprint
    assert plan.branch == "main" and plan.remote == "origin"
    assert [step.command for step in plan.steps if step.changes_state] == [
        "git add -A", "git commit -m example",
        "git push --no-follow-tags -u origin HEAD:refs/heads/main",
    ]
    assert plan.steps[-1].command == "git ls-remote --exit-code --refs /harmless/local/remote.git refs/heads/main"
    assert not any(command.startswith(("git add ", "git commit ", "git push ", "git ls-remote ")) for command in reads)


@pytest.mark.parametrize("approval", [True, False])
def test_exact_conversational_workflow_keeps_one_plan_and_one_approval(git_fixture, monkeypatch, approval):
    from unittest.mock import Mock

    from typer.testing import CliRunner

    from iceman import cli, shell

    root, _, reads = git_fixture
    monkeypatch.chdir(root)
    monkeypatch.setenv("ICEMAN_AI_PROVIDER", "local")
    planner = Mock(wraps=git.build_git_plan)
    monkeypatch.setattr(shell, "build_git_plan", planner)
    confirm = Mock(return_value=approval)
    monkeypatch.setattr("typer.confirm", confirm)
    executor = Mock(side_effect=lambda plan, **kwargs: git.GitExecutionResult(plan, True))
    monkeypatch.setattr("iceman.presentation.execute_git_plan", executor)
    result = CliRunner().invoke(cli.app, [
        "stage", "everything,", "commit", "with", "the", "message",
        "improve deployment workflow", "and", "push", "to", "github",
    ])

    assert result.exit_code == (0 if approval else 1), result.output
    planner.assert_called_once()
    request = planner.call_args.args[1]
    assert request.action == git.GitAction.COMMIT_PUSH
    assert request.message == "improve deployment workflow"
    assert confirm.call_count == 1
    assert executor.call_count == int(approval)
    assert "git add -A" in result.output
    assert "git commit -m" in result.output
    assert "git push --no-follow-tags" in result.output
    if approval:
        plan = executor.call_args.args[0]
        assert plan.requires_approval and plan.safe_to_execute
        assert [shlex.split(step.command) for step in plan.steps if step.changes_state] == [
            ["git", "add", "-A"],
            ["git", "commit", "-m", "improve deployment workflow"],
            ["git", "push", "--no-follow-tags", "-u", "origin", "HEAD:refs/heads/main"],
        ]
    assert not any(command.startswith(("git add ", "git commit ", "git push ", "git ls-remote ")) for command in reads)


def test_commit_message_remains_literal(git_fixture):
    root, _, _ = git_fixture
    message = "  example $(touch /tmp/never); 'quoted'  "
    plan = make_plan(root, message)
    assert plan.safe_to_execute
    command = next(s.command for s in plan.steps if s.label == "Create Git commit")
    assert shlex.split(command) == ["git", "commit", "-m", message]


def test_subdirectory_cannot_preview_only_part_of_repository(git_fixture):
    root, _, _ = git_fixture
    child = root / "nested"
    child.mkdir()
    plan = make_plan(child)
    assert not plan.safe_to_execute and not plan.steps
    assert "repository root" in " ".join(plan.notes)


@pytest.mark.parametrize("status", ["UU app.py\0", "?? .env\0"])
def test_conflicts_and_sensitive_paths_block_all_steps(git_fixture, status):
    root, state, _ = git_fixture
    state["status"] = status
    plan = make_plan(root)
    assert not plan.safe_to_execute and not plan.steps


def test_symlink_is_never_followed(git_fixture):
    root, _, _ = git_fixture
    target = root.parent / "private.txt"
    target.write_text("never read this")
    (root / "app.py").unlink()
    (root / "app.py").symlink_to(target)
    plan = make_plan(root)
    assert not plan.safe_to_execute and not plan.steps
    assert "symbolic" in " ".join(plan.notes)


@pytest.mark.parametrize("relative,prefix", [("tests/fixture.py", ""), ("large.txt", "a" * (1024 * 1024 + 10))])
def test_secret_scan_includes_fixtures_and_large_text(git_fixture, monkeypatch, relative, prefix):
    root, state, _ = git_fixture
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    value = "seeded_secret_value_123"
    path.write_text(prefix + value)
    state["status"] = "?? " + relative + "\0"
    monkeypatch.setattr(git, "_SECRET_PATTERNS", [re.compile(value)])
    plan = make_plan(root)
    assert not plan.safe_to_execute and not plan.steps
    assert value not in repr(plan)


def test_nul_status_preserves_rename_names(git_fixture):
    root, state, _ = git_fixture
    state["status"] = 'R  new -> name\n"quoted".txt\0old name.txt\0?? leading space.txt\0'
    assert git._compound_paths(root) == sorted(['new -> name\n"quoted".txt', "old name.txt", "leading space.txt"])


@pytest.mark.parametrize("change", ["content", "staged", "head", "remote", "branch", "mode", "destination"])
def test_repository_drift_after_preview_prevents_execution(git_fixture, change):
    root, state, _ = git_fixture
    plan = make_plan(root)
    assert plan.safe_to_execute
    if change == "content":
        (root / "app.py").write_text("print('changed')\n")
    elif change == "mode":
        (root / "app.py").chmod(0o755)
    else:
        state[change] += "changed"
    calls = []
    result = git.execute_git_plan(plan, step_runner=lambda *args: calls.append(args))
    assert not result.success and not calls
    assert "changed after the preview" in result.error


@pytest.mark.parametrize("failed", ["Stage all approved changes", "Create Git commit", "Push current branch to origin"])
def test_failure_stops_chain_with_honest_partial_state(git_fixture, failed):
    root, _, _ = git_fixture
    plan = make_plan(root)
    calls = []

    def runner(step, index, total):
        calls.append(step.label)
        return CommandResult(step.command, 1 if step.label == failed else 0, "", "")
    result = git.execute_git_plan(plan, step_runner=runner)
    assert not result.success
    assert calls[-1] == failed
    assert len(calls) == next(i for i, s in enumerate(plan.steps, 1) if s.label == failed)
    if failed == "Create Git commit":
        assert "staged" in " ".join(result.summary)
    if failed == "Push current branch to origin":
        assert "local commit was created" in " ".join(result.summary)


@pytest.mark.parametrize("matching", [True, False])
def test_success_requires_remote_revision_match(git_fixture, matching):
    root, _, _ = git_fixture
    plan = make_plan(root)
    oid = "b" * 40

    def runner(step, index, total):
        output = oid + "\n" if step.label == "Read committed revision" else ""
        if step.label == "Verify origin branch revision":
            output = (oid if matching else "c" * 40) + "\trefs/heads/main\n"
        return CommandResult(step.command, 0, output, "")
    assert git.execute_git_plan(plan, step_runner=runner).success is matching


def test_real_local_bare_remote(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git unavailable")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo, remote = tmp_path / "working", tmp_path / "remote.git"
    repo.mkdir()

    def command(*args, cwd=repo):
        return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()
    command("init", "-b", "main")
    command("init", "--bare", str(remote))
    command("config", "user.name", "Iceman Fixture")
    command("config", "user.email", "fixture@example.invalid")
    command("remote", "add", "origin", str(remote))
    (repo / "README.md").write_text("# Disposable fixture\n")
    plan = make_plan(repo, "fixture example")
    assert plan.safe_to_execute, plan.notes
    result = git.execute_git_plan(plan)
    assert result.success, result.error
    assert command("rev-parse", "HEAD") == command("rev-parse", "refs/heads/main", cwd=remote)
    assert command("status", "--porcelain") == ""
