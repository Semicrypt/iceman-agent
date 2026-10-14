from pathlib import Path

from typer.testing import CliRunner

from iceman import cli


runner = CliRunner()


def test_check_without_arguments_preserves_project_assessment(
    monkeypatch,
    tmp_path,
):
    seen = {}

    def fake_assess(path):
        seen["path"] = Path(path)
        return object()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "assess_project", fake_assess)
    monkeypatch.setattr(
        cli,
        "show_project_assessment",
        lambda assessment: None,
    )
    monkeypatch.setattr(
        cli,
        "run_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Natural-language router should not run.")
        ),
    )

    result = runner.invoke(cli.app, ["check"])

    assert result.exit_code == 0, result.output
    assert seen["path"] == tmp_path.resolve()


def test_check_existing_directory_preserves_project_assessment(
    monkeypatch,
    tmp_path,
):
    project = tmp_path / "project"
    project.mkdir()

    seen = {}

    def fake_assess(path):
        seen["path"] = Path(path)
        return object()

    monkeypatch.setattr(cli, "assess_project", fake_assess)
    monkeypatch.setattr(
        cli,
        "show_project_assessment",
        lambda assessment: None,
    )
    monkeypatch.setattr(
        cli,
        "run_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Natural-language router should not run.")
        ),
    )

    result = runner.invoke(
        cli.app,
        ["check", str(project)],
    )

    assert result.exit_code == 0, result.output
    assert seen["path"] == project.resolve()


def test_check_permissions_routes_to_natural_language(
    monkeypatch,
    tmp_path,
):
    seen = {}

    monkeypatch.chdir(tmp_path)

    def fake_run(request, project_path):
        seen["request"] = request
        seen["project_path"] = Path(project_path)
        return 0

    monkeypatch.setattr(cli, "run_request", fake_run)
    monkeypatch.setattr(
        cli,
        "assess_project",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Project assessment should not run.")
        ),
    )

    result = runner.invoke(
        cli.app,
        ["check", "permissions", "of", "readme"],
    )

    assert result.exit_code == 0, result.output
    assert seen["request"] == "check permissions of readme"
    assert seen["project_path"] == tmp_path.resolve()
    assert "Request: check permissions of readme" in result.output


def test_check_multiword_literal_routes_to_natural_language(
    monkeypatch,
    tmp_path,
):
    seen = {}

    monkeypatch.chdir(tmp_path)

    def fake_run(request, project_path):
        seen["request"] = request
        return 0

    monkeypatch.setattr(cli, "run_request", fake_run)

    result = runner.invoke(
        cli.app,
        ["check", "permissions of readme"],
    )

    assert result.exit_code == 0, result.output
    assert seen["request"] == "check permissions of readme"
