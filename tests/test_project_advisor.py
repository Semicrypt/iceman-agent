import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from iceman.project_advisor import ScanLimits, assess_project


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('fixture')\n")
    (tmp_path / "Dockerfile").write_text("FROM python:latest\nUSER root\n")
    (tmp_path / "compose.yaml").write_text("services:\n  app:\n    privileged: true\n")
    return tmp_path


def test_review_uses_static_evidence_and_locations(project, monkeypatch):
    monkeypatch.setattr("subprocess.run", Mock(side_effect=AssertionError("Project scan ran a command")))
    result = assess_project(project)
    assert {"Python", "Docker", "Compose"}.issubset(result.stacks)
    finding = next(f for f in result.findings if f.code == "privileged")
    assert finding.certainty == "observed"
    assert finding.evidence[0].path == "compose.yaml"
    assert finding.evidence[0].line == 3
    assert "No CVE verification" in result.render()
    assert "did not run tests" in result.render()


def test_missing_safeguards_are_suggestions(project):
    result = assess_project(project, "missing")
    missing = next(f for f in result.findings if f.code == "tests-not-found")
    assert missing.certainty == "suggestion"
    assert "bounded scan" in missing.explanation
    assert "may exist elsewhere" in result.render()


def test_secret_values_never_leave_evidence(project):
    secret = "live_credential_value_918273645"
    (project / "settings.py").write_text(f'API_KEY = "{secret}"\n')
    (project / ".env").write_text(f"TOKEN={secret}\n")
    result = assess_project(project, "security")
    assert any(f.code == "possible-secret" for f in result.findings)
    exported = result.render() + result.follow_up("evidence") + json.dumps(result.to_safe_dict())
    assert secret not in exported
    assert "validity is unverified" in exported


def test_symlinks_are_not_followed(project, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.py")
    outside.write_text('API_KEY = "must_not_be_read_secret"\n')
    try:
        (project / "linked.py").symlink_to(outside)
        result = assess_project(project)
        assert not any(f.code == "possible-secret" for f in result.findings)
        assert "Symbolic links were skipped." in result.limitations
    finally:
        outside.unlink()


def test_scan_limits_are_visible(project):
    result = assess_project(project, limits=ScanLimits(max_files=1))
    assert result.files_inspected <= 1
    assert any("limit" in text for text in result.limitations)


def test_empty_folder_has_natural_boundary(tmp_path):
    result = assess_project(tmp_path)
    assert not result.recognizable
    assert "couldn't identify" in result.render()
    assert "Docker/Compose" in result.render()


def test_scanner_presence_is_not_claimed_execution(project, monkeypatch):
    monkeypatch.setattr("iceman.project_advisor.shutil.which", lambda name: "/tools/" + name if name == "trivy" else None)
    report = assess_project(project).render()
    assert "Available but not run: trivy" in report
    assert "Optional scanners not found: checkov" in report


@pytest.mark.parametrize("prompt", ["why", "show me the evidence", "go deeper", "what else", "what should I do first", "fix the highest risk"])
def test_followups_are_read_only(project, prompt):
    before = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    answer = assess_project(project).follow_up(prompt)
    after = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert answer
    assert before == after
    if prompt.startswith("fix"):
        assert "haven't changed" in answer


def test_iac_and_ci_only_report_observed_configuration(project):
    (project / "main.tf").write_text('resource "aws_instance" "app" {\n publicly_accessible = true\n}\n')
    workflows = project / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("permissions: write-all\njobs: {}\n")
    result = assess_project(project)
    assert "AWS IaC" in result.stacks
    assert {"public-cloud-resource", "ci-write-all", "cloud-cost-review"}.issubset({f.code for f in result.findings})
    assert not any("$" in f.recommendation for f in result.findings)
