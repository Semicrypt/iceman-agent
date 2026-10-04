from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum

from iceman.session import SessionState
from iceman.probing import normalize_investigation_subject
from iceman.workspace_files import WorkspaceTemplate
from iceman.devops_actions import DevOpsAction
from iceman.linux_actions import LinuxAction
from iceman.git_actions import GitAction
from iceman.conversation import PromptInterpretation, interpret_prompt
from iceman.language_understanding import (
    candidates_for_context,
    candidates_for_devops_entity,
    candidates_for_linux_path,
    candidates_for_workspace_component,
    normalize_existing_resource_separators,
)


class ReasoningAction(str, Enum):
    ASSESS = "assess"
    PRIORITIZE = "prioritize"
    DIAGNOSE = "diagnose"
    EXPLAIN = "explain"
    INVESTIGATE = "investigate"
    RECOMMEND_REMEDIATION = "recommend_remediation"
    APPLY_REMEDIATION_OPTION = "apply_remediation_option"
    ANALYZE_HTTP_CONTEXT = "analyze_http_context"
    PLAN_WORKSPACE_FILES = "plan_workspace_files"
    APPLY_WORKSPACE_FILE_PLAN = "apply_workspace_file_plan"
    ROLLBACK_WORKSPACE_FILES = "rollback_workspace_files"
    EXECUTE_DEVOPS_ACTION = "execute_devops_action"
    EXECUTE_LINUX_ACTION = "execute_linux_action"
    EXECUTE_GIT_ACTION = "execute_git_action"
    SESSION = "session"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class ReasoningDecision:
    action: ReasoningAction
    explanation: str
    target: str | None = None
    option_number: int | None = None
    replacement_url: str | None = None
    file_template: WorkspaceTemplate | None = None
    component: str | None = None
    port: int | None = None
    replicas: int | None = None
    image: str | None = None
    startup_target: str | None = None
    devops_action: DevOpsAction | None = None
    context: str | None = None
    terraform_dir: str | None = None
    linux_action: LinuxAction | None = None
    path: str | None = None
    destination: str | None = None
    pattern: str | None = None
    git_action: GitAction | None = None
    commit_message: str | None = None
    repo_name: str | None = None
    visibility: str | None = None
    tag: str | None = None
    interpreted_request: str | None = None
    requires_clarification: bool = False
    clarification_kind: str | None = None
    clarification_original: str | None = None
    clarification_candidates: tuple[str, ...] = ()
    clarification_field: str | None = None



def _decorate_interpretation(
    decision: ReasoningDecision,
    interpretation: PromptInterpretation,
) -> ReasoningDecision:
    if not interpretation.changed and not interpretation.requires_clarification:
        return decision
    return replace(
        decision,
        interpreted_request=interpretation.normalized,
        requires_clarification=interpretation.requires_clarification,
    )



def _replace_entity_token(text: str, original: str, resolved: str) -> str:
    pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(original)}(?![A-Za-z0-9_.-])"
    updated, count = re.subn(pattern, resolved, text, count=1, flags=re.IGNORECASE)
    return updated if count else text


def _apply_entity_resolution(
    decision: ReasoningDecision,
    interpretation: PromptInterpretation,
    state: SessionState,
) -> ReasoningDecision:
    decision = _decorate_interpretation(decision, interpretation)

    resolution = None
    field_name: str | None = None
    original: str | None = None

    if decision.action == ReasoningAction.EXECUTE_DEVOPS_ACTION and decision.devops_action:
        if decision.component:
            original = decision.component
            resolution = candidates_for_devops_entity(
                state.workspace,
                decision.devops_action,
                original,
            )
            field_name = "component"
        elif decision.context:
            original = decision.context
            resolution = candidates_for_context(state.workspace, original)
            field_name = "context"
    elif decision.action == ReasoningAction.EXECUTE_LINUX_ACTION and decision.linux_action and decision.path:
        original = decision.path
        resolution = candidates_for_linux_path(
            state.workspace,
            decision.linux_action,
            original,
        )
        field_name = "path"
    elif decision.action == ReasoningAction.PLAN_WORKSPACE_FILES and decision.component:
        original = decision.component
        resolution = candidates_for_workspace_component(state.workspace, original)
        field_name = "component"

    if resolution is None or original is None or field_name is None:
        return decision

    if resolution.changed and resolution.resolved:
        base = decision.interpreted_request or interpretation.normalized
        corrected = _replace_entity_token(base, original, resolution.resolved)
        return replace(
            decision,
            **{field_name: resolution.resolved},
            interpreted_request=corrected,
            requires_clarification=True,
            clarification_kind=resolution.kind,
            clarification_original=original,
        )

    if resolution.candidates:
        return replace(
            decision,
            requires_clarification=True,
            clarification_kind=resolution.kind,
            clarification_original=original,
            clarification_candidates=resolution.candidates,
            clarification_field=field_name,
        )

    return decision


