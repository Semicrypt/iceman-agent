from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from iceman.assessment import ProjectAssessment
    from iceman.diagnostics import DockerDiagnosis
    from iceman.remediation import RemediationPlan
    from iceman.probing import (
        HttpEvidenceSample,
        RootCauseInvestigation,
    )
    from iceman.application_remediation import (
        ApplicationChangePlan,
        ApplicationRemediationAdvice,
    )
    from iceman.http_context import HttpContextDifferential
    from iceman.workspace_files import (
        WorkspaceFileApplyResult,
        WorkspaceFilePlan,
    )
    from iceman.devops_actions import DevOpsExecutionResult, DevOpsPlan


@dataclass
class SessionEvent:
    kind: str
    summary: str


@dataclass
class SessionState:
    workspace: Path
    last_diagnosis: DockerDiagnosis | None = None
    last_remediation: RemediationPlan | None = None
    last_assessment: ProjectAssessment | None = None
    last_investigation: RootCauseInvestigation | None = None
    last_application_remediation: ApplicationRemediationAdvice | None = None
    last_application_change: ApplicationChangePlan | None = None
    last_http_context_differential: HttpContextDifferential | None = None
    last_workspace_file_plan: WorkspaceFilePlan | None = None
    last_workspace_file_apply: WorkspaceFileApplyResult | None = None
    last_devops_plan: DevOpsPlan | None = None
    last_devops_result: DevOpsExecutionResult | None = None
    last_devops_component: str | None = None
    last_devops_image: str | None = None
    last_target: str | None = None
    investigation_evidence: dict[
        str,
        dict[str, list[HttpEvidenceSample]],
    ] = field(default_factory=dict)
    history: list[SessionEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.workspace = self.workspace.resolve()

    def add_event(self, kind: str, summary: str) -> None:
        self.history.append(SessionEvent(kind=kind, summary=summary))
        if len(self.history) > 30:
            self.history = self.history[-30:]

    def change_workspace(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.last_assessment = None
        self.add_event("workspace", f"Workspace changed to {self.workspace}")

    def remember_diagnosis(self, diagnosis: DockerDiagnosis) -> None:
        self.last_diagnosis = diagnosis
        self.last_target = diagnosis.target
        self.add_event(
            "diagnosis",
            f"{diagnosis.target}: status={diagnosis.status}, exit={diagnosis.exit_code}",
        )

    def remember_remediation(self, plan: RemediationPlan) -> None:
        self.last_remediation = plan
        self.add_event(
            "remediation",
            f"{plan.target}: confidence={plan.confidence}, steps={len(plan.steps)}",
        )

    def remember_assessment(self, assessment: ProjectAssessment) -> None:
        self.last_assessment = assessment
        self.add_event(
            "assessment",
            (
                f"{assessment.workspace}: "
                f"{assessment.high_count} high, "
                f"{assessment.medium_count} medium issue(s)"
            ),
        )

    def remember_investigation(
        self,
        investigation: RootCauseInvestigation,
    ) -> None:
        from iceman.probing import summarize_http_evidence

        key = (
            f"{investigation.workspace.resolve()}|"
            f"{investigation.subject.casefold()}|"
            f"{investigation.url or '-'}"
        )
        bucket = self.investigation_evidence.setdefault(
            key,
            {
                "host": [],
                "container": [],
                "source": [],
            },
        )

        bucket["host"].extend(investigation.host_http_samples)
        bucket["container"].extend(investigation.container_http_samples)
        bucket["source"].extend(investigation.source_http_samples)

        # Keep a bounded history: six 3-sample investigations per context.
        for context in ("host", "container", "source"):
            bucket[context] = bucket[context][-18:]

        if bucket["host"]:
            investigation.session_host_stability = summarize_http_evidence(
                "session-host-neutral",
                bucket["host"],
            )
        if bucket["container"]:
            investigation.session_container_stability = summarize_http_evidence(
                "session-application-container-neutral",
                bucket["container"],
            )
        if bucket["source"]:
            investigation.session_source_stability = summarize_http_evidence(
                "session-source-derived-application-check",
                bucket["source"],
            )

        self.last_investigation = investigation
        self.last_application_remediation = None
        self.last_application_change = None
        self.last_http_context_differential = None

        stability = (
            investigation.session_source_stability.classification
            if investigation.session_source_stability is not None
            else "unknown"
        )
        self.add_event(
            "investigation",
            (
                f"{investigation.subject}: "
                f"{investigation.likely_cause}; "
                f"session_source_stability={stability}"
            ),
        )

    def remember_application_remediation(
        self,
        advice: ApplicationRemediationAdvice,
    ) -> None:
        self.last_application_remediation = advice
        self.add_event(
            "application_remediation",
            (
                f"{advice.subject}: "
                f"{len(advice.options)} option(s)"
            ),
        )

    def remember_http_context_differential(
        self,
        differential: HttpContextDifferential,
    ) -> None:
        self.last_http_context_differential = differential
        self.add_event(
            "http_context_differential",
            (
                f"{differential.subject}: "
                f"confidence={differential.confidence}; "
                f"{differential.conclusion}"
            ),
        )

    def remember_workspace_file_plan(
        self,
        plan: WorkspaceFilePlan,
    ) -> None:
        self.last_workspace_file_plan = plan
        self.add_event(
            "workspace_file_plan",
            (
                f"{plan.request.template.value}: "
                f"{len(plan.operations)} file(s), "
                f"safe_to_apply={plan.safe_to_apply}"
            ),
        )

    def remember_workspace_file_apply(
        self,
        result: WorkspaceFileApplyResult,
    ) -> None:
        self.last_workspace_file_apply = result
        self.add_event(
            "workspace_file_apply",
            (
                f"plan={result.plan_id}, success={result.success}, "
                f"changes={len(result.changes)}"
            ),
        )

    def clear_workspace_file_apply_after_rollback(self) -> None:
        self.add_event(
            "workspace_file_rollback",
            "Last workspace-file write was rolled back.",
        )
        self.last_workspace_file_apply = None

    def remember_devops_plan(
        self,
        plan: DevOpsPlan,
    ) -> None:
        self.last_devops_plan = plan
        self.add_event(
            "devops_plan",
            (
                f"{plan.request.action.value}: steps={len(plan.steps)}, "
                f"approval={plan.requires_approval}"
            ),
        )

    def remember_devops_result(
        self,
        result: DevOpsExecutionResult,
    ) -> None:
        self.last_devops_result = result
        if result.plan.inferred_component:
            self.last_devops_component = result.plan.inferred_component
        elif result.plan.request.component:
            self.last_devops_component = result.plan.request.component
        if result.plan.inferred_image:
            self.last_devops_image = result.plan.inferred_image
        elif result.plan.request.image:
            self.last_devops_image = result.plan.request.image
        self.add_event(
            "devops_action",
            (
                f"{result.plan.request.action.value}: "
                f"success={result.success}, steps={len(result.step_results)}"
            ),
        )

    def remember_application_change(
        self,
        plan: ApplicationChangePlan,
    ) -> None:
        self.last_application_change = plan
        self.add_event(
            "application_change",
            (
                f"{plan.subject}: option={plan.option_number}, "
                f"safe_to_apply={plan.safe_to_apply}"
            ),
        )

    def latest_problem_context(self) -> str | None:
        for event in reversed(self.history):
            if event.kind == "investigation":
                return "investigation"
            if event.kind == "diagnosis":
                return "diagnosis"
        if self.last_investigation is not None:
            return "investigation"
        if self.last_diagnosis is not None:
            return "diagnosis"
        return None

    def effective_compose_workspace(self) -> Path:
        if (
            self.last_diagnosis is not None
            and self.last_diagnosis.compose_working_dir
        ):
            candidate = Path(self.last_diagnosis.compose_working_dir).expanduser()
            if candidate.exists() and candidate.is_dir():
                return candidate.resolve()
        return self.workspace
