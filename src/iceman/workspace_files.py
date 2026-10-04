from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class WorkspaceTemplate(str, Enum):
    DOCKERFILE = "dockerfile"
    DOCKERIGNORE = "dockerignore"
    DOCKER_COMPOSE = "docker_compose"
    GITIGNORE = "gitignore"
    KUBERNETES_APP = "kubernetes_app"
    KUBERNETES_RESOURCES = "kubernetes_resources"
    TERRAFORM_AWS_STARTER = "terraform_aws_starter"
    TERRAFORM_AZURE_STARTER = "terraform_azure_starter"


@dataclass(frozen=True)
class WorkspaceFileRequest:
    template: WorkspaceTemplate
    component: str | None = None
    port: int | None = None
    replicas: int | None = None
    image: str | None = None


@dataclass(frozen=True)
class FileValidation:
    valid: bool
    message: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkspaceFileOperation:
    relative_path: str
    content: str
    purpose: str
    validation: FileValidation
    existed_before: bool
    previous_content: str | None = None

    @property
    def change_type(self) -> str:
        if not self.existed_before:
            return "CREATE"
        if self.previous_content == self.content:
            return "UNCHANGED"
        return "UPDATE"


@dataclass
class WorkspaceFilePlan:
    plan_id: str
    workspace: Path
    request: WorkspaceFileRequest
    operations: list[WorkspaceFileOperation] = field(default_factory=list)
    safe_to_apply: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def changed_operations(self) -> list[WorkspaceFileOperation]:
        return [
            op for op in self.operations
            if op.change_type != "UNCHANGED"
        ]


@dataclass(frozen=True)
class AppliedWorkspaceFile:
    relative_path: str
    existed_before: bool
    backup_path: str | None
    before_sha256: str | None
    after_sha256: str


@dataclass
class WorkspaceFileApplyResult:
    success: bool
    plan_id: str
    workspace: Path
    changes: list[AppliedWorkspaceFile] = field(default_factory=list)
    error: str | None = None
    rolled_back: bool = False


@dataclass
class WorkspaceFileRollbackResult:
    success: bool
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    error: str | None = None


_SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?im)^\s*(?:aws_secret_access_key|client_secret|private_key)"
        r"\s*=\s*[\"'][^\"']+[\"']"
    ),
)

_SAFE_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,254}$")
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_DNS_LABEL_RE = re.compile(r"[^a-z0-9-]+")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_component(component: str | None) -> str | None:
    if component is None:
        return None
    value = component.strip()
    if not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(
            "Component names may contain only letters, digits, '.', '_' and '-'."
        )
    return value


def _dns_label(value: str) -> str:
    lowered = value.strip().lower().replace("_", "-").replace(".", "-")
    lowered = _DNS_LABEL_RE.sub("-", lowered).strip("-")
    lowered = re.sub(r"-+", "-", lowered)
    if not lowered:
        lowered = "app"
    return lowered[:63].rstrip("-") or "app"


def _safe_image(image: str | None, name: str) -> tuple[str, bool]:
    if image is None:
        return f"your-registry/{name}:latest", True
    value = image.strip()
    if not _SAFE_IMAGE.fullmatch(value):
        raise ValueError(
            "Container image must be a plain OCI-style image reference with no "
            "whitespace or shell syntax."
        )
    return value, False


def _validate_port(port: int | None) -> int | None:
    if port is None:
        return None
    if not 1 <= int(port) <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return int(port)


def _validate_replicas(replicas: int | None) -> int:
    value = 2 if replicas is None else int(replicas)
    if not 1 <= value <= 100:
        raise ValueError("Replicas must be between 1 and 100.")
    return value


def _scope_dir(workspace: Path, component: str | None) -> Path:
    workspace = workspace.resolve()
    component = _safe_component(component)
    if component is None:
        return workspace
    candidate = workspace / component
    if candidate.exists() and candidate.is_dir():
        return candidate.resolve()
    raise ValueError(
        f"Component directory '{component}' was not found in {workspace}."
    )


