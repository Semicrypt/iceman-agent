"""Live global CLI checks, confined to disposable local fixture repositories."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture
def live_cli(tmp_path, monkeypatch):
    executable = shutil.which("ice")
    if executable is None:
        pytest.skip("Global ice is not installed")
    monkeypatch.setenv("ICEMAN_AI_PROVIDER", "local")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("COLUMNS", "160")
    project = tmp_path / "outside-repo"
    project.mkdir()
    (project / "Dockerfile").write_text("FROM python:3\nUSER root\n")

    def invoke(words, answer=""):
        return subprocess.run([executable, *words], cwd=project, input=answer, capture_output=True, text=True, timeout=45)
    return project, invoke


@pytest.mark.parametrize("prompt", [
    "what do you think about this project?",
    "what do you think i should improve?",
    "any good suggestion?",
    "what do you think is missing?",
    "reveiw this proejct for securty vulnerabilities",
])
def test_global_ice_assessment_outside_source_checkout(live_cli, prompt):
    _, invoke = live_cli
    result = invoke(prompt.split())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Docker" in result.stdout
    assert "No CVE verification" in result.stdout


def test_global_ice_unsupported_request_has_no_execution(live_cli):
    _, invoke = live_cli
    result = invoke(["bake", "me", "a", "cake"])
    assert result.returncode == 1
    assert "not programmed" in result.stdout
    assert "Nothing was executed" in " ".join(result.stdout.split())


def test_global_ice_combined_git_only_pushes_to_local_fixture(live_cli):
    project, invoke = live_cli
    remote = project.parent / "harmless-remote.git"

    def git(*args, cwd=project):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-b", "main")
    git("init", "--bare", str(remote))
    git("config", "user.name", "Iceman Local Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("remote", "add", "origin", str(remote))
    result = invoke(["stage", "everything,", "commit", "with", "the", "message", "example", "and", "push", "to", "github"], answer="y\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("Execute the exact Git/GitHub") == 1
    assert git("rev-parse", "HEAD") == git("rev-parse", "refs/heads/main", cwd=remote)
    assert git("log", "-1", "--format=%s") == "example"
    assert git("status", "--porcelain") == ""
