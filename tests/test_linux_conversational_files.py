from iceman.git_actions import GitAction
from iceman.linux_actions import LinuxAction, LinuxRequest, build_linux_plan
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


def state_for(path):
    return SessionState(workspace=path)


def test_copy_without_destination_is_recognized_but_not_executable(tmp_path):
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")

    decision = reason_request("copy readme", state_for(tmp_path))

    assert decision.action == ReasoningAction.EXECUTE_LINUX_ACTION
    assert decision.linux_action == LinuxAction.COPY
    assert decision.path == "README.md"
    assert decision.destination is None

    plan = build_linux_plan(
        tmp_path,
        LinuxRequest(
            LinuxAction.COPY,
            path=decision.path,
            destination=decision.destination,
        ),
    )

    assert not plan.safe_to_execute
    assert not plan.steps
    assert any("destination" in note.lower() for note in plan.notes)
    assert any("no command was executed" in note.lower() for note in plan.notes)


def test_copy_short_readme_resolves_unambiguously(tmp_path):
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")

    decision = reason_request(
        "copy readme to readme.bak",
        state_for(tmp_path),
    )

    assert decision.linux_action == LinuxAction.COPY
    assert decision.path == "README.md"
    assert decision.destination == "readme.bak"

    plan = build_linux_plan(
        tmp_path,
        LinuxRequest(
            LinuxAction.COPY,
            path=decision.path,
            destination=decision.destination,
        ),
    )

    assert plan.safe_to_execute
    assert plan.steps[0].changes_state
    assert plan.steps[0].command.startswith("cp -- ")
    assert "readme.bak" in plan.steps[0].command


def test_make_a_copy_phrase(tmp_path):
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")

    decision = reason_request(
        "make a copy of README.md as README.backup.md",
        state_for(tmp_path),
    )

    assert decision.linux_action == LinuxAction.COPY
    assert decision.path == "README.md"
    assert decision.destination == "README.backup.md"


def test_rename_preserves_requested_destination_exactly(tmp_path):
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")

    decision = reason_request(
        "rename readme to examplemd",
        state_for(tmp_path),
    )

    assert decision.linux_action == LinuxAction.MOVE
    assert decision.path == "README.md"
    assert decision.destination == "examplemd"

    plan = build_linux_plan(
        tmp_path,
        LinuxRequest(
            LinuxAction.MOVE,
            path=decision.path,
            destination=decision.destination,
        ),
    )

    assert plan.safe_to_execute
    assert plan.steps[0].command.startswith("mv -- ")
    assert plan.steps[0].command.endswith("/examplemd")
    assert "examplemd.md" not in plan.steps[0].command


def test_move_phrase(tmp_path):
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")

    decision = reason_request(
        "move readme to docs-readme",
        state_for(tmp_path),
    )

    assert decision.linux_action == LinuxAction.MOVE
    assert decision.path == "README.md"
    assert decision.destination == "docs-readme"


def test_change_name_phrase(tmp_path):
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")

    decision = reason_request(
        "change readme name to examplemd",
        state_for(tmp_path),
    )

    assert decision.linux_action == LinuxAction.MOVE
    assert decision.path == "README.md"
    assert decision.destination == "examplemd"


def test_typo_heavy_permissions_request_extracts_only_filename(tmp_path):
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")

    decision = reason_request(
        "make the permisons of the file readme and make it executable",
        state_for(tmp_path),
    )

    assert decision.linux_action == LinuxAction.MAKE_EXECUTABLE
    assert decision.path == "README.md"

    plan = build_linux_plan(
        tmp_path,
        LinuxRequest(
            LinuxAction.MAKE_EXECUTABLE,
            path=decision.path,
        ),
    )

    assert plan.safe_to_execute
    assert len(plan.steps) == 1
    assert "chmod +x" in plan.steps[0].command
    assert "777" not in plan.steps[0].command
    assert "readme and make it" not in plan.steps[0].command.lower()


def test_correct_permissions_phrase_extracts_only_filename(tmp_path):
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")

    decision = reason_request(
        "make the permissions of the file readme and make it executable",
        state_for(tmp_path),
    )

    assert decision.linux_action == LinuxAction.MAKE_EXECUTABLE
    assert decision.path == "README.md"


def test_ambiguous_short_filename_requires_clarification(tmp_path):
    (tmp_path / "README.md").write_text("one\n", encoding="utf-8")
    (tmp_path / "README.txt").write_text("two\n", encoding="utf-8")

    decision = reason_request(
        "copy readme to backup",
        state_for(tmp_path),
    )

    assert decision.linux_action == LinuxAction.COPY
    assert decision.requires_clarification
    assert decision.clarification_field == "path"
    assert set(decision.clarification_candidates) >= {
        "README.md",
        "README.txt",
    }


