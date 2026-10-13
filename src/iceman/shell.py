from __future__ import annotations

from pathlib import Path

from iceman import __version__
from iceman.ai.contracts import TrustedAction
from iceman.ai.provider import (
    get_reasoning_provider,
    provider_status,
)
from iceman.assessment import assess_project
from iceman.console import console
from iceman.diagnostics import diagnose_docker_container
from iceman.inspector import inspect_project
from iceman.planner import make_plan
from iceman.reasoner import explain_context
from iceman.remediation import build_remediation_plan
from iceman.terraform_remediation import build_terraform_remediation_advice
from iceman.application_remediation import (
    build_application_remediation_advice,
    prepare_application_change_plan,
)
from iceman.probing import (
    investigate_target,
    normalize_investigation_subject,
)
from iceman.http_context import analyze_http_context_differential
from iceman.workspace_files import (
    WorkspaceFileRequest,
    WorkspaceTemplate,
    build_workspace_file_plan,
    rollback_workspace_file_result,
)
from iceman.devops_actions import (
    DevOpsAction,
    DevOpsRequest,
    build_devops_plan,
)
from iceman.linux_actions import LinuxAction, LinuxRequest, build_linux_plan
from iceman.git_actions import GitAction, GitRequest, build_git_plan
from iceman.advisor import advise_devops_plan, advise_linux_plan, advise_git_plan
from iceman.session import SessionState
from iceman.presentation import (
    apply_application_change_plan,
    apply_remediation_plan,
    execute_with_confirmation,
    print_banner,
    show_ai_provider_status,
    show_application_change_plan,
    show_application_remediation_advice,
    show_terraform_remediation_advice,
    show_ai_route,
    show_doctor,
    show_docker_diagnosis,
    show_http_context_differential,
    show_inspection,
    show_priority,
    show_project_assessment,
    show_reasoning_explanation,
    show_root_cause_investigation,
    show_remediation_plan,
    show_session_state,
    show_workspace_file_plan,
    apply_workspace_file_plan_with_confirmation,
    show_workspace_file_rollback_result,
    show_devops_plan,
    execute_devops_plan_with_confirmation,
    show_linux_plan,
    execute_linux_plan_with_confirmation,
    show_git_plan,
    execute_git_plan_with_confirmation,
    show_advisor_report,
)


HELP_TEXT = """
[bold]Interactive commands[/bold]

  [cyan]help[/cyan]                         Show this help.
  [cyan]doctor[/cyan]                       Check local DevOps tools.
  [cyan]ai status[/cyan]                    Show the active reasoning provider.
  [cyan]pwd[/cyan]                          Show the active Iceman workspace.
  [cyan]cd <path>[/cyan]                    Change the active workspace.
  [cyan]inspect [path][/cyan]               Inspect a project and make it the active workspace.
  [cyan]diagnose <container>[/cyan]         Run read-only Docker diagnostics.
  [cyan]investigate <subject>[/cyan]         Probe a reported application warning/root cause.
  [cyan]fix it[/cyan]                       Recommend remediation for the latest problem context.
  [cyan]investigate option N[/cyan]          Run a read-only investigation for a diagnostic remediation option.
  [cyan]apply option N [with URL][/cyan]    Prepare/apply a numbered application remediation option.
  [cyan]apply file plan[/cyan]              Apply the last previewed workspace file plan.
  [cyan]rollback file changes[/cyan]        Undo the latest successful Iceman file write.
  [cyan]status[/cyan]                       Show Iceman's session context.
  [cyan]!<command>[/cyan]                   Run an explicit command through Iceman safety.
  [cyan]clear[/cyan]                        Clear the terminal.
  [cyan]exit[/cyan] / [cyan]quit[/cyan]                  Leave Iceman.

[bold]AI / structured reasoning requests[/bold]

  check this project and tell me if anything is wrong
  the frontend is running but I cannot reach it; investigate
  what should I fix first?
  investigate why Openai is down
  why did that happen?
  how should I fix it?
  investigate option 1
  go deeper on the backend rejection
  apply option 2 with https://example.com/health
  create a Dockerfile for backend port 5000
  create a .gitignore
  create Kubernetes deployment and service for backend port 5000 replicas 3
  add kubernetes resource requests and limits for backend
  create Terraform starter for AWS
  build the backend
  run it on port 5050
  show me the containers
  why did backend stop
  why is backend restarting
  check if backend is healthy
  show important errors in backend logs
  why can't I reach port 5000
  check docker security for backend
  make backend image smaller
  rebuild and restart backend
  create docker compose for backend port 5000
  show docker compose status
  where am I
  show me the files
  show hidden files
  go into backend
  go back one folder
  create a folder called configs
  create an empty file called app.log
  show me package.json
  make deploy.sh executable
  show permissions for deploy.sh
  show what is using port 5000
  find files matching *.yaml
  validate my kubernetes manifests
  deploy backend to minikube
  show me the pods
  diagnose pod api-abc
  diagnose service backend
  check service backend connectivity
  diagnose deployment backend
  show pods again
  show them again
  scale backend to 5 replicas
  make it 3 instead
  validate my terraform
  validate my terrafor   (Iceman asks to confirm the correction)
  show me what terraform will create
  initialize git
  show git status
  show what changed
  stage everything
  commit my changes with message "feat: milestone"
  create a public github repo called iceman-agent
  push to github
  commit and push with message "feat: milestone"
  tag this as v0.9.7
  rollback file changes
  can you fix it?
  verify everything is healthy now
  what do you remember?

The AI provider only selects trusted Iceman actions. It cannot send arbitrary
shell commands to the executor. Natural-language DevOps and Linux actions display their exact deterministic
command(s) and require operator confirmation before running. Iceman Advisor
surfaces relevant security/reliability reminders before approval.

Provider configuration:
  ICEMAN_AI_PROVIDER=auto|local|openai
  ICEMAN_OPENAI_MODEL=gpt-5.6-luna
  OPENAI_API_KEY=<your key>
"""


