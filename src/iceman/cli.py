from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

from iceman import __version__
from iceman.console import console
from iceman.ai.provider import provider_status
from iceman.assessment import assess_project
from iceman.probing import (
    investigate_target,
    normalize_investigation_subject,
)
from iceman.diagnostics import diagnose_docker_container
from iceman.inspector import inspect_project
from iceman.planner import make_plan
from iceman.presentation import (
    execute_with_confirmation,
    print_banner,
    show_doctor,
    show_ai_provider_status,
    show_docker_diagnosis,
    show_inspection,
    show_project_assessment,
    show_root_cause_investigation,
)
from iceman.shell import start_shell

app = typer.Typer(
    name="iceman",
    help="Iceman — safety-first Cloud & DevOps agent.",
    invoke_without_command=True,
    no_args_is_help=False,
    suggest_commands=True,
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"Iceman {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        Optional[bool],
        typer.Option(
            "--version",
            "-V",
            help="Show Iceman's version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """Iceman — safety-first Cloud & DevOps agent."""
    if ctx.invoked_subcommand is None:
        start_shell()


@app.command()
def version() -> None:
    """Show Iceman's version."""
    console.print(f"Iceman {__version__}")


@app.command("ai-status")
def ai_status_command() -> None:
    """Show the configured reasoning provider."""
    show_ai_provider_status(provider_status())


@app.command()
def doctor() -> None:
    """Check the machine and common DevOps dependencies."""
    print_banner(__version__)
    show_doctor()


@app.command("inspect")
def inspect_command(
    path: Annotated[
        Path,
        typer.Argument(
            help="Project directory to inspect.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = Path("."),
) -> None:
    """Recursively inspect a project and detect its technologies."""
    show_inspection(inspect_project(path))


@app.command("check")
def check_command(
    path: Annotated[
        Path,
        typer.Argument(
            help="Project directory to assess.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = Path("."),
) -> None:
    """Run a read-only project health assessment."""
    console.print(
        "[dim]Running a read-only project health assessment.[/dim]"
    )
    show_project_assessment(assess_project(path))


@app.command("investigate")
def investigate_command(
    subject: Annotated[
        str,
        typer.Argument(
            help="Application warning/monitored-target subject to investigate.",
        ),
    ],
    path: Annotated[
        Path,
        typer.Option(
            "--path",
            "-p",
            help="Project workspace used to resolve the configured target.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = Path("."),
) -> None:
    """Run bounded read-only root-cause probing."""
    console.print(
        "[dim]Resolving target configuration and running read-only "
        "DNS/TCP/TLS/HTTP probes.[/dim]"
    )
    normalized_subject = normalize_investigation_subject(subject)
    show_root_cause_investigation(
        investigate_target(path, normalized_subject)
    )


@app.command("diagnose")
def diagnose_command(
    container: Annotated[str, typer.Argument(help="Docker container name or ID.")],
) -> None:
    """Run read-only diagnostics against a Docker container."""
    console.print(
        "[dim]Running read-only Docker diagnostics: inspect + recent logs. "
        "No changes will be made.[/dim]"
    )
    diagnosis = diagnose_docker_container(container, cwd=Path.cwd())
    show_docker_diagnosis(diagnosis)


@app.command("exec")
def exec_command(
    command: Annotated[str, typer.Argument(help="Command to preview and execute.")],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation for commands that pass the safety policy."),
    ] = False,
) -> None:
    """Execute an explicitly supplied command through Iceman's safety layer."""
    code = execute_with_confirmation(command, yes=yes, cwd=Path.cwd())
    if code:
        raise typer.Exit(code=code)


@app.command()
def ask(
    prompt: Annotated[str, typer.Argument(help="Natural-language DevOps request.")],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation for commands that pass the safety policy."),
    ] = False,
) -> None:
    """Turn a supported natural-language request into a command."""
    print_banner(__version__)
    console.print(f"\n[bold]Request:[/bold] {prompt}")

    plan = make_plan(prompt)
    console.print(f"[bold]Plan:[/bold] {plan.explanation}")

    if not plan.understood or not plan.command:
        console.print(
            "\n[dim]The AI reasoning layer is not connected yet. "
            "Try 'show docker containers', 'show git status', "
            "'who am I on aws', or 'show kubernetes pods'.[/dim]"
        )
        raise typer.Exit(code=1)

    code = execute_with_confirmation(plan.command, yes=yes, cwd=Path.cwd())
    if code:
        raise typer.Exit(code=code)
