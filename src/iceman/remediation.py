from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from iceman.diagnostics import DockerDiagnosis, diagnose_docker_container
from iceman.runner import run_command


@dataclass
class RemediationStep:
    title: str
    command: str | None
    explanation: str
    mutating: bool = False
    expect_output_contains: str | None = None


@dataclass
class RemediationPlan:
    target: str
    cause: str
    confidence: str
    steps: list[RemediationStep] = field(default_factory=list)
    dependency_diagnosis: DockerDiagnosis | None = None
    safe_to_apply: bool = False
    note: str | None = None
    working_dir: Path | None = None


def extract_missing_upstream(logs: str) -> str | None:
    match = re.search(
        r'host not found in upstream\s+["\']([^"\']+)["\']',
        logs,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def resolve_compose_workdir(
    diagnosis: DockerDiagnosis,
    fallback: Path,
) -> Path:
    if diagnosis.compose_working_dir:
        candidate = Path(diagnosis.compose_working_dir).expanduser()
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()

    return fallback.resolve()


def _compose_services(cwd: Path) -> set[str]:
    result = run_command("docker compose config --services", cwd=cwd)
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _find_compose_container(service: str, cwd: Path) -> str | None:
    command = (
        "docker ps -a "
        f"--filter label=com.docker.compose.service={service} "
        "--format {{.Names}}"
    )
    result = run_command(command, cwd=cwd)
    if result.returncode != 0:
        return None

    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return names[0] if names else None


def _verify_running_step(service: str) -> RemediationStep:
    return RemediationStep(
        title=f"Verify {service} is running",
        command=f"docker compose ps --status running --services {service}",
        explanation=(
            f"Require Compose to report {service} as running before continuing "
            "to dependent services."
        ),
        mutating=False,
        expect_output_contains=service,
    )


def _runtime_recovery_plan(
    diagnosis: DockerDiagnosis,
    cwd: Path,
    downstream_service: str | None = None,
) -> RemediationPlan | None:
    service = diagnosis.compose_service

    if not diagnosis.error or not service:
        return None

    if diagnosis.runtime_error_kind == "docker_socket_bind_mount":
        if diagnosis.host_docker_socket_ok is True:
            steps = [
                RemediationStep(
                    title=f"Recreate {service}",
                    command=f"docker compose up -d --force-recreate {service}",
                    explanation=(
                        "The host Docker socket is healthy and the failure occurred "
                        "while creating the old container's socket bind mount. Recreate "
                        "the container without rebuilding its image."
                    ),
                    mutating=True,
                ),
                _verify_running_step(service),
            ]

            if downstream_service and downstream_service != service:
                steps.extend(
                    [
                        RemediationStep(
                            title=f"Recreate {downstream_service}",
                            command=(
                                "docker compose up -d --force-recreate "
                                f"{downstream_service}"
                            ),
                            explanation=(
                                f"Recreate {downstream_service} only after {service} "
                                "is confirmed running so service discovery can resolve "
                                "the dependency during startup."
                            ),
                            mutating=True,
                        ),
                        _verify_running_step(downstream_service),
                    ]
                )

            return RemediationPlan(
                target=diagnosis.target,
                cause=(
                    f"Docker failed to create {service} because its docker.sock bind "
                    "mount became invalid, while the host socket itself is healthy."
                ),
                confidence="high",
                steps=steps,
                safe_to_apply=True,
                working_dir=cwd,
                note=(
                    "The image will not be rebuilt. Iceman will stop before touching "
                    "downstream services unless the recreated dependency is verified running."
                ),
            )

        return RemediationPlan(
            target=diagnosis.target,
            cause=(
                "Docker reported a docker.sock bind-mount failure and the host socket "
                "did not pass Iceman's Unix-socket check."
            ),
            confidence="high",
            steps=[],
            safe_to_apply=False,
            working_dir=cwd,
            note=(
                diagnosis.host_docker_socket_detail
                or "Repair/restart the Docker/WSL socket before recreating the container."
            ),
        )

    # Generic Docker/OCI runtime errors are intentionally not converted into
    # application-level rebuild plans.
    return RemediationPlan(
        target=diagnosis.target,
        cause=(
            "Docker reported a container-runtime error before the application could "
            "be trusted to have started."
        ),
        confidence="high",
        steps=[],
        safe_to_apply=False,
        working_dir=cwd,
        note=(
            "Iceman will not infer an image rebuild from the exit code while a "
            "Docker runtime error is present."
        ),
    )


def build_remediation_plan(
    diagnosis: DockerDiagnosis,
    cwd: Path,
) -> RemediationPlan:
    compose_cwd = resolve_compose_workdir(diagnosis, cwd)

    # Always prioritize a runtime failure on the diagnosed target.
    runtime_plan = _runtime_recovery_plan(diagnosis, compose_cwd)
    if runtime_plan:
        return runtime_plan

    upstream = extract_missing_upstream(diagnosis.log_excerpt)

    if upstream:
        services = _compose_services(compose_cwd)
        frontend_service = diagnosis.compose_service or "frontend"

        if upstream not in services:
            return RemediationPlan(
                target=diagnosis.target,
                cause=(
                    f'Nginx expects an upstream named "{upstream}", but that service '
                    "was not found in the Docker Compose configuration."
                ),
                confidence="high",
                steps=[
                    RemediationStep(
                        title="Inspect Nginx upstream configuration",
                        command=None,
                        explanation=(
                            f'Check the upstream/proxy target "{upstream}" in the Nginx '
                            "configuration and make it match a real Compose service name."
                        ),
                    )
                ],
                safe_to_apply=False,
                working_dir=compose_cwd,
                note=(
                    "Iceman will not rewrite Nginx automatically when the intended "
                    "service name is ambiguous."
                ),
            )

        dependency_container = _find_compose_container(upstream, compose_cwd)
        dependency_diag = None
        if dependency_container:
            dependency_diag = diagnose_docker_container(
                dependency_container,
                cwd=compose_cwd,
            )

        # Dependency has its own Docker runtime failure: fix that first.
        if dependency_diag and dependency_diag.error:
            dependency_runtime_plan = _runtime_recovery_plan(
                dependency_diag,
                compose_cwd,
                downstream_service=frontend_service,
            )
            if dependency_runtime_plan:
                dependency_runtime_plan.target = diagnosis.target
                dependency_runtime_plan.dependency_diagnosis = dependency_diag
                dependency_runtime_plan.cause = (
                    f'Nginx cannot resolve "{upstream}" because the dependency '
                    f'container is unavailable. {dependency_runtime_plan.cause}'
                )
                return dependency_runtime_plan

        if dependency_diag and dependency_diag.status == "running":
            return RemediationPlan(
                target=diagnosis.target,
                cause=(
                    f'Nginx previously could not resolve Compose service "{upstream}", '
                    "but that dependency is now running."
                ),
                confidence="high",
                steps=[
                    RemediationStep(
                        title=f"Recreate {frontend_service}",
                        command=(
                            "docker compose up -d --force-recreate "
                            f"{frontend_service}"
                        ),
                        explanation=(
                            "Recreate the Nginx/frontend container so it resolves the "
                            "now-available upstream during startup."
                        ),
                        mutating=True,
                    ),
                    _verify_running_step(frontend_service),
                ],
                dependency_diagnosis=dependency_diag,
                safe_to_apply=True,
                working_dir=compose_cwd,
            )

        if dependency_diag:
            # No Docker runtime error exists. Do not rebuild merely because exit
            # code 127 appears; recreation is the bounded first recovery attempt.
            return RemediationPlan(
                target=diagnosis.target,
                cause=(
                    f'Nginx cannot resolve "{upstream}" because the dependency is '
                    f'not running (last exit code: {dependency_diag.exit_code}).'
                ),
                confidence="medium",
                steps=[
                    RemediationStep(
                        title=f"Recreate {upstream}",
                        command=(
                            "docker compose up -d --force-recreate "
                            f"{upstream}"
                        ),
                        explanation=(
                            "Try a container recreation first. Iceman will not rebuild "
                            "the image from exit code alone."
                        ),
                        mutating=True,
                    ),
                    _verify_running_step(upstream),
                    RemediationStep(
                        title=f"Recreate {frontend_service}",
                        command=(
                            "docker compose up -d --force-recreate "
                            f"{frontend_service}"
                        ),
                        explanation=(
                            f"Only after {upstream} is confirmed running, recreate "
                            "the frontend/Nginx service."
                        ),
                        mutating=True,
                    ),
                    _verify_running_step(frontend_service),
                ],
                dependency_diagnosis=dependency_diag,
                safe_to_apply=True,
                working_dir=compose_cwd,
                note=(
                    "If the dependency fails recreation, Iceman stops before changing "
                    "the frontend and a deeper dependency diagnosis is required."
                ),
            )

        return RemediationPlan(
            target=diagnosis.target,
            cause=(
                f'Nginx cannot resolve Compose upstream "{upstream}", and no existing '
                "container was found for that Compose service."
            ),
            confidence="high",
            steps=[
                RemediationStep(
                    title=f"Create/start {upstream}",
                    command=f"docker compose up -d {upstream}",
                    explanation=f'Create/start Compose service "{upstream}".',
                    mutating=True,
                ),
                _verify_running_step(upstream),
                RemediationStep(
                    title=f"Recreate {frontend_service}",
                    command=(
                        "docker compose up -d --force-recreate "
                        f"{frontend_service}"
                    ),
                    explanation=(
                        "Recreate the frontend only after the upstream service is "
                        "confirmed running."
                    ),
                    mutating=True,
                ),
                _verify_running_step(frontend_service),
            ],
            safe_to_apply=True,
            working_dir=compose_cwd,
        )

    return RemediationPlan(
        target=diagnosis.target,
        cause=(
            "Iceman does not yet have a deterministic remediation rule for the "
            "latest diagnosis."
        ),
        confidence="low",
        steps=[],
        safe_to_apply=False,
        working_dir=compose_cwd,
        note=(
            "No changes will be proposed until Iceman has enough evidence for a "
            "bounded remediation."
        ),
    )