def test_dockerfile_case_resolution_is_bounded(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

    decision = reason_request(
        "copy dockerfile to Dockerfile.backup",
        state_for(tmp_path),
    )

    assert decision.linux_action == LinuxAction.COPY
    assert decision.path == "Dockerfile"


def test_compound_git_stage_commit_push_remains_intact(tmp_path):
    decision = reason_request(
        'stage everything, commit with the message "improve deployment workflow" and push to github',
        state_for(tmp_path),
    )

    assert decision.action == ReasoningAction.EXECUTE_GIT_ACTION
    assert decision.git_action == GitAction.COMMIT_PUSH
    assert decision.commit_message == "improve deployment workflow"


def test_delete_file_natural_language(tmp_path):
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")

    decision = reason_request(
        "delete notes",
        state_for(tmp_path),
    )

    assert decision.action == ReasoningAction.EXECUTE_LINUX_ACTION
    assert decision.linux_action == LinuxAction.DELETE_FILE
    assert decision.path == "notes.txt"

    plan = build_linux_plan(
        tmp_path,
        LinuxRequest(LinuxAction.DELETE_FILE, path=decision.path),
    )

    assert plan.safe_to_execute
    assert len(plan.steps) == 1
    assert plan.steps[0].changes_state
    assert plan.steps[0].command.startswith("rm -- ")
    assert " -r" not in plan.steps[0].command
    assert " -f" not in plan.steps[0].command


def test_remove_file_phrase(tmp_path):
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")

    decision = reason_request(
        "remove file notes",
        state_for(tmp_path),
    )

    assert decision.linux_action == LinuxAction.DELETE_FILE
    assert decision.path == "notes.txt"


def test_rm_phrase(tmp_path):
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")

    decision = reason_request(
        "rm notes",
        state_for(tmp_path),
    )

    assert decision.linux_action == LinuxAction.DELETE_FILE
    assert decision.path == "notes.txt"


def test_rm_recursive_flags_are_not_exposed(tmp_path):
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")

    decision = reason_request(
        "rm -rf notes",
        state_for(tmp_path),
    )

    assert decision.linux_action is None
    assert decision.action == ReasoningAction.FALLBACK


def test_delete_directory_is_refused(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()

    plan = build_linux_plan(
        tmp_path,
        LinuxRequest(LinuxAction.DELETE_FILE, path="data"),
    )

    assert not plan.safe_to_execute
    assert not plan.steps
    assert any("regular files only" in note for note in plan.notes)


def test_delete_cannot_escape_workspace(tmp_path):
    outside = tmp_path.parent / "outside-delete-test.txt"
    outside.write_text("keep\n", encoding="utf-8")

    try:
        plan = build_linux_plan(
            tmp_path,
            LinuxRequest(
                LinuxAction.DELETE_FILE,
                path="../outside-delete-test.txt",
            ),
        )

        assert not plan.safe_to_execute
        assert not plan.steps
        assert outside.exists()
    finally:
        outside.unlink(missing_ok=True)


def test_delete_git_metadata_is_blocked(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    config = git_dir / "config"
    config.write_text("[core]\n", encoding="utf-8")

    plan = build_linux_plan(
        tmp_path,
        LinuxRequest(
            LinuxAction.DELETE_FILE,
            path=".git/config",
        ),
    )

    assert not plan.safe_to_execute
    assert config.exists()


def test_delete_ambiguous_short_name_requires_clarification(tmp_path):
    (tmp_path / "notes.md").write_text("one\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("two\n", encoding="utf-8")

    decision = reason_request(
        "delete notes",
        state_for(tmp_path),
    )

    assert decision.linux_action == LinuxAction.DELETE_FILE
    assert decision.requires_clarification
    assert decision.clarification_field == "path"
    assert set(decision.clarification_candidates) >= {
        "notes.md",
        "notes.txt",
    }

def test_cli_accepts_rm_rf_as_prompt_text_without_executing(monkeypatch, tmp_path):
    from typer.testing import CliRunner
    from iceman import cli

    runner = CliRunner()

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        cli.app,
        ["ask", "rm", "-rf", "test-folder"],
    )

    assert "No such option" not in result.output
    assert "rm -rf" in result.output
    assert not (tmp_path / "test-folder").exists()