def _safe_target(workspace: Path, relative_path: str) -> Path:
    if not relative_path or "\x00" in relative_path:
        raise ValueError("Invalid empty file path.")

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(
            "Workspace file paths must be relative and may not contain '..'."
        )

    workspace = workspace.resolve()
    candidate = workspace.joinpath(relative)
    parent = candidate.parent
    # Planning must be read-only. Path.resolve(strict=False) safely resolves
    # existing ancestors/symlinks without creating the requested directory.
    resolved_parent = parent.resolve(strict=False)

    try:
        resolved_parent.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("File path escapes the active workspace.") from exc

    if candidate.exists() and candidate.is_symlink():
        raise ValueError(
            f"Refusing to replace symlink: {relative_path}"
        )
    if candidate.exists() and candidate.is_dir():
        raise ValueError(
            f"Refusing to replace directory: {relative_path}"
        )

    return candidate


def _read_existing(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Refusing to overwrite non-UTF-8 file: {path}"
        ) from exc


def _detect_node_port(scope: Path) -> int | None:
    patterns = (
        re.compile(
            r"process\.env\.PORT\s*\|\|\s*(\d{2,5})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\.listen\(\s*(\d{2,5})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bPORT\s*=\s*(\d{2,5})",
            re.IGNORECASE,
        ),
    )

    candidates = []
    for path in scope.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {
            "node_modules", ".git", ".venv", "dist", "build"
        } for part in path.parts):
            continue
        if path.suffix.lower() not in {".js", ".mjs", ".cjs", ".ts"}:
            continue
        try:
            if path.stat().st_size > 256_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                value = int(match.group(1))
                if 1 <= value <= 65535:
                    candidates.append(value)
        if len(candidates) >= 5:
            break

    if not candidates:
        return None

    # Deterministic: most frequent, then smallest.
    return sorted(
        set(candidates),
        key=lambda item: (-candidates.count(item), item),
    )[0]


def _package_json(scope: Path) -> dict:
    path = scope / "package.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _node_dockerfile(
    scope: Path,
    port: int | None,
) -> tuple[str, list[str]]:
    package = _package_json(scope)
    if not package:
        raise ValueError(
            f"No package.json was found in {scope}; v0.8.0 Dockerfile generation "
            "currently requires a Node.js project."
        )

    scripts = package.get("scripts", {})
    scripts = scripts if isinstance(scripts, dict) else {}
    notes: list[str] = []

    if "start" in scripts:
        command = 'CMD ["npm", "start"]'
    elif "serve" in scripts:
        command = 'CMD ["npm", "run", "serve"]'
        notes.append(
            "No npm start script was found; using npm run serve."
        )
    else:
        raise ValueError(
            "No npm 'start' or 'serve' script was found. Iceman will not guess "
            "a production entrypoint."
        )

    detected = port or _detect_node_port(scope)
    if detected is None:
        detected = 3000
        notes.append(
            "No application port was detected; Dockerfile uses port 3000. "
            "Review the preview before approval."
        )
    else:
        notes.append(f"Container port resolved as {detected}.")

    lock_copy = (
        "COPY package.json package-lock.json ./\n"
        "RUN npm ci --omit=dev"
        if (scope / "package-lock.json").exists()
        else
        "COPY package.json ./\nRUN npm install --omit=dev"
    )

    content = f"""FROM node:20-alpine

WORKDIR /app

{lock_copy}

COPY . .

EXPOSE {detected}

{command}
"""
    return content, notes


def _dockerignore_content() -> str:
    return """node_modules
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.pnpm-debug.log*
.git
.gitignore
.env
.env.*
!.env.example
dist
build
coverage
.vscode
.idea
.DS_Store
"""


def _dockerfile_expose(scope: Path) -> int | None:
    path = scope / "Dockerfile"
    if not path.is_file():
        return None
    text = _read_existing(path) or ""
    match = re.search(r"(?mi)^\s*EXPOSE\s+(\d{1,5})\b", text)
    if not match:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 65535 else None


