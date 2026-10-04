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
    DOCKER_COMPOSE_STATUS = "docker_compose_status"

    K8S_CONTEXT = "k8s_context"
    K8S_CONTEXTS = "k8s_contexts"
    K8S_VALIDATE = "k8s_validate"
    K8S_DEPLOY = "k8s_deploy"
    K8S_PODS = "k8s_pods"
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
    if config.get("Cmd") != image_config.get("Cmd") or config.get("Entrypoint") != image_config.get("Entrypoint"):
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

    if not mappings:
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
        (item.result for item in step_results if "runtime state" in item.step.label.lower() or "verify docker container" in item.step.label.lower() or "verify rebuilt container" in item.step.label.lower()),
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
            for item in step_results
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
        evidence.append(
            f"Docker reports {target}: status={status}, exit={exit_code}, restarts={restarts}, health={health}."
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

    if plan.request.action == DevOpsAction.DOCKER_BUILD and plan.inferred_image:
        summary.append(f"Docker image built: {plan.inferred_image}")
    if plan.request.action == DevOpsAction.DOCKER_RUN and plan.inferred_component:
        summary.append(f"Docker container started and verified: {plan.inferred_component}")
    if plan.request.action == DevOpsAction.DOCKER_REBUILD_RESTART and plan.inferred_component:
        summary.append(f"Docker container rebuilt, replaced, and verified: {plan.inferred_component}")
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
