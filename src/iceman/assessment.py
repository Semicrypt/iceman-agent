from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from iceman.diagnostics import DockerDiagnosis, diagnose_docker_container
from iceman.inspector import Inspection, inspect_project
from iceman.runner import run_command


@dataclass
class AssessmentIssue:
    severity: str
    title: str
    detail: str
    target: str | None = None


@dataclass
class ServiceAssessment:
    service: str
    container: str | None
    diagnosis: DockerDiagnosis | None
    severity: str
    summary: str


@dataclass
class ProjectAssessment:
    workspace: Path
    inspection: Inspection
    compose_detected: bool
    compose_services: list[str] = field(default_factory=list)
    services: list[ServiceAssessment] = field(default_factory=list)
    issues: list[AssessmentIssue] = field(default_factory=list)
    git_changes: int = 0

    @property
    def high_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "high")

    @property
    def medium_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "medium")

    @property
    def healthy(self) -> bool:
        return self.high_count == 0 and self.medium_count == 0

    @property
    def application_warning_count(self) -> int:
        subjects: set[tuple[str, str]] = set()
        for service in self.services:
            if service.diagnosis is None:
                continue
            for signal in service.diagnosis.application_signals:
                subjects.add((signal.subject, signal.state))
        return len(subjects)

    @property
    def top_issue(self) -> AssessmentIssue | None:
        rank = {"high": 0, "medium": 1, "info": 2}
        if not self.issues:
            return None
        return sorted(
            self.issues,
            key=lambda issue: rank.get(issue.severity, 99),
        )[0]


def _compose_services(workspace: Path) -> list[str]:
    result = run_command("docker compose config --services", cwd=workspace)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _container_for_service(service: str, workspace: Path) -> str | None:
    result = run_command(
        f"docker compose ps -a -q {service}",
        cwd=workspace,
    )
    if result.returncode != 0:
        return None

    ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return ids[0] if ids else None


def _service_severity(diagnosis: DockerDiagnosis) -> tuple[str, str]:
    status = (diagnosis.status or "unknown").lower()

    if diagnosis.error:
        return "high", "Docker runtime error"

    if status == "restarting":
        return "high", f"restart loop ({diagnosis.restart_count or 0} restarts)"

    if status == "exited":
        if diagnosis.exit_code == 0:
            return "medium", "container is stopped"
        return "high", f"container exited with code {diagnosis.exit_code}"

    if diagnosis.health and diagnosis.health != "healthy":
        return "high", f"health={diagnosis.health}"

    if status == "running":
        return "info", "running"

    return "medium", f"status={status}"


def assess_project(workspace: Path) -> ProjectAssessment:
    workspace = workspace.resolve()
    inspection = inspect_project(workspace)
    technologies = set(inspection.technologies)
    compose_detected = "Docker Compose" in technologies

    assessment = ProjectAssessment(
        workspace=workspace,
        inspection=inspection,
        compose_detected=compose_detected,
    )

    # Dirty Git state is context, not automatically a failure.
    if "Git" in technologies:
        git_result = run_command("git status --porcelain", cwd=workspace)
        if git_result.returncode == 0:
            assessment.git_changes = len(
                [line for line in git_result.stdout.splitlines() if line.strip()]
            )

    if not compose_detected:
        return assessment

    services = _compose_services(workspace)
    assessment.compose_services = services

    if not services:
        assessment.issues.append(
            AssessmentIssue(
                severity="medium",
                title="Compose configuration unavailable",
                detail=(
                    "Docker Compose was detected in the project, but Iceman could "
                    "not resolve any Compose services."
                ),
            )
        )
        return assessment

    for service in services:
        container = _container_for_service(service, workspace)

        if not container:
            service_result = ServiceAssessment(
                service=service,
                container=None,
                diagnosis=None,
                severity="medium",
                summary="no container exists",
            )
            assessment.services.append(service_result)
            assessment.issues.append(
                AssessmentIssue(
                    severity="medium",
                    title=f"{service}: no container",
                    detail=(
                        f'Compose service "{service}" exists but has no current '
                        "container."
                    ),
                    target=service,
                )
            )
            continue

        diagnosis = diagnose_docker_container(container, cwd=workspace)
        severity, summary = _service_severity(diagnosis)

        assessment.services.append(
            ServiceAssessment(
                service=service,
                container=container,
                diagnosis=diagnosis,
                severity=severity,
                summary=summary,
            )
        )

        if severity in {"high", "medium"}:
            assessment.issues.append(
                AssessmentIssue(
                    severity=severity,
                    title=f"{service}: {summary}",
                    detail=(
                        f"Container {diagnosis.target} is {diagnosis.status}; "
                        f"exit={diagnosis.exit_code}, "
                        f"restarts={diagnosis.restart_count or 0}."
                    ),
                    target=diagnosis.target,
                )
            )

    return assessment