def _docker_compose_files(
    workspace: Path,
    component: str | None,
    port: int | None,
) -> tuple[list[tuple[str, str, str]], list[str]]:
    existing = [
        name for name in ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")
        if (workspace / name).exists()
    ]
    if existing:
        raise ValueError(
            "An existing Docker Compose file was found (" + ", ".join(existing) + "). "
            "v0.9 will not replace an existing multi-service Compose definition automatically."
        )

    chosen = component
    if chosen is None:
        candidates: list[str | None] = []
        if (workspace / "Dockerfile").is_file():
            candidates.append(None)
        for child in sorted(workspace.iterdir()):
            if child.is_dir() and (child / "Dockerfile").is_file():
                candidates.append(child.name)
        if len(candidates) != 1:
            visible = [item or "." for item in candidates]
            raise ValueError(
                "Docker Compose generation needs one unambiguous Dockerfile component. "
                + ("Candidates: " + ", ".join(visible) if visible else "No Dockerfile was found.")
            )
        chosen = candidates[0]

    scope = _scope_dir(workspace, chosen)
    if not (scope / "Dockerfile").is_file():
        raise ValueError("Docker Compose generation requires a Dockerfile for the selected component.")

    resolved_port = _validate_port(port) or _dockerfile_expose(scope) or _detect_node_port(scope)
    if resolved_port is None:
        raise ValueError(
            "Iceman could not determine the container port. Supply it explicitly, for example: "
            "'create docker compose for backend port 5000'."
        )

    service_name = _dns_label(chosen or workspace.name or "app")
    context = "." if scope == workspace else "./" + scope.relative_to(workspace).as_posix()
    content = (
        "services:\n"
        f"  {service_name}:\n"
        "    build:\n"
        f"      context: {context}\n"
        "    ports:\n"
        f"      - \"{resolved_port}:{resolved_port}\"\n"
        "    restart: unless-stopped\n"
    )
    return [(
        "compose.yaml",
        content,
        f"Docker Compose service for {service_name}",
    )], [
        f"Compose service: {service_name}.",
        f"Build context: {context}.",
        f"Port mapping: localhost:{resolved_port} -> container:{resolved_port}.",
        "No secrets, database credentials, or invented dependencies were added.",
    ]


def _gitignore_content() -> str:
    return """# Dependencies
node_modules/
.venv/
venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.py[cod]
.coverage
htmlcov/

# Environment / local secrets
.env
.env.*
!.env.example
!.env.*.example

# Build outputs
dist/
build/
coverage/
.next/

# Logs
*.log
npm-debug.log*
yarn-debug.log*
yarn-error.log*

# Terraform
.terraform/
*.tfstate
*.tfstate.*
crash.log
crash.*.log
*.tfvars
!*.tfvars.example
.terraform.tfstate.lock.info

# IDE / OS
.vscode/
.idea/
.DS_Store
Thumbs.db
"""


def _kubernetes_app_files(
    component: str | None,
    port: int | None,
    replicas: int | None,
    image: str | None,
) -> tuple[list[tuple[str, str, str]], list[str]]:
    name = _dns_label(component or "app")
    port = _validate_port(port)
    if port is None:
        raise ValueError(
            "Kubernetes generation requires an explicit port in v0.8.0, "
            "for example: 'create kubernetes deployment and service for "
            "backend port 5000'."
        )
    replicas = _validate_replicas(replicas)
    image_value, placeholder = _safe_image(image, name)

    notes = [
        f"Kubernetes resource name: {name}.",
        f"Container/service port: {port}.",
        f"Replicas: {replicas}.",
    ]
    if placeholder:
        notes.append(
            f"Image was not supplied; using placeholder '{image_value}'. "
            "Replace it before applying to a cluster."
        )

    deployment = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
    spec:
      containers:
        - name: {name}
          image: {image_value}
          ports:
            - containerPort: {port}
          readinessProbe:
            tcpSocket:
              port: {port}
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            tcpSocket:
              port: {port}
            initialDelaySeconds: 15
            periodSeconds: 20
"""

    service = f"""apiVersion: v1
kind: Service
metadata:
  name: {name}
spec:
  type: ClusterIP
  selector:
    app: {name}
  ports:
    - name: http
      port: {port}
      targetPort: {port}
      protocol: TCP
