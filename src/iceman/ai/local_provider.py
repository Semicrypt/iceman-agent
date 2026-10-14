from __future__ import annotations

from iceman.ai.contracts import (
    AIIntent,
    AIReasoningResult,
    TrustedAction,
)
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


_ACTION_MAP = {
    ReasoningAction.PROJECT_REVIEW: TrustedAction.PROJECT_REVIEW,
    ReasoningAction.PROJECT_FOLLOWUP: TrustedAction.PROJECT_FOLLOWUP,
    ReasoningAction.ASSESS: TrustedAction.ASSESS_PROJECT,
    ReasoningAction.INSPECT: TrustedAction.INSPECT_PROJECT,
    ReasoningAction.PRIORITIZE: TrustedAction.PRIORITIZE,
    ReasoningAction.DIAGNOSE: TrustedAction.DIAGNOSE_CONTAINER,
    ReasoningAction.EXPLAIN: TrustedAction.EXPLAIN_CONTEXT,
    ReasoningAction.INVESTIGATE: TrustedAction.INVESTIGATE_APPLICATION_SIGNAL,
    ReasoningAction.RECOMMEND_REMEDIATION: TrustedAction.RECOMMEND_REMEDIATION,
    ReasoningAction.APPLY_REMEDIATION_OPTION: TrustedAction.APPLY_REMEDIATION_OPTION,
    ReasoningAction.ANALYZE_HTTP_CONTEXT: TrustedAction.ANALYZE_HTTP_CONTEXT,
    ReasoningAction.PLAN_WORKSPACE_FILES: TrustedAction.PLAN_WORKSPACE_FILES,
    ReasoningAction.APPLY_WORKSPACE_FILE_PLAN: TrustedAction.APPLY_WORKSPACE_FILE_PLAN,
    ReasoningAction.ROLLBACK_WORKSPACE_FILES: TrustedAction.ROLLBACK_WORKSPACE_FILES,
    ReasoningAction.EXECUTE_DEVOPS_ACTION: TrustedAction.EXECUTE_DEVOPS_ACTION,
    ReasoningAction.EXECUTE_LINUX_ACTION: TrustedAction.EXECUTE_LINUX_ACTION,
    ReasoningAction.EXECUTE_GIT_ACTION: TrustedAction.EXECUTE_GIT_ACTION,
    ReasoningAction.SESSION: TrustedAction.SESSION_STATUS,
    ReasoningAction.FALLBACK: TrustedAction.FALLBACK,
}


class LocalReasoningProvider:
    name = "local"

    def reason(
        self,
        prompt: str,
        state: SessionState,
    ) -> AIReasoningResult:
        decision = reason_request(prompt, state)
        action = _ACTION_MAP[decision.action]

        return AIReasoningResult(
            intent=AIIntent(
                action=action,
                target=decision.target,
                option_number=decision.option_number,
                replacement_url=decision.replacement_url,
                file_template=decision.file_template,
                component=decision.component,
                port=decision.port,
                replicas=decision.replicas,
                image=decision.image,
                startup_target=decision.startup_target,
                devops_action=decision.devops_action,
                context=decision.context,
                terraform_dir=decision.terraform_dir,
                linux_action=decision.linux_action,
                path=decision.path,
                destination=decision.destination,
                pattern=decision.pattern,
                git_action=decision.git_action,
                commit_message=decision.commit_message,
                repo_name=decision.repo_name,
                visibility=decision.visibility,
                tag=decision.tag,
                interpreted_request=decision.interpreted_request,
                requires_clarification=decision.requires_clarification,
                clarification_kind=decision.clarification_kind,
                clarification_original=decision.clarification_original,
                clarification_candidates=list(decision.clarification_candidates),
                clarification_field=decision.clarification_field,
                rationale=decision.explanation,
                confidence="high" if action != TrustedAction.FALLBACK else "low",
            ),
            provider=self.name,
            model=None,
            used_fallback=False,
        )
