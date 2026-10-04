from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from iceman.devops_actions import DevOpsAction, DevOpsPlan
from iceman.linux_actions import LinuxAction, LinuxPlan
from iceman.git_actions import GitAction, GitPlan


class AdviceLevel(str, Enum):
    BLOCKER = "blocker"
    WARNING = "warning"
    RECOMMENDATION = "recommendation"
    REMINDER = "reminder"


@dataclass(frozen=True)
class Advisory:
    level: AdviceLevel
    message: str
    suggestion: str | None = None
    fix_id: str | None = None
    fix_prompt: str | None = None


@dataclass
class AdvisorReport:
    findings: list[Advisory] = field(default_factory=list)

    @property
    def has_blocker(self) -> bool:
        return any(item.level == AdviceLevel.BLOCKER for item in self.findings)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _docker_advice(plan: DevOpsPlan) -> list[Advisory]:
    findings: list[Advisory] = []
    if plan.request.action not in {
        DevOpsAction.DOCKER_BUILD,
        DevOpsAction.DOCKER_RUN,
        DevOpsAction.DOCKER_DIAGNOSE,
        DevOpsAction.DOCKER_HEALTH,
        DevOpsAction.DOCKER_CONNECTIVITY,
        DevOpsAction.DOCKER_SECURITY,
        DevOpsAction.DOCKER_OPTIMIZE,
        DevOpsAction.DOCKER_REBUILD_RESTART,
    }:
        return findings

    component = plan.request.component or plan.inferred_component
    directory = plan.workspace / component if component and (plan.workspace / component).is_dir() else plan.workspace
    dockerfile = directory / "Dockerfile"
    if dockerfile.exists():
        text = _read(dockerfile)
        if not re.search(r"(?mi)^\s*USER\s+\S+", text):
            findings.append(Advisory(
                AdviceLevel.WARNING,
                "Dockerfile does not declare a non-root USER; the container may run as root.",
                "For production workloads, create/use a non-root user when the application permits it.",
            ))
        if not re.search(r"(?mi)^\s*HEALTHCHECK\s+", text):
            findings.append(Advisory(
                AdviceLevel.RECOMMENDATION,
                "Dockerfile has no HEALTHCHECK.",
                "Consider adding an application-aware health check for long-running services.",
            ))

        if re.search(r"(?mi)^\s*FROM\s+\S+:latest(?:\s|$)", text):
            findings.append(Advisory(
                AdviceLevel.RECOMMENDATION,
                "Dockerfile base image uses the mutable ':latest' tag.",
                "Pin a tested version tag or image digest for reproducible builds.",
            ))

        if re.search(r"(?mi)^\s*ADD\s+https?://", text):
            findings.append(Advisory(
                AdviceLevel.WARNING,
                "Dockerfile downloads a remote URL with ADD.",
                "Prefer an explicit download step with checksum verification so remote content is auditable.",
            ))

        secret_env = re.search(
            r"(?mi)^\s*ENV\s+(?:[A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY)[A-Z0-9_]*)[ =]+([^$\s][^\n]*)$",
            text,
        )
        if secret_env:
            findings.append(Advisory(
                AdviceLevel.WARNING,
                "Dockerfile appears to place a secret-like value in an ENV instruction.",
                "Do not bake credentials into image layers; inject secrets at runtime using the platform's secret mechanism.",
            ))

        if re.search(r"(?mi)^\s*COPY\s+\.\s+\.\s*$", text) and not (directory / ".dockerignore").exists():
            findings.append(Advisory(
                AdviceLevel.WARNING,
                "Dockerfile copies the full build context but no .dockerignore was found.",
                "Add a .dockerignore to reduce image/build-context size and avoid copying local secrets or development artifacts.",
            ))

        if (directory / "package-lock.json").exists() and re.search(r"(?mi)^\s*RUN\s+npm\s+install\b", text):
            findings.append(Advisory(
                AdviceLevel.RECOMMENDATION,
                "package-lock.json exists but the Dockerfile uses npm install.",
                "Use npm ci for deterministic dependency installation when the lockfile is authoritative.",
            ))

    if plan.request.action == DevOpsAction.DOCKER_REBUILD_RESTART:
        findings.append(Advisory(
            AdviceLevel.REMINDER,
            "Rebuild/restart replaces the running container with a new container instance.",
            "Iceman preserves the previous simple standalone container as an iceman-backup instead of deleting it; verify the new container before cleaning that backup up manually.",
        ))
    return findings