"""

    return [
        (
            f"k8s/{name}-deployment.yaml",
            deployment,
            f"Kubernetes Deployment for {name}",
        ),
        (
            f"k8s/{name}-service.yaml",
            service,
            f"Kubernetes ClusterIP Service for {name}",
        ),
    ], notes


def _kubernetes_resource_files(
    workspace: Path,
    component: str | None,
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Prepare a bounded starter resource request/limit patch for one Deployment.

    This intentionally supports a conservative case only: one Kubernetes Deployment
    document with exactly one container image line and no existing resources block.
    Ambiguous/multi-container manifests are left untouched for manual review.
    """
    k8s_dir = workspace / "k8s"
    if not k8s_dir.is_dir():
        raise ValueError("No k8s/ directory was found in the active workspace.")

    component = _safe_component(component)
    candidates: list[Path] = []

    if component:
        dns_name = _dns_label(component)
        for suffix in (".yaml", ".yml"):
            exact = k8s_dir / f"{dns_name}-deployment{suffix}"
            if exact.is_file():
                candidates.append(exact)

    if not candidates:
        for path in sorted(list(k8s_dir.glob("*.yaml")) + list(k8s_dir.glob("*.yml"))):
            text = _read_existing(path) or ""
            if not re.search(r"(?mi)^kind:\s*Deployment\s*$", text):
                continue
            if component:
                dns_name = _dns_label(component)
                metadata_match = re.search(
                    r"(?ms)^metadata:\s*\n(?:^[ \t].*\n)*?^[ \t]+name:\s*([^\s#]+)",
                    text,
                )
                if metadata_match and metadata_match.group(1).strip() != dns_name:
                    continue
            candidates.append(path)

    # Deduplicate exact/search matches while preserving deterministic order.
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(candidate)
    candidates = unique

    if not candidates:
        target = component or "requested component"
        raise ValueError(f"No Kubernetes Deployment manifest was found for {target}.")
    if len(candidates) != 1:
        names = ", ".join(path.name for path in candidates)
        raise ValueError(
            "Iceman found multiple candidate Deployment manifests "
            f"({names}). Specify a component before applying starter resources."
        )

    path = candidates[0]
    content = _read_existing(path) or ""
    if re.search(r"(?mi)^\s+resources:\s*$", content):
        relative = path.relative_to(workspace).as_posix()
        return [(
            relative,
            content,
            "Kubernetes starter CPU/memory requests and limits",
        )], [
            f"{relative} already contains a resources block; no resource values were changed."
        ]

    image_matches = list(re.finditer(r"(?m)^(\s+)image:\s*[^\n#]+(?:\s*#.*)?$", content))
    if len(image_matches) != 1:
        raise ValueError(
            "Automatic starter resource insertion requires exactly one container image "
            "line in the selected Deployment. Multi-container or ambiguous manifests "
            "need explicit resource choices."
        )

    match = image_matches[0]
    indent = match.group(1)
    image_line_end = content.find("\n", match.end())
    if image_line_end == -1:
        image_line_end = len(content)
        newline = "\n"
    else:
        image_line_end += 1
        newline = ""

    block = (
        f"{indent}resources:\n"
        f"{indent}  requests:\n"
        f"{indent}    cpu: 100m\n"
        f"{indent}    memory: 128Mi\n"
        f"{indent}  limits:\n"
        f"{indent}    cpu: 500m\n"
        f"{indent}    memory: 512Mi\n"
    )
    patched = content[:image_line_end] + newline + block + content[image_line_end:]
    relative = path.relative_to(workspace).as_posix()
    return [(
        relative,
        patched,
        "Kubernetes starter CPU/memory requests and limits",
    )], [
        "Starter resources are intentionally conservative defaults: requests "
        "100m CPU / 128Mi memory; limits 500m CPU / 512Mi memory.",
        "Tune these values from observed workload usage before production rollout.",
        "Iceman will preview the exact YAML diff, back up the prior file, and "
        "the deployment workflow will dry-run the manifests before apply.",
    ]