def _extract_int(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _extract_component(text: str) -> str | None:
    patterns = (
        r"\bfor\s+([A-Za-z0-9_.-]+)",
        r"\bcomponent\s+([A-Za-z0-9_.-]+)",
        r"\bapp\s+([A-Za-z0-9_.-]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match.group(1)
            if value.lower() not in {
                "aws", "azure", "kubernetes", "k8s", "terraform",
            }:
                return value
    return None


def _extract_image(text: str) -> str | None:
    match = re.search(
        r"\bimage\s+([A-Za-z0-9][A-Za-z0-9._/@:+-]{0,254})",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def _extract_workspace_file_request(
    text: str,
) -> ReasoningDecision | None:
    normalized = " ".join(text.strip().split())
    lower = normalized.lower()

    if lower in {
        "apply file plan",
        "apply last file plan",
        "apply workspace file plan",
    }:
        return ReasoningDecision(
            ReasoningAction.APPLY_WORKSPACE_FILE_PLAN,
            "Apply the last already-previewed workspace file plan through the "
            "explicit approval gate.",
        )

    if lower in {
        "rollback file changes",
        "rollback workspace files",
        "undo last file changes",
        "undo the last file changes",
    }:
        return ReasoningDecision(
            ReasoningAction.ROLLBACK_WORKSPACE_FILES,
            "Rollback the most recent successful Iceman workspace-file write.",
        )

    if not re.match(
        r"^(?:create|make|generate|add)\b",
        lower,
    ):
        return None

    component = _extract_component(normalized)
    port = _extract_int(r"\bport\s+(\d{1,5})\b", normalized)
    replicas = _extract_int(
        r"\b(?:replicas?|copies)\s+(\d{1,3})\b",
        normalized,
    )
    image = _extract_image(normalized)

    if "compose" in lower and ("docker" in lower or "compose" in lower):
        return ReasoningDecision(
            ReasoningAction.PLAN_WORKSPACE_FILES,
            "Generate a bounded Docker Compose service from an existing Dockerfile without inventing secrets or dependencies.",
            file_template=WorkspaceTemplate.DOCKER_COMPOSE,
            component=component,
            port=port,
        )

    if re.search(r"\bdocker\s*file\b|\bdockerfile\b", lower):
        return ReasoningDecision(
            ReasoningAction.PLAN_WORKSPACE_FILES,
            "Generate a deterministic Dockerfile and .dockerignore preview for "
            "the selected Node.js component.",
            file_template=WorkspaceTemplate.DOCKERFILE,
            component=component,
            port=port,
        )

    if ".dockerignore" in lower or "dockerignore" in lower:
        return ReasoningDecision(
            ReasoningAction.PLAN_WORKSPACE_FILES,
            "Generate deterministic Docker build-context ignore rules.",
            file_template=WorkspaceTemplate.DOCKERIGNORE,
            component=component,
        )

    if ".gitignore" in lower or "gitignore" in lower:
        return ReasoningDecision(
            ReasoningAction.PLAN_WORKSPACE_FILES,
            "Generate deterministic DevOps-oriented Git ignore rules.",
            file_template=WorkspaceTemplate.GITIGNORE,
        )

    if (
        ("kubernetes" in lower or re.search(r"\bk8s\b", lower))
        and "resource" in lower
        and ("request" in lower or "limit" in lower)
    ):
        return ReasoningDecision(
            ReasoningAction.PLAN_WORKSPACE_FILES,
            "Prepare a bounded starter Kubernetes CPU/memory resource patch and "
            "preview the exact manifest diff.",
            file_template=WorkspaceTemplate.KUBERNETES_RESOURCES,
            component=component,
        )

    if (
        ("kubernetes" in lower or re.search(r"\bk8s\b", lower))
        and (
            "deployment" in lower
            or "service" in lower
            or "manifest" in lower
        )
    ):
        return ReasoningDecision(
            ReasoningAction.PLAN_WORKSPACE_FILES,
            "Generate Kubernetes Deployment and Service manifests through the "
            "workspace file engine.",
            file_template=WorkspaceTemplate.KUBERNETES_APP,
            component=component,
            port=port,
            replicas=replicas,
            image=image,
        )

    if "terraform" in lower and "aws" in lower:
        return ReasoningDecision(
            ReasoningAction.PLAN_WORKSPACE_FILES,
            "Generate deterministic Terraform AWS provider scaffolding without "
            "credentials or invented cloud resources.",
            file_template=WorkspaceTemplate.TERRAFORM_AWS_STARTER,
        )

    if "terraform" in lower and "azure" in lower:
        return ReasoningDecision(
            ReasoningAction.PLAN_WORKSPACE_FILES,
            "Generate deterministic Terraform AzureRM provider scaffolding without "
            "credentials or invented cloud resources.",
            file_template=WorkspaceTemplate.TERRAFORM_AZURE_STARTER,
        )

    return None


def _component_after_verb(text: str, verb: str) -> str | None:
    match = re.match(
        rf"^{verb}\s+(?:the\s+)?([A-Za-z0-9_.-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    value = match.group(1)
    if value.lower() in {"docker", "container", "image", "kubernetes", "terraform"}:
        return None
    return value


def _explicit_image_anywhere(text: str) -> str | None:
    patterns = (
        r"\bimage\s+([A-Za-z0-9][A-Za-z0-9._/@:+-]{0,254})",
        r"\bas\s+([A-Za-z0-9][A-Za-z0-9._/@:+-]{0,254})",
        r"^run\s+([A-Za-z0-9][A-Za-z0-9._/@:+-]{0,254})\s+on\s+port",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match.group(1)
            if ":" in value or "/" in value:
                return value
    return None


def _extract_devops_action_request(
    text: str,
    state: SessionState,
) -> ReasoningDecision | None:
    normalized = " ".join(text.strip().split())
    lower = normalized.lower().rstrip("?!.")
    port = _extract_int(r"\bport\s+(\d{1,5})\b", normalized)

    # Git / GitHub natural-language workflows.
    if lower in {"initialize git", "init git", "git init", "make this a git repo", "make this a git repository", "initialize this git repository"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_GIT_ACTION, "Initialize a local Git repository on main.", git_action=GitAction.INIT)
    if lower in {"show git status", "git status", "show me git status", "what is the git status"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_GIT_ACTION, "Show the current Git working-tree status.", git_action=GitAction.STATUS)
    if lower in {"show git changes", "show me the changes", "what changed", "show what changed", "show git diff", "git diff"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_GIT_ACTION, "Show staged and unstaged Git change summaries.", git_action=GitAction.DIFF)
    if lower in {"show git remotes", "show remotes", "git remotes", "show remote"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_GIT_ACTION, "Show configured Git remotes.", git_action=GitAction.REMOTES)
    if lower in {"show current branch", "show git branch", "show branches", "what branch am i on"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_GIT_ACTION, "Show the current Git branch and local branches.", git_action=GitAction.BRANCH)
    if lower in {"stage everything", "stage all changes", "git add everything", "git add all", "stage all"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_GIT_ACTION, "Run the pre-commit safety checks and stage all approved changes.", git_action=GitAction.STAGE_ALL)

    match = re.match(r"^(?:commit|commit my changes|commit the changes|create a commit)(?:\s+(?:with\s+)?message\s+)(?:[\"'])(.+?)(?:[\"'])$", normalized, flags=re.IGNORECASE)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_GIT_ACTION, "Commit the already-staged changes with the supplied message.", git_action=GitAction.COMMIT, commit_message=match.group(1).strip())
    match = re.match(r"^(?:commit|commit my changes|commit the changes)\s+(?:as|with message)\s+(.+)$", normalized, flags=re.IGNORECASE)
    if match:
        msg = match.group(1).strip().strip('"\'')
        return ReasoningDecision(ReasoningAction.EXECUTE_GIT_ACTION, "Commit the already-staged changes with the supplied message.", git_action=GitAction.COMMIT, commit_message=msg)
    if lower in {"commit my changes", "commit changes", "commit everything", "commit this milestone"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_GIT_ACTION, "Prepare a Git commit after the user supplies a commit message.", git_action=GitAction.COMMIT)

    match = re.match(r"^(?:commit and push|stage commit and push|commit and push everything)(?:\s+(?:with\s+)?message\s+)(?:[\"'])(.+?)(?:[\"'])$", normalized, flags=re.IGNORECASE)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_GIT_ACTION, "Safely stage, commit, and push the current milestone.", git_action=GitAction.COMMIT_PUSH, commit_message=match.group(1).strip())
    push_tag_patterns = (
        r"^(?:push|publish|send)\s+(?:the\s+)?tag\s+([A-Za-z0-9][A-Za-z0-9._/+\-]{0,127})(?:\s+to\s+(?:github|origin))?$",
        r"^(?:push|publish|send)\s+(?:the\s+)?([A-Za-z0-9][A-Za-z0-9._/+\-]{0,127})\s+tag(?:\s+to\s+(?:github|origin))?$",
    )
    for pattern in push_tag_patterns:
        match = re.match(pattern, lower)
        if match:
            return ReasoningDecision(
                ReasoningAction.EXECUTE_GIT_ACTION,
                "Publish one existing local Git milestone tag to the configured origin remote.",
                git_action=GitAction.PUSH_TAG,
                tag=match.group(1),
            )

    if lower in {"push to github", "push this project to github", "push this branch", "push everything", "git push", "push to origin"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_GIT_ACTION, "Push the current Git branch to the configured origin remote.", git_action=GitAction.PUSH)

    if "github repo" in lower or "github repository" in lower or lower in {"create repo on github", "create a repo on github", "connect this project to github"}:
        visibility = "public" if re.search(r"\bpublic\b", lower) else ("private" if re.search(r"\bprivate\b", lower) else None)
        name_match = re.search(r"\b(?:called|named)\s+([A-Za-z0-9][A-Za-z0-9._-]{0,99})\b", normalized, flags=re.IGNORECASE)
        return ReasoningDecision(
            ReasoningAction.EXECUTE_GIT_ACTION,
            "Create a GitHub repository through authenticated GitHub CLI and connect it as origin.",
            git_action=GitAction.GITHUB_CREATE_REPO,
            repo_name=name_match.group(1) if name_match else None,
            visibility=visibility,
        )

    match = re.match(r"^(?:tag this as|create tag|tag milestone)\s+([A-Za-z0-9][A-Za-z0-9._/+\-]{0,127})$", lower)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_GIT_ACTION, "Create an annotated local Git milestone tag.", git_action=GitAction.TAG, tag=match.group(1))

    # Kubernetes is checked before generic restart/logs.
    if lower in {"show current kubernetes context", "show kubernetes context", "what kubernetes context am i using"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Show the current Kubernetes context.", devops_action=DevOpsAction.K8S_CONTEXT)
    if lower in {"show kubernetes contexts", "list kubernetes contexts", "show me kubernetes contexts"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "List Kubernetes contexts.", devops_action=DevOpsAction.K8S_CONTEXTS)
    if ("validate" in lower or "check" in lower) and ("kubernetes" in lower or "k8s" in lower or "manifest" in lower):
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Validate Kubernetes manifests using kubectl client dry-run.", devops_action=DevOpsAction.K8S_VALIDATE)
    match = re.match(r"^deploy\s+(?:the\s+)?([A-Za-z0-9_.-]+)(?:\s+to\s+(?:the\s+)?([A-Za-z0-9_.:@/+\-]+)(?:\s+cluster)?)?$", lower)
    if match:
        target = match.group(2)
        if target and target.lower() in {"kubernetes", "k8s", "cluster"}:
            target = None
        return ReasoningDecision(
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
            "Deploy the selected Kubernetes component using a bounded trusted workflow.",
            devops_action=DevOpsAction.K8S_DEPLOY,
            component=match.group(1),
            context=target,
        )
    if re.match(r"^(?:show(?:\s+me)?(?:\s+the)?|list|show\s+kubernetes)\s+pods?$", lower):
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "List Kubernetes pods.", devops_action=DevOpsAction.K8S_PODS)
    if re.match(r"^(?:show(?:\s+me)?(?:\s+the)?|list|show\s+kubernetes)\s+deployments?$", lower):
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "List Kubernetes deployments.", devops_action=DevOpsAction.K8S_DEPLOYMENTS)
    if re.match(r"^(?:show(?:\s+me)?(?:\s+the)?|list|show\s+kubernetes)\s+services?$", lower):
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "List Kubernetes services.", devops_action=DevOpsAction.K8S_SERVICES)
    match = re.match(r"^(?:wait until|wait for)\s+([A-Za-z0-9_.-]+)\s+(?:is\s+)?ready$", lower)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Wait for the named Kubernetes deployment rollout.", devops_action=DevOpsAction.K8S_ROLLOUT, component=match.group(1))
    match = re.match(r"^scale\s+(?:deployment\s+)?([A-Za-z0-9_.-]+)\s+(?:to\s+)?(\d+)(?:\s+replicas?)?$", lower)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Scale the named Kubernetes deployment after approval.", devops_action=DevOpsAction.K8S_SCALE, component=match.group(1), replicas=int(match.group(2)))
    match = re.match(r"^restart\s+(?:the\s+)?([A-Za-z0-9_.-]+)\s+deployment$", lower)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Restart the named Kubernetes deployment after approval.", devops_action=DevOpsAction.K8S_RESTART, component=match.group(1))
    match = re.match(r"^(?:show\s+)?(?:kubernetes|k8s)\s+logs\s+(?:for\s+)?([A-Za-z0-9_.-]+)$", lower)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Show recent Kubernetes deployment logs.", devops_action=DevOpsAction.K8S_LOGS, component=match.group(1))
    match = re.match(r"^describe\s+(?:deployment\s+)?([A-Za-z0-9_.-]+)(?:\s+deployment)?$", lower)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Describe the named Kubernetes deployment.", devops_action=DevOpsAction.K8S_DESCRIBE, component=match.group(1))

    # Terraform.
    if lower in {"initialize terraform", "initialize my terraform", "init terraform", "terraform init"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Initialize Terraform providers locally.", devops_action=DevOpsAction.TERRAFORM_INIT)
    if lower in {"format terraform", "format my terraform", "terraform format", "terraform fmt"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Format Terraform configuration files.", devops_action=DevOpsAction.TERRAFORM_FMT)
    if re.match(r"^(?:validate|check)(?:\s+my)?\s+terraform$", lower):
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Validate the active Terraform configuration.", devops_action=DevOpsAction.TERRAFORM_VALIDATE)
    if lower in {"show terraform plan", "show me the terraform plan", "terraform plan", "show me what terraform will create", "what will terraform create"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Create a read-only Terraform plan; do not apply changes.", devops_action=DevOpsAction.TERRAFORM_PLAN)
    if "terraform" in lower and "plan" in lower and any(word in lower for word in {"fail", "failing", "failed", "error", "broken", "wrong"}):
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Re-run the read-only Terraform plan so Iceman can surface the current planning failure.", devops_action=DevOpsAction.TERRAFORM_PLAN)
    if lower in {"explain terraform plan", "explain the terraform plan", "explain what terraform will create"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Create and summarize a read-only Terraform plan.", devops_action=DevOpsAction.TERRAFORM_EXPLAIN_PLAN)
    if lower in {"show terraform state", "list terraform state", "show terraform resources"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "List Terraform state resources.", devops_action=DevOpsAction.TERRAFORM_STATE)

    # Evidence-backed guided Docker remediation.
    match = re.match(
        r"^(?:fix|repair)\s+(?:the\s+)?(?:container\s+)?"
        r"([A-Za-z0-9_.-]+)"
        r"(?:\s+(?:using|with)\s+([A-Za-z0-9][A-Za-z0-9._/\-]{0,127}))?$",
        lower,
    )
    if match:
        return ReasoningDecision(
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
            (
                "Build an evidence-backed Docker repair from the latest diagnosis "
                "without inventing a replacement startup target."
            ),
            devops_action=DevOpsAction.DOCKER_GUIDED_REPAIR,
            component=match.group(1),
            startup_target=match.group(2),
        )

    match = re.match(
        r"^(?:fix|repair)\s+it"
        r"(?:\s+(?:using|with)\s+([A-Za-z0-9][A-Za-z0-9._/\-]{0,127}))?$",
        lower,
    )
    if match and state.last_devops_component:
        return ReasoningDecision(
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
            (
                "Build an evidence-backed Docker repair for the most recently "
                "diagnosed component."
            ),
            devops_action=DevOpsAction.DOCKER_GUIDED_REPAIR,
            component=state.last_devops_component,
            startup_target=match.group(1),
        )

    # Docker advanced diagnostics / intelligence. These are checked before
    # generic build/run/log actions so conversational "why" requests stay bounded.
    match = re.match(
        r"^why\s+(?:did|is)\s+(?:the\s+)?([A-Za-z0-9_.-]+)\s+(?:stop|stopped|restart|restarting|fail|failing|crash|crashing|not\s+working)$",
        lower,
    )
    if match:
        return ReasoningDecision(
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
            "Inspect Docker runtime state, published ports, and recent logs to explain the failure.",
            devops_action=DevOpsAction.DOCKER_DIAGNOSE,
            component=match.group(1),
        )
    match = re.match(
        r"^(?:diagnose|troubleshoot|check\s+what(?:'s|\s+is)\s+wrong\s+with)\s+(?:container\s+)?([A-Za-z0-9_.-]+)$",
        lower,
    )
    if match:
        return ReasoningDecision(
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
            "Run a bounded read-only Docker runtime diagnosis.",
            devops_action=DevOpsAction.DOCKER_DIAGNOSE,
            component=match.group(1),
        )
    match = re.match(
        r"^(?:check|show)(?:\s+me)?\s+(?:if\s+)?(?:container\s+)?([A-Za-z0-9_.-]+)\s+(?:is\s+)?healthy$",
        lower,
    )
    if match:
        return ReasoningDecision(
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
            "Inspect Docker runtime and health status.",
            devops_action=DevOpsAction.DOCKER_HEALTH,
            component=match.group(1),
        )
    match = re.match(
        r"^(?:show|find|check)(?:\s+me)?\s+(?:the\s+)?(?:important\s+|main\s+|relevant\s+)?errors?\s+(?:in|from)\s+([A-Za-z0-9_.-]+)(?:\s+logs?)?$",
        lower,
    )
    if match:
        return ReasoningDecision(
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
            "Inspect recent Docker logs and summarize common high-signal failures.",
            devops_action=DevOpsAction.DOCKER_DIAGNOSE,
            component=match.group(1),
        )
    # Component-aware Docker connectivity language. Keep this before the
    # port-only route so ordinary problem descriptions retain the container
    # identity and any host port the operator mentioned.
    connectivity_patterns = (
        r"^(?:why\s+)?(?:can(?:not|'t)|cant)\s+i\s+(?:reach|access|connect\s+to|open)\s+(?:the\s+)?(?:container\s+)?([A-Za-z0-9_.-]+)(?:\s+(?:on|at|using)\s+(?:host\s+)?port\s+(\d{1,5}))?$",
        r"^i\s+(?:can(?:not|'t)|cant)\s+(?:reach|access|connect\s+to|open)\s+(?:the\s+)?(?:container\s+)?([A-Za-z0-9_.-]+)(?:\s+(?:on|at|using)\s+(?:host\s+)?port\s+(\d{1,5}))?$",
        r"^(?:the\s+)?(?:container\s+)?([A-Za-z0-9_.-]+)\s+(?:is(?:n't|\s+not)|isnt)\s+(?:reachable|accessible|opening|responding)(?:\s+(?:on|at)\s+(?:host\s+)?port\s+(\d{1,5}))?$",
        r"^why\s+is\s+(?:the\s+)?(?:container\s+)?([A-Za-z0-9_.-]+)\s+not\s+(?:reachable|accessible|opening|responding)(?:\s+(?:on|at)\s+(?:host\s+)?port\s+(\d{1,5}))?$",
        r"^(?:check|diagnose|troubleshoot)\s+(?:why\s+)?(?:the\s+)?(?:container\s+)?([A-Za-z0-9_.-]+)(?:'s|\s+)?\s*(?:connection|connectivity)\s+(?:is\s+)?(?:failing|broken|not\s+working)(?:\s+(?:on|at)\s+(?:host\s+)?port\s+(\d{1,5}))?$",
    )
    for pattern in connectivity_patterns:
        match = re.match(pattern, lower)
        if match:
            requested = int(match.group(2)) if match.group(2) else None
            return ReasoningDecision(
                ReasoningAction.EXECUTE_DEVOPS_ACTION,
                "Inspect the container runtime, published ports, host-port ownership, and a bounded localhost HTTP probe to explain connectivity.",
                devops_action=DevOpsAction.DOCKER_CONNECTIVITY,
                component=match.group(1),
                port=requested,
            )

    match = re.match(
        r"^(?:why\s+)?(?:can(?:not|'t)|cant)\s+i\s+(?:reach|access|connect\s+to)\s+(?:host\s+)?port\s+(\d{1,5})$",
        lower,
    )
    if match:
        return ReasoningDecision(
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
            "Inspect Docker port publications and local listeners for the requested port.",
            devops_action=DevOpsAction.DOCKER_PORTS,
            port=int(match.group(1)),
        )

    match = re.match(
        r"^(?:host\s+)?port\s+(\d{1,5})\s+(?:is(?:n't|\s+not)|isnt)\s+(?:working|reachable|opening|responding)$",
        lower,
    )
    if match:
        return ReasoningDecision(
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
            "Inspect Docker port publications and local listeners for the requested port.",
            devops_action=DevOpsAction.DOCKER_PORTS,
            port=int(match.group(1)),
        )
    match = re.match(
        r"^(?:check|review|scan)\s+(?:the\s+)?(?:docker\s+)?security(?:\s+(?:for|of))?\s*([A-Za-z0-9_.-]+)?$",
        lower,
    )
    if match:
        return ReasoningDecision(
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
            "Review bounded Dockerfile/runtime security signals without exposing secrets.",
            devops_action=DevOpsAction.DOCKER_SECURITY,
            component=match.group(1) or None,
        )
    match = re.match(
        r"^(?:make|optimize|analyse|analyze|review)\s+(?:the\s+)?([A-Za-z0-9_.-]+)\s+(?:docker\s+)?image\s+(?:smaller|size|layers|for\s+size)$",
        lower,
    )
    if match:
        return ReasoningDecision(
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
            "Inspect Docker image layers/size and surface optimization recommendations.",
            devops_action=DevOpsAction.DOCKER_OPTIMIZE,
            component=match.group(1),
        )
    match = re.match(
        r"^(?:rebuild\s+and\s+restart|rebuild\s+then\s+restart)\s+(?:the\s+)?([A-Za-z0-9_.-]+)$",
        lower,
    )
    if match:
        return ReasoningDecision(
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
            "Safely rebuild and replace a simple standalone Docker container while preserving the old container as a rollback backup.",
            devops_action=DevOpsAction.DOCKER_REBUILD_RESTART,
            component=match.group(1),
        )
    if lower in {"show compose status", "show docker compose status", "show me compose services", "show compose services"}:
        return ReasoningDecision(
            ReasoningAction.EXECUTE_DEVOPS_ACTION,
            "Show Docker Compose service status.",
            devops_action=DevOpsAction.DOCKER_COMPOSE_STATUS,
        )

    # Docker.
    if re.match(r"^build\b", lower):
        component = _component_after_verb(normalized, "build")
        image = _explicit_image_anywhere(normalized)
        if component or "docker" in lower or "image" in lower:
            return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Build a Docker image from the selected component.", devops_action=DevOpsAction.DOCKER_BUILD, component=component, image=image)
    if re.match(r"^run\b", lower):
        component = _component_after_verb(normalized, "run")
        image = _explicit_image_anywhere(normalized)
        if component == "it":
            component = getattr(state, "last_devops_component", None)
            image = image or getattr(state, "last_devops_image", None)
        if image and component and (":" in component or "/" in component):
            component = None
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Run the selected Docker image/container.", devops_action=DevOpsAction.DOCKER_RUN, component=component, image=image or getattr(state, "last_devops_image", None), port=port)
    if lower in {"show running containers", "show me the containers", "list containers", "show docker containers", "show me running containers"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "List running Docker containers.", devops_action=DevOpsAction.DOCKER_LIST)
    if lower in {"show docker images", "show me the docker images", "list docker images", "show images", "list images"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "List Docker images.", devops_action=DevOpsAction.DOCKER_IMAGES)
    match = re.match(r"^(?:show\s+)?([A-Za-z0-9_.-]+)\s+logs$", lower)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Show recent Docker container logs.", devops_action=DevOpsAction.DOCKER_LOGS, component=match.group(1))
    match = re.match(r"^stop\s+(?:container\s+)?([A-Za-z0-9_.-]+)$", lower)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Stop the named Docker container after approval.", devops_action=DevOpsAction.DOCKER_STOP, component=match.group(1))
    match = re.match(r"^restart\s+(?:container\s+)?([A-Za-z0-9_.-]+)$", lower)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_DEVOPS_ACTION, "Restart the named Docker container after approval.", devops_action=DevOpsAction.DOCKER_RESTART, component=match.group(1))

    return None



def _extract_linux_action_request(text: str) -> ReasoningDecision | None:
    normalized = " ".join(text.strip().split())
    lower = normalized.lower().rstrip("?!.")

    if lower in {"where am i", "what directory am i in", "show current directory", "show my current directory"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Show the active Linux directory.", linux_action=LinuxAction.PWD)

    if lower in {"show files", "show me the files", "list files", "list the files", "what files are here", "show files here"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "List files in the active directory.", linux_action=LinuxAction.LIST)
    if lower in {"show hidden files", "show all files", "list all files", "list hidden files", "show me hidden files"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "List all files including hidden entries.", linux_action=LinuxAction.LIST_ALL)

    if lower in {"go back", "go back one folder", "go back one directory", "go up one folder", "go up one directory"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Change the active Iceman workspace to the parent directory.", linux_action=LinuxAction.CHANGE_DIR, path="..")
    match = re.match(r"^(?:go into|go to|change (?:folder|directory) to|change to|enter)\s+(.+)$", normalized, flags=re.IGNORECASE)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Change the active Iceman workspace.", linux_action=LinuxAction.CHANGE_DIR, path=match.group(1).strip())

    match = re.match(r"^(?:create|make)\s+(?:a\s+)?(?:folder|directory)\s+(?:called\s+)?(.+)$", normalized, flags=re.IGNORECASE)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Create a directory inside the active workspace.", linux_action=LinuxAction.MAKE_DIR, path=match.group(1).strip())
    match = re.match(r"^(?:create|make)\s+(?:an?\s+)?(?:empty\s+)?file\s+(?:called\s+)?(.+)$", normalized, flags=re.IGNORECASE)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Create an empty file inside the active workspace.", linux_action=LinuxAction.CREATE_FILE, path=match.group(1).strip())

    match = re.match(r"^(?:show|open|read|cat)\s+(?:me\s+)?(?:the\s+)?(?:file\s+)?(.+)$", normalized, flags=re.IGNORECASE)
    if match and not re.search(r"\b(pods?|deployments?|services?|containers?|docker images|terraform plan|terraform state|permissions?)\b", lower):
        candidate = match.group(1).strip()
        if "/" in candidate or "." in candidate or candidate.lower() in {"dockerfile", "makefile"}:
            return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Show the requested file contents.", linux_action=LinuxAction.SHOW_FILE, path=candidate)

    match = re.match(r"^copy\s+(.+?)\s+(?:to|into)\s+(.+)$", normalized, flags=re.IGNORECASE)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Copy a path inside the active workspace.", linux_action=LinuxAction.COPY, path=match.group(1).strip(), destination=match.group(2).strip())
    match = re.match(r"^move\s+(.+?)\s+(?:to|into)\s+(.+)$", normalized, flags=re.IGNORECASE)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Move a path inside the active workspace.", linux_action=LinuxAction.MOVE, path=match.group(1).strip(), destination=match.group(2).strip())

    match = re.match(r"^(?:make|set)\s+(.+?)\s+executable$", normalized, flags=re.IGNORECASE)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Add executable permission to the selected file.", linux_action=LinuxAction.MAKE_EXECUTABLE, path=match.group(1).strip())
    match = re.match(r"^(?:show|check)\s+(?:the\s+)?permissions?(?:\s+for|\s+of)?\s+(.+)$", normalized, flags=re.IGNORECASE)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Show file ownership and permissions.", linux_action=LinuxAction.SHOW_PERMISSIONS, path=match.group(1).strip())

    if lower in {"show disk space", "show filesystem usage", "show free disk space", "how much disk space is left"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Show filesystem capacity and free space.", linux_action=LinuxAction.FILESYSTEM_USAGE)
    match = re.match(r"^(?:show|check)\s+(?:disk\s+)?(?:usage|size)(?:\s+for|\s+of)?(?:\s+(.+))?$", normalized, flags=re.IGNORECASE)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Show disk usage for a path.", linux_action=LinuxAction.DISK_USAGE, path=(match.group(1).strip() if match.group(1) else None))

    match = re.match(r"^find\s+(?:all\s+)?files?\s+(?:named|matching|like)\s+(.+)$", normalized, flags=re.IGNORECASE)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Find files matching a filename pattern.", linux_action=LinuxAction.FIND_FILES, pattern=match.group(1).strip().strip('"\''))
    match = re.match(r"^(?:find|search for)\s+(?:text\s+)?[\"']?(.+?)[\"']?\s+(?:in|inside)\s+(?:the\s+)?(?:project|workspace|files?)$", normalized, flags=re.IGNORECASE)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Search for text in the active workspace.", linux_action=LinuxAction.FIND_TEXT, pattern=match.group(1).strip().strip('"\''))

    match = re.match(r"^(?:show|find|check)(?:\s+me)?\s+(?:what|which process)?\s*(?:is\s+)?(?:using|listening on)\s+port\s+(\d{1,5})$", lower)
    if match:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Show the process listening on the requested TCP port.", linux_action=LinuxAction.PORT_OWNER, port=int(match.group(1)))
    if lower in {"show running processes", "list running processes", "show processes", "show me the processes"}:
        return ReasoningDecision(ReasoningAction.EXECUTE_LINUX_ACTION, "Show the busiest running processes.", linux_action=LinuxAction.PROCESSES)

    return None

_ASSESS_PHRASES = (
    "check everything",
    "check this project",
    "check the project",
    "check project",
    "check for problems",
    "project health",
    "health check",
    "verify everything",
    "verify the project",
    "is everything healthy",
    "is the project healthy",
    "why is my application not working",
    "why is this application not working",
    "why is the app not working",
)

_PRIORITY_PHRASES = (
    "what should i fix first",
    "what should i fix",
    "what do i fix first",
    "prioritize",
    "highest priority",
    "biggest problem",
    "most important problem",
)

_EXPLAIN_PHRASES = (
    "what caused that",
    "why did that happen",
    "explain that",
    "explain the issue",
    "what happened",
)

_SESSION_PHRASES = (
    "session",
    "show session",
    "show context",
    "what do you remember",
    "what is in context",
)


def _extract_why_target(text: str) -> str | None:
    patterns = (
        r"^why is\s+([a-zA-Z0-9_.-]+)\s+(?:broken|down|failing|restarting|stopped|not working)\??$",
        r"^what is wrong with\s+([a-zA-Z0-9_.-]+)\??$",
        r"^check\s+([a-zA-Z0-9_.-]+)\s+(?:container|service)\??$",
    )

    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return match.group(1)
    return None



def _extract_investigation_target(text: str) -> str | None:
    patterns = (
        r"^investigate\s+(?:why\s+)?(.+?)\s+is\s+(?:down|failing|offline|unhealthy)\??$",
        r"^why\s+is\s+(.+?)\s+(?:down|failing|offline|unhealthy)\??$",
        r"^probe\s+(.+?)\??$",
        r"^check\s+why\s+(.+?)\s+is\s+(?:down|failing|offline|unhealthy)\??$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return normalize_investigation_subject(match.group(1))
    return None


_REMEDIATION_RECOMMEND_PHRASES = (
    "how should i fix it",
    "how do i fix it",
    "what are my options",
    "what are the remediation options",
    "recommend a fix",
    "recommend remediation",
    "what should i do about it",
)


_HTTP_CONTEXT_DEEP_DIVE_PHRASES = (
    "go deeper on the backend rejection",
    "go deeper on backend rejection",
    "investigate the backend-specific http rejection",
    "investigate the backend specific http rejection",
    "compare host and backend http context",
    "compare the host and backend http context",
    "analyze the http context mismatch",
    "analyze the context mismatch",
)


def _extract_investigate_option(text: str) -> int | None:
    match = re.match(
        r"^investigate\s+option\s+(\d+)$",
        text,
        flags=re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


def _extract_apply_option(
    text: str,
) -> tuple[int, str | None] | None:
    patterns = (
        r"^apply\s+option\s+(\d+)(?:\s+with\s+(https?://\S+))?$",
        r"^use\s+(https?://\S+)\s+for\s+option\s+(\d+)$",
    )

    for index, pattern in enumerate(patterns):
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue

        if index == 0:
            option = int(match.group(1))
            url = match.group(2)
        else:
            url = match.group(1)
            option = int(match.group(2))

        return option, url.rstrip(".,") if url else None

    return None


def reason_request(prompt: str, state: SessionState) -> ReasoningDecision:
    interpretation = interpret_prompt(prompt, state)

    # Ground separator variants against resources that actually exist before
    # intent routing. This lets natural phrases such as "broken backend" match
    # a real container named "broken-backend" without teaching users CLI naming
    # syntax. The corrected interpretation is still confirmed before execution.
    grounded_text, grounded_repairs = normalize_existing_resource_separators(
        interpretation.normalized,
        state.workspace,
    )
    if grounded_repairs:
        interpretation = replace(
            interpretation,
            normalized=grounded_text,
            corrections=interpretation.corrections + grounded_repairs,
            requires_clarification=True,
        )

    effective_prompt = interpretation.normalized
    text = " ".join(effective_prompt.lower().strip().split()).rstrip("?!.")

    workspace_request = _extract_workspace_file_request(effective_prompt.rstrip("?!." ))
    if workspace_request is not None:
        return _apply_entity_resolution(workspace_request, interpretation, state)

    linux_request = _extract_linux_action_request(effective_prompt)
    if linux_request is not None:
        return _apply_entity_resolution(linux_request, interpretation, state)

    devops_request = _extract_devops_action_request(effective_prompt, state)
    if devops_request is not None:
        return _apply_entity_resolution(devops_request, interpretation, state)

    if text in _SESSION_PHRASES:
        return ReasoningDecision(
            ReasoningAction.SESSION,
            "Show the state Iceman is carrying in this interactive session.",
        )

    if any(phrase in text for phrase in _PRIORITY_PHRASES):
        return ReasoningDecision(
            ReasoningAction.PRIORITIZE,
            "Rank the currently known project issues and identify what should be addressed first.",
        )

    investigate_option = _extract_investigate_option(text)
    if investigate_option is not None:
        return ReasoningDecision(
            ReasoningAction.ANALYZE_HTTP_CONTEXT,
            (
                "Investigate the selected read-only remediation option using "
                "safe HTTP execution-context differential analysis."
            ),
            option_number=investigate_option,
        )

    if text in _HTTP_CONTEXT_DEEP_DIVE_PHRASES:
        return ReasoningDecision(
            ReasoningAction.ANALYZE_HTTP_CONTEXT,
            (
                "Compare safe host and application-container HTTP context "
                "metadata for the latest root-cause investigation."
            ),
        )

    apply_request = _extract_apply_option(text)
    if apply_request:
        option_number, replacement_url = apply_request
        return ReasoningDecision(
            ReasoningAction.APPLY_REMEDIATION_OPTION,
            (
                "Prepare the selected bounded remediation option. "
                "Any write still requires Iceman's explicit approval gate."
            ),
            option_number=option_number,
            replacement_url=replacement_url,
        )

    if text in _REMEDIATION_RECOMMEND_PHRASES:
        return ReasoningDecision(
            ReasoningAction.RECOMMEND_REMEDIATION,
            (
                "Use the latest root-cause evidence to recommend bounded "
                "remediation options without making changes."
            ),
        )

    if text in _EXPLAIN_PHRASES:
        return ReasoningDecision(
            ReasoningAction.EXPLAIN,
            "Explain the most recent diagnosis, investigation, or assessment from session context.",
        )

    investigation_target = _extract_investigation_target(text)
    if investigation_target:
        return ReasoningDecision(
            ReasoningAction.INVESTIGATE,
            (
                "Resolve the monitored target configuration and run a bounded "
                "read-only DNS/TCP/TLS/HTTP investigation."
            ),
            target=investigation_target,
        )

    target = _extract_why_target(text)
    if target:
        return ReasoningDecision(
            ReasoningAction.DIAGNOSE,
            f"Run a read-only diagnosis of {target}.",
            target=target,
        )

    if any(phrase in text for phrase in _ASSESS_PHRASES):
        return ReasoningDecision(
            ReasoningAction.ASSESS,
            "Inspect the active project and assess its Docker Compose services using read-only checks.",
        )

    if text in {
        "fix it",
        "can you fix it",
        "remediate it",
        "repair it",
    }:
        if state.latest_problem_context() == "investigation":
            return ReasoningDecision(
                ReasoningAction.RECOMMEND_REMEDIATION,
                (
                    "The latest problem context is a root-cause investigation, "
                    "so recommend application remediation options before any write."
                ),
            )

    # Contextual follow-up: "is it healthy now?" after a diagnosis.
    if text in {"is it healthy now", "is it fixed now", "verify it", "check it again"}:
        if state.last_target:
            return ReasoningDecision(
                ReasoningAction.DIAGNOSE,
                f"Re-check the most recent target: {state.last_target}.",
                target=state.last_target,
            )

    return ReasoningDecision(
        ReasoningAction.FALLBACK,
        "No stateful reasoning workflow matched; fall back to the existing command planner.",
    )


def explain_context(state: SessionState) -> list[str]:
    if state.last_investigation is not None:
        investigation = state.last_investigation
        lines = [
            f"Latest investigation: {investigation.subject}",
            f"Likely cause: {investigation.likely_cause}",
            f"Confidence: {investigation.confidence}",
        ]
        if investigation.url:
            lines.append(f"Target: {investigation.url}")
        for step in investigation.steps:
            status = (
                "passed"
                if step.success is True
                else "failed"
                if step.success is False
                else "unknown"
            )
            lines.append(f"- Host {step.name}: {status} — {step.detail}")

        if investigation.signal_emitter is not None:
            emitter = investigation.signal_emitter
            lines.append(
                "Observed signal source: "
                f"{emitter.compose_service} "
                f"({emitter.matches} recent matching event(s))"
            )

        if investigation.container_probe is not None:
            probe = investigation.container_probe
            for step in probe.steps:
                status = (
                    "passed"
                    if step.success is True
                    else "failed"
                    if step.success is False
                    else "unknown"
                )
                lines.append(
                    f"- Container {step.name}: "
                    f"{status} — {step.detail}"
                )

        if investigation.context_comparison:
            lines.append(
                f"Context comparison: {investigation.context_comparison}"
            )

        if investigation.check_profile is not None:
            profile = investigation.check_profile
            lines.append(
                "Application check implementation: "
                f"{profile.source_file}:{profile.line_number}, "
                f"{profile.client} {profile.method}, "
                f"policy={profile.success_policy}, "
                f"fidelity={profile.fidelity}"
            )

        if investigation.check_replay is not None:
            replay = investigation.check_replay
            replay_summary = (
                "Source-derived replay: "
                f"context={replay.compose_service}"
            )
            if replay.status_code is not None:
                replay_summary += f", HTTP {replay.status_code}"
            if replay.accepted_by_policy is not None:
                replay_summary += (
                    ", policy="
                    + (
                        "PASS"
                        if replay.accepted_by_policy
                        else "FAIL"
                    )
                )
            if replay.error:
                replay_summary += f", error={replay.error}"
            lines.append(replay_summary)

        if investigation.reproduction_conclusion:
            lines.append(
                "Reproduction conclusion: "
                f"{investigation.reproduction_conclusion}"
            )

        for label, stability in (
            ("Host stability", investigation.session_host_stability or investigation.host_stability),
            ("Container stability", investigation.session_container_stability or investigation.container_stability),
            ("Source-derived stability", investigation.session_source_stability or investigation.source_stability),
        ):
            if stability is not None:
                lines.append(
                    f"{label}: {stability.summary}"
                )

        if investigation.request_replay_note:
            lines.append(
                f"Replay limitation: {investigation.request_replay_note}"
            )

        return lines

    diagnosis = state.last_diagnosis
    if diagnosis is not None:
        lines = [
            f"Latest target: {diagnosis.target}",
            f"Container status: {diagnosis.status}",
            f"Exit code: {diagnosis.exit_code}",
        ]

        if diagnosis.application_signals:
            lines.append("Application warnings:")
            for signal in diagnosis.application_signals[:6]:
                lines.append(
                    f"- {signal.subject}: {signal.state} "
                    f"observed {signal.count} time(s)"
                )
            lines.append(
                "Interpretation: the container itself may be healthy while the "
                "application reports monitored targets or dependencies as failing."
            )
            lines.append(
                "The current logs establish the reported DOWN/FAILED state, but "
                "they do not establish the external target's root cause. A target "
                "could be intentionally synthetic, unreachable, misconfigured, or "
                "actually unavailable."
            )
        else:
            severity_order = ("high", "medium", "warning", "info")
            primary = None
            for severity in severity_order:
                primary = next(
                    (
                        finding
                        for finding in diagnosis.findings
                        if finding.severity == severity
                    ),
                    None,
                )
                if primary is not None:
                    break

            if primary is not None:
                lines.append(f"Primary finding: {primary.title}")
                lines.append(primary.detail)
            else:
                lines.append("No failure finding is currently in context.")

        if diagnosis.error:
            lines.append(
                "Docker reported a runtime-level error, so that evidence takes "
                "priority over application exit-code guesses."
            )
        return lines

    assessment = state.last_assessment
    if assessment is not None:
        issue = assessment.top_issue
        if issue is None:
            return [
                f"Latest project assessment: {assessment.workspace}",
                "No high- or medium-severity project issues were found.",
            ]
        return [
            f"Latest project assessment: {assessment.workspace}",
            f"Highest-priority issue: {issue.title}",
            issue.detail,
        ]

    return ["There is no diagnosis or project assessment in session context yet."]