def _legacy_docker_diagnose_target(raw: str) -> str | None:
    """Return a legacy Docker diagnose target unless the request names a K8s pod."""
    stripped = raw.strip()
    parts = stripped.split(maxsplit=1)

    if len(parts) != 2 or parts[0].lower() != "diagnose":
        return None

    target = parts[1].strip()
    normalized = " ".join(target.lower().split())

    # Explicit Kubernetes pod language must fall through to Advanced
    # Intelligence instead of being consumed by the legacy Docker handler.
    if normalized.startswith("the "):
        normalized = normalized[4:].strip()

    if normalized.startswith("kubernetes "):
        normalized = normalized[len("kubernetes "):].strip()
    elif normalized.startswith("k8s "):
        normalized = normalized[len("k8s "):].strip()

    explicit_k8s_resource = normalized.split(maxsplit=1)[0] if normalized else ""
    if explicit_k8s_resource in {"pod", "service", "deployment"}:
        return None

    return target


def _run_diagnosis(
    target: str,
    state: SessionState,
    cwd: Path | None = None,
) -> None:
    diagnosis_cwd = cwd or state.workspace
    console.print(
        "[dim]Running read-only Docker diagnostics: inspect + recent logs. "
        "No changes will be made.[/dim]"
    )
    diagnosis = diagnose_docker_container(target, cwd=diagnosis_cwd)
    state.remember_diagnosis(diagnosis)
    show_docker_diagnosis(diagnosis)


def _run_assessment(state: SessionState) -> None:
    console.print(
        "[dim]Running a read-only project health assessment. "
        "Iceman will inspect the project and current Compose services.[/dim]"
    )
    assessment = assess_project(state.workspace)
    state.remember_assessment(assessment)
    show_project_assessment(assessment)



def _run_investigation(
    subject: str,
    state: SessionState,
) -> None:
    normalized_subject = normalize_investigation_subject(subject)
    console.print(
        "[dim]Running bounded read-only root-cause probing: "
        "configuration resolution + DNS + TCP + TLS + 3-sample HTTP evidence.[/dim]"
    )
    if normalized_subject != subject.strip():
        console.print(
            f"[dim]Resolved investigation subject: "
            f"{normalized_subject}[/dim]"
        )
    investigation = investigate_target(
        state.workspace,
        normalized_subject,
    )
    state.remember_investigation(investigation)
    show_root_cause_investigation(investigation)