def _terraform_aws_files() -> tuple[
    list[tuple[str, str, str]],
    list[str],
]:
    return [
        (
            "terraform/versions.tf",
            """terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}
""",
            "Terraform version and AWS provider requirements",
        ),
        (
            "terraform/providers.tf",
            """provider "aws" {
  region = var.aws_region
}
""",
            "AWS provider configuration",
        ),
        (
            "terraform/variables.tf",
            """variable "aws_region" {
  description = "AWS region used by this Terraform configuration."
  type        = string
}
""",
            "AWS Terraform input variables",
        ),
        (
            "terraform/terraform.tfvars.example",
            """aws_region = "eu-north-1"
""",
            "Example non-secret Terraform variable values",
        ),
    ], [
        "This is provider scaffolding only; v0.8.0 does not invent cloud "
        "resources that were not requested.",
        "No AWS credentials are written. Terraform will use the operator's "
        "normal credential chain.",
    ]


def _terraform_azure_files() -> tuple[
    list[tuple[str, str, str]],
    list[str],
]:
    return [
        (
            "terraform/versions.tf",
            """terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.0"
    }
  }
}
""",
            "Terraform version and AzureRM provider requirements",
        ),
        (
            "terraform/providers.tf",
            """provider "azurerm" {
  features {}
}
""",
            "AzureRM provider configuration",
        ),
    ], [
        "This is AzureRM provider scaffolding only; v0.8.0 does not invent "
        "cloud resources that were not requested.",
        "No Azure credentials or subscription secrets are written.",
    ]


def validate_generated_file(
    relative_path: str,
    content: str,
) -> FileValidation:
    if "\x00" in content:
        return FileValidation(False, "NUL bytes are not allowed.")

    encoded = content.encode("utf-8")
    if len(encoded) > 1_000_000:
        return FileValidation(
            False,
            "Generated file exceeds the 1 MB v0.8.0 safety limit.",
        )

    for pattern in _SECRET_PATTERNS:
        if pattern.search(content):
            return FileValidation(
                False,
                "Generated content looks like it contains a credential/private key.",
            )

    name = Path(relative_path).name.lower()
    suffix = Path(relative_path).suffix.lower()
    warnings: list[str] = []

    if name == "dockerfile" or name.startswith("dockerfile."):
        if not re.search(r"(?mi)^\s*FROM\s+\S+", content):
            return FileValidation(False, "Dockerfile has no FROM instruction.")
        if not re.search(r"(?mi)^\s*(CMD|ENTRYPOINT)\b", content):
            return FileValidation(
                False,
                "Dockerfile has no CMD or ENTRYPOINT instruction.",
            )

    if suffix in {".yaml", ".yml"}:
        if "\t" in content:
            return FileValidation(False, "YAML contains tab indentation.")
        if "apiVersion:" in content and "kind:" not in content:
            return FileValidation(
                False,
                "Kubernetes-like YAML contains apiVersion but no kind.",
            )
        if "your-registry/" in content:
            warnings.append(
                "Manifest contains a placeholder container image."
            )

    if suffix == ".tf":
        if content.count("{") != content.count("}"):
            return FileValidation(
                False,
                "Terraform braces are unbalanced.",
            )

    return FileValidation(
        True,
        "Static safety/structure validation passed.",
        tuple(warnings),
    )


def _operation(
    workspace: Path,
    relative_path: str,
    content: str,
    purpose: str,
) -> WorkspaceFileOperation:
    target = _safe_target(workspace, relative_path)
    previous = _read_existing(target)
    validation = validate_generated_file(relative_path, content)
    return WorkspaceFileOperation(
        relative_path=relative_path,
        content=content,
        purpose=purpose,
        validation=validation,
        existed_before=previous is not None,
        previous_content=previous,
    )


