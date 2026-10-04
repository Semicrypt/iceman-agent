from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from iceman.runner import run_command


@dataclass
class Finding:
    severity: str
    title: str
    detail: str


@dataclass
class ApplicationSignal:
    subject: str
    state: str
    count: int
    category: str = "monitored_target"


@dataclass
class DockerDiagnosis:
    target: str
    found: bool = False
    container_id: str | None = None
    image: str | None = None
    status: str | None = None
    running: bool | None = None
    restart_count: int | None = None
    exit_code: int | None = None
    error: str | None = None
    health: str | None = None
    compose_service: str | None = None
    compose_project: str | None = None
    compose_working_dir: str | None = None
    runtime_error_kind: str | None = None
    application_logs_reliable: bool = True
    host_docker_socket_ok: bool | None = None
    host_docker_socket_detail: str | None = None
    ports: list[str] = field(default_factory=list)
    mounts: list[str] = field(default_factory=list)
    log_excerpt: str = ""
    application_signals: list[ApplicationSignal] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    evidence_commands: list[str] = field(default_factory=list)


def _docker_json(command: str, cwd: Path | None = None):
    result = run_command(command, cwd=cwd)
    if result.returncode != 0:
        return None, result
    try:
        return json.loads(result.stdout), result
    except json.JSONDecodeError:
        return None, result


def _compact_logs(text: str, max_lines: int = 80) -> str:
    lines = text.strip().splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return "\n".join(lines)


def classify_runtime_error(error: str | None) -> str | None:
    if not error:
        return None

    lower = error.lower()

    if (
        "docker.sock" in lower
        and "error mounting" in lower
        and "not a directory" in lower
    ):
        return "docker_socket_bind_mount"

    if "error mounting" in lower or "mount src=" in lower:
        return "bind_mount"

    if "oci runtime create failed" in lower or "runc create failed" in lower:
        return "oci_runtime"

    return "docker_runtime"


def inspect_host_docker_socket(path: str = "/var/run/docker.sock") -> tuple[bool, str]:
    try:
        st = os.stat(path)
    except OSError as exc:
        return False, f"{path} is not accessible: {exc}"

    if not stat.S_ISSOCK(st.st_mode):
        return False, f"{path} exists but is not a Unix socket."

    mode = stat.S_IMODE(st.st_mode)
    return True, f"{path} is a Unix socket with mode {mode:o}."



_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