def _run_http_context_differential(
    state: SessionState,
) -> None:
    if state.last_investigation is None:
        console.print(
            "[yellow]No root-cause investigation is in context. "
            "Run an application investigation first.[/yellow]"
        )
        return

    console.print(
        "[dim]Running read-only HTTP context differential analysis. "
        "Only non-sensitive request/runtime/response metadata will be compared.[/dim]"
    )
    try:
        differential = analyze_http_context_differential(
            state.last_investigation,
        )
    except ValueError as exc:
        console.print(
            f"[yellow]HTTP context analysis unavailable:[/yellow] {exc}"
        )
        return

    state.remember_http_context_differential(differential)
    show_http_context_differential(differential)


def _run_investigate_remediation_option(
    option_number: int,
    state: SessionState,
) -> None:
    if state.last_investigation is None:
        console.print(
            "[yellow]No root-cause investigation is in context. "
            "No remediation option can be investigated.[/yellow]"
        )
        return

    advice = (
        state.last_application_remediation
        or build_application_remediation_advice(
            state.last_investigation,
        )
    )
    if state.last_application_remediation is None:
        state.remember_application_remediation(advice)

    selected = next(
        (
            option
            for option in advice.options
            if option.number == option_number
        ),
        None,
    )
    if selected is None:
        console.print(
            f"[yellow]Option {option_number} is not present in the current "
            "remediation plan.[/yellow]"
        )
        return

    if selected.action == "investigate_context":
        _run_http_context_differential(state)
        return

    if selected.action == "investigate_stability":
        console.print(
            "[dim]That option requests more stability evidence. "
            "Re-running the bounded multi-sample investigation...[/dim]"
        )
        _run_investigation(
            state.last_investigation.subject,
            state,
        )
        return

    console.print(
        f"[yellow]Option {option_number} ({selected.action}) is not a "
        "read-only diagnostic option. No changes were made.[/yellow]"
    )


def _latest_terraform_plan_output(
    state: SessionState,
) -> str | None:
    result = state.last_devops_result
    if result is None or not result.success:
        return None

    if result.plan.request.action not in {
        DevOpsAction.TERRAFORM_PLAN,
        DevOpsAction.TERRAFORM_EXPLAIN_PLAN,
    }:
        return None

    chunks: list[str] = []

    for step_result in result.step_results:
        if step_result.result.stdout:
            chunks.append(step_result.result.stdout)

        if step_result.result.stderr:
            chunks.append(step_result.result.stderr)

    return "\n".join(chunks).strip() or None


def _run_terraform_remediation_advice(
    state: SessionState,
) -> None:
    plan_output = _latest_terraform_plan_output(
        state
    )

    if plan_output is None:
        if state.last_terraform_remediation is not None:
            show_terraform_remediation_advice(
                state.last_terraform_remediation
            )
            return

        console.print(
            "[yellow]No successful Terraform plan analysis is in "
            "context yet. Run 'analyze terraform plan' first.[/yellow]"
        )
        return

    advice = build_terraform_remediation_advice(
        plan_output
    )
    state.remember_terraform_remediation(advice)
    show_terraform_remediation_advice(advice)


def _run_remediation_advice(
    state: SessionState,
) -> None:
    context = state.latest_remediation_context()

    if context == "terraform":
        _run_terraform_remediation_advice(state)
        return

    if context == "investigation":
        _run_application_remediation_advice(state)
        return

    if context == "diagnosis":
        _run_repair(state)
        return

    console.print(
        "[yellow]No Terraform plan, root-cause investigation, or "
        "Docker diagnosis is in remediation context yet.[/yellow]"
    )


def _run_application_remediation_advice(
    state: SessionState,
) -> None:
    if state.last_investigation is None:
        console.print(
            "[yellow]No root-cause investigation is in context yet. "
            "Run an investigation first.[/yellow]"
        )
        return

    advice = build_application_remediation_advice(
        state.last_investigation,
    )
    state.remember_application_remediation(advice)
    show_application_remediation_advice(advice)


