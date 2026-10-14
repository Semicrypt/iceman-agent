from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
import time
from typing import Callable
from enum import Enum
from pathlib import Path

from iceman.runner import CommandResult, run_command


class LinuxAction(str, Enum):
    PWD = "linux_pwd"
    LIST = "linux_list"
    LIST_ALL = "linux_list_all"
    CHANGE_DIR = "linux_change_dir"
    MAKE_DIR = "linux_make_dir"
    CREATE_FILE = "linux_create_file"
    SHOW_FILE = "linux_show_file"
    COPY = "linux_copy"
    MOVE = "linux_move"
    DELETE_FILE = "linux_delete_file"
    MAKE_EXECUTABLE = "linux_make_executable"
    SHOW_PERMISSIONS = "linux_show_permissions"
    DISK_USAGE = "linux_disk_usage"
    FILESYSTEM_USAGE = "linux_filesystem_usage"
    FIND_FILES = "linux_find_files"
    FIND_TEXT = "linux_find_text"
    PORT_OWNER = "linux_port_owner"
    PROCESSES = "linux_processes"


@dataclass(frozen=True)
class LinuxRequest:
    action: LinuxAction
    path: str | None = None
    destination: str | None = None
    pattern: str | None = None
    port: int | None = None


@dataclass(frozen=True)
class LinuxStep:
    label: str
    command: str
    changes_state: bool = False


