from __future__ import annotations

from collections import defaultdict
import re
from pathlib import Path

from rich.panel import Panel
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from iceman.console import console
from iceman.assessment import ProjectAssessment
from iceman.ai.contracts import AIReasoningResult
from iceman.ai.provider import ProviderStatus
from iceman.session import SessionState
from iceman.probing import RootCauseInvestigation
from iceman.http_context import HttpContextDifferential, HttpContextSnapshot
from iceman.workspace_files import (
    WorkspaceFileApplyResult,
    WorkspaceFilePlan,
    WorkspaceFileRollbackResult,
    apply_workspace_file_plan,
    rollback_workspace_file_result,
    unified_diff_for_operation,
)
from iceman.devops_actions import (
    DevOpsAction,
    DevOpsExecutionResult,
    DevOpsPlan,
    execute_devops_plan,
)
from iceman.linux_actions import (
    LinuxExecutionResult,
    LinuxPlan,
    execute_linux_plan,
)
from iceman.git_actions import GitExecutionResult, GitPlan, execute_git_plan
from iceman.advisor import AdviceLevel, AdvisorReport
from iceman.diagnostics import DockerDiagnosis
from iceman.doctor import check_tools, system_summary
from iceman.inspector import Inspection
from iceman.remediation import RemediationPlan
from iceman.application_remediation import (
    ApplicationChangePlan,
    ApplicationChangeResult,
    ApplicationRemediationAdvice,
    execute_application_change,
)
from iceman.runner import CommandBlockedError, run_command, run_command_streaming
from iceman.safety import check_command


def print_banner(version: str) -> None:
    console.print(
        Panel.fit(
            f"[bold cyan]ICEMAN[/bold cyan] [dim]v{version}[/dim]\n"
            "[white]Cloud & DevOps CLI Agent[/white]",
            border_style="cyan",
        )
    )


def show_doctor() -> None:
    summary = system_summary()
    console.print("\n[bold]System[/bold]")
    for key, value in summary.items():
        console.print(f"  {key}: {value}")

    table = Table(title="DevOps tools")
    table.add_column("Tool")
    table.add_column("Status")
    table.add_column("Path")

    for tool in check_tools():
        table.add_row(
            tool.name,
            "[green]installed[/green]" if tool.installed else "[yellow]missing[/yellow]",
            tool.path or "-",
        )

    console.print()
    console.print(table)


def show_inspection(inspection: Inspection) -> None:
    console.print(f"[bold]Project:[/bold] {inspection.path}")
    console.print(f"[dim]Scanned {inspection.scanned_files} files (generated/heavy directories ignored).[/dim]")

    if not inspection.detections:
        console.print("\n[yellow]No supported technologies detected yet.[/yellow]")
        return

    grouped = defaultdict(list)
    for detection in inspection.detections:
        grouped[detection.category].append(detection)

    for category in sorted(grouped):
        console.print(f"\n[bold]{category}[/bold]")
        for detection in grouped[category]:
            console.print(f"  • [cyan]{detection.technology}[/cyan]")
            for evidence in detection.evidence[:3]:
                console.print(f"      [dim]↳ {evidence}[/dim]")


_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _terminal_text(value: str) -> str:
    return escape(_ANSI_ESCAPE_RE.sub("", value))


