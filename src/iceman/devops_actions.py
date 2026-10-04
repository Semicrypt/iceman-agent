from __future__ import annotations

import json
import re
import shlex
import socket
from dataclasses import dataclass, field
import time
from typing import Callable
from enum import Enum
from pathlib import Path

from iceman.runner import CommandResult, run_command


class DevOpsAction(str, Enum):
    DOCKER_BUILD = "docker_build"
    DOCKER_RUN = "docker_run"
    DOCKER_LIST = "docker_list"
    DOCKER_IMAGES = "docker_images"
    DOCKER_LOGS = "docker_logs"
    DOCKER_STOP = "docker_stop"
    DOCKER_RESTART = "docker_restart"
    DOCKER_DIAGNOSE = "docker_diagnose"
    DOCKER_HEALTH = "docker_health"
    DOCKER_PORTS = "docker_ports"
    DOCKER_CONNECTIVITY = "docker_connectivity"
    DOCKER_SECURITY = "docker_security"
    DOCKER_OPTIMIZE = "docker_optimize"
    DOCKER_REBUILD_RESTART = "docker_rebuild_restart"
    DOCKER_GUIDED_REPAIR = "docker_guided_repair"
    DOCKER_COMPOSE_STATUS = "docker_compose_status"

    K8S_CONTEXT = "k8s_context"
    K8S_CONTEXTS = "k8s_contexts"
    K8S_NAMESPACES = "k8s_namespaces"
    K8S_VALIDATE = "k8s_validate"
    K8S_DEPLOY = "k8s_deploy"
    K8S_PODS = "k8s_pods"
    K8S_DIAGNOSE_POD = "k8s_diagnose_pod"
    K8S_DIAGNOSE_SERVICE = "k8s_diagnose_service"
    K8S_SERVICE_CONNECTIVITY = "k8s_service_connectivity"
    K8S_DIAGNOSE_DEPLOYMENT = "k8s_diagnose_deployment"
    K8S_DEPLOYMENTS = "k8s_deployments"
    K8S_SERVICES = "k8s_services"
    K8S_ROLLOUT = "k8s_rollout"
    K8S_SCALE = "k8s_scale"
    K8S_LOGS = "k8s_logs"
    K8S_DESCRIBE = "k8s_describe"
    K8S_RESTART = "k8s_restart"

    TERRAFORM_INIT = "terraform_init"
    TERRAFORM_FMT = "terraform_fmt"
    TERRAFORM_VALIDATE = "terraform_validate"
    TERRAFORM_PLAN = "terraform_plan"
    TERRAFORM_EXPLAIN_PLAN = "terraform_explain_plan"
    TERRAFORM_STATE = "terraform_state"


@dataclass(frozen=True)
class DevOpsRequest:
    action: DevOpsAction
    component: str | None = None
    image: str | None = None
    startup_target: str | None = None
    port: int | None = None
    replicas: int | None = None
    context: str | None = None
    terraform_dir: str | None = None


@dataclass(frozen=True)
class DevOpsStep:
    label: str
    command: str
    changes_state: bool = False
    stream_output: bool = True


@dataclass
class DevOpsPlan:
    request: DevOpsRequest
    workspace: Path
    title: str
    steps: list[DevOpsStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    safe_to_execute: bool = True
    requires_approval: bool = False
    inferred_component: str | None = None
    inferred_image: str | None = None
    target_context: str | None = None
    context_inferred: bool = False
    target_namespace: str | None = None


@dataclass(frozen=True)
class DevOpsStepResult:
    step: DevOpsStep
    result: CommandResult


@dataclass
class DevOpsExecutionResult:
    plan: DevOpsPlan
    success: bool
    step_results: list[DevOpsStepResult] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    diagnosis: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0
    interrupted: bool = False


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_SAFE_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,254}$")
_SAFE_CONTEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/+\-]{0,255}$")
_SAFE_STARTUP_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,127}$")


def _quote(value: str) -> str:
    return shlex.quote(value)


def _safe_name(value: str | None, *, field_name: str = "name") -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not _SAFE_NAME.fullmatch(value):
        raise ValueError(
            f"{field_name} may contain only letters, digits, '.', '_' and '-'."
        )
    return value


def _safe_image(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not _SAFE_IMAGE.fullmatch(value):
        raise ValueError("Unsafe container image reference.")
    return value


def _safe_startup_target(value: str | None) -> str | None:
    """Validate an explicit application startup file supplied by the operator."""
    if value is None:
        return None

    value = value.strip()
    candidate = Path(value)

    if (
        not value
        or not _SAFE_STARTUP_TARGET.fullmatch(value)
        or candidate.is_absolute()
        or ".." in candidate.parts
        or "\\" in value
    ):
        raise ValueError(
            "Startup target must be a safe relative application path such as "
            "server.js or dist/server.js."
        )

    return value


def _safe_context(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not _SAFE_CONTEXT.fullmatch(value):
        raise ValueError("Unsafe Kubernetes context name.")
    return value


def _validate_port(port: int | None) -> int | None:
    if port is None:
        return None
    value = int(port)
    if not 1 <= value <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return value


def _validate_replicas(replicas: int | None) -> int | None:
    if replicas is None:
        return None
    value = int(replicas)
    if not 1 <= value <= 100:
        raise ValueError("Replicas must be between 1 and 100.")
    return value


def _component_dir(workspace: Path, component: str | None) -> Path:
    if component is None:
        return workspace
    candidate = (workspace / component).resolve()
    try:
        candidate.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError("Component path escapes the workspace.") from exc
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError(f"Component directory '{component}' was not found.")
    return candidate


def _package_name(component_dir: Path) -> str | None:
    path = component_dir / "package.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name.strip():
        return None
    sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name.strip()).strip("-")
    return sanitized or None


def _dockerfile_exposed_port(component_dir: Path) -> int | None:
    path = component_dir / "Dockerfile"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r"(?mi)^\s*EXPOSE\s+(\d{1,5})\b", text)
    if not match:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 65535 else None