def _k8s_advice(plan: DevOpsPlan) -> list[Advisory]:
    findings: list[Advisory] = []
    if plan.request.action not in {DevOpsAction.K8S_VALIDATE, DevOpsAction.K8S_DEPLOY}:
        return findings
    k8s_dir = plan.workspace / "k8s"
    text = "\n".join(_read(p) for p in sorted(list(k8s_dir.glob("*.yaml")) + list(k8s_dir.glob("*.yml"))))
    if not text:
        return findings
    if re.search(r"(?mi)^\s*image:\s*[^\s#]+:latest\s*$", text):
        findings.append(Advisory(
            AdviceLevel.RECOMMENDATION,
            "A Kubernetes workload uses the ':latest' image tag.",
            "Use an immutable version tag or image digest for reproducible deployments.",
        ))
    if "resources:" not in text:
        findings.append(Advisory(
            AdviceLevel.WARNING,
            "No Kubernetes CPU/memory requests or limits were detected.",
            "Add resource requests and appropriate limits before production deployment.",
            fix_id="k8s_starter_resources",
            fix_prompt=(
                "Would you like Iceman to prepare starter CPU/memory requests "
                "and limits for this Deployment?"
            ),
        ))
    if "readinessProbe:" not in text:
        findings.append(Advisory(
            AdviceLevel.RECOMMENDATION,
            "No readinessProbe was detected.",
            "Add a readiness probe so Services avoid routing traffic before the application is ready.",
        ))
    if "livenessProbe:" not in text:
        findings.append(Advisory(
            AdviceLevel.RECOMMENDATION,
            "No livenessProbe was detected.",
            "Consider a liveness probe when automatic recovery from deadlocked processes is appropriate.",
        ))
    if not re.search(r"(?mi)^\s*namespace:\s*\S+", text):
        namespace = plan.target_namespace or "default"
        findings.append(Advisory(
            AdviceLevel.REMINDER,
            f"No explicit Kubernetes namespace was detected; the target context is configured for namespace '{namespace}'.",
            "Confirm the target namespace before deploying shared or production workloads.",
        ))

    if plan.request.action == DevOpsAction.K8S_DEPLOY and plan.target_context:
        if plan.context_inferred:
            findings.append(Advisory(
                AdviceLevel.REMINDER,
                f"No specific cluster/context was named. Iceman resolved the deployment target to the current context '{plan.target_context}'.",
                "Verify the destination shown in the execution plan before approving the deployment.",
            ))
        lower_context = plan.target_context.lower()
        is_local = (
            lower_context == "minikube"
            or lower_context == "docker-desktop"
            or lower_context.startswith("kind-")
            or lower_context.startswith("k3d-")
            or lower_context.startswith("microk8s")
        )
        if not is_local:
            findings.append(Advisory(
                AdviceLevel.WARNING,
                f"Deployment target '{plan.target_context}' does not look like a common local Kubernetes context.",
                "Confirm the cloud/account, cluster, namespace, and environment before approving a remote deployment.",
            ))
    return findings