def build_workspace_file_plan(
    workspace: Path,
    request: WorkspaceFileRequest,
) -> WorkspaceFilePlan:
    workspace = workspace.resolve()
    plan = WorkspaceFilePlan(
        plan_id=uuid.uuid4().hex[:12],
        workspace=workspace,
        request=request,
    )

    try:
        component = _safe_component(request.component)
        port = _validate_port(request.port)
        generated: list[tuple[str, str, str]]
        notes: list[str]

        if request.template == WorkspaceTemplate.DOCKERFILE:
            scope = _scope_dir(workspace, component)
            content, notes = _node_dockerfile(scope, port)
            prefix = (
                str(scope.relative_to(workspace)).replace("\\", "/") + "/"
                if scope != workspace
                else ""
            )
            generated = [
                (
                    f"{prefix}Dockerfile",
                    content,
                    "Production-oriented Node.js Dockerfile",
                ),
                (
                    f"{prefix}.dockerignore",
                    _dockerignore_content(),
                    "Docker build-context ignore rules",
                ),
            ]

        elif request.template == WorkspaceTemplate.DOCKERIGNORE:
            scope = _scope_dir(workspace, component)
            prefix = (
                str(scope.relative_to(workspace)).replace("\\", "/") + "/"
                if scope != workspace
                else ""
            )
            generated = [
                (
                    f"{prefix}.dockerignore",
                    _dockerignore_content(),
                    "Docker build-context ignore rules",
                )
            ]
            notes = []

        elif request.template == WorkspaceTemplate.DOCKER_COMPOSE:
            generated, notes = _docker_compose_files(
                workspace,
                component,
                port,
            )

        elif request.template == WorkspaceTemplate.GITIGNORE:
            generated = [
                (
                    ".gitignore",
                    _gitignore_content(),
                    "Common DevOps/project ignore rules",
                )
            ]
            notes = [
                "The generated .gitignore excludes local environment files and "
                "Terraform state/variable files that commonly contain secrets."
            ]

        elif request.template == WorkspaceTemplate.KUBERNETES_APP:
            generated, notes = _kubernetes_app_files(
                component,
                port,
                request.replicas,
                request.image,
            )

        elif request.template == WorkspaceTemplate.KUBERNETES_RESOURCES:
            generated, notes = _kubernetes_resource_files(
                workspace,
                component,
            )

        elif request.template == WorkspaceTemplate.TERRAFORM_AWS_STARTER:
            generated, notes = _terraform_aws_files()

        elif request.template == WorkspaceTemplate.TERRAFORM_AZURE_STARTER:
            generated, notes = _terraform_azure_files()

        else:
            raise ValueError(
                f"Unsupported workspace template: {request.template}"
            )

        plan.notes.extend(notes)
        for relative_path, content, purpose in generated:
            plan.operations.append(
                _operation(
                    workspace,
                    relative_path,
                    content,
                    purpose,
                )
            )

        plan.safe_to_apply = (
            bool(plan.operations)
            and all(op.validation.valid for op in plan.operations)
        )

    except ValueError as exc:
        plan.safe_to_apply = False
        plan.notes.append(str(exc))

    return plan


def unified_diff_for_operation(
    operation: WorkspaceFileOperation,
) -> str:
    before = (
        operation.previous_content.splitlines(keepends=True)
        if operation.previous_content is not None
        else []
    )
    after = operation.content.splitlines(keepends=True)
    from_name = (
        f"a/{operation.relative_path}"
        if operation.existed_before
        else "/dev/null"
    )
    to_name = f"b/{operation.relative_path}"
    diff = difflib.unified_diff(
        before,
        after,
        fromfile=from_name,
        tofile=to_name,
    )
    return "".join(diff)


