from __future__ import annotations

from enum import Enum
from typing import Literal

from iceman.workspace_files import WorkspaceTemplate
from iceman.devops_actions import DevOpsAction
from iceman.linux_actions import LinuxAction
from iceman.git_actions import GitAction

from pydantic import BaseModel, ConfigDict, Field


class TrustedAction(str, Enum):
    PROJECT_REVIEW = "project_review"
    PROJECT_FOLLOWUP = "project_followup"
    ASSESS_PROJECT = "assess_project"
    PRIORITIZE = "prioritize"
    DIAGNOSE_CONTAINER = "diagnose_container"
    EXPLAIN_CONTEXT = "explain_context"
    INVESTIGATE_APPLICATION_SIGNAL = "investigate_application_signal"
    RECOMMEND_REMEDIATION = "recommend_remediation"
    APPLY_REMEDIATION_OPTION = "apply_remediation_option"
    ANALYZE_HTTP_CONTEXT = "analyze_http_context"
    PLAN_WORKSPACE_FILES = "plan_workspace_files"
    APPLY_WORKSPACE_FILE_PLAN = "apply_workspace_file_plan"
    ROLLBACK_WORKSPACE_FILES = "rollback_workspace_files"
    EXECUTE_DEVOPS_ACTION = "execute_devops_action"
    EXECUTE_LINUX_ACTION = "execute_linux_action"
    EXECUTE_GIT_ACTION = "execute_git_action"
    SESSION_STATUS = "session_status"
    REPAIR_LAST_DIAGNOSIS = "repair_last_diagnosis"
    INSPECT_PROJECT = "inspect_project"
    DOCTOR = "doctor"
    FALLBACK = "fallback"


class AIIntent(BaseModel):
    """
    Strict contract returned by an AI reasoning provider.

    Intentionally contains no shell-command field. A provider can select only
    trusted Iceman actions; Iceman owns command construction and execution.
    """

    model_config = ConfigDict(extra="forbid")

    action: TrustedAction
    target: str | None = Field(
        default=None,
        description=(
            "Target name. For diagnose_container this is the container/service. "
            "For investigate_application_signal this is the application warning "
            "subject, such as Openai or Broken Website. Otherwise null."
        ),
    )
    rationale: str = Field(
        min_length=1,
        max_length=600,
        description="Concise reason for choosing the trusted action.",
    )
    option_number: int | None = Field(
        default=None,
        ge=1,
        le=9,
        description=(
            "For apply_remediation_option or analyze_http_context when the "
            "user references a numbered remediation option: that option number. "
            "Otherwise null."
        ),
    )
    replacement_url: str | None = Field(
        default=None,
        max_length=2048,
        description=(
            "For apply_remediation_option only: an explicit HTTP/HTTPS replacement "
            "URL supplied by the user. Never invent one. Otherwise null."
        ),
    )
    file_template: WorkspaceTemplate | None = Field(
        default=None,
        description=(
            "For plan_workspace_files only: choose one trusted deterministic "
            "workspace template. Never provide arbitrary file content."
        ),
    )
    component: str | None = Field(
        default=None,
        max_length=63,
        description=(
            "Optional simple project component/resource name such as backend, "
            "frontend-v2, or api. Never use a path containing '/' or '..'."
        ),
    )
    port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description="Optional application/container port supplied by the user.",
    )
    replicas: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Optional Kubernetes replica count supplied by the user.",
    )
    image: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Optional explicit container image reference supplied by the user. "
            "Never invent credentials or embed secrets."
        ),
    )
    startup_target: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "For docker_guided_repair only: an explicit relative startup entry "
            "file supplied by the user, such as server.js or dist/server.js. "
            "Never invent this value."
        ),
    )

    devops_action: DevOpsAction | None = Field(
        default=None,
        description=(
            "For execute_devops_action only: one trusted Docker, Kubernetes, "
            "Terraform, Git, or GitHub action. Never encode a shell command."
        ),
    )
    context: str | None = Field(
        default=None,
        max_length=256,
        description=(
            "Optional Kubernetes context explicitly requested by the user, "
            "such as minikube. Otherwise null."
        ),
    )
    terraform_dir: str | None = Field(
        default=None,
        max_length=256,
        description=(
            "Optional relative Terraform configuration directory. Never use "
            "absolute paths or path traversal."
        ),
    )

    git_action: GitAction | None = Field(
        default=None,
        description=(
            "For execute_git_action only: one trusted Git/GitHub action. "
            "Never encode a shell command."
        ),
    )
    commit_message: str | None = Field(
        default=None,
        max_length=240,
        description="Optional explicit one-line Git commit message supplied by the user.",
    )
    repo_name: str | None = Field(
        default=None,
        max_length=100,
        description="Optional GitHub repository name explicitly supplied by the user.",
    )
    visibility: Literal["public", "private"] | None = Field(
        default=None,
        description="Optional GitHub repository visibility explicitly supplied by the user.",
    )
    tag: str | None = Field(
        default=None,
        max_length=128,
        description="Optional Git milestone tag such as v0.9.7.",
    )

    linux_action: LinuxAction | None = Field(
        default=None,
        description=(
            "For execute_linux_action only: one trusted Linux filesystem/navigation "
            "or inspection action. Never encode a shell command."
        ),
    )
    path: str | None = Field(
        default=None,
        max_length=512,
        description="Optional user-supplied path for a trusted Linux action.",
    )
    destination: str | None = Field(
        default=None,
        max_length=512,
        description="Optional user-supplied destination path for copy/move.",
    )
    pattern: str | None = Field(
        default=None,
        max_length=256,
        description="Optional user-supplied filename/text search pattern.",
    )
    interpreted_request: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Optional normalized interpretation of the user's request when "
            "Iceman repaired a typo, grammar issue, or conversational reference. "
            "This is display-only and is never executed as shell text."
        ),
    )
    requires_clarification: bool = Field(
        default=False,
        description=(
            "True when Iceman should ask the operator to confirm the normalized "
            "interpretation before dispatching the trusted action."
        ),
    )
    clarification_kind: str | None = Field(
        default=None,
        max_length=80,
        description="Human-readable kind of entity being clarified, such as Docker container.",
    )
    clarification_original: str | None = Field(
        default=None,
        max_length=256,
        description="Original entity token supplied by the user when entity clarification is needed.",
    )
    clarification_candidates: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Bounded real entity names that are plausible matches. Never shell commands.",
    )
    clarification_field: Literal["component", "context", "path", "image"] | None = Field(
        default=None,
        description="Structured intent field to update after the operator selects an entity candidate.",
    )
    confidence: Literal["low", "medium", "high"] = "medium"


class AIReasoningResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: AIIntent
    provider: str
    model: str | None = None
    used_fallback: bool = False
    error: str | None = None