def _run_application_option(
    option_number: int,
    replacement_url: str | None,
    state: SessionState,
) -> None:
    if state.latest_remediation_context() == "terraform":
        console.print(
            "[yellow]Terraform remediation options are advisory-only. "
            "Iceman will not convert 'apply option' into an HCL write, "
            "terraform apply, or terraform destroy operation.[/yellow]"
        )
        return

    if state.last_investigation is None:
        console.print(
            "[yellow]No root-cause investigation is in context. "
            "No remediation option can be prepared.[/yellow]"
        )
        return

    if state.last_application_remediation is None:
        advice = build_application_remediation_advice(
            state.last_investigation,
        )
        state.remember_application_remediation(advice)

    plan = prepare_application_change_plan(
        state.last_investigation,
        option_number,
        replacement_url,
        state.workspace,
    )
    state.remember_application_change(plan)
    show_application_change_plan(plan)

    if not plan.safe_to_apply:
        return

    result = apply_application_change_plan(plan)
    if not result.success:
        return

    console.print(
        "\n[dim]Re-running root-cause investigation after the verified "
        "configuration change...[/dim]"
    )
    refreshed = investigate_target(
        state.workspace,
        state.last_investigation.subject,
    )
    state.remember_investigation(refreshed)
    show_root_cause_investigation(refreshed)


def _run_repair(state: SessionState) -> None:
    remediation_context = state.latest_remediation_context()

    if remediation_context == "terraform":
        _run_terraform_remediation_advice(state)
        return

    if remediation_context == "investigation":
        _run_application_remediation_advice(state)
        return

    if state.last_diagnosis is None:
        console.print(
            "[yellow]No diagnosis or root-cause investigation is in context yet. "
            "Run a diagnosis/investigation first.[/yellow]"
        )
        return

    plan = build_remediation_plan(
        state.last_diagnosis,
        state.workspace,
    )
    state.remember_remediation(plan)
    show_remediation_plan(plan)
    applied = apply_remediation_plan(plan, state.workspace)

    if applied:
        console.print(
            "\n[dim]Re-running diagnosis after remediation...[/dim]"
        )
        diagnosis_cwd = plan.working_dir or state.workspace
        refreshed = diagnose_docker_container(
            state.last_diagnosis.target,
            cwd=diagnosis_cwd,
        )
        state.remember_diagnosis(refreshed)
        show_docker_diagnosis(refreshed)


def _run_workspace_file_plan(
    template: WorkspaceTemplate | None,
    component: str | None,
    port: int | None,
    replicas: int | None,
    image: str | None,
    state: SessionState,
) -> None:
    if template is None:
        console.print(
            "[yellow]A trusted workspace file template is required. "
            "No file was generated.[/yellow]"
        )
        return

    request = WorkspaceFileRequest(
        template=template,
        component=component,
        port=port,
        replicas=replicas,
        image=image,
    )
    plan = build_workspace_file_plan(
        state.workspace,
        request,
    )
    state.remember_workspace_file_plan(plan)
    show_workspace_file_plan(plan)

    if not plan.safe_to_apply:
        return

    result = apply_workspace_file_plan_with_confirmation(plan)
    if result.success:
        state.remember_workspace_file_apply(result)


def _run_apply_last_workspace_file_plan(
    state: SessionState,
) -> None:
    plan = state.last_workspace_file_plan
    if plan is None:
        console.print(
            "[yellow]No workspace file plan is in context. "
            "Create/preview files first.[/yellow]"
        )
        return

    show_workspace_file_plan(plan)
    result = apply_workspace_file_plan_with_confirmation(plan)
    if result.success:
        state.remember_workspace_file_apply(result)


def _run_workspace_file_rollback(
    state: SessionState,
) -> None:
    result = state.last_workspace_file_apply
    if result is None:
        console.print(
            "[yellow]No successful Iceman workspace-file write is available "
            "to roll back in this session.[/yellow]"
        )
        return

    from typer import confirm

    console.print(
        "[yellow]This will restore/delete files from the latest successful "
        "Iceman workspace-file write.[/yellow]"
    )
    if not confirm("Rollback the latest workspace file changes?"):
        console.print("[yellow]Rollback cancelled.[/yellow]")
        return

    rollback = rollback_workspace_file_result(result)
    show_workspace_file_rollback_result(rollback)
    if rollback.success:
        state.clear_workspace_file_apply_after_rollback()