def _docker_image_exposed_port(workspace: Path, image: str) -> int | None:
    try:
        result = run_command(
            f'docker image inspect --format "{{{{json .Config.ExposedPorts}}}}" {_quote(image)}',
            cwd=workspace,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    ports = []
    for key in payload:
        match = re.match(r"^(\d{1,5})/(?:tcp|udp)$", str(key))
        if match:
            value = int(match.group(1))
            if 1 <= value <= 65535:
                ports.append(value)
    return min(ports) if ports else None


def _infer_image(workspace: Path, component: str | None, explicit: str | None) -> str:
    explicit = _safe_image(explicit)
    if explicit:
        return explicit
    directory = _component_dir(workspace, component)
    package = _package_name(directory)
    base = package or component or workspace.name or "app"
    base = re.sub(r"[^a-zA-Z0-9_.-]+", "-", base).strip("-") or "app"
    return f"{base}:latest"


def _container_name(component: str | None, image: str) -> str:
    if component:
        return _safe_name(component, field_name="component") or "app"
    base = image.split("/")[-1].split(":")[0].split("@")[0]
    base = re.sub(r"[^a-zA-Z0-9_.-]+", "-", base).strip("-") or "app"
    return base[:63]


def _docker_port_owners(workspace: Path) -> dict[int, str]:
    try:
        result = run_command(
            'docker ps --format "{{.Names}}\\t{{.Ports}}"',
            cwd=workspace,
        )
    except (FileNotFoundError, OSError):
        return {}
    if result.returncode != 0:
        return {}
    owners: dict[int, str] = {}
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        name, ports = line.split("\t", 1)
        for match in re.finditer(r"(?:0\.0\.0\.0|\[::\]|127\.0\.0\.1):(\d+)->", ports):
            owners[int(match.group(1))] = name
    return owners


def _socket_port_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def _suggest_port(start: int, owners: dict[int, str]) -> int:
    for port in range(start, min(65535, start + 200)):
        if port not in owners and _socket_port_free(port):
            return port
    raise ValueError("No free host port found in the bounded search range.")



def _docker_container_brief(workspace: Path, name: str) -> dict | None:
    try:
        result = run_command(f"docker inspect {_quote(name)}", cwd=workspace)
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    item = payload[0]
    state = item.get("State") or {}
    host_config = item.get("HostConfig") or {}
    port_bindings = host_config.get("PortBindings") or {}
    host_ports: set[int] = set()
    if isinstance(port_bindings, dict):
        for bindings in port_bindings.values():
            if not isinstance(bindings, list):
                continue
            for binding in bindings:
                if isinstance(binding, dict):
                    value = str(binding.get("HostPort") or "")
                    if value.isdigit():
                        host_ports.add(int(value))
    return {
        "running": bool(state.get("Running")),
        "status": state.get("Status"),
        "image": (item.get("Config") or {}).get("Image"),
        "host_ports": host_ports,
    }


def _docker_container_exists(workspace: Path, name: str) -> bool:
    try:
        result = run_command(
            f"docker inspect {_quote(name)}",
            cwd=workspace,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def _docker_image_exists(workspace: Path, image: str) -> bool:
    try:
        result = run_command(
            f"docker image inspect {_quote(image)}",
            cwd=workspace,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def _docker_container_image(workspace: Path, container: str) -> str:
    """Return only the configured image reference for a container."""
    try:
        result = run_command(
            f"docker inspect --format {_quote('{{.Config.Image}}')} {_quote(container)}",
            cwd=workspace,
        )
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("Docker is not available for guided remediation.") from exc

    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError(
            f"Could not determine the image used by Docker container '{container}'."
        )

    return result.stdout.strip()


def _docker_running_check_command(target: str) -> str:
    """Build a bounded pass/fail runtime verification.

    Unlike a plain `docker inspect`, this command returns non-zero when the
    replacement container is no longer running after a short stabilization
    period.
    """
    fmt = "{{.State.Running}}"
    script = (
        "sleep 2; "
        f'value=$(docker inspect --format {_quote(fmt)} {_quote(target)}); '
        'test "$value" = "true"'
    )
    return "sh -c " + _quote(script)


def _env_map(values) -> dict[str, str]:
    output: dict[str, str] = {}
    if not isinstance(values, list):
        return output
    for item in values:
        if not isinstance(item, str) or "=" not in item:
            continue
        key, value = item.split("=", 1)
        output[key] = value
    return output


def _standalone_recreate_recipe(
    workspace: Path,
    container: str,
    image: str,
    *,
    require_port_mapping: bool = True,
    allow_command_override: bool = False,
) -> tuple[list[str], list[str]]:
    """Return safe docker-run args for replacing a simple standalone container.

    The recreation path is intentionally conservative. Iceman refuses to
    reconstruct containers with mounts, custom networks, runtime environment
    overrides, or command overrides because losing those settings can cause
    outages or expose secrets in a command preview.
    """
    try:
        container_result = run_command(
            f"docker inspect {_quote(container)}",
            cwd=workspace,
        )
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("Docker is not available for container recreation.") from exc
    if container_result.returncode != 0:
        raise ValueError(
            f"Docker container '{container}' was not found. Build/run it first."
        )
    try:
        container_data = json.loads(container_result.stdout)[0]
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise ValueError("Could not safely inspect the existing container.") from exc

    mounts = container_data.get("Mounts") or []
    host_config = container_data.get("HostConfig") or {}
    config = container_data.get("Config") or {}
    if mounts or host_config.get("Binds"):
        raise ValueError(
            "Automatic rebuild/restart is disabled for containers with mounts or binds. "
            "Use an explicit Compose workflow so storage is preserved visibly."
        )

    network_mode = str(host_config.get("NetworkMode") or "default")
    if network_mode not in {"default", "bridge"}:
        raise ValueError(
            f"Automatic rebuild/restart is disabled for custom Docker network mode '{network_mode}'."
        )

    existing_image = str(config.get("Image") or image)
    try:
        image_result = run_command(
            f"docker image inspect {_quote(existing_image)}",
            cwd=workspace,
        )
    except (FileNotFoundError, OSError):
        image_result = None
    if image_result is None or image_result.returncode != 0:
        raise ValueError(
            "Could not inspect the current image to verify runtime environment overrides."
        )
    try:
        image_data = json.loads(image_result.stdout)[0]
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise ValueError("Could not parse current image metadata safely.") from exc

    container_env = _env_map(config.get("Env"))
    image_env = _env_map((image_data.get("Config") or {}).get("Env"))
    runtime_env = {
        key: value
        for key, value in container_env.items()
        if image_env.get(key) != value
    }
    if runtime_env:
        raise ValueError(
            "Automatic rebuild/restart is disabled because the container has runtime "
            "environment overrides. Iceman will not copy or expose possible secrets "
            "into a generated docker run command."
        )

    image_config = image_data.get("Config") or {}
    if (
        not allow_command_override
        and (
            config.get("Cmd") != image_config.get("Cmd")
            or config.get("Entrypoint") != image_config.get("Entrypoint")
        )
    ):
        raise ValueError(
            "Automatic rebuild/restart is disabled because the container overrides "
            "the image command or entrypoint."
        )

    port_bindings = host_config.get("PortBindings") or {}
    mappings: list[str] = []
    for container_key, bindings in port_bindings.items():
        match = re.match(r"^(\d{1,5})/(tcp|udp)$", str(container_key))
        if not match or not isinstance(bindings, list):
            raise ValueError("Unsupported Docker port binding in existing container.")
        container_port, protocol = int(match.group(1)), match.group(2)
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            host_ip = str(binding.get("HostIp") or "")
            host_port = str(binding.get("HostPort") or "")
            if host_ip not in {"", "0.0.0.0", "::"} or not host_port.isdigit():
                raise ValueError("Unsupported bound host address in existing container.")
            suffix = "" if protocol == "tcp" else f"/{protocol}"
            mappings.append(f"{int(host_port)}:{container_port}{suffix}")

    if require_port_mapping and not mappings:
        raise ValueError(
            "Automatic rebuild/restart requires at least one explicit host port mapping."
        )

    args: list[str] = []
    restart_name = str((host_config.get("RestartPolicy") or {}).get("Name") or "no")
    if restart_name and restart_name != "no":
        args.extend(["--restart", restart_name])
    for mapping in mappings:
        args.extend(["-p", mapping])
    return args, mappings


def _docker_state_command(target: str) -> str:
    # No environment values or other potentially sensitive configuration are exposed.
    fmt = (
        '{{.State.Status}}|{{.State.Running}}|{{.State.ExitCode}}|'
        '{{.RestartCount}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|'
        '{{.State.Error}}'
    )
    return f"docker inspect --format {_quote(fmt)} {_quote(target)}"


def _docker_startup_hint_command(target: str) -> str:
    """Capture a bounded startup shape without exposing the full argument list.

    Docker images frequently use an entrypoint wrapper (for example
    ``docker-entrypoint.sh``) while the real application command lives in the
    first two runtime arguments (for example ``node missing.js``). Capturing only
    Path + Args[0] + Args[1] is enough to reconstruct that common case while
    avoiding a full command/environment dump. The raw result is kept hidden from
    terminal streaming and is sanitized before it is shown in diagnosis output.
    """
    fmt = (
        '{{.Path}}|'
        '{{if gt (len .Args) 0}}{{index .Args 0}}{{end}}|'
        '{{if gt (len .Args) 1}}{{index .Args 1}}{{end}}'
    )
    return f"docker inspect --format {_quote(fmt)} {_quote(target)}"


def _docker_http_probe_command(host_port: int) -> str:
    """Return a non-fatal localhost-only HTTP probe command.

    The inner curl exit code is reported as data while the trusted diagnostic
    step itself exits zero, so an expected connectivity failure can be
    summarized instead of aborting the read-only diagnostic workflow.
    """
    script = (
        f"out=$(curl -sS -o /dev/null -w 'HTTP %{{http_code}}' "
        f"--connect-timeout 3 --max-time 5 http://127.0.0.1:{host_port}/); "
        "rc=$?; printf 'curl_rc=%s|%s\\n' \"$rc\" \"$out\"; exit 0"
    )
    return "sh -c " + _quote(script)


def _docker_security_command(target: str) -> str:
    fmt = (
        'user={{if .Config.User}}{{.Config.User}}{{else}}root/default{{end}}|'
        'privileged={{.HostConfig.Privileged}}|'
        'readonly_rootfs={{.HostConfig.ReadonlyRootfs}}|'
        'network={{.HostConfig.NetworkMode}}'
    )
    return f"docker inspect --format {_quote(fmt)} {_quote(target)}"


def _docker_history_command(image: str) -> str:
    # CreatedBy can contain build arguments or other sensitive build metadata, so
    # optimization output intentionally exposes only layer IDs and sizes.
    fmt = '{{.ID}}|{{.Size}}'
    return f"docker history --format {_quote(fmt)} {_quote(image)}"

def build_guided_docker_repair_plan(
    workspace: Path,
    request: DevOpsRequest,
    previous_result: DevOpsExecutionResult | None,
) -> DevOpsPlan:
    """Build a repair from a previous evidence-backed Docker diagnosis.

    v0.9.9 intentionally supports only the failure class for which Iceman can
    construct a bounded repair without guessing: a Node.js startup entry file
    proven missing by the previous Docker diagnosis.

    The replacement entry file must be explicitly supplied by the operator.
    """
    workspace = workspace.resolve()
    plan = DevOpsPlan(
        request=request,
        workspace=workspace,
        title="Guided Docker remediation",
        requires_approval=True,
    )

    try:
        if request.action != DevOpsAction.DOCKER_GUIDED_REPAIR:
            raise ValueError("This planner only accepts docker_guided_repair.")

        if previous_result is None:
            raise ValueError(
                "No recent DevOps diagnosis is available. Diagnose the container first."
            )

        if not previous_result.success:
            raise ValueError(
                "The previous Docker diagnosis did not complete successfully. "
                "Run the diagnosis again before attempting repair."
            )

        if previous_result.plan.request.action != DevOpsAction.DOCKER_DIAGNOSE:
            raise ValueError(
                "The latest DevOps result is not a Docker diagnosis. "
                "Diagnose the container immediately before guided repair."
            )

        diagnosed_target = (
            previous_result.plan.inferred_component
            or previous_result.plan.request.component
        )
        requested_target = _safe_name(
            request.component or diagnosed_target,
            field_name="container",
        )

        if not requested_target:
            raise ValueError(
                "A diagnosed Docker container is required for guided repair."
            )

        if requested_target != diagnosed_target:
            raise ValueError(
                f"The latest diagnosis is for '{diagnosed_target}', not "
                f"'{requested_target}'. Diagnose '{requested_target}' first so "
                "Iceman does not repair a different container from stale evidence."
            )

        startup_hint = _docker_startup_hint(previous_result.step_results)
        if startup_hint is None:
            raise ValueError(
                "The previous diagnosis did not capture a safe startup command. "
                "Re-run Docker diagnosis before guided repair."
            )

        log_result = next(
            (
                item.result
                for item in previous_result.step_results
                if "recent logs" in item.step.label.lower()
            ),
            None,
        )
        log_text = ""
        if log_result is not None:
            log_text = (log_result.stdout or "") + "\n" + (log_result.stderr or "")

        missing = _node_missing_module_details(log_text, startup_hint)
        if not missing or missing[0] != "entry_file":
            raise ValueError(
                "The latest diagnosis does not prove a missing Node.js startup "
                "entry file. Iceman will not convert a different failure class "
                "into a startup-command repair."
            )

        _, missing_target = missing
        current_startup = _safe_effective_startup_command(startup_hint)
        if not current_startup:
            raise ValueError(
                "Iceman could not safely reconstruct the diagnosed startup command."
            )

        try:
            startup_tokens = shlex.split(current_startup)
        except ValueError as exc:
            raise ValueError(
                "Iceman could not safely parse the diagnosed startup command."
            ) from exc

        if len(startup_tokens) != 2 or Path(startup_tokens[0]).name not in {
            "node",
            "nodejs",
        }:
            raise ValueError(
                "Guided startup repair currently supports only a bounded Node.js "
                "entry command such as 'node missing.js'."
            )

        replacement = _safe_startup_target(request.startup_target)
        if replacement is None:
            plan.safe_to_execute = False
            plan.inferred_component = requested_target
            plan.notes.extend([
                (
                    f"The previous diagnosis proves that startup target "
                    f"'{missing_target}' is missing."
                ),
                (
                    "Iceman cannot prove the intended replacement entry file and "
                    "will not guess one."
                ),
                (
                    f"Provide it explicitly, for example: "
                    f"fix {requested_target} using server.js"
                ),
            ])
            return plan

        if replacement == startup_tokens[1]:
            raise ValueError(
                f"The supplied startup target '{replacement}' is the same target "
                "that the diagnosis already proved missing."
            )

        if not _docker_container_exists(workspace, requested_target):
            raise ValueError(
                f"Docker container '{requested_target}' no longer exists. "
                "Re-run diagnosis before repair."
            )

        image = _docker_container_image(workspace, requested_target)
        run_args, mappings = _standalone_recreate_recipe(
            workspace,
            requested_target,
            image,
            require_port_mapping=False,
            allow_command_override=True,
        )

        backup_name = f"{requested_target}-iceman-backup"
        if _docker_container_exists(workspace, backup_name):
            raise ValueError(
                f"Safety backup container '{backup_name}' already exists. "
                "Iceman will not overwrite or remove it automatically."
            )

        # Execution-time stale-evidence guard: if the startup shape changed after
        # diagnosis, abort before any state-changing operation.
        expected_hint = "|".join(startup_hint)
        inspect_command = _docker_startup_hint_command(requested_target)
        stale_check_script = (
            f"actual=$({inspect_command}); "
            f"test \"$actual\" = {_quote(expected_hint)}"
        )

        executable = startup_tokens[0]

        run_command_parts = (
            ["docker", "run", "-d", "--name", _quote(requested_target)]
            + [_quote(item) for item in run_args]
            + [
                _quote(image),
                _quote(executable),
                _quote(replacement),
            ]
        )

        plan.inferred_component = requested_target
        plan.inferred_image = image

        plan.steps.extend([
            DevOpsStep(
                "Verify diagnosed startup command is unchanged",
                "sh -c " + _quote(stale_check_script),
            ),
            DevOpsStep(
                f"Inspect current runtime state for {requested_target}",
                _docker_state_command(requested_target),
            ),
            DevOpsStep(
                f"Stop current container {requested_target}",
                f"docker stop {_quote(requested_target)}",
                changes_state=True,
            ),
            DevOpsStep(
                f"Preserve current container as {backup_name}",
                f"docker rename {_quote(requested_target)} {_quote(backup_name)}",
                changes_state=True,
            ),
            DevOpsStep(
                f"Start corrected container {requested_target}",
                " ".join(run_command_parts),
                changes_state=True,
            ),
            DevOpsStep(
                f"Verify corrected container {requested_target} remains running",
                _docker_running_check_command(requested_target),
            ),
            DevOpsStep(
                f"Inspect repaired runtime state for {requested_target}",
                _docker_state_command(requested_target),
            ),
            DevOpsStep(
                f"Read bounded post-repair logs for {requested_target}",
                f"docker logs --tail 40 {_quote(requested_target)}",
                stream_output=False,
            ),
        ])

        plan.notes.extend([
            f"Evidence-backed failure: Node.js startup target '{missing_target}' is missing.",
            f"Operator-supplied replacement startup target: '{replacement}'.",
            (
                f"The old container will be preserved as '{backup_name}' "
                "for manual rollback."
            ),
            (
                "Iceman will verify that the diagnosed startup command has not "
                "changed before touching the container."
            ),
            (
                "Recovery verification is pass/fail: the replacement must still "
                "be running after a short stabilization period."
            ),
            (
                "This changes the standalone container runtime command only. "
                "Update the Dockerfile/Compose startup definition separately to "
                "make the repair reproducible."
            ),
        ])

        if mappings:
            plan.notes.append(
                "Recreated port mapping(s): " + ", ".join(mappings) + "."
            )
        else:
            plan.notes.append(
                "The diagnosed container has no host-published ports; none will "
                "be invented during recreation."
            )

    except ValueError as exc:
        plan.safe_to_execute = False
        plan.notes.append(str(exc))

    return plan


def _terraform_dir(workspace: Path, explicit: str | None) -> str:
    if explicit:
        value = explicit.strip().strip("/") or "."
        if value.startswith("/") or ".." in Path(value).parts:
            raise ValueError("Terraform directory must stay inside the workspace.")
        path = (workspace / value).resolve()
        try:
            path.relative_to(workspace.resolve())
        except ValueError as exc:
            raise ValueError("Terraform directory escapes workspace.") from exc
        if not path.exists() or not path.is_dir():
            raise ValueError(f"Terraform directory '{value}' was not found.")
        return value

    candidate = workspace / "terraform"
    if candidate.is_dir() and any(candidate.glob("*.tf")):
        return "terraform"
    if any(workspace.glob("*.tf")):
        return "."
    raise ValueError(
        "No Terraform configuration was found. Expected ./terraform/*.tf or ./*.tf."
    )


def _k8s_dir(workspace: Path) -> str:
    candidate = workspace / "k8s"
    if not candidate.is_dir():
        raise ValueError("No k8s/ directory was found in the active workspace.")
    manifests = list(candidate.glob("*.yaml")) + list(candidate.glob("*.yml"))
    if not manifests:
        raise ValueError("No Kubernetes YAML files were found in k8s/.")
    return "k8s"


def _manifest_image(workspace: Path, component: str | None) -> str | None:
    k8s = workspace / "k8s"
    candidates: list[Path] = []
    if component:
        for suffix in ("yaml", "yml"):
            path = k8s / f"{component}-deployment.{suffix}"
            if path.exists():
                candidates.append(path)
    if not candidates:
        candidates.extend(sorted(k8s.glob("*deployment*.yaml")))
        candidates.extend(sorted(k8s.glob("*deployment*.yml")))
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = re.search(r"(?mi)^\s*image:\s*([^\s#]+)", text)
        if match:
            value = match.group(1).strip('"\'')
            if _SAFE_IMAGE.fullmatch(value):
                return value
    return None


def _current_k8s_context(workspace: Path) -> str | None:
    try:
        result = run_command("kubectl config current-context", cwd=workspace)
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _available_k8s_contexts(workspace: Path) -> list[str]:
    """Return kubeconfig context names without changing the active context."""
    try:
        result = run_command("kubectl config get-contexts -o name", cwd=workspace)
    except (FileNotFoundError, OSError):
        return []
    if result.returncode != 0:
        return []
    contexts: list[str] = []
    for line in result.stdout.splitlines():
        value = line.strip()
        if value and _SAFE_CONTEXT.fullmatch(value):
            contexts.append(value)
    return contexts


def _context_namespace(workspace: Path, context: str) -> str:
    """Read a context's configured namespace from redacted kubeconfig JSON."""
    try:
        result = run_command("kubectl config view -o json", cwd=workspace)
    except (FileNotFoundError, OSError):
        return "default"
    if result.returncode != 0:
        return "default"
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return "default"
    entries = payload.get("contexts") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return "default"
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("name") != context:
            continue
        details = entry.get("context")
        if not isinstance(details, dict):
            return "default"
        namespace = details.get("namespace")
        if isinstance(namespace, str) and namespace.strip():
            return namespace.strip()
        return "default"
    return "default"


def _is_local_k8s_context(context: str) -> bool:
    lower = context.lower()
    return (
        lower == "minikube"
        or lower == "docker-desktop"
        or lower.startswith("kind-")
        or lower.startswith("k3d-")
        or lower.startswith("microk8s")
    )



def _k8s_service_connectivity_probe_command(
    scoped: str,
    service: str,
    requested_port: int | None,
) -> str:
    """Build a bounded non-mutating TCP probe through kubectl port-forward."""

    ports_jsonpath = (
        '{range .spec.ports[*]}'
        '{"PORT|"}{.port}{"|"}{.targetPort}{"|"}{.protocol}{"\\n"}{end}'
    )
    selector_template = (
        '{{range $k, $v := .spec.selector}}'
        '{{printf "%s=%s," $k $v}}{{end}}'
    )
    ready_jsonpath = (
        '{range .items[*]}{range .endpoints[*]}'
        '{.conditions.ready}{"\\n"}{end}{end}'
    )

    requested = "" if requested_port is None else str(requested_port)
    endpoint_selector = f"kubernetes.io/service-name={service}"

    lines = [
        f"requested_port={_quote(requested)}",
        (
            f'ports="$({scoped} get service {_quote(service)} '
            f'-o jsonpath={_quote(ports_jsonpath)})"'
        ),
        'selected=""',
        'tcp_count=0',
        'while IFS="|" read -r tag svc_port target_port proto; do',
        '  [ "$tag" = "PORT" ] || continue',
        '  [ "$proto" = "TCP" ] || continue',
        '  tcp_count=$((tcp_count + 1))',
        (
            '  if [ -n "$requested_port" ] && '
            '[ "$svc_port" = "$requested_port" ]; then '
            'selected="$tag|$svc_port|$target_port|$proto"; fi'
        ),
        (
            '  if [ -z "$requested_port" ] && '
            '[ "$tcp_count" -eq 1 ]; then '
            'selected="$tag|$svc_port|$target_port|$proto"; fi'
        ),
        'done <<< "$ports"',
        (
            'if [ -z "$requested_port" ] && [ "$tcp_count" -eq 0 ]; then '
            'echo "CONNECTIVITY|SKIPPED|reason=no-tcp-service-port"; '
            'exit 0; fi'
        ),
        (
            'if [ -z "$requested_port" ] && [ "$tcp_count" -gt 1 ]; then '
            'echo "CONNECTIVITY|SKIPPED|reason=multiple-tcp-service-ports'
            '|count=$tcp_count"; exit 0; fi'
        ),
        (
            'if [ -n "$requested_port" ] && [ -z "$selected" ]; then '
            'echo "CONNECTIVITY|SKIPPED'
            '|reason=requested-port-not-tcp-service-port'
            '|requested_port=$requested_port"; exit 0; fi'
        ),
        'IFS="|" read -r _ service_port target_port protocol <<< "$selected"',
        (
            f'selector="$({scoped} get service {_quote(service)} '
            f'-o go-template={_quote(selector_template)})"'
        ),
        'selector="${selector%,}"',
        (
            'if [ -z "$selector" ]; then '
            'echo "CONNECTIVITY|SKIPPED|reason=no-service-selector'
            '|service_port=$service_port|target_port=$target_port"; '
            'exit 0; fi'
        ),
        (
            f'ready_text="$({scoped} get endpointslices.discovery.k8s.io '
            f'-l {_quote(endpoint_selector)} '
            f'-o jsonpath={_quote(ready_jsonpath)})"'
        ),
        'ready_count=0',
        'while IFS= read -r ready; do',
        '  [ "$ready" = "true" ] && ready_count=$((ready_count + 1))',
        'done <<< "$ready_text"',
        (
            'if [ "$ready_count" -eq 0 ]; then '
            'echo "CONNECTIVITY|SKIPPED|reason=no-ready-endpoints'
            '|service_port=$service_port|target_port=$target_port"; '
            'exit 0; fi'
        ),
        'tmp="$(mktemp)"',
        'pf_pid=""',
        'cleanup() {',
        '  if [ -n "$pf_pid" ]; then',
        '    kill "$pf_pid" >/dev/null 2>&1 || true',
        '    wait "$pf_pid" >/dev/null 2>&1 || true',
        '  fi',
        '  rm -f "$tmp"',
        '}',
        'trap cleanup EXIT',
        (
            f'{scoped} port-forward --address 127.0.0.1 '
            f'{_quote("service/" + service)} ":$service_port" '
            '>"$tmp" 2>&1 &'
        ),
        'pf_pid=$!',
        'local_port=""',
        'for _ in {1..30}; do',
        '  while IFS= read -r line; do',
        '    case "$line" in',
        '      "Forwarding from 127.0.0.1:"*)',
        '        candidate="${line#Forwarding from 127.0.0.1:}"',
        '        candidate="${candidate%% *}"',
        '        case "$candidate" in',
        '          ""|*[!0-9]*) ;;',
        '          *) local_port="$candidate" ;;',
        '        esac',
        '        ;;',
        '    esac',
        '  done < "$tmp"',
        '  [ -n "$local_port" ] && break',
        '  kill -0 "$pf_pid" >/dev/null 2>&1 || break',
        '  sleep 0.1',
        'done',
        (
            'if [ -z "$local_port" ]; then '
            'echo "CONNECTIVITY|FAIL|reason=port-forward-start'
            '|service_port=$service_port|target_port=$target_port"; '
            'exit 0; fi'
        ),
        (
            'if timeout 4 bash -c '
            '"exec 3<>/dev/tcp/127.0.0.1/$local_port; '
            'sleep 0.4; exec 3>&-; exec 3<&-" '
            '>/dev/null 2>&1; then tcp_rc=0; else tcp_rc=$?; fi'
        ),
        'sleep 0.2',
        (
            'if grep -Eqi '
            '"error forwarding port|connection refused|'
            'failed to execute portforward" "$tmp"; then '
            'echo "CONNECTIVITY|FAIL|reason=target-port-connect-failed'
            '|service_port=$service_port|target_port=$target_port'
            '|local_port=$local_port"; '
            'elif [ "$tcp_rc" -ne 0 ]; then '
            'echo "CONNECTIVITY|FAIL|reason=local-probe-failed'
            '|service_port=$service_port|target_port=$target_port'
            '|local_port=$local_port"; '
            'else '
            'echo "CONNECTIVITY|SUCCESS|reason=tcp-connected'
            '|service_port=$service_port|target_port=$target_port'
            '|local_port=$local_port"; fi'
        ),
    ]

    return "bash -c " + _quote("\n".join(lines))


def build_devops_plan(workspace: Path, request: DevOpsRequest) -> DevOpsPlan:
    workspace = workspace.resolve()
    plan = DevOpsPlan(
        request=request,
        workspace=workspace,
        title=request.action.value.replace("_", " ").title(),
    )

    try:
        component = _safe_name(request.component, field_name="component")
        port = _validate_port(request.port)
        replicas = _validate_replicas(request.replicas)
        context = _safe_context(request.context)
        action = request.action

        if action == DevOpsAction.DOCKER_BUILD:
            directory = _component_dir(workspace, component)
            if not (directory / "Dockerfile").exists():
                raise ValueError(
                    f"No Dockerfile found in {directory.relative_to(workspace) or Path('.')}"
                )
            image = _infer_image(workspace, component, request.image)
            context_path = str(directory.relative_to(workspace)) if directory != workspace else "."
            plan.inferred_component = component
            plan.inferred_image = image
            plan.steps.append(
                DevOpsStep(
                    "Build Docker image",
                    f"docker build -t {_quote(image)} {_quote(context_path)}",
                    changes_state=True,
                )
            )
            plan.notes.append(f"Image: {image}")

        elif action == DevOpsAction.DOCKER_RUN:
            image = _infer_image(workspace, component, request.image)
            internal_port = None
            if component is not None and (workspace / component).is_dir():
                directory = _component_dir(workspace, component)
                internal_port = _dockerfile_exposed_port(directory)
            elif component is None:
                internal_port = _dockerfile_exposed_port(workspace)
            if internal_port is None:
                internal_port = _docker_image_exposed_port(workspace, image)
            if internal_port is None:
                raise ValueError(
                    "Could not infer the container port from Dockerfile EXPOSE "
                    "or image metadata."
                )
            owners = _docker_port_owners(workspace)
            requested_host = port or internal_port
            host_port = requested_host
            if requested_host in owners or not _socket_port_free(requested_host):
                host_port = _suggest_port(max(requested_host + 1, 1024), owners)
                owner = owners.get(requested_host, "another local process")
                plan.notes.append(
                    f"Host port {requested_host} is already in use by {owner}; "
                    f"Iceman selected free host port {host_port}."
                )
            name = _container_name(component, image)
            plan.inferred_component = name
            plan.inferred_image = image
            existing = _docker_container_brief(workspace, name)
            if existing is not None:
                existing_ports = sorted(existing.get("host_ports") or [])
                if port is not None and port not in existing_ports:
                    raise ValueError(
                        f"Container '{name}' already exists with host port(s) "
                        f"{existing_ports or 'none'}, so it cannot be remapped to port {port} "
                        "with docker start. Use an explicit rebuild/recreate workflow."
                    )
                if existing.get("running"):
                    plan.notes.append(
                        f"Container '{name}' already exists and is running; Iceman will verify it instead of creating a duplicate."
                    )
                    plan.steps.append(
                        DevOpsStep(
                            f"Inspect runtime state for {name}",
                            _docker_state_command(name),
                        )
                    )
                    plan.steps.append(
                        DevOpsStep(
                            f"Show published ports for {name}",
                            f"docker port {_quote(name)}",
                        )
                    )
                    plan.notes.append(
                        "Existing published host port(s): " + (", ".join(map(str, existing_ports)) if existing_ports else "none") + "."
                    )
                else:
                    plan.notes.append(
                        f"Container '{name}' already exists but is stopped; Iceman will start and verify the existing container rather than creating a duplicate."
                    )
                    plan.steps.append(
                        DevOpsStep(
                            f"Start existing Docker container {name}",
                            f"docker start {_quote(name)}",
                            changes_state=True,
                        )
                    )
                    plan.steps.append(
                        DevOpsStep(
                            f"Verify Docker container {name}",
                            _docker_state_command(name),
                        )
                    )
                    plan.steps.append(
                        DevOpsStep(
                            f"Show published ports for {name}",
                            f"docker port {_quote(name)}",
                        )
                    )
                # Existing-container run requests are fully planned here.
                plan.requires_approval = any(step.changes_state for step in plan.steps)
                plan.safe_to_execute = bool(plan.steps)
                return plan

            plan.steps.append(
                DevOpsStep(
                    "Run Docker container",
                    (
                        f"docker run -d --name {_quote(name)} "
                        f"-p {host_port}:{internal_port} {_quote(image)}"
                    ),
                    changes_state=True,
                )
            )
            plan.steps.append(
                DevOpsStep(
                    f"Verify Docker container {name}",
                    _docker_state_command(name),
                )
            )
            plan.steps.append(
                DevOpsStep(
                    f"Show published ports for {name}",
                    f"docker port {_quote(name)}",
                )
            )
            plan.notes.append(
                f"Port mapping: localhost:{host_port} -> container:{internal_port}."
            )

        elif action == DevOpsAction.DOCKER_LIST:
            plan.steps.append(
                DevOpsStep(
                    "List running Docker containers",
                    'docker ps --format "table {{.Names}}\\t{{.Status}}\\t{{.Ports}}"',
                )
            )

        elif action == DevOpsAction.DOCKER_IMAGES:
            plan.steps.append(
                DevOpsStep(
                    "List Docker images",
                    'docker images --format "table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}"',
                )
            )

        elif action == DevOpsAction.DOCKER_LOGS:
            target = component or request.image
            if not target:
                raise ValueError("A container name is required for Docker logs.")
            target = _safe_name(target, field_name="container")
            plan.steps.append(
                DevOpsStep(
                    f"Show recent logs for {target}",
                    f"docker logs --tail 100 {_quote(target)}",
                )
            )

        elif action == DevOpsAction.DOCKER_STOP:
            if not component:
                raise ValueError("A container name is required to stop a container.")
            plan.steps.append(
                DevOpsStep(
                    f"Stop Docker container {component}",
                    f"docker stop {_quote(component)}",
                    changes_state=True,
                )
            )

        elif action == DevOpsAction.DOCKER_RESTART:
            if not component:
                raise ValueError("A container name is required to restart a container.")
            plan.steps.append(
                DevOpsStep(
                    f"Restart Docker container {component}",
                    f"docker restart {_quote(component)}",
                    changes_state=True,
                )
            )

        elif action in {DevOpsAction.DOCKER_DIAGNOSE, DevOpsAction.DOCKER_HEALTH}:
            target = component or request.image
            if not target:
                raise ValueError("A container name is required for Docker diagnosis/health checks.")
            target = _safe_name(target, field_name="container")
            if not _docker_container_exists(workspace, target):
                raise ValueError(f"Docker container '{target}' was not found.")
            plan.inferred_component = target
            plan.steps.append(
                DevOpsStep(
                    f"Inspect runtime state for {target}",
                    _docker_state_command(target),
                )
            )
            plan.steps.append(
                DevOpsStep(
                    f"Show published ports for {target}",
                    f"docker port {_quote(target)}",
                )
            )
            if action == DevOpsAction.DOCKER_DIAGNOSE:
                plan.steps.append(
                    DevOpsStep(
                        f"Inspect safe startup command for {target}",
                        _docker_startup_hint_command(target),
                        stream_output=False,
                    )
                )
                plan.steps.append(
                    DevOpsStep(
                        f"Read recent logs for {target}",
                        f"docker logs --tail 120 {_quote(target)}",
                        stream_output=False,
                    )
                )
                plan.notes.append(
                    "Diagnosis is read-only and summarizes runtime state plus common high-signal log failures. "
                    "Startup inspection is intentionally limited to the executable and first two runtime arguments; raw startup output is hidden and sanitized before display."
                )

        elif action == DevOpsAction.DOCKER_PORTS:
            requested_port = port
            plan.steps.append(
                DevOpsStep(
                    "List Docker published ports",
                    'docker ps --format "table {{.Names}}\\t{{.Status}}\\t{{.Ports}}"',
                )
            )
            if requested_port is not None:
                owners = _docker_port_owners(workspace)
                owner = owners.get(requested_port)
                if owner:
                    plan.notes.append(
                        f"Host port {requested_port} is currently published by Docker container '{owner}'."
                    )
                elif not _socket_port_free(requested_port):
                    plan.notes.append(
                        f"Host port {requested_port} is occupied by a non-Docker process or another listener."
                    )
                    plan.steps.append(
                        DevOpsStep(
                            f"Inspect local TCP listeners around port {requested_port}",
                            "ss -lntp",
                        )
                    )
                else:
                    plan.notes.append(
                        f"Host port {requested_port} appears free; no Docker container is publishing it."
                    )

        elif action == DevOpsAction.DOCKER_CONNECTIVITY:
            target = component or request.image
            if not target:
                raise ValueError("A container/component name is required for Docker connectivity diagnosis.")
            target = _safe_name(target, field_name="container")
            if not _docker_container_exists(workspace, target):
                raise ValueError(f"Docker container '{target}' was not found.")
            plan.inferred_component = target
            brief = _docker_container_brief(workspace, target) or {}
            host_ports = sorted(int(value) for value in (brief.get("host_ports") or set()))
            plan.steps.extend([
                DevOpsStep(
                    f"Inspect runtime state for {target}",
                    _docker_state_command(target),
                ),
                DevOpsStep(
                    f"Show published ports for {target}",
                    f"docker port {_quote(target)}",
                ),
            ])
            if port is not None:
                owners = _docker_port_owners(workspace)
                owner = owners.get(port)
                if owner:
                    plan.notes.append(
                        f"Requested host port {port} is currently published by Docker container '{owner}'."
                    )
                elif not _socket_port_free(port):
                    plan.notes.append(
                        f"Requested host port {port} is occupied by a non-Docker process or another listener."
                    )
                    plan.steps.append(
                        DevOpsStep(
                            f"Inspect local TCP listeners around port {port}",
                            "ss -lntp",
                        )
                    )
                else:
                    plan.notes.append(
                        f"Requested host port {port} appears free; no listener is currently bound there."
                    )
            if host_ports:
                plan.notes.append(
                    "Container-published host port(s): " + ", ".join(str(value) for value in host_ports) + "."
                )
                if len(host_ports) == 1:
                    host_port = host_ports[0]
                    plan.steps.append(
                        DevOpsStep(
                            f"Probe localhost:{host_port} over HTTP",
                            _docker_http_probe_command(host_port),
                        )
                    )
                    plan.notes.append(
                        f"Iceman will safely probe http://127.0.0.1:{host_port}/ with a bounded GET request."
                    )
            else:
                plan.notes.append(
                    f"Container '{target}' has no host-published TCP port in its current Docker configuration."
                )

        elif action == DevOpsAction.DOCKER_SECURITY:
            target = component or request.image
            if target and _docker_container_exists(workspace, target):
                target = _safe_name(target, field_name="container")
                plan.inferred_component = target
                plan.steps.append(
                    DevOpsStep(
                        f"Inspect runtime security settings for {target}",
                        _docker_security_command(target),
                    )
                )
            else:
                directory = _component_dir(workspace, component)
                if not (directory / "Dockerfile").exists():
                    raise ValueError(
                        "Docker security review requires a container name or a component with a Dockerfile."
                    )
                plan.inferred_component = component
                plan.steps.append(
                    DevOpsStep(
                        "Validate Dockerfile with BuildKit checks",
                        f"docker build --check {_quote(str(directory.relative_to(workspace)) if directory != workspace else '.')}",
                    )
                )
            plan.notes.append(
                "Iceman Advisor performs the static Dockerfile security review before execution."
            )

        elif action == DevOpsAction.DOCKER_OPTIMIZE:
            image = _infer_image(workspace, component, request.image)
            plan.inferred_image = image
            if _docker_image_exists(workspace, image):
                plan.steps.append(
                    DevOpsStep(
                        f"Inspect image layers for {image}",
                        _docker_history_command(image),
                    )
                )
                plan.steps.append(
                    DevOpsStep(
                        f"Inspect image size for {image}",
                        f'docker image inspect --format "{{{{.Size}}}}" {_quote(image)}',
                    )
                )
            else:
                directory = _component_dir(workspace, component)
                if not (directory / "Dockerfile").exists():
                    raise ValueError("Docker optimization review needs an image or Dockerfile.")
                context_path = str(directory.relative_to(workspace)) if directory != workspace else "."
                plan.steps.append(
                    DevOpsStep(
                        "Run Dockerfile build checks",
                        f"docker build --check {_quote(context_path)}",
                    )
                )
                plan.notes.append(
                    f"Image '{image}' is not present locally, so Iceman will review the Dockerfile instead of image layers."
                )

        elif action == DevOpsAction.DOCKER_REBUILD_RESTART:
            if not component:
                raise ValueError("A component/container name is required for rebuild and restart.")
            directory = _component_dir(workspace, component)
            if not (directory / "Dockerfile").exists():
                raise ValueError(f"No Dockerfile found for component '{component}'.")
            image = _infer_image(workspace, component, request.image)
            container = _container_name(component, image)
            if not _docker_container_exists(workspace, container):
                raise ValueError(
                    f"Docker container '{container}' was not found. Iceman will not guess which running container to replace."
                )
            run_args, mappings = _standalone_recreate_recipe(workspace, container, image)
            backup_name = f"{container}-iceman-backup"
            if _docker_container_exists(workspace, backup_name):
                raise ValueError(
                    f"Safety backup container '{backup_name}' already exists. Remove/rename it explicitly before another rebuild/restart."
                )
            context_path = str(directory.relative_to(workspace)) if directory != workspace else "."
            plan.inferred_component = container
            plan.inferred_image = image
            plan.steps.extend([
                DevOpsStep(
                    "Build replacement Docker image",
                    f"docker build -t {_quote(image)} {_quote(context_path)}",
                    changes_state=True,
                ),
                DevOpsStep(
                    f"Stop current container {container}",
                    f"docker stop {_quote(container)}",
                    changes_state=True,
                ),
                DevOpsStep(
                    f"Preserve current container as {backup_name}",
                    f"docker rename {_quote(container)} {_quote(backup_name)}",
                    changes_state=True,
                ),
                DevOpsStep(
                    f"Start rebuilt container {container}",
                    " ".join(
                        ["docker", "run", "-d", "--name", _quote(container)]
                        + [_quote(item) for item in run_args]
                        + [_quote(image)]
                    ),
                    changes_state=True,
                ),
                DevOpsStep(
                    f"Verify rebuilt container {container}",
                    _docker_state_command(container),
                ),
                DevOpsStep(
                    f"Show published ports for {container}",
                    f"docker port {_quote(container)}",
                ),
            ])
            plan.notes.append(
                f"The old container will be preserved as '{backup_name}' instead of being deleted."
            )
            plan.notes.append(
                "Recreated port mapping(s): " + ", ".join(mappings) + "."
            )
            plan.notes.append(
                "Automatic recreation is intentionally refused for mounts, custom networks, runtime env overrides, or command overrides."
            )

        elif action == DevOpsAction.DOCKER_COMPOSE_STATUS:
            compose_candidates = [
                workspace / "compose.yaml",
                workspace / "compose.yml",
                workspace / "docker-compose.yml",
                workspace / "docker-compose.yaml",
            ]
            if not any(path.is_file() for path in compose_candidates):
                raise ValueError("No Docker Compose file was found in the active workspace.")
            plan.steps.append(
                DevOpsStep(
                    "Show Docker Compose service status",
                    'docker compose ps --format "table {{.Service}}\\t{{.Name}}\\t{{.State}}\\t{{.Ports}}"',
                )
            )

        elif action == DevOpsAction.K8S_CONTEXT:
            plan.steps.append(DevOpsStep("Show current Kubernetes context", "kubectl config current-context"))

        elif action == DevOpsAction.K8S_CONTEXTS:
            plan.steps.append(DevOpsStep("List Kubernetes contexts", "kubectl config get-contexts"))

        elif action == DevOpsAction.K8S_VALIDATE:
            directory = _k8s_dir(workspace)
            plan.steps.append(
                DevOpsStep(
                    "Validate Kubernetes manifests with kubectl client dry-run",
                    f"kubectl apply --dry-run=client -f {_quote(directory + '/')}",
                )
            )

        elif action == DevOpsAction.K8S_DEPLOY:
            directory = _k8s_dir(workspace)
            current = _current_k8s_context(workspace)
            contexts = _available_k8s_contexts(workspace)

            # "Kubernetes" and "k8s" name the platform, not a kubeconfig context.
            # The reasoner normally normalizes these to None, but keep this defensive
            # check here so provider-selected intents cannot accidentally create a
            # bogus `use-context kubernetes` workflow.
            if context and context.lower() in {"kubernetes", "k8s", "cluster"}:
                context = None

            if not contexts:
                raise ValueError(
                    "Iceman could not enumerate kubeconfig contexts. Confirm kubectl "
                    "is installed and kubeconfig is readable before deployment."
                )

            if context is None:
                if not current:
                    raise ValueError(
                        "No Kubernetes context was specified and kubectl has no active "
                        "context. Available contexts: " + ", ".join(contexts)
                    )
                if current not in contexts:
                    raise ValueError(
                        f"The active Kubernetes context '{current}' is not present in "
                        "the kubeconfig context inventory. No deployment was planned."
                    )
                target_context = current
                plan.context_inferred = True
                plan.notes.append(
                    f"No specific Kubernetes context was named; Iceman will target "
                    f"the current context '{target_context}'."
                )
            else:
                target_context = context
                if target_context not in contexts:
                    raise ValueError(
                        f"Kubernetes context '{target_context}' was not found. "
                        f"Available contexts: {', '.join(contexts)}. No deployment was planned."
                    )

            plan.target_context = target_context
            plan.target_namespace = _context_namespace(workspace, target_context)
            plan.notes.append(
                f"Kubernetes destination: context '{target_context}', configured namespace "
                f"'{plan.target_namespace}'."
            )
            if current and current != target_context:
                plan.notes.append(
                    f"Current context is '{current}'. Iceman will NOT change your active "
                    f"kubeconfig context; every deployment command is explicitly scoped "
                    f"to '{target_context}'."
                )

            scoped = f"kubectl --context {_quote(target_context)}"
            plan.steps.append(
                DevOpsStep(
                    f"Confirm Kubernetes context {target_context} exists",
                    "kubectl config get-contexts -o name",
                )
            )
            plan.steps.append(
                DevOpsStep(
                    f"Check Kubernetes API reachability for {target_context}",
                    f"{scoped} cluster-info",
                )
            )

            image = _manifest_image(workspace, component)
            if target_context == "minikube" and image and "/" not in image:
                try:
                    inspect_result = run_command(
                        f"docker image inspect {_quote(image)}",
                        cwd=workspace,
                    )
                except (FileNotFoundError, OSError):
                    inspect_result = None
                if inspect_result is not None and inspect_result.returncode == 0:
                    plan.steps.append(
                        DevOpsStep(
                            f"Load local image {image} into Minikube",
                            f"minikube image load {_quote(image)}",
                            changes_state=True,
                        )
                    )
                    plan.notes.append(
                        f"Local image {image} will be loaded into Minikube before deployment."
                    )
            plan.steps.append(
                DevOpsStep(
                    "Validate Kubernetes manifests",
                    f"{scoped} apply --dry-run=client -f {_quote(directory + '/')}",
                )
            )
            plan.steps.append(
                DevOpsStep(
                    "Apply Kubernetes manifests",
                    f"{scoped} apply -f {_quote(directory + '/')}",
                    changes_state=True,
                )
            )
            if component:
                plan.steps.append(
                    DevOpsStep(
                        f"Wait for {component} rollout",
                        f"{scoped} rollout status deployment/{_quote(component)} --timeout=90s",
                    )
                )
            plan.steps.append(DevOpsStep("Show pods", f"{scoped} get pods"))
            plan.steps.append(DevOpsStep("Show services", f"{scoped} get svc"))

        elif action == DevOpsAction.K8S_PODS:
            plan.steps.append(DevOpsStep("Show Kubernetes pods", "kubectl get pods -o wide"))

        elif action == DevOpsAction.K8S_DIAGNOSE_POD:
            pod = _safe_name(component, field_name="pod")
            if not pod:
                raise ValueError("A pod name is required for Kubernetes pod diagnosis.")

            requested_context = _safe_context(request.context)
            current = _current_k8s_context(workspace)
            contexts = _available_k8s_contexts(workspace)

            if requested_context and requested_context.lower() in {
                "kubernetes",
                "k8s",
                "cluster",
            }:
                requested_context = None

            if not contexts:
                raise ValueError(
                    "Iceman could not enumerate kubeconfig contexts. Confirm "
                    "kubectl is installed and kubeconfig is readable."
                )

            if requested_context is None:
                if not current:
                    raise ValueError(
                        "No Kubernetes context was specified and kubectl has no "
                        "active context."
                    )
                if current not in contexts:
                    raise ValueError(
                        f"The active Kubernetes context '{current}' is not present "
                        "in the kubeconfig context inventory."
                    )
                target_context = current
                plan.context_inferred = True
            else:
                target_context = requested_context
                if target_context not in contexts:
                    raise ValueError(
                        f"Kubernetes context '{target_context}' was not found. "
                        f"Available contexts: {', '.join(contexts)}."
                    )

            namespace = _context_namespace(workspace, target_context) or "default"
            namespace = _safe_name(namespace, field_name="namespace") or "default"

            plan.inferred_component = pod
            plan.target_context = target_context
            plan.target_namespace = namespace

            scoped = (
                f"kubectl --context {_quote(target_context)} "
                f"--namespace {_quote(namespace)}"
            )

            status_jsonpath = (
                '{"POD|"}{.metadata.name}{"|"}{.status.phase}{"|"}'
                '{.status.reason}{"\\n"}'
                '{range .status.initContainerStatuses[*]}'
                '{"INIT|"}{.name}{"|"}{.ready}{"|"}{.restartCount}{"|"}'
                '{.state.waiting.reason}{"|"}'
                '{.state.terminated.reason}{"|"}'
                '{.state.terminated.exitCode}{"|"}'
                '{.lastState.terminated.reason}{"|"}'
                '{.lastState.terminated.exitCode}{"\\n"}{end}'
                '{range .status.containerStatuses[*]}'
                '{"CONTAINER|"}{.name}{"|"}{.ready}{"|"}{.restartCount}{"|"}'
                '{.state.waiting.reason}{"|"}'
                '{.state.terminated.reason}{"|"}'
                '{.state.terminated.exitCode}{"|"}'
                '{.lastState.terminated.reason}{"|"}'
                '{.lastState.terminated.exitCode}{"\\n"}{end}'
            )

            conditions_jsonpath = (
                '{range .status.conditions[*]}'
                '{"CONDITION|"}{.type}{"|"}{.status}{"|"}'
                '{.reason}{"|"}{.message}{"\\n"}{end}'
            )

            event_selector = (
                f"involvedObject.kind=Pod,involvedObject.name={pod}"
            )

            current_logs = (
                f"{scoped} logs {_quote('pod/' + pod)} "
                "--all-containers=true --prefix=true --tail=100"
            )
            previous_logs = (
                f"{scoped} logs {_quote('pod/' + pod)} "
                "--all-containers=true --prefix=true --previous --tail=100"
            )

            current_log_script = (
                f"{current_logs} 2>&1; rc=$?; "
                'printf "\\nICEMAN_LOGS_RC=%s\\n" "$rc"; exit 0'
            )
            previous_log_script = (
                f"{previous_logs} 2>&1; rc=$?; "
                'printf "\\nICEMAN_LOGS_RC=%s\\n" "$rc"; exit 0'
            )

            events_command = (
                f"{scoped} get events "
                f"--field-selector {_quote(event_selector)} "
                "--sort-by=.lastTimestamp "
                "-o custom-columns=TYPE:.type,REASON:.reason,MESSAGE:.message "
                "--no-headers"
            )

            plan.steps.extend([
                DevOpsStep(
                    f"Confirm pod {pod} exists",
                    f"{scoped} get pod {_quote(pod)} -o name",
                ),
                DevOpsStep(
                    f"Inspect pod status for {pod}",
                    f"{scoped} get pod {_quote(pod)} "
                    f"-o jsonpath={_quote(status_jsonpath)}",
                    stream_output=False,
                ),
                DevOpsStep(
                    f"Inspect pod conditions for {pod}",
                    f"{scoped} get pod {_quote(pod)} "
                    f"-o jsonpath={_quote(conditions_jsonpath)}",
                    stream_output=False,
                ),
                DevOpsStep(
                    f"Read recent pod events for {pod}",
                    "sh -c " + _quote(events_command + " | tail -n 20"),
                    stream_output=False,
                ),
                DevOpsStep(
                    f"Read bounded current logs for {pod}",
                    "sh -c " + _quote(current_log_script),
                    stream_output=False,
                ),
                DevOpsStep(
                    f"Read bounded previous logs for {pod}",
                    "sh -c " + _quote(previous_log_script),
                    stream_output=False,
                ),
            ])

            plan.notes.extend([
                (
                    f"Pod diagnosis is read-only and explicitly scoped to context "
                    f"'{target_context}', namespace '{namespace}'."
                ),
                (
                    "Iceman inspects selected status fields rather than the full pod "
                    "spec so literal environment values are not unnecessarily exposed."
                ),
                (
                    "Current and previous logs are bounded to 100 lines per kubectl "
                    "request; unavailable logs do not abort the diagnosis."
                ),
                (
                    "Phase 1 diagnosis never deletes, restarts, patches, scales, "
                    "or recreates Kubernetes resources."
                ),
            ])

        elif action in {
            DevOpsAction.K8S_DIAGNOSE_SERVICE,
            DevOpsAction.K8S_SERVICE_CONNECTIVITY,
        }:
            service = _safe_name(request.component, field_name="service")
            if not service:
                raise ValueError(
                    "An explicit Kubernetes Service name is required for diagnosis."
                )

            requested_port = (
                _validate_port(request.port)
                if action == DevOpsAction.K8S_SERVICE_CONNECTIVITY
                else None
            )

            requested_context = _safe_context(request.context)
            if requested_context and requested_context.lower() in {
                "kubernetes",
                "k8s",
                "cluster",
            }:
                requested_context = None

            current_context = _current_k8s_context(workspace)
            contexts = _available_k8s_contexts(workspace)

            if requested_context:
                target_context = requested_context
            else:
                target_context = current_context
                plan.context_inferred = True

            if not target_context:
                raise ValueError(
                    "No active Kubernetes context is available for Service diagnosis."
                )

            if target_context not in contexts:
                raise ValueError(
                    f"Kubernetes context '{target_context}' was not found. "
                    f"Available contexts: {', '.join(contexts)}."
                )

            namespace = _context_namespace(workspace, target_context) or "default"
            namespace = _safe_name(namespace, field_name="namespace") or "default"

            plan.inferred_component = service
            plan.target_context = target_context
            plan.target_namespace = namespace

            scoped = (
                f"kubectl --context {_quote(target_context)} "
                f"--namespace {_quote(namespace)}"
            )

            service_jsonpath = (
                '{"SERVICE|"}{.metadata.name}{"|"}{.spec.type}{"|"}'
                '{.spec.clusterIP}{"|"}{.spec.selector}{"\\n"}'
                '{range .spec.ports[*]}'
                '{"PORT|"}{.name}{"|"}{.port}{"|"}'
                '{.targetPort}{"|"}{.protocol}{"\\n"}{end}'
            )

            endpoints_jsonpath = (
                '{range .items[*]}'
                '{"SLICE|"}{.metadata.name}{"\\n"}'
                '{range .endpoints[*]}'
                '{"ENDPOINT|"}{.addresses[0]}{"|"}'
                '{.conditions.ready}{"|"}'
                '{.conditions.serving}{"|"}'
                '{.conditions.terminating}{"\\n"}'
                '{end}{end}'
            )

            pod_jsonpath = (
                '{range .items[*]}'
                '{"POD|"}{.metadata.name}{"|"}{.status.phase}{"|"}'
                '{range .status.conditions[?(@.type=="Ready")]}'
                '{.status}{end}{"\\n"}{end}'
            )

            selector_template = (
                '{{range $k, $v := .spec.selector}}'
                '{{printf "%s=%s," $k $v}}{{end}}'
            )

            selector_command = (
                f"{scoped} get service {_quote(service)} "
                f"-o go-template={_quote(selector_template)}"
            )

            matching_pods_script = (
                f'selector="$({selector_command})"; '
                'selector="${selector%,}"; '
                'printf "SELECTOR|%s\\n" "$selector"; '
                'if [ -z "$selector" ]; then '
                'printf "ICEMAN_MATCHING_PODS|selector-empty\\n"; '
                'exit 0; '
                'fi; '
                f'{scoped} get pods -l "$selector" '
                f'-o jsonpath={_quote(pod_jsonpath)}'
            )

            endpoint_selector = f"kubernetes.io/service-name={service}"

            plan.steps.extend([
                DevOpsStep(
                    f"Confirm service {service} exists",
                    f"{scoped} get service {_quote(service)} -o name",
                ),
                DevOpsStep(
                    f"Inspect service routing for {service}",
                    f"{scoped} get service {_quote(service)} "
                    f"-o jsonpath={_quote(service_jsonpath)}",
                    stream_output=False,
                ),
                DevOpsStep(
                    f"Inspect EndpointSlices for {service}",
                    f"{scoped} get endpointslices.discovery.k8s.io "
                    f"-l {_quote(endpoint_selector)} "
                    f"-o jsonpath={_quote(endpoints_jsonpath)}",
                    stream_output=False,
                ),
                DevOpsStep(
                    f"Inspect pods matching service selector for {service}",
                    "sh -c " + _quote(matching_pods_script),
                    stream_output=False,
                ),
            ])

            if action == DevOpsAction.K8S_SERVICE_CONNECTIVITY:
                plan.steps.append(
                    DevOpsStep(
                        f"Probe Service-selected backend TCP connectivity for {service}",
                        _k8s_service_connectivity_probe_command(
                            scoped,
                            service,
                            requested_port,
                        ),
                        stream_output=False,
                    )
                )
                plan.notes.extend([
                    (
                        "The connectivity probe uses a temporary local kubectl "
                        "port-forward and automatically cleans up its local process "
                        "and temporary output file."
                    ),
                    (
                        "The probe does not create Pods or mutate cluster resources. "
                        "It checks Service targetPort/backend listener reachability; "
                        "kubectl port-forward bypasses the normal ClusterIP/kube-proxy "
                        "dataplane."
                    ),
                ])

            plan.notes.extend([
                (
                    f"Service diagnosis is read-only and explicitly scoped to "
                    f"context '{target_context}', namespace '{namespace}'."
                ),
                (
                    "Iceman reads only selected Service routing fields, "
                    "EndpointSlice readiness metadata, and matching Pod readiness."
                ),
                (
                    "The Service selector is converted to a label selector and "
                    "passed as a quoted kubectl argument; it is never evaluated "
                    "as shell syntax."
                ),
                (
                    "Phase 2 Service diagnosis never patches selectors, restarts "
                    "pods, changes Services, scales workloads, or deletes resources."
                ),
            ])

        elif action == DevOpsAction.K8S_DIAGNOSE_DEPLOYMENT:
            deployment = _safe_name(request.component, field_name="deployment")
            if not deployment:
                raise ValueError(
                    "An explicit Kubernetes Deployment name is required for diagnosis."
                )

            requested_context = _safe_context(request.context)
            if requested_context and requested_context.lower() in {
                "kubernetes",
                "k8s",
                "cluster",
            }:
                requested_context = None

            current_context = _current_k8s_context(workspace)
            contexts = _available_k8s_contexts(workspace)

            if requested_context:
                target_context = requested_context
            else:
                target_context = current_context
                plan.context_inferred = True

            if not target_context:
                raise ValueError(
                    "No active Kubernetes context is available for Deployment diagnosis."
                )

            if target_context not in contexts:
                raise ValueError(
                    f"Kubernetes context '{target_context}' was not found. "
                    f"Available contexts: {', '.join(contexts)}."
                )

            namespace = _context_namespace(workspace, target_context) or "default"
            namespace = _safe_name(namespace, field_name="namespace") or "default"

            plan.inferred_component = deployment
            plan.target_context = target_context
            plan.target_namespace = namespace

            scoped = (
                f"kubectl --context {_quote(target_context)} "
                f"--namespace {_quote(namespace)}"
            )

            status_jsonpath = (
                '{"DEPLOYMENT|"}{.metadata.name}{"|"}'
                '{.metadata.generation}{"|"}'
                '{.status.observedGeneration}{"|"}'
                '{.spec.replicas}{"|"}'
                '{.status.replicas}{"|"}'
                '{.status.updatedReplicas}{"|"}'
                '{.status.readyReplicas}{"|"}'
                '{.status.availableReplicas}{"|"}'
                '{.status.unavailableReplicas}{"\\n"}'
            )

            conditions_jsonpath = (
                '{range .status.conditions[*]}'
                '{"CONDITION|"}{.type}{"|"}{.status}{"|"}'
                '{.reason}{"|"}{.message}{"\\n"}{end}'
            )

            selector_template = (
                '{{range $k, $v := .spec.selector.matchLabels}}'
                '{{printf "%s=%s," $k $v}}{{end}}'
                '{{range .spec.selector.matchExpressions}}'
                '{{if eq .operator "In"}}'
                '{{printf "%s in (" .key}}'
                '{{range $i, $v := .values}}'
                '{{if $i}}{{printf ","}}{{end}}{{printf "%s" $v}}{{end}}'
                '{{printf "),"}}'
                '{{else if eq .operator "NotIn"}}'
                '{{printf "%s notin (" .key}}'
                '{{range $i, $v := .values}}'
                '{{if $i}}{{printf ","}}{{end}}{{printf "%s" $v}}{{end}}'
                '{{printf "),"}}'
                '{{else if eq .operator "Exists"}}'
                '{{printf "%s," .key}}'
                '{{else if eq .operator "DoesNotExist"}}'
                '{{printf "!%s," .key}}'
                '{{end}}'
                '{{end}}'
            )

            selector_command = (
                f"{scoped} get deployment {_quote(deployment)} "
                f"-o go-template={_quote(selector_template)}"
            )

            rs_jsonpath = (
                '{range .items[*]}'
                '{"RS|"}{.metadata.name}{"|"}'
                '{.spec.replicas}{"|"}'
                '{.status.replicas}{"|"}'
                '{.status.readyReplicas}{"|"}'
                '{.status.availableReplicas}{"|"}'
                '{range .metadata.ownerReferences[*]}'
                '{.kind}{":"}{.name}{end}{"\\n"}'
                '{end}'
            )

            pod_jsonpath = (
                '{range .items[*]}'
                '{"POD|"}{.metadata.name}{"|"}'
                '{.status.phase}{"|"}'
                '{range .status.conditions[?(@.type=="Ready")]}'
                '{.status}{end}{"|"}'
                '{range .status.containerStatuses[*]}'
                '{.state.waiting.reason}{","}{end}{"|"}'
                '{range .status.containerStatuses[*]}'
                '{.restartCount}{","}{end}{"\\n"}'
                '{end}'
            )

            rs_script = (
                f'selector="$({selector_command})"; '
                'selector="${selector%,}"; '
                'printf "SELECTOR|%s\\n" "$selector"; '
                'if [ -z "$selector" ]; then exit 0; fi; '
                f'{scoped} get replicasets -l "$selector" '
                f'-o jsonpath={_quote(rs_jsonpath)}'
            )

            pods_script = (
                f'selector="$({selector_command})"; '
                'selector="${selector%,}"; '
                'printf "SELECTOR|%s\\n" "$selector"; '
                'if [ -z "$selector" ]; then exit 0; fi; '
                f'{scoped} get pods -l "$selector" '
                f'-o jsonpath={_quote(pod_jsonpath)}'
            )

            event_selector = (
                f"involvedObject.kind=Deployment,"
                f"involvedObject.name={deployment}"
            )

            events_command = (
                f"{scoped} get events "
                f"--field-selector {_quote(event_selector)} "
                "--sort-by=.lastTimestamp "
                "-o custom-columns=TYPE:.type,REASON:.reason,MESSAGE:.message "
                "--no-headers"
            )

            plan.steps.extend([
                DevOpsStep(
                    f"Confirm deployment {deployment} exists",
                    f"{scoped} get deployment {_quote(deployment)} -o name",
                ),
                DevOpsStep(
                    f"Inspect deployment status for {deployment}",
                    f"{scoped} get deployment {_quote(deployment)} "
                    f"-o jsonpath={_quote(status_jsonpath)}",
                    stream_output=False,
                ),
                DevOpsStep(
                    f"Inspect deployment conditions for {deployment}",
                    f"{scoped} get deployment {_quote(deployment)} "
                    f"-o jsonpath={_quote(conditions_jsonpath)}",
                    stream_output=False,
                ),
                DevOpsStep(
                    f"Inspect ReplicaSets for deployment {deployment}",
                    "sh -c " + _quote(rs_script),
                    stream_output=False,
                ),
                DevOpsStep(
                    f"Inspect pods for deployment {deployment}",
                    "sh -c " + _quote(pods_script),
                    stream_output=False,
                ),
                DevOpsStep(
                    f"Read recent deployment events for {deployment}",
                    "sh -c " + _quote(events_command + " | tail -n 20"),
                    stream_output=False,
                ),
            ])

            plan.notes.extend([
                (
                    f"Deployment diagnosis is read-only and explicitly scoped to "
                    f"context '{target_context}', namespace '{namespace}'."
                ),
                (
                    "Iceman inspects selected Deployment status/condition fields, "
                    "matching ReplicaSets, matching Pod readiness, and recent events."
                ),
                (
                    "The Deployment selector is converted to a quoted kubectl label "
                    "selector and is never evaluated as shell syntax."
                ),
                (
                    "Phase 2 Deployment diagnosis never rolls out, restarts, scales, "
                    "patches, deletes, or recreates Kubernetes resources."
                ),
            ])

        elif action == DevOpsAction.K8S_NAMESPACES:
            current_context = _current_k8s_context(workspace)
            contexts = _available_k8s_contexts(workspace)

            if not current_context:
                raise ValueError(
                    "No active Kubernetes context is available for namespace listing."
                )

            if current_context not in contexts:
                raise ValueError(
                    f"Kubernetes context '{current_context}' was not found. "
                    f"Available contexts: {', '.join(contexts)}."
                )

            plan.target_context = current_context
            plan.context_inferred = True
            plan.steps.append(
                DevOpsStep(
                    "List Kubernetes namespaces",
                    f"kubectl --context {_quote(current_context)} "
                    "get namespaces -o wide",
                )
            )
            plan.notes.append(
                f"Namespace listing is read-only and scoped to Kubernetes "
                f"context '{current_context}'."
            )

        elif action == DevOpsAction.K8S_DEPLOYMENTS:
            plan.steps.append(DevOpsStep("Show Kubernetes deployments", "kubectl get deployments"))

        elif action == DevOpsAction.K8S_SERVICES:
            plan.steps.append(DevOpsStep("Show Kubernetes services", "kubectl get svc"))

        elif action == DevOpsAction.K8S_ROLLOUT:
            if not component:
                raise ValueError("A deployment name is required for rollout status.")
            plan.steps.append(
                DevOpsStep(
                    f"Wait for deployment/{component}",
                    f"kubectl rollout status deployment/{_quote(component)} --timeout=90s",
                )
            )

        elif action == DevOpsAction.K8S_SCALE:
            if not component or replicas is None:
                raise ValueError("Scaling requires a deployment name and replica count.")
            plan.steps.append(
                DevOpsStep(
                    f"Scale deployment/{component} to {replicas}",
                    f"kubectl scale deployment/{_quote(component)} --replicas={replicas}",
                    changes_state=True,
                )
            )
            plan.steps.append(
                DevOpsStep(
                    f"Wait for deployment/{component}",
                    f"kubectl rollout status deployment/{_quote(component)} --timeout=90s",
                )
            )

        elif action == DevOpsAction.K8S_LOGS:
            if not component:
                raise ValueError("A deployment name is required for Kubernetes logs.")
            plan.steps.append(
                DevOpsStep(
                    f"Show logs for deployment/{component}",
                    f"kubectl logs deployment/{_quote(component)} --tail=100",
                )
            )

        elif action == DevOpsAction.K8S_DESCRIBE:
            if not component:
                raise ValueError("A deployment name is required to describe it.")
            plan.steps.append(
                DevOpsStep(
                    f"Describe deployment/{component}",
                    f"kubectl describe deployment {_quote(component)}",
                )
            )

        elif action == DevOpsAction.K8S_RESTART:
            if not component:
                raise ValueError("A deployment name is required to restart it.")
            plan.steps.append(
                DevOpsStep(
                    f"Restart deployment/{component}",
                    f"kubectl rollout restart deployment/{_quote(component)}",
                    changes_state=True,
                )
            )
            plan.steps.append(
                DevOpsStep(
                    f"Wait for deployment/{component}",
                    f"kubectl rollout status deployment/{_quote(component)} --timeout=90s",
                )
            )

        elif action in {
            DevOpsAction.TERRAFORM_INIT,
            DevOpsAction.TERRAFORM_FMT,
            DevOpsAction.TERRAFORM_VALIDATE,
            DevOpsAction.TERRAFORM_PLAN,
            DevOpsAction.TERRAFORM_EXPLAIN_PLAN,
            DevOpsAction.TERRAFORM_STATE,
        }:
            directory = _terraform_dir(workspace, request.terraform_dir)
            prefix = f"terraform -chdir={_quote(directory)}"
            terraform_path = workspace if directory == "." else workspace / directory

            if action == DevOpsAction.TERRAFORM_INIT:
                plan.steps.append(
                    DevOpsStep(
                        "Initialize Terraform providers locally",
                        f"{prefix} init -backend=false -input=false -no-color",
                        changes_state=True,
                    )
                )
            elif action == DevOpsAction.TERRAFORM_FMT:
                plan.steps.append(
                    DevOpsStep(
                        "Format Terraform files",
                        f"{prefix} fmt",
                        changes_state=True,
                    )
                )
            elif action == DevOpsAction.TERRAFORM_VALIDATE:
                if not (terraform_path / ".terraform").exists():
                    plan.steps.append(
                        DevOpsStep(
                            "Initialize Terraform providers locally",
                            f"{prefix} init -backend=false -input=false -no-color",
                            changes_state=True,
                        )
                    )
                    plan.notes.append(
                        "Terraform was not initialized, so local provider initialization is included before validation."
                    )
                plan.steps.append(
                    DevOpsStep(
                        "Check Terraform formatting",
                        f"{prefix} fmt -check",
                    )
                )
                plan.steps.append(
                    DevOpsStep(
                        "Validate Terraform configuration",
                        f"{prefix} validate -no-color",
                    )
                )
            elif action in {DevOpsAction.TERRAFORM_PLAN, DevOpsAction.TERRAFORM_EXPLAIN_PLAN}:
                plan.steps.append(
                    DevOpsStep(
                        "Create a read-only Terraform plan",
                        f"{prefix} plan -input=false -no-color",
                    )
                )
            elif action == DevOpsAction.TERRAFORM_STATE:
                plan.steps.append(
                    DevOpsStep(
                        "List Terraform state resources",
                        f"{prefix} state list",
                    )
                )

        else:
            raise ValueError(f"Unsupported DevOps action: {action}")

        plan.requires_approval = any(step.changes_state for step in plan.steps)
        plan.safe_to_execute = bool(plan.steps)

    except ValueError as exc:
        plan.safe_to_execute = False
        plan.notes.append(str(exc))

    return plan



def _parse_docker_state_line(output: str) -> dict[str, str] | None:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    parts = lines[-1].split("|", 5)
    if len(parts) < 5:
        return None
    while len(parts) < 6:
        parts.append("")
    status, running, exit_code, restarts, health, error = parts
    return {
        "status": status,
        "running": running,
        "exit_code": exit_code,
        "restarts": restarts,
        "health": health,
        "error": error,
    }


_SENSITIVE_STARTUP_ARG = re.compile(
    r"(?i)(?:password|passwd|token|secret|api[_-]?key|access[_-]?key|credential|authorization|bearer)"
)


def _sanitize_startup_arg(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) > 160:
        return "<redacted-long-arg>"
    if _SENSITIVE_STARTUP_ARG.search(value):
        return "<redacted-sensitive-arg>"
    # URLs containing userinfo can embed credentials. Do not surface them.
    if re.search(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/\s]+@", value):
        return "<redacted-url-arg>"
    return value


def _docker_startup_hint(step_results: list[DevOpsStepResult]) -> tuple[str, str, str] | None:
    result = next(
        (
            item.result
            for item in step_results
            if "startup" in item.step.label.lower()
            and "inspect" in item.step.label.lower()
        ),
        None,
    )
    if result is None:
        return None
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if not lines:
        return None
    parts = lines[-1].split("|", 2)
    while len(parts) < 3:
        parts.append("")
    executable, first_arg, second_arg = (part.strip() for part in parts)
    if not executable and not first_arg and not second_arg:
        return None
    return executable, first_arg, second_arg


def _safe_effective_startup_command(
    startup_hint: tuple[str, str, str] | None,
) -> str | None:
    if not startup_hint:
        return None
    executable, first_arg, second_arg = startup_hint
    exe_name = Path(executable).name.lower() if executable else ""
    wrapper_names = {
        "docker-entrypoint.sh",
        "docker-entrypoint",
        "entrypoint.sh",
        "tini",
        "dumb-init",
    }
    # Common image wrappers delegate to Args[0:]. Show the application command
    # rather than implying the wrapper itself is the failing program.
    tokens = [first_arg, second_arg] if exe_name in wrapper_names and first_arg else [executable, first_arg, second_arg]
    rendered = [_sanitize_startup_arg(token) for token in tokens if token]
    rendered = [token for token in rendered if token]
    if not rendered:
        return None
    return " ".join(shlex.quote(token) if re.search(r"\s", token) else token for token in rendered)


def _node_missing_module_details(
    text: str,
    startup_hint: tuple[str, str, str] | None = None,
) -> tuple[str, str] | None:
    """Classify Node.js MODULE_NOT_FOUND without treating every case as npm deps.

    Returns ``(kind, target)`` where kind is one of ``entry_file``,
    ``local_module``, ``dependency`` or ``unknown``.
    """
    match = re.search(r"Cannot find module\s+['\"]([^'\"]+)['\"]", text, re.I)
    if not match:
        # ESM errors commonly use ERR_MODULE_NOT_FOUND with similar wording.
        match = re.search(r"module\s+['\"]([^'\"]+)['\"]\s+not found", text, re.I)
    if not match:
        return None

    missing = match.group(1).strip()
    executable = ""
    first_arg = ""
    second_arg = ""
    if startup_hint:
        executable, first_arg, second_arg = startup_hint

    exe_name = Path(executable).name.lower() if executable else ""
    first_name = Path(first_arg).name.lower() if first_arg else ""
    wrapper_names = {"docker-entrypoint.sh", "docker-entrypoint", "entrypoint.sh", "tini", "dumb-init"}
    if exe_name in wrapper_names and first_name in {"node", "nodejs"}:
        node_runtime = True
        node_entry_arg = second_arg
    else:
        node_runtime = exe_name in {"node", "nodejs"}
        node_entry_arg = first_arg
    js_suffixes = {".js", ".cjs", ".mjs", ".json"}
    missing_path = Path(missing)
    arg_path = Path(node_entry_arg) if node_entry_arg else None

    # ``node missing.js`` often appears in the log as ``/app/missing.js``.
    if node_runtime and node_entry_arg and arg_path is not None:
        same_name = missing_path.name == arg_path.name
        arg_looks_file = arg_path.suffix.lower() in js_suffixes
        if same_name and arg_looks_file:
            return "entry_file", missing

    # Node prints an empty require stack when the initial entry module itself
    # could not be loaded. Treat this as strong supporting evidence only when
    # the missing target is file-like, not a bare package name.
    empty_require_stack = bool(
        re.search(r"requireStack\s*:\s*\[\s*\]", text, re.I)
    )
    file_like = (
        missing.startswith(("/", "./", "../"))
        or missing_path.suffix.lower() in js_suffixes
    )
    if node_runtime and file_like and empty_require_stack:
        return "entry_file", missing

    if missing.startswith(("/", "./", "../")):
        return "local_module", missing

    # Bare module specifiers (express, pg, @scope/pkg) are normally package
    # dependencies. A slash inside a scoped/bare package does not make it a
    # local file unless it starts with ./, ../ or /.
    if missing and not any(ch.isspace() for ch in missing):
        return "dependency", missing

    return "unknown", missing


def _high_signal_log_excerpts(text: str, limit: int = 3) -> list[str]:
    """Return a tiny evidence excerpt instead of dumping the whole log sample."""
    needles = (
        "cannot find module",
        "module_not_found",
        "err_module_not_found",
        "econnrefused",
        "connection refused",
        "eaddrinuse",
        "address already in use",
        "permission denied",
        "no such file or directory",
        "out of memory",
        "oom",
        "syntax error",
    )
    excerpts: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.strip().split())
        if not line:
            continue
        lower = line.lower()
        if any(needle in lower for needle in needles):
            if len(line) > 180:
                line = line[:177] + "..."
            if line not in excerpts:
                excerpts.append(line)
        if len(excerpts) >= limit:
            break
    return excerpts


def _high_signal_docker_log_findings(
    text: str,
    startup_hint: tuple[str, str, str] | None = None,
) -> list[str]:
    lower = text.lower()
    findings: list[str] = []

    missing = _node_missing_module_details(text, startup_hint)
    if missing:
        kind, target = missing
        if kind == "entry_file":
            findings.append(
                f"Node.js logs show a missing startup/entry file: {target}."
            )
        elif kind == "local_module":
            findings.append(
                f"Node.js logs show a missing local application module/file: {target}."
            )
        elif kind == "dependency":
            findings.append(
                f"Node.js logs show a missing runtime dependency/module: {target}."
            )
        else:
            findings.append(f"Node.js logs show MODULE_NOT_FOUND for: {target}.")

    patterns = (
        (("econnrefused",), "Logs contain ECONNREFUSED; a configured dependency is refusing connections."),
        (("connection refused",), "Logs contain 'connection refused'; verify dependency host/port and Docker networking."),
        (("address already in use",), "Logs report an address/port already in use."),
        (("eaddrinuse",), "Logs report EADDRINUSE; the application could not bind its configured port."),
        (("permission denied",), "Logs contain a permission-denied error; check container user and mounted-file permissions."),
        (("no such file or directory",), "Logs reference a missing file or executable."),
        (("out of memory",), "Logs contain an out-of-memory message."),
        (("oom", "killed"), "Logs suggest the process may have been killed because of memory pressure."),
        (("syntax error",), "Logs contain a syntax error; inspect the referenced file and line."),
    )
    for needles, message in patterns:
        if all(needle in lower for needle in needles) and message not in findings:
            findings.append(message)
    return findings[:5]

def summarize_docker_execution(
    action: DevOpsAction,
    step_results: list[DevOpsStepResult],
) -> list[str]:
    summary: list[str] = []
    state_result = next(
        (
            item.result
            for item in reversed(step_results)
            if "runtime state" in item.step.label.lower()
            or "verify docker container" in item.step.label.lower()
            or "verify rebuilt container" in item.step.label.lower()
        ),
        None,
    )
    if state_result is not None:
        state = _parse_docker_state_line(state_result.stdout)
        if state:
            status = state["status"] or "unknown"
            health = state["health"] or "none"
            restarts = state["restarts"] or "0"
            exit_code = state["exit_code"] or "unknown"
            summary.append(
                f"Docker runtime: status={status}, health={health}, restarts={restarts}, exit={exit_code}."
            )
            if status != "running":
                summary.append(
                    f"Container is not running (status={status}, exit={exit_code})."
                )
            if health == "unhealthy":
                summary.append("Docker health check reports UNHEALTHY.")
            elif health == "none" and action in {DevOpsAction.DOCKER_HEALTH, DevOpsAction.DOCKER_DIAGNOSE}:
                summary.append("No Docker HEALTHCHECK is active for this container; runtime status alone does not prove application readiness.")
            try:
                if int(restarts) > 0:
                    summary.append(
                        f"Container has restarted {restarts} time(s); investigate recent logs and dependency health."
                    )
            except ValueError:
                pass
            if state["error"]:
                summary.append(f"Docker runtime error: {state['error']}")

    if action == DevOpsAction.DOCKER_CONNECTIVITY:
        port_result = next(
            (item.result for item in step_results if "published ports" in item.step.label.lower()),
            None,
        )
        mappings: list[tuple[int, int]] = []
        if port_result is not None:
            for line in port_result.stdout.splitlines():
                match = re.search(r"(\d+)/(?:tcp|udp)\s+->\s+(?:0\.0\.0\.0|\[::\]|127\.0\.0\.1):(\d+)", line)
                if match:
                    mappings.append((int(match.group(1)), int(match.group(2))))
            if mappings:
                unique: list[tuple[int, int]] = []
                for pair in mappings:
                    if pair not in unique:
                        unique.append(pair)
                rendered = ", ".join(
                    f"container {container_port} -> host {host_port}"
                    for container_port, host_port in unique
                )
                summary.append("Docker port mapping: " + rendered + ".")
                for _, host_port in unique:
                    summary.append(f"Host-accessible endpoint candidate: http://localhost:{host_port}/")
            else:
                summary.append("No host-published Docker port was reported for this container.")

        probe_result = next(
            (item.result for item in step_results if "probe localhost:" in item.step.label.lower()),
            None,
        )
        if probe_result is not None:
            text = (probe_result.stdout or "").strip()
            probe_match = re.search(r"curl_rc=(\d+)\|HTTP\s+(\d{3})", text)
            if probe_match and probe_match.group(1) == "0":
                summary.append(f"Local HTTP probe succeeded (HTTP {probe_match.group(2)}).")
            elif probe_match:
                summary.append(
                    f"Local HTTP probe could not connect successfully (curl exit {probe_match.group(1)}, HTTP {probe_match.group(2)})."
                )
            elif text:
                summary.append(f"Local HTTP probe result: {text}")

    if action == DevOpsAction.DOCKER_DIAGNOSE:
        log_result = next(
            (item.result for item in step_results if "recent logs" in item.step.label.lower()),
            None,
        )
        if log_result is not None:
            findings = _high_signal_docker_log_findings(
                log_result.stdout + "\n" + log_result.stderr,
                _docker_startup_hint(step_results),
            )
            if findings:
                summary.extend(findings)
            else:
                summary.append(
                    "No common high-signal Docker/application error pattern was detected in the sampled logs."
                )

    if action == DevOpsAction.DOCKER_SECURITY:
        security = next(
            (item.result.stdout.strip() for item in step_results if "security settings" in item.step.label.lower()),
            "",
        )
        if security:
            summary.append("Runtime security snapshot: " + security.replace("|", ", "))
            lower_security = security.lower()
            if "user=root/default" in lower_security:
                summary.append("Security review: container uses the image default/root user unless the image changes user internally.")
            if "privileged=true" in lower_security:
                summary.append("Security review: container is running in privileged mode; review whether that level of host access is necessary.")
            if "readonly_rootfs=false" in lower_security:
                summary.append("Security review: root filesystem is writable; consider read-only rootfs when the application supports it.")

    if action == DevOpsAction.DOCKER_OPTIMIZE:
        size_result = next(
            (item.result for item in step_results if "image size" in item.step.label.lower()),
            None,
        )
        if size_result and size_result.stdout.strip().isdigit():
            size_bytes = int(size_result.stdout.strip())
            summary.append(f"Docker image size: {size_bytes / (1024 * 1024):.1f} MiB.")

    return summary



def _docker_execution_facts(
    step_results: list[DevOpsStepResult],
) -> tuple[dict[str, str] | None, list[tuple[int, int]], tuple[int, int] | None, str]:
    state_result = next(
        (
            item.result
            for item in reversed(step_results)
            if "runtime state" in item.step.label.lower()
            or "verify docker container" in item.step.label.lower()
            or "verify rebuilt container" in item.step.label.lower()
        ),
        None,
    )
    state = _parse_docker_state_line(state_result.stdout) if state_result else None

    port_result = next(
        (item.result for item in step_results if "published ports" in item.step.label.lower()),
        None,
    )
    mappings: list[tuple[int, int]] = []
    if port_result is not None:
        for line in port_result.stdout.splitlines():
            match = re.search(
                r"(\d+)/(?:tcp|udp)\s+->\s+(?:0\.0\.0\.0|\[::\]|127\.0\.0\.1):(\d+)",
                line,
            )
            if match:
                pair = (int(match.group(1)), int(match.group(2)))
                if pair not in mappings:
                    mappings.append(pair)

    probe_result = next(
        (item.result for item in step_results if "probe localhost:" in item.step.label.lower()),
        None,
    )
    probe: tuple[int, int] | None = None
    probe_text = ""
    if probe_result is not None:
        probe_text = (probe_result.stdout or "").strip()
        match = re.search(r"curl_rc=(\d+)\|HTTP\s+(\d{3})", probe_text)
        if match:
            probe = (int(match.group(1)), int(match.group(2)))

    return state, mappings, probe, probe_text


def reason_docker_execution(
    plan: DevOpsPlan,
    step_results: list[DevOpsStepResult],
) -> tuple[list[str], list[str], list[str]]:
    """Turn trusted Docker evidence into a concise operator-facing diagnosis.

    This function only reasons over evidence already collected by trusted read-only
    steps and plan metadata. It does not run new commands or make state changes.
    """

    action = plan.request.action
    if not action.value.startswith("docker_"):
        return [], [], []

    target = plan.inferred_component or plan.request.component or "container"
    diagnosis: list[str] = []
    evidence: list[str] = []
    next_actions: list[str] = []
    state, mappings, probe, probe_text = _docker_execution_facts(step_results)

    if state:
        status = state.get("status") or "unknown"
        health = state.get("health") or "none"
        restarts = state.get("restarts") or "0"
        exit_code = state.get("exit_code") or "unknown"
        if action != DevOpsAction.DOCKER_GUIDED_REPAIR:
            evidence.append(
                f"Docker reports {target}: status={status}, exit={exit_code}, "
                f"restarts={restarts}, health={health}."
            )

    if action == DevOpsAction.DOCKER_GUIDED_REPAIR:
        pre_state_result = next(
            (
                item.result
                for item in step_results
                if "current runtime state" in item.step.label.lower()
            ),
            None,
        )
        post_state_result = next(
            (
                item.result
                for item in reversed(step_results)
                if "repaired runtime state" in item.step.label.lower()
            ),
            None,
        )

        pre_state = (
            _parse_docker_state_line(pre_state_result.stdout)
            if pre_state_result is not None
            else None
        )
        post_state = (
            _parse_docker_state_line(post_state_result.stdout)
            if post_state_result is not None
            else state
        )

        if pre_state:
            evidence.append(
                "Pre-repair Docker state: "
                f"status={pre_state.get('status') or 'unknown'}, "
                f"exit={pre_state.get('exit_code') or 'unknown'}, "
                f"restarts={pre_state.get('restarts') or '0'}, "
                f"health={pre_state.get('health') or 'none'}."
            )

        if plan.request.startup_target:
            evidence.append(
                "Replacement startup target explicitly supplied by operator: "
                f"'{plan.request.startup_target}'."
            )

        if post_state:
            evidence.append(
                "Post-repair Docker state: "
                f"status={post_state.get('status') or 'unknown'}, "
                f"exit={post_state.get('exit_code') or 'unknown'}, "
                f"restarts={post_state.get('restarts') or '0'}, "
                f"health={post_state.get('health') or 'none'}."
            )

        post_logs = next(
            (
                item.result
                for item in reversed(step_results)
                if "post-repair logs" in item.step.label.lower()
            ),
            None,
        )
        if post_logs is not None and post_logs.returncode == 0:
            evidence.append(
                "Bounded post-repair logs were collected successfully."
            )

        evidence.append(
            f"Rollback backup preserved as '{target}-iceman-backup'."
        )

    if action == DevOpsAction.DOCKER_CONNECTIVITY:
        requested = plan.request.port
        owner: str | None = None
        if requested is not None:
            pattern = re.compile(
                rf"Requested host port {requested} is currently published by Docker container '([^']+)'\."
            )
            for note in plan.notes:
                match = pattern.search(note)
                if match:
                    owner = match.group(1)
                    break

        if mappings:
            rendered = ", ".join(
                f"container {container_port} -> host {host_port}"
                for container_port, host_port in mappings
            )
            evidence.append(f"Published mapping: {rendered}.")

        if probe is not None:
            curl_rc, http_code = probe
            if curl_rc == 0 and 200 <= http_code < 400:
                evidence.append(f"Local HTTP probe succeeded with HTTP {http_code}.")
                diagnosis.append(f"{target} is running and its published HTTP endpoint is reachable locally.")
            elif curl_rc == 0:
                evidence.append(f"Local HTTP probe reached the application but returned HTTP {http_code}.")
                diagnosis.append(
                    f"{target} is reachable at the transport level, but the application returned HTTP {http_code}."
                )
            else:
                evidence.append(f"Local HTTP probe failed with curl exit {curl_rc} and HTTP {http_code}.")
                diagnosis.append(
                    f"{target} has a published port, but the bounded localhost HTTP probe could not establish a healthy application response."
                )
        elif probe_text:
            evidence.append(f"Local HTTP probe output: {probe_text}")

        if requested is not None and mappings:
            host_ports = [host for _, host in mappings]
            if requested not in host_ports:
                actual = host_ports[0]
                diagnosis.append(
                    f"The port mismatch is the likely cause of the reachability confusion: host port {requested} is not the published port for {target}; use host port {actual}."
                )
                if owner and owner != target:
                    evidence.append(f"Host port {requested} is currently owned by Docker container '{owner}'.")
                next_actions.append(f"Use http://localhost:{actual}/ when accessing {target} from this host.")
            else:
                next_actions.append(f"Continue using host port {requested}; it is published by {target}.")
        elif mappings:
            next_actions.append(f"Use http://localhost:{mappings[0][1]}/ for a local HTTP check.")

        if state and (state.get("health") or "none") == "none":
            next_actions.append(
                "Consider adding an application-aware Docker HEALTHCHECK so Docker can distinguish a running process from a ready application."
            )

    elif action in {DevOpsAction.DOCKER_HEALTH, DevOpsAction.DOCKER_DIAGNOSE}:
        if state:
            status = state.get("status") or "unknown"
            health = state.get("health") or "none"
            restarts_text = state.get("restarts") or "0"
            exit_code = state.get("exit_code") or "unknown"
            try:
                restarts = int(restarts_text)
            except ValueError:
                restarts = 0

            if status != "running":
                diagnosis.append(f"{target} is not running (status={status}, exit={exit_code}).")
                if action == DevOpsAction.DOCKER_HEALTH:
                    next_actions.append(f"Inspect recent errors with: show important errors in {target} logs")
            elif health == "unhealthy":
                diagnosis.append(f"{target} is running, but Docker marks its health check as unhealthy.")
                next_actions.append(f"Inspect health-check/application failures with: show important errors in {target} logs")
            elif restarts > 0:
                diagnosis.append(f"{target} is currently running but has restarted {restarts} time(s), which indicates runtime instability.")
                next_actions.append(f"Inspect recent errors with: show important errors in {target} logs")
            elif action == DevOpsAction.DOCKER_HEALTH and health == "none":
                diagnosis.append(
                    f"{target} is running without restarts, but no Docker HEALTHCHECK is configured, so Docker runtime state alone cannot prove application readiness."
                )
                next_actions.append(
                    "Add an application-aware HEALTHCHECK or run a bounded connectivity check against the published application port."
                )
            elif action == DevOpsAction.DOCKER_HEALTH:
                diagnosis.append(f"{target} is running and Docker reports health={health}.")

        if action == DevOpsAction.DOCKER_DIAGNOSE:
            log_result = next(
                (item.result for item in step_results if "recent logs" in item.step.label.lower()),
                None,
            )
            log_text = ""
            if log_result is not None:
                log_text = (log_result.stdout or "") + "\n" + (log_result.stderr or "")
            lower = log_text.lower()
            startup_hint = _docker_startup_hint(step_results)
            excerpts = _high_signal_log_excerpts(log_text)
            safe_startup = _safe_effective_startup_command(startup_hint)
            if "econnrefused" in lower or "connection refused" in lower:
                diagnosis.append(
                    "The strongest log signal is a refused dependency connection; the application is reaching a host/port where nothing is accepting the connection."
                )
                evidence.append("Recent logs contain ECONNREFUSED/connection refused.")
                endpoint = re.search(r"(?:ECONNREFUSED|connection refused)[^\n]*?((?:\d{1,3}\.){3}\d{1,3}|localhost|[A-Za-z0-9_.-]+):(\d{2,5})", log_text, re.I)
                if endpoint:
                    evidence.append(f"Refused endpoint observed in logs: {endpoint.group(1)}:{endpoint.group(2)}.")
                next_actions.append(
                    "Verify the dependency host/port and the dependency container/service health; inside a container, localhost refers to that same container."
                )
            elif "eaddrinuse" in lower or "address already in use" in lower:
                diagnosis.append("The application failed to bind a port because that address/port is already in use.")
                evidence.append("Recent logs contain EADDRINUSE/address already in use.")
                next_actions.append("Identify the existing listener or change the application's configured bind port before restarting.")
            elif "permission denied" in lower:
                diagnosis.append("The strongest log signal is a permissions failure inside the container.")
                evidence.append("Recent logs contain 'permission denied'.")
                next_actions.append("Check the container user plus ownership/mode of mounted or application files.")
            elif "cannot find module" in lower or "module not found" in lower or "module_not_found" in lower:
                missing = _node_missing_module_details(log_text, startup_hint)
                if missing:
                    kind, missing_target = missing
                    if kind == "entry_file":
                        diagnosis.append(
                            f"The startup command references a Node.js entry file/module that is not present in the container: {missing_target}."
                        )
                        evidence.append(
                            f"Recent logs report MODULE_NOT_FOUND for startup target '{missing_target}'."
                        )
                        safe_startup = _safe_effective_startup_command(startup_hint)
                        if safe_startup:
                            evidence.append(
                                f"Safe startup reconstruction: {safe_startup}."
                            )
                        next_actions.append(
                            "Verify the intended Node.js entry file and correct the container startup command before restarting or recreating the container."
                        )
                    elif kind == "local_module":
                        diagnosis.append(
                            f"The application references a local module/file that is missing from the container: {missing_target}."
                        )
                        evidence.append(
                            f"Recent logs report MODULE_NOT_FOUND for local target '{missing_target}'."
                        )
                        next_actions.append(
                            "Verify COPY/build-context paths, .dockerignore rules, working directory, and the local import path before rebuilding."
                        )
                    elif kind == "dependency":
                        diagnosis.append(
                            f"The application is missing a runtime package/module dependency: {missing_target}."
                        )
                        evidence.append(
                            f"Recent logs report MODULE_NOT_FOUND for package/module '{missing_target}'."
                        )
                        next_actions.append(
                            "Verify the dependency is declared and installed in the runtime image, then rebuild before restarting."
                        )
                    else:
                        diagnosis.append(
                            f"Node.js reported MODULE_NOT_FOUND for '{missing_target}', but the sampled evidence is not sufficient to classify it as an entry file, local module, or package dependency."
                        )
                        evidence.append(
                            f"Recent logs report MODULE_NOT_FOUND for '{missing_target}'."
                        )
                        next_actions.append(
                            "Inspect the referenced module path and startup/import configuration before rebuilding or restarting."
                        )
                else:
                    diagnosis.append(
                        "Node.js reported a missing module, but the sampled log did not expose a target clearly enough to distinguish an entry file from a package dependency."
                    )
                    evidence.append("Recent logs contain MODULE_NOT_FOUND/cannot find module.")
                    next_actions.append(
                        "Inspect the high-signal log excerpt and startup/import configuration before choosing whether to correct the command or rebuild dependencies."
                    )
                for excerpt in excerpts[:2]:
                    evidence.append(f"Log excerpt: {excerpt}")
            elif "no such file or directory" in lower:
                diagnosis.append("The application references a file or executable that is missing from the container filesystem.")
                evidence.append("Recent logs contain 'no such file or directory'.")
                next_actions.append("Verify COPY paths, working directory, entrypoint/command, and ignored build-context files.")
            elif "out of memory" in lower or ("oom" in lower and "killed" in lower):
                diagnosis.append("Logs suggest the container process may be failing because of memory pressure.")
                evidence.append("Recent logs contain out-of-memory/OOM-killed signals.")
                next_actions.append("Inspect container memory limits and workload usage before increasing limits or restarting repeatedly.")
            elif action == DevOpsAction.DOCKER_DIAGNOSE and not diagnosis:
                diagnosis.append("No common high-signal failure pattern was established from the sampled Docker state and logs.")
                next_actions.append("Inspect the full application logs or run a component-specific connectivity/health check next.")

            # Surface only the small set of high-signal lines used by the
            # diagnosis. Full sampled logs stay hidden unless the operator
            # explicitly asks to show logs.
            if not ("cannot find module" in lower or "module not found" in lower or "module_not_found" in lower):
                for excerpt in excerpts[:2]:
                    evidence.append(f"Log excerpt: {excerpt}")

    # Keep suggestions concise and de-duplicated.
    diagnosis = list(dict.fromkeys(diagnosis))[:4]
    evidence = list(dict.fromkeys(evidence))[:6]
    next_actions = list(dict.fromkeys(next_actions))[:4]
    return diagnosis, evidence, next_actions

def _k8s_step_text(
    step_results: list[DevOpsStepResult],
    label_fragment: str,
) -> str:
    fragment = label_fragment.lower()
    result = next(
        (
            item.result
            for item in step_results
            if fragment in item.step.label.lower()
        ),
        None,
    )
    if result is None:
        return ""
    return ((result.stdout or "") + "\n" + (result.stderr or "")).strip()


def _parse_k8s_pod_status(
    output: str,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    pod: dict[str, str] = {}
    containers: list[dict[str, str]] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("POD|"):
            parts = line.split("|", 3)
            while len(parts) < 4:
                parts.append("")
            _, name, phase, reason = parts
            pod = {
                "name": name.strip(),
                "phase": phase.strip(),
                "reason": reason.strip(),
            }
            continue

        if line.startswith("CONTAINER|") or line.startswith("INIT|"):
            parts = line.split("|", 8)
            while len(parts) < 9:
                parts.append("")

            (
                kind,
                name,
                ready,
                restarts,
                waiting_reason,
                terminated_reason,
                terminated_exit,
                last_reason,
                last_exit,
            ) = parts

            containers.append({
                "kind": kind.strip(),
                "name": name.strip(),
                "ready": ready.strip(),
                "restarts": restarts.strip(),
                "waiting_reason": waiting_reason.strip(),
                "terminated_reason": terminated_reason.strip(),
                "terminated_exit": terminated_exit.strip(),
                "last_reason": last_reason.strip(),
                "last_exit": last_exit.strip(),
            })

    return pod, containers


def _k8s_oom_termination(
    container: dict[str, str],
) -> tuple[str, str] | None:
    """Return OOMKilled state as (current|previous, exit_code)."""
    terminated = (container.get("terminated_reason") or "").lower()
    last_reason = (container.get("last_reason") or "").lower()

    if terminated == "oomkilled":
        return (
            "current",
            container.get("terminated_exit") or "unknown",
        )

    if last_reason == "oomkilled":
        return (
            "previous",
            container.get("last_exit") or "unknown",
        )

    return None


def summarize_k8s_pod_execution(
    plan: DevOpsPlan,
    step_results: list[DevOpsStepResult],
) -> list[str]:
    if plan.request.action != DevOpsAction.K8S_DIAGNOSE_POD:
        return []

    status_text = _k8s_step_text(step_results, "inspect pod status")
    pod, containers = _parse_k8s_pod_status(status_text)

    phase = pod.get("phase") or "unknown"
    target = pod.get("name") or plan.inferred_component or plan.request.component or "pod"

    total_restarts = 0
    for container in containers:
        try:
            total_restarts += int(container.get("restarts") or "0")
        except ValueError:
            pass

    summary = [
        (
            f"Kubernetes pod {target}: phase={phase}, "
            f"containers={len(containers)}, total_restarts={total_restarts}."
        )
    ]

    if plan.target_context:
        summary.append(
            f"Kubernetes target: context={plan.target_context}, "
            f"namespace={plan.target_namespace or 'default'}."
        )

    return summary


def reason_k8s_pod_execution(
    plan: DevOpsPlan,
    step_results: list[DevOpsStepResult],
) -> tuple[list[str], list[str], list[str]]:
    if plan.request.action != DevOpsAction.K8S_DIAGNOSE_POD:
        return [], [], []

    status_text = _k8s_step_text(step_results, "inspect pod status")
    conditions_text = _k8s_step_text(step_results, "inspect pod conditions")
    events_text = _k8s_step_text(step_results, "recent pod events")
    current_logs = _k8s_step_text(step_results, "bounded current logs")
    previous_logs = _k8s_step_text(step_results, "bounded previous logs")

    pod, containers = _parse_k8s_pod_status(status_text)

    target = (
        pod.get("name")
        or plan.inferred_component
        or plan.request.component
        or "pod"
    )
    phase = pod.get("phase") or "unknown"
    pod_reason = pod.get("reason") or ""

    diagnosis: list[str] = []
    evidence: list[str] = []
    next_actions: list[str] = []

    evidence.append(f"Pod {target} phase={phase}.")
    if pod_reason:
        evidence.append(f"Pod-level reason: {pod_reason}.")

    has_image_pull = False
    has_crashloop = False
    has_oom = False
    total_restarts = 0

    for container in containers:
        name = container.get("name") or "container"
        waiting = (container.get("waiting_reason") or "").lower()
        terminated = (container.get("terminated_reason") or "").lower()
        last_reason = (container.get("last_reason") or "").lower()
        last_exit = container.get("last_exit") or ""
        terminated_exit = container.get("terminated_exit") or ""

        try:
            restarts = int(container.get("restarts") or "0")
        except ValueError:
            restarts = 0
        total_restarts += restarts

        if waiting in {"imagepullbackoff", "errimagepull"}:
            has_image_pull = True
            diagnosis.append(
                f"Container '{name}' cannot start because Kubernetes reports "
                f"{container.get('waiting_reason')}."
            )
            evidence.append(
                f"{name}: waiting={container.get('waiting_reason')}."
            )

        if waiting == "crashloopbackoff":
            has_crashloop = True
            diagnosis.append(
                f"Container '{name}' is in CrashLoopBackOff after repeated failures."
            )
            evidence.append(
                f"{name}: waiting=CrashLoopBackOff, restarts={restarts}."
            )

        oom_termination = _k8s_oom_termination(container)
        if oom_termination is not None:
            has_oom = True
            termination_scope, exit_code = oom_termination
            diagnosis.append(
                f"Container '{name}' was terminated with OOMKilled "
                f"(exit={exit_code})."
            )
            evidence.append(
                f"{name}: {termination_scope} termination=OOMKilled, "
                f"exit={exit_code}."
            )

        if restarts > 0 and not (
            waiting == "crashloopbackoff"
            or terminated == "oomkilled"
            or last_reason == "oomkilled"
        ):
            reason = (
                container.get("last_reason")
                or container.get("terminated_reason")
                or "unknown"
            )
            exit_code = (
                container.get("last_exit")
                or container.get("terminated_exit")
                or "unknown"
            )
            evidence.append(
                f"{name}: restarts={restarts}, previous_reason={reason}, "
                f"previous_exit={exit_code}."
            )

    signal_text = (conditions_text + "\n" + events_text).lower()

    if "liveness probe failed" in signal_text:
        diagnosis.append(
            f"{target} has recent liveness-probe failures."
        )
        evidence.append("Recent Kubernetes events report a liveness probe failure.")
        next_actions.append(
            "Verify the liveness probe path/port, startup timing, timeout, and "
            "application responsiveness before changing restart behavior."
        )

    if "readiness probe failed" in signal_text:
        diagnosis.append(
            f"{target} has recent readiness-probe failures."
        )
        evidence.append("Recent Kubernetes events report a readiness probe failure.")
        next_actions.append(
            "Verify the readiness probe path/port and confirm the application "
            "becomes ready only after its dependencies are available."
        )

    if phase.lower() == "pending" and not has_image_pull:
        if "failedscheduling" in signal_text or "unschedulable" in signal_text:
            diagnosis.append(
                f"{target} is Pending because the scheduler could not place the pod."
            )
            evidence.append(
                "Recent Kubernetes events contain FailedScheduling/Unschedulable."
            )
            next_actions.append(
                "Review pod resource requests, node capacity, taints/tolerations, "
                "node selectors/affinity, and any unbound PVCs."
            )
        else:
            diagnosis.append(
                f"{target} is Pending, but the bounded evidence does not yet "
                "identify a specific scheduling or image-pull cause."
            )

    if has_image_pull:
        next_actions.append(
            "Verify the container image name/tag, registry reachability, and "
            "imagePullSecrets before retrying the workload."
        )

    if has_oom:
        next_actions.append(
            "Review actual memory usage and configured memory requests/limits "
            "before increasing limits or restarting repeatedly."
        )

    if has_crashloop:
        next_actions.append(
            "Review the bounded previous-container logs and the previous exit "
            "reason before changing the workload or restarting it."
        )

    if total_restarts > 0 and not has_crashloop and not has_oom:
        diagnosis.append(
            f"{target} has restarted {total_restarts} time(s); inspect the previous "
            "termination reason and bounded previous logs for the failure path."
        )

    if not diagnosis:
        if phase.lower() == "running":
            diagnosis.append(
                f"{target} is Running and the bounded Kubernetes evidence did not "
                "show CrashLoopBackOff, image-pull, OOM, scheduling, or probe failures."
            )
        elif phase.lower() == "failed":
            diagnosis.append(
                f"{target} is in Failed phase; inspect the recorded termination "
                "reason and exit code."
            )
        else:
            diagnosis.append(
                f"No supported high-signal pod failure class was established for "
                f"{target} from the bounded evidence."
            )

    current_match = re.search(r"ICEMAN_LOGS_RC=(\d+)", current_logs)
    previous_match = re.search(r"ICEMAN_LOGS_RC=(\d+)", previous_logs)

    if current_match:
        evidence.append(
            f"Bounded current-log probe exit={current_match.group(1)}."
        )
    if previous_match:
        evidence.append(
            f"Bounded previous-log probe exit={previous_match.group(1)}."
        )

    diagnosis = list(dict.fromkeys(diagnosis))[:5]
    evidence = list(dict.fromkeys(evidence))[:8]
    next_actions = list(dict.fromkeys(next_actions))[:5]

    return diagnosis, evidence, next_actions


def summarize_terraform_plan(output: str) -> list[str]:
    if "No changes." in output or "No changes. Your infrastructure matches" in output:
        return ["Terraform reports no infrastructure changes."]
    match = re.search(
        r"Plan:\s*(\d+)\s+to add,\s*(\d+)\s+to change,\s*(\d+)\s+to destroy",
        output,
    )
    if match:
        add, change, destroy = map(int, match.groups())
        return [
            f"Terraform plan summary: {add} to add, {change} to change, {destroy} to destroy."
        ]
    return []



def _parse_k8s_service_status(
    output: str,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    service: dict[str, str] = {}
    ports: list[dict[str, str]] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("SERVICE|"):
            parts = line.split("|", 4)
            while len(parts) < 5:
                parts.append("")
            _, name, service_type, cluster_ip, selector = parts
            service = {
                "name": name.strip(),
                "type": service_type.strip(),
                "cluster_ip": cluster_ip.strip(),
                "selector_raw": selector.strip(),
            }
            continue

        if line.startswith("PORT|"):
            parts = line.split("|", 4)
            while len(parts) < 5:
                parts.append("")
            _, name, port, target_port, protocol = parts
            ports.append({
                "name": name.strip(),
                "port": port.strip(),
                "target_port": target_port.strip(),
                "protocol": protocol.strip(),
            })

    return service, ports


def _parse_k8s_service_endpoints(
    output: str,
) -> tuple[list[str], list[dict[str, str]]]:
    slices: list[str] = []
    endpoints: list[dict[str, str]] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("SLICE|"):
            _, _, name = line.partition("|")
            if name.strip():
                slices.append(name.strip())
            continue

        if line.startswith("ENDPOINT|"):
            parts = line.split("|", 4)
            while len(parts) < 5:
                parts.append("")
            _, address, ready, serving, terminating = parts
            endpoints.append({
                "address": address.strip(),
                "ready": ready.strip(),
                "serving": serving.strip(),
                "terminating": terminating.strip(),
            })

    return slices, endpoints


def _parse_k8s_service_matching_pods(
    output: str,
) -> tuple[str, list[dict[str, str]]]:
    selector = ""
    pods: list[dict[str, str]] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("SELECTOR|"):
            selector = line.partition("|")[2].strip()
            continue

        if line.startswith("POD|"):
            parts = line.split("|", 3)
            while len(parts) < 4:
                parts.append("")
            _, name, phase, ready = parts
            pods.append({
                "name": name.strip(),
                "phase": phase.strip(),
                "ready": ready.strip(),
            })

    return selector, pods


def summarize_k8s_service_execution(
    plan: DevOpsPlan,
    step_results: list[DevOpsStepResult],
) -> list[str]:
    if plan.request.action not in {
        DevOpsAction.K8S_DIAGNOSE_SERVICE,
        DevOpsAction.K8S_SERVICE_CONNECTIVITY,
    }:
        return []

    service_text = _k8s_step_text(step_results, "inspect service routing")
    endpoints_text = _k8s_step_text(step_results, "inspect endpointslices")
    pods_text = _k8s_step_text(
        step_results,
        "inspect pods matching service selector",
    )

    service, _ = _parse_k8s_service_status(service_text)
    _, endpoints = _parse_k8s_service_endpoints(endpoints_text)
    selector, pods = _parse_k8s_service_matching_pods(pods_text)

    target = (
        service.get("name")
        or plan.inferred_component
        or plan.request.component
        or "service"
    )
    service_type = service.get("type") or "unknown"

    ready_pods = sum(
        1 for pod in pods
        if (pod.get("ready") or "").lower() == "true"
    )
    ready_endpoints = sum(
        1 for endpoint in endpoints
        if (endpoint.get("ready") or "").lower() == "true"
    )

    summary = [
        (
            f"Kubernetes service {target}: type={service_type}, "
            f"selector={selector or 'none'}, "
            f"matching_pods={len(pods)}, ready_matching_pods={ready_pods}, "
            f"endpoints={len(endpoints)}, ready_endpoints={ready_endpoints}."
        )
    ]

    if plan.target_context:
        summary.append(
            f"Kubernetes target: context={plan.target_context}, "
            f"namespace={plan.target_namespace or 'default'}."
        )

    return summary


def reason_k8s_service_execution(
    plan: DevOpsPlan,
    step_results: list[DevOpsStepResult],
) -> tuple[list[str], list[str], list[str]]:
    if plan.request.action not in {
        DevOpsAction.K8S_DIAGNOSE_SERVICE,
        DevOpsAction.K8S_SERVICE_CONNECTIVITY,
    }:
        return [], [], []

    service_text = _k8s_step_text(step_results, "inspect service routing")
    endpoints_text = _k8s_step_text(step_results, "inspect endpointslices")
    pods_text = _k8s_step_text(
        step_results,
        "inspect pods matching service selector",
    )

    service, ports = _parse_k8s_service_status(service_text)
    slices, endpoints = _parse_k8s_service_endpoints(endpoints_text)
    selector, pods = _parse_k8s_service_matching_pods(pods_text)

    target = (
        service.get("name")
        or plan.inferred_component
        or plan.request.component
        or "service"
    )
    service_type = service.get("type") or "unknown"
    cluster_ip = service.get("cluster_ip") or "unknown"

    ready_pods = [
        pod for pod in pods
        if (pod.get("ready") or "").lower() == "true"
    ]
    ready_endpoints = [
        endpoint for endpoint in endpoints
        if (endpoint.get("ready") or "").lower() == "true"
    ]

    diagnosis: list[str] = []
    evidence: list[str] = []
    next_actions: list[str] = []

    evidence.append(
        f"Service {target}: type={service_type}, clusterIP={cluster_ip}."
    )

    if ports:
        rendered_ports = ", ".join(
            (
                f"{port.get('port') or '?'}"
                f"->{port.get('target_port') or '?'}"
                f"/{port.get('protocol') or 'TCP'}"
            )
            for port in ports
        )
        evidence.append(f"Service ports: {rendered_ports}.")

    evidence.append(
        f"EndpointSlices={len(slices)}, endpoints={len(endpoints)}, "
        f"ready_endpoints={len(ready_endpoints)}."
    )

    if not selector:
        if service_type.lower() == "externalname":
            diagnosis.append(
                f"Service '{target}' is ExternalName and does not select Pods."
            )
            next_actions.append(
                "Verify the ExternalName target and DNS resolution rather than "
                "looking for Pod selector endpoints."
            )
        else:
            diagnosis.append(
                f"Service '{target}' has no Pod selector, so Iceman cannot "
                "associate it with backend Pods through label selection."
            )
            next_actions.append(
                "Confirm whether the Service intentionally uses manually managed "
                "EndpointSlices/endpoints or whether a selector is missing."
            )
        return diagnosis, evidence, next_actions

    evidence.append(f"Service selector: {selector}.")
    evidence.append(
        f"Selector matched {len(pods)} Pod(s); "
        f"{len(ready_pods)} are Ready."
    )

    if not pods:
        diagnosis.append(
            f"Service '{target}' selector '{selector}' matches no Pods "
            "in the target namespace."
        )
        if not endpoints:
            diagnosis.append(
                f"Service '{target}' currently has no EndpointSlice backends."
            )
        next_actions.append(
            "Compare the Service selector with the intended workload Pod labels. "
            "Do not patch either side until the intended label contract is confirmed."
        )
        return diagnosis, evidence, next_actions

    if not ready_pods:
        diagnosis.append(
            f"Service '{target}' selector matches {len(pods)} Pod(s), "
            "but none of the matching Pods are Ready."
        )
        if not ready_endpoints:
            diagnosis.append(
                f"Service '{target}' has no ready backend endpoints."
            )
        next_actions.append(
            "Diagnose the matching Pods' readiness, startup state, and readiness "
            "probes before changing the Service selector."
        )
        return diagnosis, evidence, next_actions

    if not ready_endpoints:
        diagnosis.append(
            f"Service '{target}' has Ready matching Pods but no ready "
            "EndpointSlice endpoints are currently published."
        )
        next_actions.append(
            "Re-check EndpointSlice publication and the matching Pods' readiness "
            "state before changing Service routing."
        )
        return diagnosis, evidence, next_actions

    diagnosis.append(
        f"Service '{target}' has {len(ready_endpoints)} ready backend "
        "endpoint(s); the bounded evidence does not show a selector/no-endpoint fault."
    )
    if plan.request.action == DevOpsAction.K8S_DIAGNOSE_SERVICE:
        next_actions.append(
            "If traffic is still failing, continue with Service-to-Pod connectivity "
            "and targetPort/application-listener checks."
        )

    return diagnosis, evidence, next_actions




def _parse_k8s_service_connectivity_probe(output: str) -> dict[str, str]:
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("CONNECTIVITY|"):
            continue

        parts = line.split("|")
        result: dict[str, str] = {
            "status": parts[1].strip().upper() if len(parts) > 1 else "",
        }

        for part in parts[2:]:
            key, separator, value = part.partition("=")
            if separator:
                result[key.strip()] = value.strip()

        return result

    return {}


def reason_k8s_service_connectivity_execution(
    plan: DevOpsPlan,
    step_results: list[DevOpsStepResult],
) -> tuple[list[str], list[str], list[str]]:
    if plan.request.action != DevOpsAction.K8S_SERVICE_CONNECTIVITY:
        return [], [], []

    probe_text = _k8s_step_text(
        step_results,
        "probe service-selected backend tcp connectivity",
    )
    probe = _parse_k8s_service_connectivity_probe(probe_text)

    target = (
        plan.inferred_component
        or plan.request.component
        or "service"
    )

    diagnosis: list[str] = []
    evidence: list[str] = []
    next_actions: list[str] = []

    if not probe:
        diagnosis.append(
            f"Service '{target}' connectivity probe returned no structured result."
        )
        next_actions.append(
            "Re-run the bounded connectivity diagnosis and inspect whether "
            "kubectl port-forward is available for the selected Service."
        )
        return diagnosis, evidence, next_actions

    status = probe.get("status", "")
    reason = probe.get("reason", "")
    service_port = probe.get("service_port", "")
    target_port = probe.get("target_port", "")
    local_port = probe.get("local_port", "")

    mapping = ""
    if service_port or target_port:
        mapping = (
            f"Service port {service_port or '?'} -> "
            f"targetPort {target_port or '?'}"
        )
        evidence.append(mapping + ".")

    if status == "SUCCESS":
        diagnosis.append(
            f"Service '{target}' selected backend accepted a bounded TCP "
            "connection through the Service targetPort mapping; no "
            "targetPort/application-listener failure is shown by this probe."
        )
        evidence.append(
            "Temporary kubectl port-forward TCP probe succeeded"
            + (f" via localhost:{local_port}" if local_port else "")
            + "."
        )
        next_actions.append(
            "If traffic still fails from workloads through the Service ClusterIP, "
            "investigate cluster DNS, NetworkPolicy, and the Service dataplane. "
            "The port-forward probe intentionally bypasses kube-proxy/ClusterIP routing."
        )
        return diagnosis, evidence, next_actions

    if status == "FAIL":
        if reason == "target-port-connect-failed":
            diagnosis.append(
                f"Service '{target}' has routing evidence, but the selected backend "
                "did not accept the bounded TCP connection at the resolved targetPort. "
                "A targetPort/application-listener mismatch is a strong candidate."
            )
            next_actions.append(
                "Confirm the application's actual listening port against the Service "
                "targetPort before changing the Service or restarting workloads."
            )
        elif reason == "port-forward-start":
            diagnosis.append(
                f"Service '{target}' backend probe could not establish the temporary "
                "kubectl port-forward. This does not prove an application-listener fault."
            )
            next_actions.append(
                "Check kubectl port-forward/RBAC access and whether an attachable "
                "backend Pod is available, then repeat the connectivity check."
            )
        else:
            diagnosis.append(
                f"Service '{target}' bounded backend TCP probe failed "
                f"({reason or 'unknown reason'})."
            )
            next_actions.append(
                "Inspect the Service port mapping and backend listener before making "
                "any routing changes."
            )

        evidence.append(
            f"Connectivity probe status=FAIL, reason={reason or 'unknown'}."
        )
        return diagnosis, evidence, next_actions

    if status == "SKIPPED":
        evidence.append(
            f"Connectivity probe skipped: {reason or 'unspecified reason'}."
        )

        if reason == "multiple-tcp-service-ports":
            count = probe.get("count", "?")
            diagnosis.append(
                f"Service '{target}' exposes {count} TCP ports, so Iceman did not "
                "guess which port to probe."
            )
            next_actions.append(
                f"Specify the Service port explicitly, for example: "
                f"check service {target} connectivity on port <service-port>"
            )
        elif reason == "requested-port-not-tcp-service-port":
            requested = probe.get("requested_port", "?")
            diagnosis.append(
                f"Requested port {requested} is not a TCP Service port on "
                f"Service '{target}'."
            )
            next_actions.append(
                "Choose one of the Service's declared TCP ports and repeat the check."
            )
        elif reason == "no-tcp-service-port":
            diagnosis.append(
                f"Service '{target}' exposes no TCP Service port that this bounded "
                "TCP probe can test."
            )
        elif reason == "no-service-selector":
            diagnosis.append(
                f"Service '{target}' has no selector, so Iceman did not attempt "
                "a Service-selected Pod port-forward."
            )
        elif reason == "no-ready-endpoints":
            next_actions.append(
                "Resolve the selector/Pod-readiness/EndpointSlice issue before "
                "testing backend listener connectivity."
            )
        else:
            diagnosis.append(
                f"Service '{target}' connectivity probe was skipped "
                f"({reason or 'unspecified reason'})."
            )

        return diagnosis, evidence, next_actions

    diagnosis.append(
        f"Service '{target}' connectivity probe returned unknown status "
        f"'{status or 'empty'}'."
    )
    return diagnosis, evidence, next_actions


def _parse_k8s_deployment_status(output: str) -> dict[str, str]:
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("DEPLOYMENT|"):
            continue

        parts = line.split("|", 9)
        while len(parts) < 10:
            parts.append("")

        (
            _,
            name,
            generation,
            observed_generation,
            desired,
            replicas,
            updated,
            ready,
            available,
            unavailable,
        ) = parts

        return {
            "name": name.strip(),
            "generation": generation.strip(),
            "observed_generation": observed_generation.strip(),
            "desired": desired.strip(),
            "replicas": replicas.strip(),
            "updated": updated.strip(),
            "ready": ready.strip(),
            "available": available.strip(),
            "unavailable": unavailable.strip(),
        }

    return {}


def _parse_k8s_deployment_conditions(
    output: str,
) -> list[dict[str, str]]:
    conditions: list[dict[str, str]] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("CONDITION|"):
            continue

        parts = line.split("|", 4)
        while len(parts) < 5:
            parts.append("")

        _, condition_type, status, reason, message = parts

        conditions.append({
            "type": condition_type.strip(),
            "status": status.strip(),
            "reason": reason.strip(),
            "message": message.strip(),
        })

    return conditions


def _parse_k8s_deployment_replicasets(
    output: str,
) -> tuple[str, list[dict[str, str]]]:
    selector = ""
    replicasets: list[dict[str, str]] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("SELECTOR|"):
            selector = line.partition("|")[2].strip()
            continue

        if not line.startswith("RS|"):
            continue

        parts = line.split("|", 6)
        while len(parts) < 7:
            parts.append("")

        _, name, desired, replicas, ready, available, owner = parts
        replicasets.append({
            "name": name.strip(),
            "desired": desired.strip(),
            "replicas": replicas.strip(),
            "ready": ready.strip(),
            "available": available.strip(),
            "owner": owner.strip(),
        })

    return selector, replicasets


def _parse_k8s_deployment_pods(
    output: str,
) -> tuple[str, list[dict[str, str]]]:
    selector = ""
    pods: list[dict[str, str]] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("SELECTOR|"):
            selector = line.partition("|")[2].strip()
            continue

        if not line.startswith("POD|"):
            continue

        parts = line.split("|", 5)
        while len(parts) < 6:
            parts.append("")

        _, name, phase, ready, waiting, restarts = parts
        pods.append({
            "name": name.strip(),
            "phase": phase.strip(),
            "ready": ready.strip(),
            "waiting": waiting.strip().strip(","),
            "restarts": restarts.strip().strip(","),
        })

    return selector, pods


def _k8s_int(value: str | None) -> int:
    try:
        return int(value or "0")
    except (TypeError, ValueError):
        return 0


def summarize_k8s_deployment_execution(
    plan: DevOpsPlan,
    step_results: list[DevOpsStepResult],
) -> list[str]:
    if plan.request.action != DevOpsAction.K8S_DIAGNOSE_DEPLOYMENT:
        return []

    status = _parse_k8s_deployment_status(
        _k8s_step_text(step_results, "inspect deployment status")
    )

    target = (
        status.get("name")
        or plan.inferred_component
        or plan.request.component
        or "deployment"
    )

    summary = [
        (
            f"Kubernetes deployment {target}: "
            f"desired={_k8s_int(status.get('desired'))}, "
            f"current={_k8s_int(status.get('replicas'))}, "
            f"updated={_k8s_int(status.get('updated'))}, "
            f"ready={_k8s_int(status.get('ready'))}, "
            f"available={_k8s_int(status.get('available'))}, "
            f"unavailable={_k8s_int(status.get('unavailable'))}."
        )
    ]

    if plan.target_context:
        summary.append(
            f"Kubernetes target: context={plan.target_context}, "
            f"namespace={plan.target_namespace or 'default'}."
        )

    return summary


def reason_k8s_deployment_execution(
    plan: DevOpsPlan,
    step_results: list[DevOpsStepResult],
) -> tuple[list[str], list[str], list[str]]:
    if plan.request.action != DevOpsAction.K8S_DIAGNOSE_DEPLOYMENT:
        return [], [], []

    status = _parse_k8s_deployment_status(
        _k8s_step_text(step_results, "inspect deployment status")
    )
    conditions = _parse_k8s_deployment_conditions(
        _k8s_step_text(step_results, "inspect deployment conditions")
    )
    selector, replicasets = _parse_k8s_deployment_replicasets(
        _k8s_step_text(step_results, "inspect replicasets for deployment")
    )
    _, pods = _parse_k8s_deployment_pods(
        _k8s_step_text(step_results, "inspect pods for deployment")
    )
    events_text = _k8s_step_text(
        step_results,
        "recent deployment events",
    )

    target = (
        status.get("name")
        or plan.inferred_component
        or plan.request.component
        or "deployment"
    )

    generation = _k8s_int(status.get("generation"))
    observed = _k8s_int(status.get("observed_generation"))
    desired = _k8s_int(status.get("desired"))
    current = _k8s_int(status.get("replicas"))
    updated = _k8s_int(status.get("updated"))
    ready = _k8s_int(status.get("ready"))
    available = _k8s_int(status.get("available"))
    unavailable = _k8s_int(status.get("unavailable"))

    owned_rs = [
        rs for rs in replicasets
        if rs.get("owner") == f"Deployment:{target}"
    ]

    ready_pods = [
        pod for pod in pods
        if (pod.get("ready") or "").lower() == "true"
    ]

    waiting_reasons: list[str] = []
    for pod in pods:
        for reason in (pod.get("waiting") or "").split(","):
            reason = reason.strip()
            if reason and reason not in waiting_reasons:
                waiting_reasons.append(reason)

    diagnosis: list[str] = []
    evidence: list[str] = []
    next_actions: list[str] = []
    fault_detected = False

    evidence.append(
        (
            f"Deployment {target}: generation={generation}, "
            f"observedGeneration={observed}, desired={desired}, "
            f"current={current}, updated={updated}, ready={ready}, "
            f"available={available}, unavailable={unavailable}."
        )
    )

    if selector:
        evidence.append(f"Deployment selector: {selector}.")

    evidence.append(
        f"Matching Pods={len(pods)}; Ready matching Pods={len(ready_pods)}."
    )
    evidence.append(
        f"Owned ReplicaSets={len(owned_rs)}; selector-matching ReplicaSets={len(replicasets)}."
    )

    progress_deadline = next(
        (
            c for c in conditions
            if c.get("type") == "Progressing"
            and c.get("status") == "False"
            and c.get("reason") == "ProgressDeadlineExceeded"
        ),
        None,
    )

    replica_failure = next(
        (
            c for c in conditions
            if c.get("type") == "ReplicaFailure"
            and c.get("status") == "True"
        ),
        None,
    )

    if progress_deadline:
        fault_detected = True
        diagnosis.append(
            f"Deployment '{target}' exceeded its rollout progress deadline."
        )
        evidence.append(
            "Progressing=False, reason=ProgressDeadlineExceeded."
        )

    if replica_failure:
        fault_detected = True
        reason = replica_failure.get("reason") or "unknown"
        diagnosis.append(
            f"Deployment '{target}' reports a replica creation failure "
            f"(reason={reason})."
        )
        evidence.append(
            f"ReplicaFailure=True, reason={reason}."
        )

    if generation > 0 and observed < generation:
        fault_detected = True
        diagnosis.append(
            f"Deployment '{target}' controller has not yet observed the latest "
            f"generation ({observed} < {generation})."
        )

    if desired == 0:
        if not fault_detected:
            diagnosis.append(
                f"Deployment '{target}' is intentionally scaled to zero replicas; "
                "zero Pod availability is not treated as a failure."
            )
        next_actions.append(
            "No availability remediation is required unless zero replicas were unintended."
        )
        return diagnosis, evidence, next_actions

    if updated < desired:
        fault_detected = True
        diagnosis.append(
            f"Deployment '{target}' rollout is incomplete: "
            f"{updated}/{desired} desired replicas are updated."
        )

    if ready < desired or available < desired or unavailable > 0:
        fault_detected = True
        diagnosis.append(
            f"Deployment '{target}' is not fully available: "
            f"ready={ready}/{desired}, available={available}/{desired}, "
            f"unavailable={unavailable}."
        )

    if desired > 0 and not pods:
        fault_detected = True
        diagnosis.append(
            f"Deployment '{target}' currently has no Pods matching its selector."
        )

    if waiting_reasons:
        fault_detected = True
        diagnosis.append(
            "Matching Pods report container waiting reason(s): "
            + ", ".join(waiting_reasons)
            + "."
        )
        evidence.append(
            "Pod waiting reasons: " + ", ".join(waiting_reasons) + "."
        )

    high_signal_events: list[str] = []
    for raw_line in events_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        parts = line.split(None, 2)
        reason = parts[1].lower() if len(parts) >= 2 else ""

        if any(
            token in reason
            for token in (
                "failed",
                "error",
                "backoff",
                "deadline",
                "quota",
                "forbidden",
            )
        ):
            high_signal_events.append(line)

    if high_signal_events:
        evidence.append(
            "Recent deployment event: " + high_signal_events[-1]
        )

    for condition in conditions:
        ctype = condition.get("type") or "unknown"
        cstatus = condition.get("status") or "unknown"
        creason = condition.get("reason") or "unknown"
        evidence.append(
            f"Condition {ctype}={cstatus}, reason={creason}."
        )

    if fault_detected:
        next_actions.append(
            "Inspect the Deployment conditions, owned ReplicaSets, and failing "
            "matching Pods before restarting, scaling, or changing the rollout."
        )

        failing_pods = [
            pod.get("name")
            for pod in pods
            if (
                (pod.get("ready") or "").lower() != "true"
                or pod.get("waiting")
            )
            and pod.get("name")
        ]

        if failing_pods:
            next_actions.append(
                f"Run bounded Pod diagnosis on '{failing_pods[0]}' to inspect "
                "its status, events, and current/previous logs."
            )

        return diagnosis, evidence, next_actions

    diagnosis.append(
        f"Deployment '{target}' is fully available and the bounded evidence "
        "does not show a rollout or replica-availability fault."
    )
    next_actions.append(
        "If application traffic is still failing, continue with Service and "
        "Service-to-Pod connectivity diagnosis."
    )

    return diagnosis, evidence, next_actions


def execute_devops_plan(
    plan: DevOpsPlan,
    step_runner: Callable[[DevOpsStep, int, int], CommandResult] | None = None,
) -> DevOpsExecutionResult:
    started = time.monotonic()
    if not plan.safe_to_execute:
        return DevOpsExecutionResult(
            plan=plan,
            success=False,
            error="DevOps plan is not safe to execute.",
            duration_seconds=max(0.0, time.monotonic() - started),
        )

    step_results: list[DevOpsStepResult] = []
    summary: list[str] = []
    diagnosis: list[str] = []
    evidence: list[str] = []
    next_actions: list[str] = []
    total_steps = len(plan.steps)

    for index, step in enumerate(plan.steps, 1):
        try:
            result = (
                step_runner(step, index, total_steps)
                if step_runner is not None
                else run_command(step.command, cwd=plan.workspace)
            )
        except FileNotFoundError as exc:
            return DevOpsExecutionResult(
                plan=plan,
                success=False,
                step_results=step_results,
                summary=summary,
                error=f"Executable not found: {exc.filename}",
                duration_seconds=max(0.0, time.monotonic() - started),
            )
        except KeyboardInterrupt:
            return DevOpsExecutionResult(
                plan=plan,
                success=False,
                step_results=step_results,
                summary=summary,
                error="Execution interrupted by operator.",
                duration_seconds=max(0.0, time.monotonic() - started),
                interrupted=True,
            )
        except Exception as exc:
            return DevOpsExecutionResult(
                plan=plan,
                success=False,
                step_results=step_results,
                summary=summary,
                error=str(exc),
                duration_seconds=max(0.0, time.monotonic() - started),
            )

        step_results.append(DevOpsStepResult(step=step, result=result))
        if result.interrupted:
            return DevOpsExecutionResult(
                plan=plan,
                success=False,
                step_results=step_results,
                summary=summary,
                error=f"Execution interrupted during: {step.label}",
                duration_seconds=max(0.0, time.monotonic() - started),
                interrupted=True,
            )
        if result.returncode != 0:
            return DevOpsExecutionResult(
                plan=plan,
                success=False,
                step_results=step_results,
                summary=summary,
                error=f"Step failed: {step.label}",
                duration_seconds=max(0.0, time.monotonic() - started),
            )

        if plan.request.action in {
            DevOpsAction.TERRAFORM_PLAN,
            DevOpsAction.TERRAFORM_EXPLAIN_PLAN,
        }:
            summary.extend(summarize_terraform_plan(result.stdout + "\n" + result.stderr))

    if plan.request.action.value.startswith("docker_"):
        summary.extend(summarize_docker_execution(plan.request.action, step_results))
        diagnosis, evidence, next_actions = reason_docker_execution(plan, step_results)

    if plan.request.action == DevOpsAction.K8S_DIAGNOSE_POD:
        summary.extend(summarize_k8s_pod_execution(plan, step_results))
        diagnosis, evidence, next_actions = reason_k8s_pod_execution(
            plan,
            step_results,
        )

    if plan.request.action in {
        DevOpsAction.K8S_DIAGNOSE_SERVICE,
        DevOpsAction.K8S_SERVICE_CONNECTIVITY,
    }:
        summary.extend(summarize_k8s_service_execution(plan, step_results))
        diagnosis, evidence, next_actions = reason_k8s_service_execution(
            plan,
            step_results,
        )
        if plan.request.action == DevOpsAction.K8S_SERVICE_CONNECTIVITY:
            (
                connectivity_diagnosis,
                connectivity_evidence,
                connectivity_next_actions,
            ) = reason_k8s_service_connectivity_execution(
                plan,
                step_results,
            )
            diagnosis.extend(connectivity_diagnosis)
            evidence.extend(connectivity_evidence)
            next_actions.extend(connectivity_next_actions)

    if plan.request.action == DevOpsAction.K8S_DIAGNOSE_DEPLOYMENT:
        summary.extend(summarize_k8s_deployment_execution(plan, step_results))
        diagnosis, evidence, next_actions = reason_k8s_deployment_execution(
            plan,
            step_results,
        )

    if plan.request.action == DevOpsAction.DOCKER_BUILD and plan.inferred_image:
        summary.append(f"Docker image built: {plan.inferred_image}")
    if plan.request.action == DevOpsAction.DOCKER_RUN and plan.inferred_component:
        summary.append(f"Docker container started and verified: {plan.inferred_component}")
    if plan.request.action == DevOpsAction.DOCKER_REBUILD_RESTART and plan.inferred_component:
        summary.append(f"Docker container rebuilt, replaced, and verified: {plan.inferred_component}")
    if plan.request.action == DevOpsAction.DOCKER_GUIDED_REPAIR and plan.inferred_component:
        summary.append(
            f"Guided Docker repair applied and running-state verification passed: "
            f"{plan.inferred_component}"
        )
    if plan.request.action == DevOpsAction.K8S_DEPLOY:
        summary.append("Kubernetes deployment workflow completed successfully.")
    if plan.request.action == DevOpsAction.TERRAFORM_VALIDATE:
        summary.append("Terraform configuration validation completed successfully.")

    return DevOpsExecutionResult(
        plan=plan,
        success=True,
        step_results=step_results,
        summary=summary,
        diagnosis=diagnosis,
        evidence=evidence,
        next_actions=next_actions,
        duration_seconds=max(0.0, time.monotonic() - started),
    )