_APPLICATION_STATUS_RE = re.compile(
    r"""
    (?:
        ❌|⚠️|⚠|ERROR:|WARN(?:ING)?:
    )?
    \s*
    (?P<subject>[^|\n]{1,100}?)
    \s*\|\s*
    (?P<state>DOWN|FAILED|ERROR|UNHEALTHY|OFFLINE)
    \b
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


def extract_application_signals(logs: str) -> list[ApplicationSignal]:
    """
    Aggregate application-reported monitored-target failures.

    These signals describe what the application says about dependencies or
    monitored targets. They do not, by themselves, prove the container or
    deployment is unhealthy.
    """
    counts: dict[tuple[str, str], int] = {}

    for raw_line in logs.splitlines():
        line = _ANSI_ESCAPE_RE.sub("", raw_line).strip()
        if not line:
            continue

        match = _APPLICATION_STATUS_RE.search(line)
        if not match:
            continue

        subject = match.group("subject").strip(" -:[]")
        state = match.group("state").upper()

        # Avoid turning generic log-level prefixes into fake target names.
        if not subject or subject.lower() in {
            "error",
            "warning",
            "warn",
            "status",
        }:
            continue

        key = (subject, state)
        counts[key] = counts.get(key, 0) + 1

    signals = [
        ApplicationSignal(
            subject=subject,
            state=state,
            count=count,
        )
        for (subject, state), count in counts.items()
    ]
    signals.sort(key=lambda item: (-item.count, item.subject.lower()))
    return signals


def _summarize_application_signals(
    diagnosis: DockerDiagnosis,
) -> None:
    if not diagnosis.application_signals:
        return

    repeated = [
        signal
        for signal in diagnosis.application_signals
        if signal.count >= 2
    ]

    if repeated:
        description = "; ".join(
            f"{signal.subject}: {signal.state} ×{signal.count}"
            for signal in repeated[:6]
        )
        diagnosis.findings.append(
            Finding(
                "warning",
                "Repeated application-level failure signals",
                (
                    f"Recent application logs repeatedly report {description}. "
                    "These are application-reported monitored-target/dependency "
                    "statuses, not proof that the container itself is unhealthy."
                ),
            )
        )
        return

    description = "; ".join(
        f"{signal.subject}: {signal.state}"
        for signal in diagnosis.application_signals[:6]
    )
    diagnosis.findings.append(
        Finding(
            "warning",
            "Application-level failure signals",
            (
                f"Recent application logs report {description}. "
                "Treat these as application/dependency warnings separately from "
                "Docker container health."
            ),
        )
    )

def _analyze_logs(logs: str, diagnosis: DockerDiagnosis) -> None:
    lower = logs.lower()

    patterns = [
        (
            ("nginx", "[emerg]"),
            "high",
            "Nginx startup failure",
            "The logs contain an Nginx emergency-level startup error. Check the Nginx configuration and referenced files.",
        ),
        (
            ("connection refused",),
            "high",
            "Connection refused",
            "A dependency rejected a network connection. Check the target host/port, service availability, Docker networking, and environment variables.",
        ),
        (
            ("econnrefused",),
            "high",
            "Application connection refused",
            "The application reported ECONNREFUSED. A configured dependency such as a database or API is not reachable at the configured address.",
        ),
        (
            ("permission denied",),
            "high",
            "Permission error",
            "The container logs show a permission-denied error. Check file ownership, mounted-volume permissions, and the container user.",
        ),
        (
            ("address already in use",),
            "high",
            "Port already in use",
            "A process could not bind to a port because the address is already in use.",
        ),
        (
            ("no such file or directory",),
            "medium",
            "Missing file",
            "The container references a file or executable that does not exist at runtime.",
        ),
        (
            ("module not found",),
            "medium",
            "Missing application dependency",
            "The application reported a missing module. Check image build steps and dependency installation.",
        ),
        (
            ("cannot find module",),
            "medium",
            "Missing Node.js module",
            "Node.js could not find a required module. Check package installation and image build layers.",
        ),
        (
            ("syntax error",),
            "medium",
            "Configuration or application syntax error",
            "A syntax error appears in the logs. Inspect the referenced file and line number.",
        ),
    ]

    for needles, severity, title, detail in patterns:
        if all(needle in lower for needle in needles):
            diagnosis.findings.append(Finding(severity, title, detail))


def diagnose_docker_container(target: str, cwd: Path | None = None) -> DockerDiagnosis:
    diagnosis = DockerDiagnosis(target=target)

    inspect_data, inspect_result = _docker_json(
        f"docker inspect {target}",
        cwd=cwd,
    )
    diagnosis.evidence_commands.append(f"docker inspect {target}")

    if not inspect_data or not isinstance(inspect_data, list):
        diagnosis.findings.append(
            Finding(
                "high",
                "Container not found",
                inspect_result.stderr.strip() or f"Docker could not inspect '{target}'.",
            )
        )
        return diagnosis

    item = inspect_data[0]
    diagnosis.found = True
    diagnosis.container_id = str(item.get("Id", ""))[:12] or None

    config = item.get("Config") or {}
    state = item.get("State") or {}
    network_settings = item.get("NetworkSettings") or {}

    diagnosis.image = config.get("Image") or item.get("Image")

    labels = config.get("Labels") or {}
    if isinstance(labels, dict):
        diagnosis.compose_service = labels.get("com.docker.compose.service")
        diagnosis.compose_project = labels.get("com.docker.compose.project")
        diagnosis.compose_working_dir = labels.get(
            "com.docker.compose.project.working_dir"
        )

    diagnosis.status = state.get("Status")
    diagnosis.running = state.get("Running")
    diagnosis.restart_count = item.get("RestartCount", 0)
    diagnosis.exit_code = state.get("ExitCode")
    diagnosis.error = state.get("Error") or None
    diagnosis.runtime_error_kind = classify_runtime_error(diagnosis.error)

    # A Docker/OCI create error means the current application process may never
    # have started. `docker logs` can therefore contain output from an older run.
    if diagnosis.runtime_error_kind:
        diagnosis.application_logs_reliable = False

    if diagnosis.runtime_error_kind == "docker_socket_bind_mount":
        ok, detail = inspect_host_docker_socket()
        diagnosis.host_docker_socket_ok = ok
        diagnosis.host_docker_socket_detail = detail

    health_obj = state.get("Health") or {}
    if isinstance(health_obj, dict):
        diagnosis.health = health_obj.get("Status")

    ports_obj = network_settings.get("Ports") or {}
    for container_port, bindings in ports_obj.items():
        if not bindings:
            diagnosis.ports.append(container_port)
            continue
        for binding in bindings:
            host_ip = binding.get("HostIp", "")
            host_port = binding.get("HostPort", "")
            diagnosis.ports.append(f"{host_ip}:{host_port} -> {container_port}")

    mounts_obj = item.get("Mounts") or []
    for mount in mounts_obj:
        source = mount.get("Source", "?")
        destination = mount.get("Destination", "?")
        mode = "rw" if mount.get("RW") else "ro"
        diagnosis.mounts.append(f"{source} -> {destination} ({mode})")

    logs_result = run_command(f"docker logs --tail 100 {target}", cwd=cwd)
    diagnosis.evidence_commands.append(f"docker logs --tail 100 {target}")
    combined_logs = "\n".join(
        part for part in (logs_result.stdout, logs_result.stderr) if part
    )
    diagnosis.log_excerpt = _compact_logs(combined_logs)

    status = (diagnosis.status or "").lower()
    restart_count = diagnosis.restart_count or 0
    exit_code = diagnosis.exit_code

    if status == "restarting":
        diagnosis.findings.append(
            Finding(
                "high",
                "Restart loop",
                f"The container is in Docker's restarting state and has restarted {restart_count} time(s).",
            )
        )
    elif status == "exited":
        severity = "medium" if exit_code == 0 else "high"
        diagnosis.findings.append(
            Finding(
                severity,
                "Container is stopped",
                f"The container has exited with code {exit_code}.",
            )
        )
    elif status == "running":
        diagnosis.findings.append(
            Finding(
                "info",
                "Container is running",
                "Docker reports the container as running.",
            )
        )

    if restart_count >= 3:
        diagnosis.findings.append(
            Finding(
                "high",
                "Repeated restarts",
                f"Docker reports {restart_count} restarts, indicating a persistent startup/runtime failure.",
            )
        )

    # Runtime errors outrank exit-code interpretation because Docker may have
    # failed before the configured application process began.
    if diagnosis.error:
        diagnosis.findings.append(
            Finding(
                "high",
                "Docker runtime error",
                diagnosis.error,
            )
        )

        if diagnosis.runtime_error_kind == "docker_socket_bind_mount":
            socket_detail = diagnosis.host_docker_socket_detail or "Socket state unknown."
            diagnosis.findings.append(
                Finding(
                    "high",
                    "Docker socket bind-mount failure",
                    (
                        "Docker failed while mounting /var/run/docker.sock into the "
                        f"container. Host check: {socket_detail}"
                    ),
                )
            )

        diagnosis.findings.append(
            Finding(
                "info",
                "Exit code interpretation suppressed",
                (
                    f"Exit code {exit_code} is not being used to infer an application "
                    "failure because Docker reported a container-runtime error first."
                ),
            )
        )
    elif isinstance(exit_code, int) and exit_code not in (0, None):
        exit_hints = {
            1: "Generic application/startup failure.",
            126: "Command found but could not be executed.",
            127: "Command or executable was not found.",
            137: "Process was killed (often SIGKILL; memory pressure is one possible cause).",
            139: "Process terminated with a segmentation fault.",
            143: "Process received SIGTERM.",
        }
        diagnosis.findings.append(
            Finding(
                "medium",
                f"Non-zero exit code {exit_code}",
                exit_hints.get(
                    exit_code,
                    "The container's main process returned a failure exit code.",
                ),
            )
        )

    if diagnosis.health and diagnosis.health != "healthy":
        diagnosis.findings.append(
            Finding(
                "high",
                f"Health status: {diagnosis.health}",
                "The configured Docker health check is not currently healthy.",
            )
        )

    if diagnosis.log_excerpt:
        if diagnosis.application_logs_reliable:
            _analyze_logs(diagnosis.log_excerpt, diagnosis)
            diagnosis.application_signals = extract_application_signals(
                diagnosis.log_excerpt
            )
            _summarize_application_signals(diagnosis)
        else:
            diagnosis.findings.append(
                Finding(
                    "info",
                    "Application logs may be stale",
                    (
                        "Docker failed during container/runtime initialization. Recent "
                        "docker logs may belong to an earlier successful container run "
                        "and are not being used for root-cause inference."
                    ),
                )
            )

    if not diagnosis.findings:
        diagnosis.findings.append(
            Finding(
                "info",
                "No obvious failure signature",
                "The basic container state and recent logs did not match Iceman's current diagnostic rules.",
            )
        )

    return diagnosis
