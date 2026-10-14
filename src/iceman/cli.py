from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from typer.core import TyperGroup

from iceman import __version__
from iceman.console import console
from iceman.presentation import show_response_footer
from iceman.ai.provider import provider_status
from iceman.assessment import assess_project
from iceman.probing import (
    investigate_target,
    normalize_investigation_subject,
)
from iceman.diagnostics import diagnose_docker_container
from iceman.inspector import inspect_project
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
from iceman.reasoner import is_current_folder_inspection_request
from iceman.shell import run_request, start_shell


class RequestGroup(TyperGroup):
    """Treat unregistered command words as one natural-language request."""

    def resolve_command(self, ctx, args):
        # Preserve inspect [PATH], including a quoted path containing spaces.
        # Only the complete multi-word current-folder phrases override its parser.
        if (
            len(args) > 2
            and args[0] == "inspect"
            and not ctx.resilient_parsing
            and is_current_folder_inspection_request(" ".join(args))
        ):
            return "ask", self.get_command(ctx, "ask"), args
        if (
            args
            and not ctx.resilient_parsing
            and not args[0].startswith("-")
            and self.get_command(ctx, args[0]) is None
        ):
            return "ask", self.get_command(ctx, "ask"), args
        return super().resolve_command(ctx, args)


app = typer.Typer(
    name="iceman",
    cls=RequestGroup,
    help=(
        "Iceman — safety-first Cloud & DevOps agent. "
        "Use iceman or ice with no arguments for the interactive shell, "
        "or ask: ice what do you think about this project? "
        "Use ask for requests that begin with a built-in command name."
    ),
    invoke_without_command=True,
    no_args_is_help=False,
    suggest_commands=True,
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"Iceman {__version__}")
        show_response_footer()
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
        return

    # Run exactly once when the CLI response is complete, including
    # non-zero trusted refusals/cancellations that exit through Typer.
    ctx.call_on_close(show_response_footer)


@app.command()
def version() -> None:
    """Show Iceman's version."""
    console.print(f"Iceman {__version__}")


@app.command("ai-status")
def ai_status_command() -> None:
    """Show the configured reasoning provider."""
    show_ai_provider_status(provider_status())

    from iceman.ai.ollama_config import (
        chosen_model,
        installed_models,
    )

    model, source = chosen_model()
    available = installed_models()

    console.print(f"Knowledge model preference: {model} ({source})", markup=False)

    if available is None:
        console.print("Ollama: unavailable; built-in knowledge fallback", markup=False)
    elif model in available:
        console.print("Ollama: connected; selected model installed", markup=False)
    else:
        console.print("Ollama: connected; selected model not installed", markup=False)


@app.command("ai-model")
def ai_model_command(
    model: str | None = typer.Argument(
        None, help="Installed Ollama model to select."
    ),
) -> None:
    """Show or select an installed local knowledge model."""
    import os
    from iceman.ai.ollama_config import (
        chosen_model,
        installed_models,
        save_model,
    )

    available = installed_models()

    if model is not None:
        if os.getenv("ICEMAN_OLLAMA_MODEL"):
            console.print(
                "Unset ICEMAN_OLLAMA_MODEL before saving a preference.",
                markup=False,
            )
            raise typer.Exit(code=1)

        if available is None:
            console.print("Ollama is unavailable.", markup=False)
            raise typer.Exit(code=1)

        if model not in available:
            console.print(
                f"Model {model!r} is not installed. "
                "No download was started.",
                markup=False,
            )
            raise typer.Exit(code=1)

        save_model(model, available)
        console.print(f"Saved model preference: {model}", markup=False)

    selected, source = chosen_model()
    console.print(
        f"Knowledge model: {selected} ({source})", markup=False
    )

    if available is None:
        console.print("Ollama is unavailable.", markup=False)
    else:
        console.print(
            "Installed: " + (", ".join(available) or "none"),
            markup=False,
        )


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


@app.command(
    "check",
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
    },
)
def check_command(
    ctx: typer.Context,
    first: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Optional project directory, or the beginning of a "
                "natural-language check request."
            ),
        ),
    ] = None,
) -> None:
    """Assess a project or run a conversational read-only check."""
    parts: list[str] = []

    if first:
        parts.append(first)

    parts.extend(ctx.args)

    # Preserve the legacy `ice check` behavior.
    if not parts:
        path = Path.cwd().resolve()
        console.print(
            "[dim]Running a read-only project health assessment.[/dim]"
        )
        show_project_assessment(assess_project(path))
        return

    # Preserve `ice check /existing/project`.
    # Only one argument resolving to a real directory is treated as a path.
    if len(parts) == 1:
        candidate = Path(parts[0]).expanduser()

        if candidate.exists() and candidate.is_dir():
            path = candidate.resolve()
            console.print(
                "[dim]Running a read-only project health assessment.[/dim]"
            )
            show_project_assessment(assess_project(path))
            return

    # Otherwise `check` is part of the natural-language request.
    from iceman.redaction import redact_text

    request = "check " + " ".join(parts).strip()

    console.print(
        f"Request: {redact_text(request)}",
        markup=False,
    )

    code = run_request(
        request,
        project_path=Path.cwd(),
    )

    if code:
        raise typer.Exit(code=code)

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


@app.command(
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
    }
)
def ask(
    prompt: Annotated[
        list[str],
        typer.Argument(help="Natural-language request (quoted or multiple words)."),
    ],
) -> None:
    """Run one trusted request and exit; normal approval gates still apply."""
    import json
    from iceman.redaction import redact_text

    words = list(prompt)

    # Unknown option-looking tokens are normally CLI usage errors. The narrow
    # exception is `rm` flags: Iceman must receive requests such as
    # `rm -rf target` intact so the trusted reasoner can explicitly refuse
    # recursive/forced deletion instead of Click/Typer consuming the flag.
    option_like = [
        word
        for word in words
        if word.startswith("-") and word not in {"-", "--"}
    ]
    if option_like:
        is_rm_request = bool(words) and words[0].lower() == "rm"
        rm_flags = {
            "-r",
            "-f",
            "-rf",
            "-fr",
            "--recursive",
            "--force",
        }

        if not (
            is_rm_request
            and all(word.lower() in rm_flags for word in option_like)
        ):
            raise typer.BadParameter(
                f"Unsupported option in natural-language request: "
                f"{option_like[0]}"
            )

    # The parent shell removes quoting but retains an argument's boundaries.
    # Preserve a multiword literal message in a compound request.
    if len(words) > 1 and any(w.lower() == "push" for w in words):
        for index, word in enumerate(words[:-1]):
            if word.lower() in {"message", "as", "-m"} and any(c.isspace() for c in words[index + 1]):
                words[index + 1] = json.dumps(words[index + 1], ensure_ascii=False)
                break
    request = " ".join(words).strip()
    console.print(f"Request: {redact_text(request)}", markup=False)
    code = run_request(request, project_path=Path.cwd())
    if code:
        raise typer.Exit(code=code)