def _maybe_apply_guided_devops_advice(
    plan,
    advisor,
    request: DevOpsRequest,
    state: SessionState,
):
    """Offer bounded Advisor remediations without giving AI arbitrary write access."""
    if request.action != DevOpsAction.K8S_DEPLOY:
        return plan, advisor

    finding = next(
        (item for item in advisor.findings if item.fix_id == "k8s_starter_resources"),
        None,
    )
    if finding is None or not finding.fix_prompt:
        return plan, advisor

    from typer import confirm

    if not confirm(finding.fix_prompt):
        console.print("[dim]Advisor recommendation left unchanged.[/dim]")
        return plan, advisor

    fix_request = WorkspaceFileRequest(
        template=WorkspaceTemplate.KUBERNETES_RESOURCES,
        component=request.component,
    )
    fix_plan = build_workspace_file_plan(state.workspace, fix_request)
    state.remember_workspace_file_plan(fix_plan)

    console.print(
        "\n[bold]Guided Advisor Fix[/bold]\n"
        "[dim]Iceman prepared a bounded workspace-file change. Review the exact "
        "diff before approving the write.[/dim]"
    )
    show_workspace_file_plan(fix_plan)
    if not fix_plan.safe_to_apply:
        console.print(
            "[yellow]Advisor could not prepare this fix safely. The original "
            "deployment plan remains unchanged.[/yellow]"
        )
        return plan, advisor

    result = apply_workspace_file_plan_with_confirmation(fix_plan)
    if not result.success:
        console.print(
            "[dim]The Advisor fix was not applied. You can still review or cancel "
            "the original deployment below.[/dim]"
        )
        return plan, advisor

    state.remember_workspace_file_apply(result)

    # Rebuild from disk after the verified write so Advisor/execution decisions
    # use the exact current manifest rather than stale pre-fix evidence.
    refreshed_plan = build_devops_plan(state.workspace, request)
    state.remember_devops_plan(refreshed_plan)
    refreshed_advisor = advise_devops_plan(refreshed_plan)
    console.print("\n[dim]Re-checking Advisor findings after the verified file change...[/dim]")
    if refreshed_advisor.findings:
        show_advisor_report(refreshed_advisor)
    else:
        console.print("[green]No remaining Advisor findings for this workflow.[/green]")
    return refreshed_plan, refreshed_advisor


def _run_devops_action(
    action: DevOpsAction | None,
    component: str | None,
    image: str | None,
    startup_target: str | None,
    port: int | None,
    replicas: int | None,
    context: str | None,
    terraform_dir: str | None,
    state: SessionState,
) -> None:
    if action is None:
        console.print(
            "[yellow]Advanced Intelligence selected a DevOps workflow without "
            "a trusted DevOps action. Nothing was executed.[/yellow]"
        )
        return

    if action == DevOpsAction.DOCKER_GUIDED_REPAIR and component is None:
        component = state.last_devops_component

    request = DevOpsRequest(
        action=action,
        component=component,
        image=image,
        startup_target=startup_target,
        port=port,
        replicas=replicas,
        context=context,
        terraform_dir=terraform_dir,
    )
    if action == DevOpsAction.DOCKER_GUIDED_REPAIR:
        from iceman.devops_actions import build_guided_docker_repair_plan

        plan = build_guided_docker_repair_plan(
            state.workspace,
            request,
            state.last_devops_result,
        )
    else:
        plan = build_devops_plan(state.workspace, request)

    state.remember_devops_plan(plan)
    advisor = advise_devops_plan(plan)
    show_advisor_report(advisor)
    plan, advisor = _maybe_apply_guided_devops_advice(
        plan, advisor, request, state
    )
    show_devops_plan(plan)
    if not plan.safe_to_execute:
        return

    result = execute_devops_plan_with_confirmation(plan)
    if result.success:
        state.remember_devops_result(result)



def _run_linux_action(
    action: LinuxAction | None,
    path: str | None,
    destination: str | None,
    pattern: str | None,
    port: int | None,
    state: SessionState,
) -> None:
    if action is None:
        console.print(
            "[yellow]Advanced Intelligence selected a Linux workflow without "
            "a trusted Linux action. Nothing was executed.[/yellow]"
        )
        return
    request = LinuxRequest(
        action=action,
        path=path,
        destination=destination,
        pattern=pattern,
        port=port,
    )
    plan = build_linux_plan(state.workspace, request)
    advisor = advise_linux_plan(plan)
    show_advisor_report(advisor)
    show_linux_plan(plan)
    if not plan.safe_to_execute:
        return
    result = execute_linux_plan_with_confirmation(plan)
    if result.success and plan.new_workspace is not None:
        state.change_workspace(plan.new_workspace)


