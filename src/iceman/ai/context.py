from __future__ import annotations

from iceman.session import SessionState


def build_reasoning_context(state: SessionState) -> str:
    parts: list[str] = [
        f"Active workspace: {state.workspace}",
    ]

    if state.last_target:
        parts.append(f"Last target: {state.last_target}")

    if state.last_diagnosis is not None:
        d = state.last_diagnosis
        parts.extend(
            [
                (
                    "Last diagnosis: "
                    f"target={d.target}, status={d.status}, "
                    f"exit_code={d.exit_code}, restarts={d.restart_count or 0}"
                ),
                f"Compose service: {d.compose_service or 'unknown'}",
                f"Docker runtime error present: {bool(d.error)}",
            ]
        )

        high_findings = [
            finding.title
            for finding in d.findings
            if finding.severity == "high"
        ]
        if high_findings:
            parts.append(
                "High-severity findings: " + ", ".join(high_findings[:5])
            )

        if d.application_signals:
            parts.append(
                "Application signals: "
                + "; ".join(
                    f"{signal.subject}={signal.state}x{signal.count}"
                    for signal in d.application_signals[:6]
                )
            )

    if state.last_assessment is not None:
        a = state.last_assessment
        parts.append(
            (
                "Last project assessment: "
                f"{a.high_count} high, {a.medium_count} medium issue(s)"
            )
        )
        if a.top_issue:
            parts.append(f"Top issue: {a.top_issue.title}")

    if state.last_investigation is not None:
        inv = state.last_investigation
        summary = (
            "Last root-cause investigation: "
            f"subject={inv.subject}, cause={inv.likely_cause}, "
            f"confidence={inv.confidence}"
        )
        if inv.signal_emitter is not None:
            summary += (
                f", emitter={inv.signal_emitter.compose_service}"
            )
        if inv.context_comparison:
            summary += (
                f", comparison={inv.context_comparison}"
            )
        if inv.check_profile is not None:
            summary += (
                f", check={inv.check_profile.client} "
                f"{inv.check_profile.method}, "
                f"policy={inv.check_profile.success_policy}, "
                f"fidelity={inv.check_profile.fidelity}"
            )
        if inv.check_replay is not None and inv.check_replay.status_code is not None:
            summary += (
                f", source_replay_http={inv.check_replay.status_code}, "
                f"policy_pass={inv.check_replay.accepted_by_policy}"
            )
        for label, stability in (
            ("host_stability", inv.session_host_stability or inv.host_stability),
            ("container_stability", inv.session_container_stability or inv.container_stability),
            ("source_stability", inv.session_source_stability or inv.source_stability),
        ):
            if stability is not None:
                summary += (
                    f", {label}={stability.classification}"
                    f"[{stability.success_count} success/"
                    f"{stability.failure_count} failure/"
                    f"{stability.unknown_count} unknown]"
                )
        parts.append(summary)

    if state.last_application_remediation is not None:
        advice = state.last_application_remediation
        option_summary = ", ".join(
            (
                f"{option.number}:{option.action}"
                + ("(recommended)" if option.recommended else "")
            )
            for option in advice.options
        )
        parts.append(
            (
                "Last application remediation advice: "
                f"subject={advice.subject}, options=[{option_summary}], "
                f"ranking_basis={advice.ranking_basis}"
            )
        )

    if state.last_http_context_differential is not None:
        differential = state.last_http_context_differential
        parts.append(
            (
                "Last HTTP context differential: "
                f"subject={differential.subject}, "
                f"confidence={differential.confidence}, "
                f"confidence_basis={differential.confidence_basis}, "
                f"application_transport="
                f"{differential.application.selected_remote_address}/"
                f"{differential.application.address_family}/"
                f"{differential.application.tls_version}/"
                f"{differential.application.alpn_protocol}"
                f" via {differential.application.transport_observation_source}"
                f" event={differential.application.transport_event}, "
                f"hypotheses="
                + ",".join(
                    f"{item.status}:{item.hypothesis}"
                    for item in differential.hypotheses
                )
                + f", conclusion={differential.conclusion}"
            )
        )

    if state.last_application_change is not None:
        change = state.last_application_change
        parts.append(
            (
                "Last application change plan: "
                f"subject={change.subject}, option={change.option_number}, "
                f"safe_to_apply={change.safe_to_apply}"
            )
        )

    if state.last_remediation is not None:
        r = state.last_remediation
        parts.append(
            (
                "Last remediation plan: "
                f"target={r.target}, confidence={r.confidence}, "
                f"steps={len(r.steps)}"
            )
        )

    if state.history:
        parts.append("Recent session events:")
        for event in state.history[-6:]:
            parts.append(f"- {event.kind}: {event.summary}")

    if state.last_workspace_file_plan is not None:
        file_plan = state.last_workspace_file_plan
        parts.append(
            (
                "Last workspace file plan: "
                f"template={file_plan.request.template.value}, "
                f"operations={len(file_plan.operations)}, "
                f"safe_to_apply={file_plan.safe_to_apply}"
            )
        )

    if state.last_workspace_file_apply is not None:
        file_apply = state.last_workspace_file_apply
        parts.append(
            (
                "Last workspace file write: "
                f"plan={file_apply.plan_id}, success={file_apply.success}, "
                f"changes={len(file_apply.changes)}"
            )
        )

    if state.last_devops_plan is not None:
        devops_plan = state.last_devops_plan
        parts.append(
            (
                "Last DevOps plan: "
                f"action={devops_plan.request.action.value}, "
                f"component={devops_plan.request.component}, "
                f"image={devops_plan.inferred_image or devops_plan.request.image}, "
                f"steps={len(devops_plan.steps)}, "
                f"approval={devops_plan.requires_approval}"
            )
        )

    if state.last_devops_result is not None:
        devops_result = state.last_devops_result
        parts.append(
            (
                "Last DevOps result: "
                f"action={devops_result.plan.request.action.value}, "
                f"success={devops_result.success}, "
                f"component={state.last_devops_component}, "
                f"image={state.last_devops_image}"
            )
        )

    return "\n".join(parts)