@dataclass
class LinuxPlan:
    request: LinuxRequest
    workspace: Path
    title: str
    steps: list[LinuxStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    safe_to_execute: bool = True
    requires_approval: bool = True
    new_workspace: Path | None = None


@dataclass(frozen=True)
class LinuxStepResult:
    step: LinuxStep
    result: CommandResult


@dataclass
class LinuxExecutionResult:
    plan: LinuxPlan
    success: bool
    step_results: list[LinuxStepResult] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0
    interrupted: bool = False


_UNSAFE_PATH_CHARS = re.compile(r"[\x00\n\r]")
_SAFE_FIND_PATTERN = re.compile(r"^[A-Za-z0-9_.*/?\-]+$")
_SAFE_SEARCH_TEXT = re.compile(r"^[A-Za-z0-9_ .:/@+\-=,]+$")


def _quote(value: str) -> str:
    return shlex.quote(value)


def _clean_path(value: str | None, *, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise ValueError("A path is required for this Linux action.")
        return None
    value = value.strip()
    if not value and required:
        raise ValueError("A path is required for this Linux action.")
    if _UNSAFE_PATH_CHARS.search(value):
        raise ValueError("Unsafe path characters were rejected.")
    return value


def _resolved(workspace: Path, value: str) -> Path:
    raw = Path(value).expanduser()
    return raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()


def _mutation_path(workspace: Path, value: str, *, must_exist: bool = False) -> Path:
    path = _resolved(workspace, value)
    try:
        path.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(
            "File-changing Linux actions are restricted to the active Iceman workspace. "
            "Change workspace first if you intend to work elsewhere."
        ) from exc
    if must_exist and not path.exists():
        raise ValueError(f"Path not found: {value}")
    return path


def _display_rel(workspace: Path, path: Path) -> str:
    try:
        rel = path.relative_to(workspace.resolve())
        return str(rel) if str(rel) else "."
    except ValueError:
        return str(path)


def build_linux_plan(workspace: Path, request: LinuxRequest) -> LinuxPlan:
    workspace = workspace.resolve()
    plan = LinuxPlan(
        request=request,
        workspace=workspace,
        title=request.action.value.replace("linux_", "").replace("_", " ").title(),
    )

    try:
        action = request.action

        if action == LinuxAction.PWD:
            plan.steps.append(LinuxStep("Show current directory", "pwd"))

        elif action in {LinuxAction.LIST, LinuxAction.LIST_ALL}:
            path = _clean_path(request.path, required=False) or "."
            target = _resolved(workspace, path)
            if not target.exists() or not target.is_dir():
                raise ValueError(f"Directory not found: {path}")
            flags = "-la" if action == LinuxAction.LIST_ALL else "-lh"
            plan.steps.append(
                LinuxStep("List directory contents", f"ls {flags} -- {_quote(str(target))}")
            )

        elif action == LinuxAction.CHANGE_DIR:
            path = _clean_path(request.path)
            target = _resolved(workspace, path)
            if not target.exists() or not target.is_dir():
                raise ValueError(f"Directory not found: {path}")
            # cd is handled by Iceman session state; the displayed command is exact operator intent.
            plan.steps.append(LinuxStep("Change active directory", f"cd -- {_quote(str(target))}", True))
            plan.new_workspace = target
            plan.notes.append(f"Iceman workspace will become: {target}")

        elif action == LinuxAction.MAKE_DIR:
            path = _clean_path(request.path)
            target = _mutation_path(workspace, path)
            if target.exists():
                raise ValueError(f"Path already exists: {path}")
            parent = target.parent
            if not parent.exists():
                raise ValueError(f"Parent directory does not exist: {_display_rel(workspace, parent)}")
            plan.steps.append(
                LinuxStep("Create directory", f"mkdir -- {_quote(str(target))}", True)
            )

        elif action == LinuxAction.CREATE_FILE:
            path = _clean_path(request.path)
            target = _mutation_path(workspace, path)
            if target.exists():
                plan.notes.append("The file already exists; touch will only update its timestamp.")
            elif not target.parent.exists():
                raise ValueError("Parent directory does not exist.")
            plan.steps.append(
                LinuxStep("Create/touch file", f"touch -- {_quote(str(target))}", True)
            )

        elif action == LinuxAction.SHOW_FILE:
            path = _clean_path(request.path)
            target = _resolved(workspace, path)
            if not target.exists() or not target.is_file():
                raise ValueError(f"File not found: {path}")
            plan.steps.append(LinuxStep("Show file contents", f"cat -- {_quote(str(target))}"))

        elif action in {LinuxAction.COPY, LinuxAction.MOVE}:
            source_raw = _clean_path(request.path)
            source = _mutation_path(workspace, source_raw, must_exist=True)

            if request.destination is None or not request.destination.strip():
                verb = "copy" if action == LinuxAction.COPY else "move"
                source_display = _display_rel(workspace, source)
                raise ValueError(
                    f"A destination is required. Where should I {verb} "
                    f"{source_display}? No command was executed."
                )

            dest_raw = _clean_path(request.destination)
            dest = _mutation_path(workspace, dest_raw)

            if source == dest:
                raise ValueError("Source and destination are the same path.")

            command = "cp" if action == LinuxAction.COPY else "mv"
            label = "Copy path" if action == LinuxAction.COPY else "Move path"
            plan.steps.append(
                LinuxStep(label, f"{command} -- {_quote(str(source))} {_quote(str(dest))}", True)
            )

        elif action == LinuxAction.DELETE_FILE:
            path = _clean_path(request.path)

            # Check the lexical entry before resolving it so a symlink can never
            # cause Iceman to delete the file it points at.
            raw = Path(path).expanduser()
            lexical_target = raw if raw.is_absolute() else workspace / raw

            if lexical_target.is_symlink():
                raise ValueError(
                    "Iceman will not delete symbolic links through this action "
                    "because their target semantics can be surprising."
                )

            target = _mutation_path(workspace, path, must_exist=True)

            relative = target.relative_to(workspace)
            if relative.parts and relative.parts[0] == ".git":
                raise ValueError(
                    "Deletion inside .git is blocked to protect repository metadata."
                )

            if not target.is_file():
                raise ValueError(
                    "This trusted delete action removes regular files only. "
                    "Recursive directory deletion and rm -rf are not supported."
                )

            plan.steps.append(
                LinuxStep(
                    "Delete file",
                    f"rm -- {_quote(str(target))}",
                    True,
                )
            )
            plan.notes.append(
                "Deletion is irreversible at the filesystem level. "
                "Iceman will remove only the exact file shown after approval."
            )

        elif action == LinuxAction.MAKE_EXECUTABLE:
            path = _clean_path(request.path)
            target = _mutation_path(workspace, path, must_exist=True)
            if not target.is_file():
                raise ValueError("Only files can be made executable by this action.")
            plan.steps.append(
                LinuxStep("Add executable permission", f"chmod +x -- {_quote(str(target))}", True)
            )

        elif action == LinuxAction.SHOW_PERMISSIONS:
            path = _clean_path(request.path)
            target = _resolved(workspace, path)
            if not target.exists():
                raise ValueError(f"Path not found: {path}")
            plan.steps.append(
                LinuxStep("Show permissions", f"stat -c '%A %a %U:%G %n' -- {_quote(str(target))}")
            )

        elif action == LinuxAction.DISK_USAGE:
            path = _clean_path(request.path, required=False) or "."
            target = _resolved(workspace, path)
            if not target.exists():
                raise ValueError(f"Path not found: {path}")
            plan.steps.append(LinuxStep("Show disk usage", f"du -sh -- {_quote(str(target))}"))

        elif action == LinuxAction.FILESYSTEM_USAGE:
            plan.steps.append(LinuxStep("Show filesystem usage", "df -h"))

        elif action == LinuxAction.FIND_FILES:
            pattern = (request.pattern or "").strip()
            if not pattern or not _SAFE_FIND_PATTERN.fullmatch(pattern):
                raise ValueError("Use a simple filename pattern such as '*.yaml' or 'Dockerfile'.")
            plan.steps.append(
                LinuxStep("Find matching files", f"find . -type f -name {_quote(pattern)} -print")
            )

        elif action == LinuxAction.FIND_TEXT:
            pattern = (request.pattern or "").strip()
            if not pattern or not _SAFE_SEARCH_TEXT.fullmatch(pattern):
                raise ValueError("Search text contains unsupported characters.")
            # grep is deterministic and remains confined to the active workspace.
            plan.steps.append(
                LinuxStep("Find text in workspace", f"grep -RIn --exclude-dir=.git -- {_quote(pattern)} .")
            )

        elif action == LinuxAction.PORT_OWNER:
            if request.port is None or not 1 <= int(request.port) <= 65535:
                raise ValueError("A valid TCP port is required.")
            port = int(request.port)
            plan.steps.append(
                LinuxStep("Show process using port", f"ss -lntp 'sport = :{port}'")
            )

        elif action == LinuxAction.PROCESSES:
            plan.steps.append(
                LinuxStep("Show running processes", "ps -eo pid,user,%cpu,%mem,etime,comm --sort=-%cpu")
            )

        else:
            raise ValueError("Unsupported Linux action.")

    except ValueError as exc:
        plan.safe_to_execute = False
        plan.notes.append(str(exc))

    plan.requires_approval = True
    return plan


def execute_linux_plan(
    plan: LinuxPlan,
    step_runner: Callable[[LinuxStep, int, int], CommandResult] | None = None,
) -> LinuxExecutionResult:
    started = time.monotonic()
    if not plan.safe_to_execute:
        return LinuxExecutionResult(
            plan=plan,
            success=False,
            error="Plan is not safe to execute.",
            duration_seconds=max(0.0, time.monotonic() - started),
        )

    # Session-changing cd is not run in a subprocess because it would not persist.
    if plan.request.action == LinuxAction.CHANGE_DIR:
        return LinuxExecutionResult(
            plan=plan,
            success=True,
            summary=[f"Active Iceman workspace changed to {plan.new_workspace}"],
            duration_seconds=max(0.0, time.monotonic() - started),
        )

    step_results: list[LinuxStepResult] = []
    total_steps = len(plan.steps)
    for index, step in enumerate(plan.steps, 1):
        try:
            result = (
                step_runner(step, index, total_steps)
                if step_runner is not None
                else run_command(step.command, cwd=plan.workspace)
            )
        except (FileNotFoundError, OSError) as exc:
            return LinuxExecutionResult(
                plan=plan,
                success=False,
                step_results=step_results,
                error=str(exc),
                duration_seconds=max(0.0, time.monotonic() - started),
            )
        except KeyboardInterrupt:
            return LinuxExecutionResult(
                plan=plan,
                success=False,
                step_results=step_results,
                error="Execution interrupted by operator.",
                duration_seconds=max(0.0, time.monotonic() - started),
                interrupted=True,
            )
        step_results.append(LinuxStepResult(step=step, result=result))
        if result.interrupted:
            return LinuxExecutionResult(
                plan=plan,
                success=False,
                step_results=step_results,
                error=f"Execution interrupted during: {step.label}",
                duration_seconds=max(0.0, time.monotonic() - started),
                interrupted=True,
            )
        if result.returncode != 0:
            return LinuxExecutionResult(
                plan=plan,
                success=False,
                step_results=step_results,
                error=f"'{step.label}' exited with code {result.returncode}.",
                duration_seconds=max(0.0, time.monotonic() - started),
            )

    return LinuxExecutionResult(
        plan=plan,
        success=True,
        step_results=step_results,
        duration_seconds=max(0.0, time.monotonic() - started),
    )