def _run_git_action(
    action: GitAction | None,
    commit_message: str | None,
    repo_name: str | None,
    visibility: str | None,
    tag: str | None,
    state: SessionState,
) -> None:
    if action is None:
        console.print(
            "[yellow]Advanced Intelligence selected a Git workflow without "
            "a trusted Git action. Nothing was executed.[/yellow]"
        )
        return
    request = GitRequest(
        action=action,
        message=commit_message,
        repo_name=repo_name,
        visibility=visibility,
        tag=tag,
    )
    plan = build_git_plan(state.workspace, request)
    advisor = advise_git_plan(plan)
    show_advisor_report(advisor)
    show_git_plan(plan)
    if not plan.safe_to_execute:
        return
    result = execute_git_plan_with_confirmation(plan)
    if result.success:
        state.add_event("git_action", f"{action.value}: success=True")



def _dispatch_trusted_intent(
    action: TrustedAction,
    target: str | None,
    state: SessionState,
    option_number: int | None = None,
    replacement_url: str | None = None,
    file_template: WorkspaceTemplate | None = None,
    component: str | None = None,
    port: int | None = None,
    replicas: int | None = None,
    image: str | None = None,
    startup_target: str | None = None,
    devops_action: DevOpsAction | None = None,
    context: str | None = None,
    terraform_dir: str | None = None,
    linux_action: LinuxAction | None = None,
    path: str | None = None,
    destination: str | None = None,
    pattern: str | None = None,
    git_action: GitAction | None = None,
    commit_message: str | None = None,
    repo_name: str | None = None,
    visibility: str | None = None,
    tag: str | None = None,
) -> bool:
    """
    Execute one trusted action selected by a provider.

    Returns True when handled. No provider-supplied shell command exists in the
    contract, so command construction remains inside Iceman.
    """

    if action == TrustedAction.ASSESS_PROJECT:
        _run_assessment(state)
        return True

    if action == TrustedAction.PRIORITIZE:
        if state.last_assessment is None:
            assessment = assess_project(state.workspace)
            state.remember_assessment(assessment)
        show_priority(state.last_assessment)
        return True

    if action == TrustedAction.DIAGNOSE_CONTAINER:
        if not target:
            console.print(
                "[yellow]The reasoner selected a container diagnosis but "
                "did not provide a target. No action was taken.[/yellow]"
            )
            return True
        _run_diagnosis(target, state)
        return True

    if action == TrustedAction.EXPLAIN_CONTEXT:
        show_reasoning_explanation(explain_context(state))
        return True

    if action == TrustedAction.INVESTIGATE_APPLICATION_SIGNAL:
        if not target:
            console.print(
                "[yellow]Advanced Intelligence selected a root-cause "
                "investigation but did not provide a warning subject. "
                "No probe was run.[/yellow]"
            )
            return True
        _run_investigation(target, state)
        return True

    if action == TrustedAction.RECOMMEND_REMEDIATION:
        _run_remediation_advice(state)
        return True

    if action == TrustedAction.ANALYZE_HTTP_CONTEXT:
        if option_number is not None:
            _run_investigate_remediation_option(
                option_number,
                state,
            )
        else:
            _run_http_context_differential(state)
        return True

    if action == TrustedAction.APPLY_REMEDIATION_OPTION:
        if option_number is None:
            console.print(
                "[yellow]A remediation option number is required. "
                "No changes were made.[/yellow]"
            )
            return True
        _run_application_option(
            option_number,
            replacement_url,
            state,
        )
        return True

    if action == TrustedAction.PLAN_WORKSPACE_FILES:
        _run_workspace_file_plan(
            file_template,
            component,
            port,
            replicas,
            image,
            state,
        )
        return True

    if action == TrustedAction.APPLY_WORKSPACE_FILE_PLAN:
        _run_apply_last_workspace_file_plan(state)
        return True

    if action == TrustedAction.ROLLBACK_WORKSPACE_FILES:
        _run_workspace_file_rollback(state)
        return True

    if action == TrustedAction.EXECUTE_DEVOPS_ACTION:
        _run_devops_action(
            devops_action,
            component,
            image,
            startup_target,
            port,
            replicas,
            context,
            terraform_dir,
            state,
        )
        return True

    if action == TrustedAction.EXECUTE_LINUX_ACTION:
        _run_linux_action(
            linux_action,
            path,
            destination,
            pattern,
            port,
            state,
        )
        return True

    if action == TrustedAction.EXECUTE_GIT_ACTION:
        _run_git_action(
            git_action,
            commit_message,
            repo_name,
            visibility,
            tag,
            state,
        )
        return True

    if action == TrustedAction.SESSION_STATUS:
        show_session_state(state)
        return True

    if action == TrustedAction.REPAIR_LAST_DIAGNOSIS:
        _run_repair(state)
        return True

    if action == TrustedAction.INSPECT_PROJECT:
        show_inspection(inspect_project(state.workspace))
        return True

    if action == TrustedAction.DOCTOR:
        show_doctor()
        return True

    return False