def execute_with_confirmation(
    command: str,
    yes: bool = False,
    cwd: Path | None = None,
) -> int:
    safety = check_command(command)

    console.print("\n[bold]Proposed command[/bold]")
    console.print(Text(f"  {command}", style="cyan"))
    if cwd:
        console.print(f"  [dim]workspace: {cwd}[/dim]")

    if not safety.allowed:
        console.print(f"\n[bold red]BLOCKED:[/bold red] {safety.reason}")
        return 2

    if not yes:
        from typer import confirm
        if not confirm("Execute this command?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return 0

    console.print("[dim]Live execution progress enabled.[/dim]")
    heartbeat_count = 0
    spinners = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def on_output(_stream_name: str, line: str) -> None:
        text = line.rstrip("\r\n")
        if text:
            console.print(_terminal_text(text))

    def on_heartbeat(elapsed: float, silent_for: float) -> None:
        nonlocal heartbeat_count
        glyph = spinners[heartbeat_count % len(spinners)]
        heartbeat_count += 1
        detail = (
            f"no new output for {_format_elapsed(silent_for)}"
            if silent_for >= 1.5
            else "process is still running"
        )
        console.print(
            f"[dim]{glyph} running · elapsed {_format_elapsed(elapsed)} · {detail}[/dim]"
        )

    try:
        result = run_command_streaming(
            command,
            cwd=cwd,
            on_output=on_output,
            on_heartbeat=on_heartbeat,
            heartbeat_interval=2.0,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]Executable not found:[/red] {exc.filename}")
        return 127
    except CommandBlockedError as exc:
        console.print(f"[bold red]BLOCKED:[/bold red] {exc}")
        return 2

    if result.interrupted:
        console.print(
            f"\n[yellow]Command interrupted after {_format_elapsed(result.duration_seconds)}.[/yellow]"
        )
    elif result.returncode == 0:
        console.print(
            f"\n[green]Command completed successfully in "
            f"{_format_elapsed(result.duration_seconds)}.[/green]"
        )
    else:
        console.print(
            f"\n[red]Command exited with code {result.returncode} after "
            f"{_format_elapsed(result.duration_seconds)}.[/red]"
        )

    return result.returncode


def show_docker_diagnosis(d: DockerDiagnosis) -> None:
    console.print()
    console.print(
        Panel.fit(
            f"[bold]Docker Diagnosis[/bold]\nTarget: [cyan]{d.target}[/cyan]",
            border_style="cyan",
        )
    )

    if not d.found:
        for finding in d.findings:
            console.print(f"[bold red]{finding.title}:[/bold red] {finding.detail}")
        return

    summary = Table(show_header=False, box=None)
    summary.add_column("Field", style="bold")
    summary.add_column("Value")

    summary.add_row("Container ID", d.container_id or "-")
    summary.add_row("Image", d.image or "-")
    summary.add_row("Status", d.status or "-")
    summary.add_row("Running", str(d.running))
    summary.add_row("Restart count", str(d.restart_count))
    summary.add_row("Exit code", str(d.exit_code))
    summary.add_row("Health", d.health or "not configured/unknown")
    summary.add_row("Compose service", d.compose_service or "-")
    summary.add_row("Compose workspace", d.compose_working_dir or "-")
    summary.add_row("Logs reliable", str(d.application_logs_reliable))
    console.print(summary)

    if d.ports:
        console.print("\n[bold]Ports[/bold]")
        for port in d.ports:
            console.print(f"  • {port}")

    if d.mounts:
        console.print("\n[bold]Mounts[/bold]")
        for mount in d.mounts[:10]:
            console.print(f"  • {mount}")

    console.print("\n[bold]Findings[/bold]")
    severity_style = {
        "high": "bold red",
        "medium": "yellow",
        "warning": "yellow",
        "info": "cyan",
    }
    for finding in d.findings:
        style = severity_style.get(finding.severity, "white")
        console.print(f"  • [{style}]{finding.title}[/{style}]")
        console.print(f"      {finding.detail}")

    if d.application_signals:
        console.print("\n[bold]Application signals[/bold]")
        signal_table = Table()
        signal_table.add_column("Subject")
        signal_table.add_column("State")
        signal_table.add_column("Occurrences", justify="right")

        for signal in d.application_signals:
            signal_table.add_row(
                signal.subject,
                f"[yellow]{signal.state}[/yellow]",
                str(signal.count),
            )

        console.print(signal_table)
        console.print(
            "[dim]These statuses are reported by the application about monitored "
            "targets/dependencies. They do not automatically mean the Docker "
            "container itself is unhealthy.[/dim]"
        )

    if d.log_excerpt:
        console.print("\n[bold]Recent logs[/bold]")
        console.print(Panel(d.log_excerpt, border_style="dim", expand=False))

    console.print("\n[dim]Evidence collected with read-only Docker commands. No changes were made.[/dim]")



def show_remediation_plan(plan: RemediationPlan) -> None:
    console.print()
    console.print(
        Panel.fit(
            f"[bold]Remediation Plan[/bold]\nTarget: [cyan]{plan.target}[/cyan]",
            border_style="yellow",
        )
    )
    console.print(f"[bold]Cause:[/bold] {plan.cause}")
    console.print(f"[bold]Confidence:[/bold] {plan.confidence}")

    if plan.working_dir:
        console.print(f"[bold]Compose workspace:[/bold] {plan.working_dir}")

    if plan.dependency_diagnosis:
        d = plan.dependency_diagnosis
        console.print(
            f"\n[bold]Dependency evidence[/bold]\n"
            f"  Container: {d.target}\n"
            f"  Status: {d.status}\n"
            f"  Exit code: {d.exit_code}\n"
            f"  Restarts: {d.restart_count}"
        )

    if not plan.steps:
        if plan.note:
            console.print(f"\n[yellow]{plan.note}[/yellow]")
        return

    console.print("\n[bold]Steps[/bold]")
    for index, step in enumerate(plan.steps, start=1):
        risk = "[yellow]WRITE[/yellow]" if step.mutating else "[cyan]READ[/cyan]"
        console.print(f"  {index}. {risk} [bold]{step.title}[/bold]")
        console.print(f"     {step.explanation}")
        if step.command:
            console.print(f"     [dim]$ {step.command}[/dim]")

    if plan.note:
        console.print(f"\n[dim]{plan.note}[/dim]")


def apply_remediation_plan(plan: RemediationPlan, cwd: Path) -> bool:
    if not plan.safe_to_apply or not plan.steps:
        console.print("[yellow]This remediation is not safe to apply automatically.[/yellow]")
        return False

    from typer import confirm

    execution_cwd = plan.working_dir or cwd
    mutating_steps = [step for step in plan.steps if step.mutating]
    console.print(
        f"\n[yellow]This plan contains {len(mutating_steps)} write step(s).[/yellow] "
        "Iceman will stop on the first failed command or failed verification."
    )
    console.print(f"[dim]Execution workspace: {execution_cwd}[/dim]")

    if not confirm("Apply this remediation plan?"):
        console.print("[yellow]Remediation cancelled.[/yellow]")
        return False

    for index, step in enumerate(plan.steps, start=1):
        if not step.command:
            continue

        console.print(f"\n[bold]Step {index}: {step.title}[/bold]")
        result = run_command(step.command, cwd=execution_cwd)

        if result.stdout:
            console.print(result.stdout.rstrip())
        if result.stderr:
            console.print(f"[red]{result.stderr.rstrip()}[/red]")

        if result.returncode != 0:
            console.print(
                f"[bold red]Stopped:[/bold red] command failed with exit code {result.returncode}."
            )
            return False

        if (
            step.expect_output_contains is not None
            and step.expect_output_contains not in result.stdout.split()
        ):
            console.print(
                "[bold red]Stopped:[/bold red] verification did not confirm "
                f"'{step.expect_output_contains}' is running."
            )
            return False

        console.print("[green]✓ completed[/green]")

    console.print("\n[green]Remediation sequence completed.[/green]")
    return True



def show_project_assessment(assessment: ProjectAssessment) -> None:
    border = "green" if assessment.healthy else "yellow"
    status = "HEALTHY" if assessment.healthy else "ATTENTION NEEDED"

    console.print()
    console.print(
        Panel.fit(
            f"[bold]Project Health Assessment[/bold]\n"
            f"{assessment.workspace}\n"
            f"Status: [bold]{status}[/bold]",
            border_style=border,
        )
    )

    console.print(
        f"[bold]Technologies detected:[/bold] "
        f"{len(assessment.inspection.technologies)}"
    )

    if assessment.git_changes:
        console.print(
            f"[bold]Git working tree:[/bold] "
            f"{assessment.git_changes} changed/untracked path(s)"
        )
    else:
        console.print("[bold]Git working tree:[/bold] clean or not available")

    if assessment.compose_detected:
        console.print(
            f"[bold]Compose services:[/bold] "
            f"{len(assessment.compose_services)}"
        )
    else:
        console.print("[bold]Compose:[/bold] not detected")

    console.print(
        f"[bold]Application warnings:[/bold] "
        f"{assessment.application_warning_count}"
    )

    if assessment.services:
        table = Table(title="Service health")
        table.add_column("Service")
        table.add_column("Container")
        table.add_column("Status")
        table.add_column("Assessment")

        for item in assessment.services:
            style = {
                "high": "red",
                "medium": "yellow",
                "info": "green",
            }.get(item.severity, "white")
            table.add_row(
                item.service,
                item.container or "-",
                f"[{style}]{item.severity.upper()}[/{style}]",
                item.summary,
            )

        console.print()
        console.print(table)

    if assessment.application_warning_count:
        console.print(
            "\n[yellow]Runtime/container health is healthy, but application logs "
            "contain monitored-target/dependency warnings. These are reported "
            "separately and do not automatically make the deployment unhealthy."
            "[/yellow]"
        )

    if assessment.issues:
        console.print("\n[bold]Issues[/bold]")
        severity_style = {
            "high": "bold red",
            "medium": "yellow",
            "info": "cyan",
        }
        for issue in assessment.issues:
            style = severity_style.get(issue.severity, "white")
            console.print(
                f"  • [{style}]{issue.title}[/{style}]"
            )
            console.print(f"      {issue.detail}")
    else:
        console.print(
            "\n[green]No high- or medium-severity issues were found "
            "by the current project health checks.[/green]"
        )

    if assessment.top_issue:
        console.print(
            f"\n[bold]Fix first:[/bold] {assessment.top_issue.title}"
        )


def show_priority(assessment: ProjectAssessment) -> None:
    console.print()
    issue = assessment.top_issue

    if issue is None:
        console.print(
            Panel.fit(
                "[green]No high- or medium-severity issue is currently "
                "known for this project.[/green]",
                title="Priority",
                border_style="green",
            )
        )
        return

    console.print(
        Panel.fit(
            f"[bold]{issue.title}[/bold]\n{issue.detail}",
            title="Fix First",
            border_style="yellow",
        )
    )
    if issue.target:
        console.print(
            f"[dim]Suggested next step: diagnose {issue.target}[/dim]"
        )


def show_reasoning_explanation(lines: list[str]) -> None:
    console.print()
    console.print(
        Panel(
            "\n".join(lines),
            title="Reasoning Context",
            border_style="cyan",
            expand=False,
        )
    )


def show_session_state(state: SessionState) -> None:
    console.print()
    table = Table(title="Iceman Session")
    table.add_column("Context")
    table.add_column("Value")

    table.add_row("Workspace", str(state.workspace))
    table.add_row("Last target", state.last_target or "-")
    table.add_row(
        "Last diagnosis",
        (
            f"{state.last_diagnosis.target}: {state.last_diagnosis.status}"
            if state.last_diagnosis
            else "-"
        ),
    )
    table.add_row(
        "Last assessment",
        (
            f"{state.last_assessment.high_count} high / "
            f"{state.last_assessment.medium_count} medium"
            if state.last_assessment
            else "-"
        ),
    )
    table.add_row(
        "Last investigation",
        (
            f"{state.last_investigation.subject}: "
            f"{state.last_investigation.confidence}"
            if state.last_investigation
            else "-"
        ),
    )
    table.add_row(
        "App remediation advice",
        (
            f"{state.last_application_remediation.subject}: "
            f"{len(state.last_application_remediation.options)} options"
            if state.last_application_remediation
            else "-"
        ),
    )
    table.add_row(
        "App change plan",
        (
            f"{state.last_application_change.subject}: "
            f"option {state.last_application_change.option_number}"
            if state.last_application_change
            else "-"
        ),
    )
    table.add_row(
        "HTTP context differential",
        (
            f"{state.last_http_context_differential.subject}: "
            f"{state.last_http_context_differential.confidence}"
            if state.last_http_context_differential
            else "-"
        ),
    )
    table.add_row(
        "Workspace file plan",
        (
            f"{state.last_workspace_file_plan.request.template.value}: "
            f"{len(state.last_workspace_file_plan.operations)} file(s)"
            if state.last_workspace_file_plan
            else "-"
        ),
    )
    table.add_row(
        "Last file write",
        (
            f"{len(state.last_workspace_file_apply.changes)} file(s), "
            f"success={state.last_workspace_file_apply.success}"
            if state.last_workspace_file_apply
            else "-"
        ),
    )
    table.add_row(
        "Last DevOps action",
        (
            f"{state.last_devops_result.plan.request.action.value}: "
            f"success={state.last_devops_result.success}"
            if state.last_devops_result
            else "-"
        ),
    )
    table.add_row(
        "DevOps component/image",
        (
            f"{state.last_devops_component or '-'} / "
            f"{state.last_devops_image or '-'}"
            if state.last_devops_component or state.last_devops_image
            else "-"
        ),
    )
    table.add_row(
        "Last remediation",
        (
            f"{state.last_remediation.target}: "
            f"{len(state.last_remediation.steps)} steps"
            if state.last_remediation
            else "-"
        ),
    )
    console.print(table)

    if state.history:
        console.print("\n[bold]Recent context[/bold]")
        for event in state.history[-8:]:
            console.print(f"  • [cyan]{event.kind}[/cyan]: {event.summary}")



def show_ai_provider_status(status: ProviderStatus) -> None:
    console.print()
    table = Table(title="Iceman AI Provider")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_row("Requested", status.requested)
    table.add_row("Selected", status.selected)
    table.add_row(
        "OPENAI_API_KEY",
        "configured" if status.openai_key_present else "not configured",
    )
    table.add_row("OpenAI model", status.model)
    console.print(table)
    console.print(f"[dim]{status.note}[/dim]")
    console.print(
        "[dim]API keys are read from environment variables and are not "
        "stored by Iceman.[/dim]"
    )


def show_ai_route(result: AIReasoningResult) -> None:
    console.print("[bold cyan]Advanced Intelligence[/bold cyan]")
    route_target = result.intent.target
    if route_target is None and result.intent.devops_action is not None:
        route_target = result.intent.devops_action.value
    if route_target is None and getattr(result.intent, "git_action", None) is not None:
        route_target = result.intent.git_action.value
    console.print(
        f"[bold]Intent:[/bold] {result.intent.action.value}"
        + (
            f" → {route_target}"
            if route_target
            else ""
        )
    )
    console.print(
        f"[bold]Reasoning:[/bold] {result.intent.rationale}"
    )

    if result.used_fallback:
        console.print(
            "[yellow]Advanced Intelligence is currently using Iceman's "
            "local reasoning fallback.[/yellow]"
        )



def show_root_cause_investigation(
    investigation: RootCauseInvestigation,
) -> None:
    console.print()
    console.print(
        Panel.fit(
            f"[bold]Root-Cause Investigation[/bold]\n"
            f"Subject: {investigation.subject}",
            border_style="cyan",
        )
    )

    resolution = investigation.resolution

    if resolution.selected is not None:
        console.print(
            f"[bold]Configured target:[/bold] {resolution.selected.url}"
        )
        source_location = resolution.selected.source_file
        if resolution.selected.line_number is not None:
            source_location += f":{resolution.selected.line_number}"
        console.print(
            f"[bold]Source:[/bold] {source_location}"
        )
        console.print(
            f"[bold]Source type:[/bold] {resolution.selected.source_kind}"
        )
        console.print(
            f"[bold]Resolution confidence:[/bold] {resolution.confidence}"
        )
    else:
        console.print(
            f"[yellow]{resolution.note or 'Target could not be resolved.'}[/yellow]"
        )
        if resolution.candidates:
            console.print("\n[bold]Possible targets[/bold]")
            for candidate in resolution.candidates:
                source_location = candidate.source_file
                if candidate.line_number is not None:
                    source_location += f":{candidate.line_number}"
                console.print(
                    f"  • {candidate.url} ({source_location})"
                )

    if investigation.blocked:
        console.print(
            f"\n[bold yellow]Probe blocked:[/bold yellow] "
            f"{investigation.likely_cause}"
        )
        return

    if investigation.steps:
        table = Table(title="Host probe results")
        table.add_column("Layer")
        table.add_column("Result")
        table.add_column("Latency")
        table.add_column("Evidence")

        for step in investigation.steps:
            if step.success is True:
                result = "[green]PASS[/green]"
            elif step.success is False:
                result = "[red]FAIL[/red]"
            else:
                result = "[yellow]UNKNOWN[/yellow]"

            latency = (
                f"{step.latency_ms} ms"
                if step.latency_ms is not None
                else "-"
            )
            table.add_row(
                step.name,
                result,
                latency,
                step.detail,
            )

        console.print()
        console.print(table)

    if investigation.signal_emitter is not None:
        emitter = investigation.signal_emitter
        console.print(
            "\n[bold]Observed signal source:[/bold] "
            f"{emitter.compose_service} "
            f"({emitter.container_id[:12]}) — "
            f"{emitter.matches} matching recent log event(s)"
        )

    if investigation.container_probe is not None:
        container_probe = investigation.container_probe
        title = "Application-container probe"
        if container_probe.compose_service:
            title += f" — {container_probe.compose_service}"

        if container_probe.steps:
            table = Table(title=title)
            table.add_column("Layer")
            table.add_column("Result")
            table.add_column("Latency")
            table.add_column("Evidence")

            for step in container_probe.steps:
                if step.success is True:
                    result = "[green]PASS[/green]"
                elif step.success is False:
                    result = "[red]FAIL[/red]"
                else:
                    result = "[yellow]UNKNOWN[/yellow]"

                latency = (
                    f"{step.latency_ms} ms"
                    if step.latency_ms is not None
                    else "-"
                )
                table.add_row(
                    step.name,
                    result,
                    latency,
                    step.detail,
                )

            console.print()
            console.print(table)

        if container_probe.runtime:
            console.print(
                f"[bold]Container probe runtime:[/bold] "
                f"{container_probe.runtime}"
            )
        if container_probe.note:
            console.print(
                f"[yellow]Container probe note:[/yellow] "
                f"{container_probe.note}"
            )

    if investigation.context_comparison:
        console.print(
            f"\n[bold]Context comparison:[/bold] "
            f"{investigation.context_comparison}"
        )

    if investigation.check_profile is not None:
        profile = investigation.check_profile
        console.print("\n[bold]Application check implementation[/bold]")
        console.print(
            f"  Source: {profile.source_file}:{profile.line_number}"
        )
        console.print(
            f"  Client: {profile.client}"
        )
        console.print(
            f"  Method: {profile.method}"
        )
        console.print(
            "  Timeout: "
            + (
                f"{profile.timeout_ms} ms"
                if profile.timeout_ms is not None
                else "not statically determined"
            )
        )
        console.print(
            f"  Success policy: {profile.success_policy}"
        )
        console.print(
            "  Redirects: "
            + (
                "follow"
                if profile.follow_redirects is True
                else "do not follow"
                if profile.follow_redirects is False
                else "not statically determined"
            )
        )
        console.print(
            f"  Static-analysis confidence: {profile.confidence}"
        )
        console.print(
            f"  Reproduction fidelity: {profile.fidelity}"
        )
        if profile.headers:
            console.print(
                "  Static headers: "
                + ", ".join(sorted(profile.headers))
            )
        if profile.omitted_header_names:
            console.print(
                "[yellow]  Omitted dynamic/sensitive headers: "
                + ", ".join(profile.omitted_header_names)
                + "[/yellow]"
            )
        if profile.note:
            console.print(
                f"[dim]  {profile.note}[/dim]"
            )

    if investigation.check_replay is not None:
        replay = investigation.check_replay
        console.print(
            "\n[bold]Source-derived application check replay[/bold]"
        )
        console.print(
            f"  Context: {replay.compose_service} "
            f"({replay.container_id[:12]})"
        )
        console.print(
            f"  Client: {replay.client}"
        )
        if replay.runtime:
            console.print(
                f"  Runtime: {replay.runtime}"
            )
        if replay.status_code is not None:
            console.print(
                f"  HTTP status: {replay.status_code}"
            )
        if replay.latency_ms is not None:
            console.print(
                f"  Latency: {replay.latency_ms} ms"
            )
        if replay.accepted_by_policy is not None:
            console.print(
                "  Application policy result: "
                + (
                    "[green]PASS[/green]"
                    if replay.accepted_by_policy
                    else "[red]FAIL[/red]"
                )
            )
        if replay.error:
            console.print(
                f"[yellow]  Replay error: {replay.error}[/yellow]"
            )
        if replay.note:
            console.print(
                f"[dim]  {replay.note}[/dim]"
            )

    if investigation.reproduction_conclusion:
        console.print(
            f"\n[bold]Check reproduction conclusion:[/bold] "
            f"{investigation.reproduction_conclusion}"
        )

    current_stabilities = [
        investigation.host_stability,
        investigation.container_stability,
        investigation.source_stability,
    ]
    current_stabilities = [
        item for item in current_stabilities
        if item is not None
    ]
    if current_stabilities:
        table = Table(title="Current-run evidence stability")
        table.add_column("Context")
        table.add_column("Samples")
        table.add_column("Classification")
        for stability in current_stabilities:
            statuses = ", ".join(
                str(sample.status_code)
                if sample.status_code is not None
                else "no-status"
                for sample in stability.samples
            )
            classification = stability.classification.replace("_", " ").upper()
            table.add_row(
                stability.context,
                statuses,
                classification,
            )
        console.print()
        console.print(table)

    session_stabilities = [
        investigation.session_host_stability,
        investigation.session_container_stability,
        investigation.session_source_stability,
    ]
    session_stabilities = [
        item for item in session_stabilities
        if item is not None and len(item.samples) > 3
    ]
    if session_stabilities:
        table = Table(title="Interactive-session evidence")
        table.add_column("Context")
        table.add_column("Samples retained")
        table.add_column("Classification")
        table.add_column("Status counts")
        for stability in session_stabilities:
            counts = ", ".join(
                f"{status}×{count}"
                for status, count in sorted(stability.status_counts.items())
            ) or "-"
            table.add_row(
                stability.context,
                str(len(stability.samples)),
                stability.classification.replace("_", " ").upper(),
                counts,
            )
        console.print()
        console.print(table)

    if investigation.request_replay_note:
        console.print(
            f"[dim]Request replay: "
            f"{investigation.request_replay_note}[/dim]"
        )

    console.print(
        f"\n[bold]Likely cause:[/bold] {investigation.likely_cause}"
    )
    console.print(
        f"[bold]Confidence:[/bold] {investigation.confidence}"
    )
    console.print(
        "[dim]This workflow is read-only. It performs bounded network "
        "reachability/status probes and makes no system changes.[/dim]"
    )



def show_application_remediation_advice(
    advice: ApplicationRemediationAdvice,
) -> None:
    console.print()
    console.print(
        Panel.fit(
            f"[bold]Remediation Intelligence[/bold]\n"
            f"Subject: [cyan]{advice.subject}[/cyan]",
            border_style="yellow",
        )
    )
    console.print(f"[bold]Established cause:[/bold] {advice.cause}")
    console.print(f"[bold]Confidence:[/bold] {advice.confidence}")
    if advice.ranking_basis:
        console.print(
            f"[bold]Ranking basis:[/bold] {advice.ranking_basis}"
        )

    console.print("\n[bold]Options[/bold]")
    for option in advice.options:
        recommended = (
            " [green](recommended)[/green]"
            if option.recommended
            else ""
        )
        auto = (
            "[green]bounded write available[/green]"
            if option.automatically_applicable
            else "[yellow]manual/semantic decision[/yellow]"
        )
        console.print(
            f"  [bold]{option.number}. {option.title}[/bold]"
            f"{recommended}"
        )
        console.print(
            f"     Risk: {option.risk} · {auto}"
        )
        console.print(
            f"     [dim]Action type: {option.action}[/dim]"
        )
        console.print(f"     {option.rationale}")
        if option.requires:
            console.print(
                f"     [dim]Requires: {option.requires}[/dim]"
            )
        if option.note:
            console.print(
                f"     [dim]{option.note}[/dim]"
            )

    if advice.note:
        console.print(f"\n[dim]{advice.note}[/dim]")


def show_application_change_plan(
    plan: ApplicationChangePlan,
) -> None:
    console.print()
    console.print(
        Panel.fit(
            f"[bold]Application Change Plan[/bold]\n"
            f"Subject: [cyan]{plan.subject}[/cyan]\n"
            f"Option: {plan.option_number} — {plan.title}",
            border_style="yellow",
        )
    )

    console.print(f"[bold]Cause:[/bold] {plan.cause}")

    if plan.old_url:
        console.print(
            f"[bold]Current target:[/bold] {plan.old_url}"
        )
    if plan.new_url:
        console.print(
            f"[bold]Proposed target:[/bold] {plan.new_url}"
        )

    if plan.preflight_stability is not None:
        statuses = ", ".join(
            str(sample.status_code)
            if sample.status_code is not None
            else "no-status"
            for sample in plan.preflight_stability.samples
        )
        console.print(
            f"[bold]Application-container preflight:[/bold] "
            f"{plan.preflight_stability.classification.replace('_', ' ').upper()} "
            f"· samples [{statuses}]"
        )
        if plan.preflight is not None and plan.preflight.runtime:
            console.print(
                f"[dim]Replay runtime: {plan.preflight.runtime}[/dim]"
            )
    elif plan.preflight is not None:
        preflight = plan.preflight
        status = (
            f"HTTP {preflight.status_code}"
            if preflight.status_code is not None
            else (preflight.error or "no HTTP result")
        )
        console.print(
            f"[bold]Application-container preflight:[/bold] {status}"
        )

    if plan.locator is not None:
        locator = plan.locator
        console.print(
            "[bold]Database location:[/bold] "
            f"{locator.table}.{locator.url_column} "
            f"(matched by {locator.table}.{locator.name_column})"
        )

    console.print(
        f"[bold]Automatic apply:[/bold] "
        + (
            "[green]ready[/green]"
            if plan.safe_to_apply
            else "[yellow]not ready[/yellow]"
        )
    )

    if plan.safe_to_apply:
        console.print(
            "\n[yellow]WRITE preview[/yellow]\n"
            "  • Update exactly one PostgreSQL row from the current URL to the "
            "proposed URL.\n"
            "  • Abort inside PostgreSQL if the old-value precondition does not "
            "match exactly one row.\n"
            "  • Read back and verify exactly one row after the write.\n"
            "  • Keep a bounded inverse command available if immediate read-back "
            "verification fails."
        )

    if plan.note:
        console.print(f"\n[dim]{plan.note}[/dim]")


def apply_application_change_plan(
    plan: ApplicationChangePlan,
) -> ApplicationChangeResult:
    if not plan.safe_to_apply:
        console.print(
            "[yellow]This application change is not ready for automatic apply. "
            "No changes were made.[/yellow]"
        )
        return ApplicationChangeResult(
            success=False,
            mutation_applied=False,
            verification_passed=False,
            message="Plan not ready for automatic apply.",
        )

    from typer import confirm

    console.print(
        "\n[yellow]This will write one database-backed monitor target.[/yellow]"
    )
    console.print(
        f"[dim]{plan.old_url}  →  {plan.new_url}[/dim]"
    )

    if not confirm("Apply this application remediation?"):
        console.print("[yellow]Application remediation cancelled.[/yellow]")
        return ApplicationChangeResult(
            success=False,
            mutation_applied=False,
            verification_passed=False,
            message="Cancelled by user.",
        )

    result = execute_application_change(plan)

    if result.success:
        console.print(
            f"[green]✓ {result.message}[/green]"
        )
    else:
        console.print(
            f"[bold red]Application remediation stopped:[/bold red] "
            f"{result.message}"
        )

    return result



def _show_http_context_snapshot(
    title: str,
    snapshot: HttpContextSnapshot,
) -> None:
    console.print(f"\n[bold]{title}[/bold]")

    summary = Table(show_header=False, box=None)
    summary.add_column("Field", style="bold")
    summary.add_column("Value")

    summary.add_row("Client", snapshot.client or "-")
    summary.add_row("Runtime", snapshot.runtime or "-")
    summary.add_row("Method", snapshot.method or "-")
    summary.add_row(
        "Bounded statuses",
        ", ".join(
            str(status) if status is not None else "no-status"
            for status in snapshot.sample_statuses
        ) or "-",
    )
    summary.add_row(
        "Representative status",
        str(snapshot.status_code)
        if snapshot.status_code is not None
        else "-",
    )
    summary.add_row("Final URL", snapshot.final_url or "-")
    summary.add_row(
        "Redirects",
        ", ".join(snapshot.redirect_chain) or "none observed",
    )
    summary.add_row(
        "Resolved addresses",
        ", ".join(snapshot.resolved_addresses) or "-",
    )
    summary.add_row(
        "Selected remote address",
        snapshot.selected_remote_address or "-",
    )
    summary.add_row("Address family", snapshot.address_family or "-")
    summary.add_row("TLS", snapshot.tls_version or "-")
    summary.add_row("ALPN", snapshot.alpn_protocol or "-")
    summary.add_row(
        "Transport observed via",
        snapshot.transport_observation_source or "-",
    )
    summary.add_row(
        "Transport event",
        snapshot.transport_event or "-",
    )
    summary.add_row(
        "Proxy env present",
        ", ".join(snapshot.proxy_env_present) or "none",
    )
    if snapshot.dns_servers:
        summary.add_row(
            "Container DNS servers",
            ", ".join(snapshot.dns_servers),
        )
    if snapshot.container_networks:
        summary.add_row(
            "Docker networks",
            ", ".join(snapshot.container_networks),
        )
    if snapshot.latency_ms is not None:
        summary.add_row("Representative latency", f"{snapshot.latency_ms} ms")

    console.print(summary)

    if snapshot.request_headers:
        console.print("  [bold]Safe request headers[/bold]")
        for key, value in sorted(snapshot.request_headers.items()):
            console.print(f"    {key}: {value}")

    if snapshot.response_headers:
        console.print("  [bold]Safe response headers[/bold]")
        for key, value in sorted(snapshot.response_headers.items()):
            console.print(f"    {key}: {value}")

    if snapshot.omitted_header_names:
        console.print(
            "  [dim]Omitted dynamic/sensitive source headers: "
            + ", ".join(snapshot.omitted_header_names)
            + "[/dim]"
        )

    if snapshot.note:
        console.print(f"  [dim]{snapshot.note}[/dim]")
    if snapshot.error:
        console.print(f"  [yellow]Probe note: {snapshot.error}[/yellow]")


def show_http_context_differential(
    differential: HttpContextDifferential,
) -> None:
    console.print()
    console.print(
        Panel.fit(
            "[bold]HTTP Context Differential Analysis[/bold]\n"
            f"Subject: [cyan]{differential.subject}[/cyan]\n"
            f"Target: {differential.target_url}",
            border_style="cyan",
        )
    )

    _show_http_context_snapshot(
        "Host request context",
        differential.host,
    )
    _show_http_context_snapshot(
        "Application-container request context",
        differential.application,
    )

    console.print("\n[bold]Meaningful differences / controls[/bold]")
    if not differential.findings:
        console.print(
            "  [dim]No supported non-sensitive differential was established.[/dim]"
        )
    for finding in differential.findings:
        console.print(
            f"  • [cyan]{finding.category}[/cyan]: "
            f"{finding.observation}"
        )
        console.print(
            f"      [dim]{finding.interpretation}[/dim]"
        )

    if differential.hypotheses:
        console.print("\n[bold]Differential hypothesis assessment[/bold]")
        groups = (
            (
                "Ruled down / unsupported",
                {"ruled_down", "unsupported"},
            ),
            (
                "Still plausible",
                {"plausible"},
            ),
            (
                "Not established",
                {"not_established"},
            ),
        )
        for title, statuses in groups:
            matching = [
                item
                for item in differential.hypotheses
                if item.status in statuses
            ]
            if not matching:
                continue
            console.print(f"  [bold]{title}[/bold]")
            for item in matching:
                console.print(
                    f"    • {item.hypothesis}"
                )
                console.print(
                    f"      [dim]{item.evidence}[/dim]"
                )

    console.print(
        f"\n[bold]Conclusion:[/bold] {differential.conclusion}"
    )
    console.print(
        f"[bold]Confidence:[/bold] {differential.confidence}"
    )
    if differential.confidence_basis:
        console.print(
            f"[bold]Confidence basis:[/bold] "
            f"{differential.confidence_basis}"
        )
    console.print(
        f"[bold]Recommended next action:[/bold] "
        f"{differential.recommended_next_action}"
    )
    console.print(
        f"\n[dim]{differential.safety_note}[/dim]"
    )



def show_workspace_file_plan(plan: WorkspaceFilePlan) -> None:
    border = "green" if plan.safe_to_apply else "yellow"
    console.print()
    console.print(
        Panel.fit(
            "[bold]Workspace File Plan[/bold]\n"
            f"Template: [cyan]{plan.request.template.value}[/cyan]\n"
            f"Workspace: {plan.workspace}\n"
            f"Plan ID: {plan.plan_id}",
            border_style=border,
        )
    )

    if plan.notes:
        console.print("\n[bold]Planning notes[/bold]")
        for note in plan.notes:
            console.print(f"  • {note}")

    if not plan.operations:
        console.print(
            "\n[yellow]No file operations were produced.[/yellow]"
        )
        return

    table = Table(title="Planned file operations")
    table.add_column("Action")
    table.add_column("Path")
    table.add_column("Purpose")
    table.add_column("Validation")
    for op in plan.operations:
        action_style = {
            "CREATE": "green",
            "UPDATE": "yellow",
            "UNCHANGED": "dim",
        }.get(op.change_type, "white")
        valid = (
            "[green]PASS[/green]"
            if op.validation.valid
            else "[red]FAIL[/red]"
        )
        table.add_row(
            f"[{action_style}]{op.change_type}[/{action_style}]",
            op.relative_path,
            op.purpose,
            valid,
        )
    console.print(table)

    for op in plan.operations:
        console.print(
            f"\n[bold]{op.change_type}: {op.relative_path}[/bold]"
        )
        console.print(
            f"[dim]{op.validation.message}[/dim]"
        )
        for warning in op.validation.warnings:
            console.print(f"[yellow]Warning: {warning}[/yellow]")

        diff = unified_diff_for_operation(op)
        if diff:
            console.print(
                Panel(
                    Text(diff.rstrip()),
                    title=f"Preview diff — {op.relative_path}",
                    border_style="dim",
                    expand=False,
                )
            )
        else:
            console.print("[dim]No content change.[/dim]")

    if plan.safe_to_apply:
        console.print(
            "\n[green]Static validation passed.[/green] "
            "No files have been written yet."
        )
    else:
        console.print(
            "\n[yellow]This plan is not safe to apply automatically.[/yellow]"
        )


def apply_workspace_file_plan_with_confirmation(
    plan: WorkspaceFilePlan,
) -> WorkspaceFileApplyResult:
    from typer import confirm

    if not plan.safe_to_apply:
        result = WorkspaceFileApplyResult(
            success=False,
            plan_id=plan.plan_id,
            workspace=plan.workspace,
            error="Plan is not safe to apply.",
        )
        console.print(
            "[yellow]File plan cannot be applied because validation/planning "
            "did not complete safely.[/yellow]"
        )
        return result

    changed = plan.changed_operations
    if not changed:
        result = WorkspaceFileApplyResult(
            success=True,
            plan_id=plan.plan_id,
            workspace=plan.workspace,
            changes=[],
        )
        console.print("[green]Files already match the generated plan.[/green]")
        return result

    console.print(
        f"\n[yellow]This plan will write {len(changed)} file(s) inside "
        f"{plan.workspace}.[/yellow]"
    )
    console.print(
        "[dim]Existing files are backed up under ~/.cache/iceman before "
        "replacement. Writes are atomic and verified after writing.[/dim]"
    )

    if not confirm("Apply this workspace file plan?"):
        console.print("[yellow]File write cancelled.[/yellow]")
        return WorkspaceFileApplyResult(
            success=False,
            plan_id=plan.plan_id,
            workspace=plan.workspace,
            error="Cancelled by operator.",
        )

    result = apply_workspace_file_plan(plan)
    show_workspace_file_apply_result(result)
    return result


def show_workspace_file_apply_result(
    result: WorkspaceFileApplyResult,
) -> None:
    if not result.success:
        console.print(
            f"\n[bold red]Workspace file write failed.[/bold red] "
            f"{result.error or ''}"
        )
        if result.rolled_back:
            console.print(
                "[green]Changes already written during this attempt were "
                "rolled back.[/green]"
            )
        return

    if not result.changes:
        console.print(
            "\n[green]No write was necessary; generated files already "
            "matched the workspace.[/green]"
        )
        return

    console.print("\n[green]Workspace files written and verified.[/green]")
    for change in result.changes:
        action = "updated" if change.existed_before else "created"
        console.print(
            f"  ✓ {change.relative_path} ({action})"
        )
    console.print(
        "[dim]Use 'rollback file changes' in this Iceman session to restore "
        "the previous state.[/dim]"
    )


def show_workspace_file_rollback_result(
    result: WorkspaceFileRollbackResult,
) -> None:
    if not result.success:
        console.print(
            f"[bold red]Rollback failed:[/bold red] {result.error or '-'}"
        )
        return

    console.print("[green]Workspace file rollback completed.[/green]")
    for path in result.restored:
        console.print(f"  ↶ restored {path}")
    for path in result.removed:
        console.print(f"  ↶ removed newly-created {path}")
    if not result.restored and not result.removed:
        console.print("[dim]There were no file changes to roll back.[/dim]")



def show_devops_plan(plan: DevOpsPlan) -> None:
    style = "green" if plan.safe_to_execute else "yellow"
    console.print()
    console.print(
        Panel.fit(
            "[bold]Natural-Language DevOps Plan[/bold]\n"
            f"Action: [cyan]{plan.request.action.value}[/cyan]\n"
            f"Workspace: {plan.workspace}",
            border_style=style,
        )
    )

    if plan.notes:
        console.print("\n[bold]Notes[/bold]")
        for note in plan.notes:
            console.print(f"  • {_terminal_text(note)}")

    if not plan.safe_to_execute:
        console.print("\n[yellow]Iceman cannot safely execute this request yet.[/yellow]")
        return

    table = Table(title="Trusted execution steps")
    table.add_column("#", justify="right")
    table.add_column("Type")
    table.add_column("Step")
    table.add_column("Command")
    for index, step in enumerate(plan.steps, 1):
        kind = "CHANGE" if step.changes_state else "READ"
        kind_text = "[yellow]CHANGE[/yellow]" if step.changes_state else "[cyan]READ[/cyan]"
        table.add_row(
            str(index),
            kind_text,
            step.label,
            _terminal_text(step.command),
        )
    console.print(table)

    if plan.requires_approval:
        console.print(
            "[yellow]This workflow contains state-changing steps. Review the "
            "exact trusted command(s) above before approving execution.[/yellow]"
        )
    else:
        console.print(
            "[dim]This workflow is read-only. Review the exact trusted command(s) "
            "above before approving execution.[/dim]"
        )


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _streaming_step_runner(workspace: Path):
    spinners = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def runner(step, index: int, total: int):
        start_percent = int(((index - 1) / max(total, 1)) * 100)
        console.print(
            f"\n[bold cyan][{index}/{total} · {start_percent}%][/bold cyan] "
            f"[bold]▶ {_terminal_text(step.label)}[/bold]"
        )
        heartbeat_count = 0

        def on_output(stream_name: str, line: str) -> None:
            if not getattr(step, "stream_output", True):
                return
            text = line.rstrip("\r\n")
            if not text:
                return
            # Many DevOps tools intentionally send normal progress to stderr
            # (for example some Docker/BuildKit paths), so do not paint live
            # stderr red unless the command ultimately fails.
            console.print(_terminal_text(text))

        def on_heartbeat(elapsed: float, silent_for: float) -> None:
            nonlocal heartbeat_count
            glyph = spinners[heartbeat_count % len(spinners)]
            heartbeat_count += 1
            if silent_for >= 1.5:
                detail = f"no new output for {_format_elapsed(silent_for)}"
            else:
                detail = "process is still running"
            console.print(
                f"[dim]{glyph} {_terminal_text(step.label)} · elapsed {_format_elapsed(elapsed)} "
                f"· {detail}[/dim]"
            )

        result = run_command_streaming(
            step.command,
            cwd=workspace,
            on_output=on_output,
            on_heartbeat=on_heartbeat,
            heartbeat_interval=2.0,
        )
        finish_percent = int((index / max(total, 1)) * 100)
        if result.interrupted:
            console.print(
                f"[yellow]■ [{index}/{total}] Interrupted after "
                f"{_format_elapsed(result.duration_seconds)}[/yellow]"
            )
        elif result.returncode == 0:
            console.print(
                f"[green]✓ [{index}/{total} · {finish_percent}%] completed in "
                f"{_format_elapsed(result.duration_seconds)}[/green]"
            )
        else:
            console.print(
                f"[red]✗ [{index}/{total}] failed after "
                f"{_format_elapsed(result.duration_seconds)} (exit {result.returncode})[/red]"
            )
        return result

    return runner


def execute_devops_plan_with_confirmation(plan: DevOpsPlan) -> DevOpsExecutionResult:
    if not plan.safe_to_execute:
        result = DevOpsExecutionResult(
            plan=plan,
            success=False,
            error="Plan is not safe to execute.",
        )
        show_devops_result(result)
        return result

    from typer import confirm

    if (
        plan.request.action == DevOpsAction.K8S_DEPLOY
        and plan.context_inferred
        and plan.target_context
    ):
        if not confirm(
            f"Deploy to current Kubernetes context '{plan.target_context}'?"
        ):
            console.print("[yellow]Kubernetes target confirmation cancelled.[/yellow]")
            return DevOpsExecutionResult(
                plan=plan,
                success=False,
                error="Kubernetes target context was not approved.",
            )

    if not confirm("Execute the exact command(s) shown above?"):
        console.print("[yellow]DevOps workflow cancelled.[/yellow]")
        return DevOpsExecutionResult(
            plan=plan,
            success=False,
            error="Cancelled by operator.",
        )

    console.print(
        "[dim]Live progress enabled. Percentages reflect completed workflow steps; "
        "Iceman does not invent command-level percentages.[/dim]"
    )
    result = execute_devops_plan(plan, step_runner=_streaming_step_runner(plan.workspace))
    show_devops_result(result, include_step_output=False)
    return result


def show_devops_result(
    result: DevOpsExecutionResult, *, include_step_output: bool = True
) -> None:
    if include_step_output:
        for item in result.step_results:
            console.print(f"\n[bold]{item.step.label}[/bold]")
            if item.result.stdout:
                console.print(_terminal_text(item.result.stdout.rstrip()))
            if item.result.stderr:
                console.print(f"[red]{_terminal_text(item.result.stderr.rstrip())}[/red]")
            if item.result.returncode == 0:
                console.print("[green]✓ completed[/green]")
            else:
                console.print(
                    f"[red]✗ exited with code {item.result.returncode}[/red]"
                )

    if result.success:
        console.print(
            f"\n[bold green]DevOps workflow completed successfully.[/bold green] "
            f"[dim]Total: {_format_elapsed(result.duration_seconds)}[/dim]"
        )
        for line in result.summary:
            console.print(f"  • {_terminal_text(line)}")

        if result.diagnosis:
            console.print("\n[bold cyan]Diagnosis[/bold cyan]")
            for line in result.diagnosis:
                console.print(f"  • {_terminal_text(line)}")
        if result.evidence:
            console.print("\n[bold]Evidence[/bold]")
            for line in result.evidence:
                console.print(f"  • {_terminal_text(line)}")
        if result.next_actions:
            console.print("\n[bold green]Recommended next actions[/bold green]")
            for index, line in enumerate(result.next_actions, 1):
                console.print(f"  {index}. {_terminal_text(line)}")
    else:
        console.print(
            f"\n[bold red]DevOps workflow stopped.[/bold red] "
            f"{_terminal_text(result.error or 'Unknown error')} "
            f"[dim]Total: {_format_elapsed(result.duration_seconds)}[/dim]"
        )
        total = len(result.plan.steps)
        completed = sum(
            1 for item in result.step_results
            if item.result.returncode == 0 and not item.result.interrupted
        )
        if result.interrupted:
            interrupted_item = next(
                (item for item in reversed(result.step_results) if item.result.interrupted),
                None,
            )
            console.print(f"[yellow]Completed: {completed}/{total}[/yellow]")
            if interrupted_item is not None:
                console.print(
                    f"[yellow]Interrupted: {_terminal_text(interrupted_item.step.label)}[/yellow]"
                )
            remaining = max(0, total - len(result.step_results))
            console.print(f"[dim]Not executed: {remaining} remaining step(s).[/dim]")
        console.print(
            "[dim]Iceman stops on the first failed or interrupted step; later "
            "state-changing steps are not attempted.[/dim]"
        )



def show_advisor_report(report: AdvisorReport) -> None:
    if not report.findings:
        return
    console.print("\n[bold]Iceman Advisor[/bold]")
    labels = {
        AdviceLevel.BLOCKER: "[bold red]⛔ BLOCKER[/bold red]",
        AdviceLevel.WARNING: "[yellow]⚠ WARNING[/yellow]",
        AdviceLevel.RECOMMENDATION: "[cyan]💡 RECOMMENDATION[/cyan]",
        AdviceLevel.REMINDER: "[dim]ℹ REMINDER[/dim]",
    }
    for finding in report.findings:
        console.print(f"{labels[finding.level]}  {_terminal_text(finding.message)}")
        if finding.suggestion:
            console.print(f"   [dim]{_terminal_text(finding.suggestion)}[/dim]")
        if finding.fix_id and finding.fix_prompt:
            console.print("   [green]Guided fix available.[/green]")


def show_linux_plan(plan: LinuxPlan) -> None:
    style = "green" if plan.safe_to_execute else "yellow"
    console.print()
    console.print(
        Panel.fit(
            "[bold]Natural-Language Linux Plan[/bold]\n"
            f"Action: [cyan]{plan.request.action.value}[/cyan]\n"
            f"Workspace: {plan.workspace}",
            border_style=style,
        )
    )
    if plan.notes:
        console.print("\n[bold]Notes[/bold]")
        for note in plan.notes:
            console.print(f"  • {_terminal_text(note)}")
    if not plan.safe_to_execute:
        console.print("\n[yellow]Iceman cannot safely execute this Linux request.[/yellow]")
        return
    table = Table(title="Trusted Linux execution steps")
    table.add_column("#", justify="right")
    table.add_column("Type")
    table.add_column("Step")
    table.add_column("Command")
    for index, step in enumerate(plan.steps, 1):
        kind_text = "[yellow]CHANGE[/yellow]" if step.changes_state else "[cyan]READ[/cyan]"
        table.add_row(str(index), kind_text, step.label, _terminal_text(step.command))
    console.print(table)
    console.print(
        "[yellow]Review the exact trusted command(s) above before approving execution.[/yellow]"
        if any(step.changes_state for step in plan.steps)
        else "[dim]This workflow is read-only. Review the exact trusted command(s) above before approving execution.[/dim]"
    )


def execute_linux_plan_with_confirmation(plan: LinuxPlan) -> LinuxExecutionResult:
    if not plan.safe_to_execute:
        result = LinuxExecutionResult(plan=plan, success=False, error="Plan is not safe to execute.")
        show_linux_result(result)
        return result
    from typer import confirm
    if not confirm("Execute the exact command(s) shown above?"):
        console.print("[yellow]Linux workflow cancelled.[/yellow]")
        return LinuxExecutionResult(plan=plan, success=False, error="Cancelled by operator.")
    console.print(
        "[dim]Live progress enabled. Percentages reflect completed workflow steps; "
        "Iceman does not invent command-level percentages.[/dim]"
    )
    result = execute_linux_plan(plan, step_runner=_streaming_step_runner(plan.workspace))
    show_linux_result(result, include_step_output=False)
    return result


def show_linux_result(
    result: LinuxExecutionResult, *, include_step_output: bool = True
) -> None:
    if include_step_output:
        for item in result.step_results:
            console.print(f"\n[bold]{item.step.label}[/bold]")
            if item.result.stdout:
                console.print(_terminal_text(item.result.stdout.rstrip()))
            if item.result.stderr:
                console.print(f"[red]{_terminal_text(item.result.stderr.rstrip())}[/red]")
            if item.result.returncode == 0:
                console.print("[green]✓ completed[/green]")
            else:
                console.print(f"[red]✗ exited with code {item.result.returncode}[/red]")
    if result.success:
        console.print(
            f"\n[bold green]Linux workflow completed successfully.[/bold green] "
            f"[dim]Total: {_format_elapsed(result.duration_seconds)}[/dim]"
        )
        for line in result.summary:
            console.print(f"  • {_terminal_text(line)}")
    else:
        console.print(
            f"\n[bold red]Linux workflow stopped.[/bold red] "
            f"{_terminal_text(result.error or 'Unknown error')} "
            f"[dim]Total: {_format_elapsed(result.duration_seconds)}[/dim]"
        )
        if result.interrupted:
            total = len(result.plan.steps)
            completed = sum(
                1 for item in result.step_results
                if item.result.returncode == 0 and not item.result.interrupted
            )
            console.print(f"[yellow]Completed: {completed}/{total}[/yellow]")
            console.print(
                "[dim]Iceman stopped immediately; later steps were not executed.[/dim]"
            )


def show_git_plan(plan: GitPlan) -> None:
    style = "green" if plan.safe_to_execute else "yellow"
    console.print()
    console.print(
        Panel.fit(
            "[bold]Natural-Language Git/GitHub Plan[/bold]\n"
            f"Action: [cyan]{plan.request.action.value}[/cyan]\n"
            f"Workspace: {plan.workspace}",
            border_style=style,
        )
    )
    if plan.notes:
        console.print("\n[bold]Notes[/bold]")
        for note in plan.notes:
            console.print(f"  • {_terminal_text(note)}")
    if not plan.safe_to_execute:
        console.print("\n[yellow]Iceman cannot safely execute this Git/GitHub request yet.[/yellow]")
        return
    table = Table(title="Trusted Git/GitHub execution steps")
    table.add_column("#", justify="right")
    table.add_column("Type")
    table.add_column("Step")
    table.add_column("Command")
    for index, step in enumerate(plan.steps, 1):
        kind_text = "[yellow]CHANGE[/yellow]" if step.changes_state else "[cyan]READ[/cyan]"
        table.add_row(str(index), kind_text, step.label, _terminal_text(step.command))
    console.print(table)
    console.print(
        "[yellow]Review the exact trusted command(s) above before approving execution.[/yellow]"
        if any(step.changes_state for step in plan.steps)
        else "[dim]This workflow is read-only. Review the exact trusted command(s) above before approving execution.[/dim]"
    )


def execute_git_plan_with_confirmation(plan: GitPlan) -> GitExecutionResult:
    if not plan.safe_to_execute:
        result = GitExecutionResult(plan=plan, success=False, error="Plan is not safe to execute.")
        show_git_result(result)
        return result
    from typer import confirm
    if not confirm("Execute the exact Git/GitHub command(s) shown above?"):
        console.print("[yellow]Git/GitHub workflow cancelled.[/yellow]")
        return GitExecutionResult(plan=plan, success=False, error="Cancelled by operator.")
    console.print(
        "[dim]Live progress enabled. Percentages reflect completed workflow steps; "
        "Iceman does not invent command-level percentages.[/dim]"
    )
    result = execute_git_plan(plan, step_runner=_streaming_step_runner(plan.workspace))
    show_git_result(result, include_step_output=False)
    return result


def show_git_result(result: GitExecutionResult, *, include_step_output: bool = True) -> None:
    if include_step_output:
        for item in result.step_results:
            console.print(f"\n[bold]{item.step.label}[/bold]")
            if item.result.stdout:
                console.print(_terminal_text(item.result.stdout.rstrip()))
            if item.result.stderr:
                console.print(f"[red]{_terminal_text(item.result.stderr.rstrip())}[/red]")
            if item.result.returncode == 0:
                console.print("[green]✓ completed[/green]")
            else:
                console.print(f"[red]✗ exited with code {item.result.returncode}[/red]")
    if result.success:
        console.print(
            f"\n[bold green]Git/GitHub workflow completed successfully.[/bold green] "
            f"[dim]Total: {_format_elapsed(result.duration_seconds)}[/dim]"
        )
        for line in result.summary:
            console.print(f"  • {_terminal_text(line)}")
    else:
        console.print(
            f"\n[bold red]Git/GitHub workflow stopped.[/bold red] "
            f"{_terminal_text(result.error or 'Unknown error')} "
            f"[dim]Total: {_format_elapsed(result.duration_seconds)}[/dim]"
        )
        if result.interrupted:
            total = len(result.plan.steps)
            completed = sum(1 for item in result.step_results if item.result.returncode == 0 and not item.result.interrupted)
            console.print(f"[yellow]Completed: {completed}/{total}[/yellow]")
            console.print("[dim]Iceman stopped immediately; later Git/GitHub steps were not executed.[/dim]")