def _backup_root(workspace: Path, plan_id: str) -> Path:
    digest = hashlib.sha256(
        str(workspace.resolve()).encode("utf-8")
    ).hexdigest()[:16]
    root = (
        Path.home()
        / ".cache"
        / "iceman"
        / "workspace-backups"
        / digest
        / plan_id
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _restore_changes(
    workspace: Path,
    changes: list[AppliedWorkspaceFile],
) -> WorkspaceFileRollbackResult:
    restored: list[str] = []
    removed: list[str] = []

    try:
        for change in reversed(changes):
            target = _safe_target(workspace, change.relative_path)

            if change.existed_before:
                if not change.backup_path:
                    raise RuntimeError(
                        f"Missing backup for {change.relative_path}"
                    )
                backup = Path(change.backup_path)
                data = backup.read_bytes()
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.parent / (
                    f".{target.name}.iceman-rollback-{uuid.uuid4().hex}"
                )
                tmp.write_bytes(data)
                os.replace(tmp, target)
                restored.append(change.relative_path)
            else:
                if target.exists():
                    target.unlink()
                removed.append(change.relative_path)

        return WorkspaceFileRollbackResult(
            success=True,
            restored=restored,
            removed=removed,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return WorkspaceFileRollbackResult(
            success=False,
            restored=restored,
            removed=removed,
            error=str(exc),
        )


def apply_workspace_file_plan(
    plan: WorkspaceFilePlan,
) -> WorkspaceFileApplyResult:
    if not plan.safe_to_apply:
        return WorkspaceFileApplyResult(
            success=False,
            plan_id=plan.plan_id,
            workspace=plan.workspace,
            error="Plan is not safe to apply.",
        )

    changed_ops = plan.changed_operations
    if not changed_ops:
        return WorkspaceFileApplyResult(
            success=True,
            plan_id=plan.plan_id,
            workspace=plan.workspace,
            changes=[],
        )

    backup_root = _backup_root(plan.workspace, plan.plan_id)
    applied: list[AppliedWorkspaceFile] = []

    try:
        for operation in changed_ops:
            target = _safe_target(
                plan.workspace,
                operation.relative_path,
            )
            existing_bytes = target.read_bytes() if target.exists() else None
            backup_path: Path | None = None

            if existing_bytes is not None:
                backup_path = backup_root / operation.relative_path
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_path.write_bytes(existing_bytes)

            new_bytes = operation.content.encode("utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)

            tmp_fd, tmp_name = tempfile.mkstemp(
                prefix=f".{target.name}.iceman-",
                dir=str(target.parent),
            )
            try:
                with os.fdopen(tmp_fd, "wb") as handle:
                    handle.write(new_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, target)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)

            validation = validate_generated_file(
                operation.relative_path,
                target.read_text(encoding="utf-8"),
            )
            if not validation.valid:
                raise RuntimeError(
                    f"Post-write validation failed for "
                    f"{operation.relative_path}: {validation.message}"
                )

            actual = target.read_bytes()
            expected_sha = _sha256(new_bytes)
            actual_sha = _sha256(actual)
            if actual_sha != expected_sha:
                raise RuntimeError(
                    f"Post-write hash verification failed for "
                    f"{operation.relative_path}."
                )

            applied.append(
                AppliedWorkspaceFile(
                    relative_path=operation.relative_path,
                    existed_before=existing_bytes is not None,
                    backup_path=(
                        str(backup_path)
                        if backup_path is not None
                        else None
                    ),
                    before_sha256=(
                        _sha256(existing_bytes)
                        if existing_bytes is not None
                        else None
                    ),
                    after_sha256=actual_sha,
                )
            )

        metadata = {
            "plan_id": plan.plan_id,
            "workspace": str(plan.workspace),
            "created_at_unix": int(time.time()),
            "changes": [
                {
                    "relative_path": item.relative_path,
                    "existed_before": item.existed_before,
                    "backup_path": item.backup_path,
                    "before_sha256": item.before_sha256,
                    "after_sha256": item.after_sha256,
                }
                for item in applied
            ],
        }
        (backup_root / "metadata.json").write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )

        return WorkspaceFileApplyResult(
            success=True,
            plan_id=plan.plan_id,
            workspace=plan.workspace,
            changes=applied,
        )

    except (OSError, RuntimeError, ValueError) as exc:
        rollback = _restore_changes(plan.workspace, applied)
        return WorkspaceFileApplyResult(
            success=False,
            plan_id=plan.plan_id,
            workspace=plan.workspace,
            changes=applied,
            error=str(exc),
            rolled_back=rollback.success,
        )


def rollback_workspace_file_result(
    result: WorkspaceFileApplyResult,
) -> WorkspaceFileRollbackResult:
    if not result.success:
        return WorkspaceFileRollbackResult(
            success=False,
            error="Only a successful applied file plan can be rolled back.",
        )
    if not result.changes:
        return WorkspaceFileRollbackResult(
            success=True,
        )
    return _restore_changes(result.workspace, result.changes)