def start_shell(project_path: Path | None = None) -> None:
    print_banner(__version__)

    state = SessionState((project_path or Path.cwd()).resolve())
    provider = get_reasoning_provider()
    ai_status = provider_status()

    console.print(f"\n[bold]Workspace:[/bold] {state.workspace}")
    console.print("[bold cyan]Advanced Intelligence[/bold cyan]")
    console.print(
        "[dim]Type 'help' for commands. Advanced Intelligence can only "
        "select trusted Iceman actions.[/dim]\n"
    )

    while True:
        prompt_name = state.workspace.name or str(state.workspace)
        try:
            raw = console.input(
                f"[bold cyan]iceman[/bold cyan] "
                f"[dim]({prompt_name})[/dim] "
                f"[bold cyan]>[/bold cyan] "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Leaving Iceman.[/dim]")
            return

        if not raw:
            continue

        lowered = raw.lower()

        if lowered in {"exit", "quit"}:
            console.print("[dim]Leaving Iceman.[/dim]")
            return

        if lowered == "help":
            console.print(HELP_TEXT)
            continue

        if lowered == "clear":
            console.clear()
            print_banner(__version__)
            continue

        if lowered == "doctor":
            show_doctor()
            continue

        if lowered in {"ai status", "ai-status", "provider"}:
            show_ai_provider_status(provider_status())
            continue

        if lowered in {"status", "context"}:
            show_session_state(state)
            continue

        if lowered == "pwd":
            console.print(state.workspace)
            continue

        if lowered == "cd" or lowered.startswith("cd "):
            target_text = raw[2:].strip()
            if not target_text:
                target = Path.home()
            else:
                target = Path(target_text).expanduser()
                if not target.is_absolute():
                    target = state.workspace / target
            target = target.resolve()

            if not target.exists() or not target.is_dir():
                console.print(f"[red]Directory not found:[/red] {target}")
                continue

            state.change_workspace(target)
            console.print(f"[green]Workspace:[/green] {state.workspace}")
            continue

        if lowered == "inspect" or lowered.startswith("inspect "):
            target_text = raw[7:].strip()
            if target_text:
                target = Path(target_text).expanduser()
                if not target.is_absolute():
                    target = state.workspace / target
            else:
                target = state.workspace

            target = target.resolve()
            if not target.exists() or not target.is_dir():
                console.print(f"[red]Directory not found:[/red] {target}")
                continue

            state.change_workspace(target)
            show_inspection(inspect_project(state.workspace))
            console.print(
                f"\n[green]Active workspace changed to:[/green] "
                f"{state.workspace}"
            )
            continue

        legacy_diagnose_target = _legacy_docker_diagnose_target(raw)
        if legacy_diagnose_target is not None:
            if not legacy_diagnose_target:
                console.print("[yellow]Usage: diagnose <container>[/yellow]")
                continue
            _run_diagnosis(legacy_diagnose_target, state)
            continue

        investigate_option_match = __import__("re").match(
            r"^investigate\s+option\s+(\d+)$",
            raw,
            flags=__import__("re").IGNORECASE,
        )
        if investigate_option_match:
            _run_investigate_remediation_option(
                int(investigate_option_match.group(1)),
                state,
            )
            continue

        if lowered.startswith("investigate "):
            subject = raw[len("investigate "):].strip()
            if not subject:
                console.print(
                    "[yellow]Usage: investigate <application signal>[/yellow]"
                )
                continue
            _run_investigation(subject, state)
            continue

        apply_match = __import__("re").match(
            r"^apply\s+option\s+(\d+)(?:\s+with\s+(https?://\S+))?$",
            raw,
            flags=__import__("re").IGNORECASE,
        )
        if apply_match:
            option_number = int(apply_match.group(1))
            replacement_url = apply_match.group(2)
            if replacement_url:
                replacement_url = replacement_url.rstrip(".,")
            _run_application_option(
                option_number,
                replacement_url,
                state,
            )
            continue

        if lowered in {
            "how should i fix it",
            "how do i fix it",
            "what are my options",
            "what are the remediation options",
            "recommend a fix",
            "recommend remediation",
        }:
            _run_remediation_advice(state)
            continue

        if lowered in {
            "fix it",
            "fix",
            "remediate",
            "remediate it",
            "can you fix it",
        }:
            _run_repair(state)
            continue

        if raw.startswith("!"):
            command = raw[1:].strip()
            if not command:
                console.print("[yellow]Provide a command after ![/yellow]")
                continue
            execute_with_confirmation(
                command,
                cwd=state.workspace,
            )
            continue

        # Natural-language path: provider selects ONLY a trusted intent.
        result = provider.reason(raw, state)
        show_ai_route(result)

        intent = result.intent
        entity_choice_confirmed = False
        if intent.clarification_candidates:
            from typer import prompt as typer_prompt

            kind = intent.clarification_kind or "resource"
            original = intent.clarification_original or "that name"
            console.print(
                f"Iceman could not uniquely match {original!r} to a {kind}.",
                markup=False,
            )
            console.print("Possible matches:")
            for index, candidate in enumerate(intent.clarification_candidates, start=1):
                console.print(f"  {index}. {candidate}", markup=False)
            console.print("  0. Cancel")

            selected = None
            while selected is None:
                raw_choice = typer_prompt("Which one did you mean?", default="0")
                try:
                    choice = int(str(raw_choice).strip())
                except ValueError:
                    console.print("[yellow]Enter one of the numbers shown above.[/yellow]")
                    continue
                if choice == 0:
                    console.print(
                        "[yellow]Interpretation cancelled. No command was executed.[/yellow]"
                    )
                    selected = "__cancel__"
                    break
                if 1 <= choice <= len(intent.clarification_candidates):
                    selected = intent.clarification_candidates[choice - 1]
                    break
                console.print("[yellow]Enter one of the numbers shown above.[/yellow]")

            if selected == "__cancel__":
                continue

            field = intent.clarification_field
            if field not in {"component", "context", "path", "image"}:
                console.print(
                    "[yellow]Iceman could not safely bind that clarification to a trusted field. "
                    "No command was executed.[/yellow]"
                )
                continue
            intent = intent.model_copy(
                update={
                    field: selected,
                    "clarification_candidates": [],
                    "requires_clarification": False,
                }
            )
            entity_choice_confirmed = True
            console.print(
                f"Using {kind}: {selected}",
                markup=False,
            )

        if (
            intent.requires_clarification
            and intent.interpreted_request
            and not entity_choice_confirmed
        ):
            from typer import confirm

            interpreted = intent.interpreted_request.strip()
            console.print(
                f"Iceman interpreted your request as: {interpreted!r}",
                markup=False,
            )
            if not confirm(f"Do you mean: {interpreted!r}?"):
                console.print(
                    "[yellow]Interpretation cancelled. Rephrase the request and "
                    "Iceman will not execute anything.[/yellow]"
                )
                continue

        if _dispatch_trusted_intent(
            intent.action,
            intent.target,
            state,
            option_number=intent.option_number,
            replacement_url=intent.replacement_url,
            file_template=intent.file_template,
            component=intent.component,
            port=intent.port,
            replicas=intent.replicas,
            image=intent.image,
            startup_target=intent.startup_target,
            devops_action=intent.devops_action,
            context=intent.context,
            terraform_dir=intent.terraform_dir,
            linux_action=intent.linux_action,
            path=intent.path,
            destination=intent.destination,
            pattern=intent.pattern,
            git_action=intent.git_action,
            commit_message=intent.commit_message,
            repo_name=intent.repo_name,
            visibility=intent.visibility,
            tag=intent.tag,
        ):
            continue

        # Existing deterministic command planner remains a bounded fallback.
        plan = make_plan(raw)
        console.print(f"[bold]Plan:[/bold] {plan.explanation}")

        if not plan.understood or not plan.command:
            console.print(
                "[dim]The provider did not map that request to a trusted "
                "workflow, and the bounded command planner has no match. "
                "No command was executed.[/dim]"
            )
            continue

        execute_with_confirmation(
            plan.command,
            cwd=state.workspace,
        )