def _terraform_advice(plan: DevOpsPlan) -> list[Advisory]:
    findings: list[Advisory] = []
    if plan.request.action not in {
        DevOpsAction.TERRAFORM_INIT,
        DevOpsAction.TERRAFORM_VALIDATE,
        DevOpsAction.TERRAFORM_PLAN,
        DevOpsAction.TERRAFORM_EXPLAIN_PLAN,
    }:
        return findings
    directory = plan.workspace / (plan.request.terraform_dir or "terraform")
    if not directory.exists():
        directory = plan.workspace
    tf_text = "\n".join(_read(p) for p in sorted(directory.glob("*.tf")))
    if tf_text and not re.search(r"(?ms)terraform\s*\{.*?backend\s+\"", tf_text):
        findings.append(Advisory(
            AdviceLevel.RECOMMENDATION,
            "No remote Terraform backend configuration was detected.",
            "For team/production infrastructure, use protected remote state and state locking where supported.",
        ))
    for path in directory.glob("*.tfvars"):
        if path.name.endswith(".example"):
            continue
        text = _read(path)
        if re.search(r"(?mi)^\s*(password|secret|token|access_key|secret_key)\s*=", text):
            findings.append(Advisory(
                AdviceLevel.WARNING,
                f"Potential sensitive values may be stored in {path.name}.",
                "Keep secret-bearing tfvars out of version control and prefer a secret manager or external credential chain.",
            ))
            break
    if plan.request.action in {DevOpsAction.TERRAFORM_PLAN, DevOpsAction.TERRAFORM_EXPLAIN_PLAN}:
        findings.append(Advisory(
            AdviceLevel.REMINDER,
            "A Terraform plan is a point-in-time preview and does not change infrastructure.",
            "Review create/change/destroy counts and sensitive diffs before any future apply.",
        ))
    return findings


def advise_devops_plan(plan: DevOpsPlan) -> AdvisorReport:
    findings: list[Advisory] = []
    findings.extend(_docker_advice(plan))
    findings.extend(_k8s_advice(plan))
    findings.extend(_terraform_advice(plan))
    return AdvisorReport(findings=findings)


def advise_linux_plan(plan: LinuxPlan) -> AdvisorReport:
    findings: list[Advisory] = []
    if plan.request.action == LinuxAction.MAKE_EXECUTABLE:
        findings.append(Advisory(
            AdviceLevel.REMINDER,
            "Iceman is adding only the execute bit with chmod +x rather than broad permissions such as 777.",
            "Prefer the minimum permissions required for the task.",
        ))
    if plan.request.action in {LinuxAction.MOVE, LinuxAction.COPY}:
        findings.append(Advisory(
            AdviceLevel.REMINDER,
            "The source and destination are both constrained to the active workspace for file-changing operations.",
        ))
    return AdvisorReport(findings=findings)


def advise_git_plan(plan: GitPlan) -> AdvisorReport:
    findings: list[Advisory] = []
    action = plan.request.action
    if action in {GitAction.STAGE_ALL, GitAction.COMMIT, GitAction.COMMIT_PUSH}:
        if plan.sensitive_paths:
            findings.append(Advisory(
                AdviceLevel.BLOCKER,
                "Potential sensitive files or secret-like content were detected in the Git candidate set.",
                "Remove/ignore the sensitive files or replace real secrets with safe examples before staging or committing.",
            ))
        if plan.large_paths:
            findings.append(Advisory(
                AdviceLevel.BLOCKER,
                "One or more files meet or exceed GitHub's 100 MiB file limit.",
                "Remove the large artifacts from normal Git history or use an appropriate artifact/LFS workflow.",
            ))
        if not (plan.workspace / ".gitignore").exists():
            findings.append(Advisory(
                AdviceLevel.WARNING,
                "No .gitignore file was found in the repository root.",
                "Create a .gitignore before staging everything so virtual environments, caches, credentials, and build outputs are not committed accidentally.",
            ))
    if action == GitAction.GITHUB_CREATE_REPO:
        findings.append(Advisory(
            AdviceLevel.REMINDER,
            "Creating a GitHub repository changes remote state but does not push commits automatically in this workflow.",
            "Review the repository visibility and configured origin, then use a separate push action after the local commit is ready.",
        ))
    if action == GitAction.PUSH:
        findings.append(Advisory(
            AdviceLevel.REMINDER,
            "A Git push publishes committed history to the configured remote.",
            "Confirm the branch and origin shown in the plan before approving the push.",
        ))
    if action == GitAction.COMMIT_PUSH:
        findings.append(Advisory(
            AdviceLevel.WARNING,
            "This combined workflow will stage all approved changes, create a commit, and publish it to origin.",
            "Review the changed-file set and commit message carefully before approving the workflow.",
        ))
    return AdvisorReport(findings=findings)
