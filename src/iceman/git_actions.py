from __future__ import annotations

import re
import shlex
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from iceman.runner import CommandResult, run_command


class GitAction(str, Enum):
    STATUS = "git_status"
    DIFF = "git_diff"
    INIT = "git_init"
    STAGE_ALL = "git_stage_all"
    COMMIT = "git_commit"
    PUSH = "git_push"
    COMMIT_PUSH = "git_commit_push"
    REMOTES = "git_remotes"
    BRANCH = "git_branch"
    TAG = "git_tag"
    GITHUB_CREATE_REPO = "github_create_repo"


@dataclass(frozen=True)
class GitRequest:
    action: GitAction
    message: str | None = None
    repo_name: str | None = None
    visibility: str | None = None
    tag: str | None = None


@dataclass(frozen=True)
class GitStep:
    label: str
    command: str
    changes_state: bool = False
    stream_output: bool = True


@dataclass
class GitPlan:
    request: GitRequest
    workspace: Path
    title: str
    steps: list[GitStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    safe_to_execute: bool = True
    requires_approval: bool = True
    changed_paths: list[str] = field(default_factory=list)
    sensitive_paths: list[str] = field(default_factory=list)
    large_paths: list[str] = field(default_factory=list)
    branch: str | None = None
    remote: str | None = None


@dataclass(frozen=True)
class GitStepResult:
    step: GitStep
    result: CommandResult


@dataclass
class GitExecutionResult:
    plan: GitPlan
    success: bool
    step_results: list[GitStepResult] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0
    interrupted: bool = False


_SAFE_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_SAFE_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+\-]{0,127}$")

_HIGH_RISK_NAMES = {
    ".env", "terraform.tfvars", "credentials", "id_rsa", "id_ed25519",
    "kubeconfig", ".npmrc",
}
_HIGH_RISK_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".tfstate")
_HEAVY_PREFIXES = (".venv/", ".pytest_cache/", "node_modules/", ".terraform/", "__pycache__/")
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bAccountKey\s*="),
)


def _quote(value: str) -> str:
    return shlex.quote(value)


def _run(workspace: Path, command: str) -> CommandResult | None:
    try:
        return run_command(command, cwd=workspace)
    except (FileNotFoundError, OSError):
        return None


def _is_repo(workspace: Path) -> bool:
    result = _run(workspace, "git rev-parse --is-inside-work-tree")
    return bool(result and result.returncode == 0 and result.stdout.strip() == "true")


def _branch(workspace: Path) -> str | None:
    result = _run(workspace, "git branch --show-current")
    if not result or result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _remote_names(workspace: Path) -> list[str]:
    result = _run(workspace, "git remote")
    if not result or result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _status_paths(workspace: Path) -> list[str]:
    result = _run(workspace, "git status --porcelain=v1 --untracked-files=all")
    if not result or result.returncode != 0:
        return []
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        raw = line[3:]
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        raw = raw.strip().strip('"')
        if raw:
            paths.append(raw)
    return sorted(set(paths))


def _staged_paths(workspace: Path) -> list[str]:
    result = _run(workspace, "git diff --cached --name-only --diff-filter=ACMR")
    if not result or result.returncode != 0:
        return []
    return sorted(set(line.strip() for line in result.stdout.splitlines() if line.strip()))


def _is_sensitive_name(path: str) -> bool:
    p = path.replace("\\", "/")
    name = Path(p).name
    lower = p.lower()
    if name in _HIGH_RISK_NAMES:
        return True
    if name.startswith(".env.") and name not in {".env.example", ".env.sample", ".env.template"}:
        return True
    if lower.endswith(_HIGH_RISK_SUFFIXES):
        return True
    return False


def _scan_paths(workspace: Path, paths: list[str]) -> tuple[list[str], list[str], list[str]]:
    sensitive: list[str] = []
    large: list[str] = []
    heavy: list[str] = []
    for rel in paths:
        norm = rel.replace("\\", "/")
        if norm.startswith(_HEAVY_PREFIXES) or any(f"/{prefix}" in f"/{norm}" for prefix in _HEAVY_PREFIXES):
            heavy.append(rel)
        if _is_sensitive_name(rel):
            sensitive.append(rel)
        path = workspace / rel
        try:
            if path.is_file():
                size = path.stat().st_size
                if size >= 100 * 1024 * 1024:
                    large.append(rel)
                if size <= 1024 * 1024:
                    # Unit-test/fixture files often contain deliberately fake secret
                    # signatures to validate scanners. Keep filename-based checks
                    # active, but do not hard-block on content-only signatures in
                    # those bounded fixture locations.
                    parts = {part.lower() for part in Path(norm).parts}
                    fixture_like = bool(parts & {"tests", "test", "fixtures", "fixture"})
                    if not fixture_like:
                        data = path.read_text(encoding="utf-8", errors="ignore")
                        if any(pattern.search(data) for pattern in _SECRET_PATTERNS):
                            sensitive.append(rel)
        except OSError:
            continue
    return sorted(set(sensitive)), sorted(set(large)), sorted(set(heavy))


def _validate_message(message: str | None) -> str:
    value = (message or "").strip()
    if not value:
        raise ValueError("A commit message is required. Example: commit changes with message 'feat: add Git workflow'.")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("Commit messages must be a single line.")
    if len(value) > 240:
        raise ValueError("Commit message is too long; keep it under 240 characters.")
    return value


def _validate_repo_name(name: str | None, workspace: Path) -> str:
    value = (name or workspace.name).strip()
    if not _SAFE_REPO.fullmatch(value):
        raise ValueError("GitHub repository name contains unsupported characters.")
    return value


def _validate_visibility(value: str | None) -> str:
    if value is None:
        return "private"
    normalized = value.strip().lower()
    if normalized not in {"public", "private"}:
        raise ValueError("GitHub visibility must be 'public' or 'private'.")
    return normalized


def build_git_plan(workspace: Path, request: GitRequest) -> GitPlan:
    workspace = workspace.resolve()
    plan = GitPlan(
        request=request,
        workspace=workspace,
        title=request.action.value.replace("_", " ").title(),
    )
    try:
        action = request.action
        repo = _is_repo(workspace)

        if action == GitAction.INIT:
            if repo:
                plan.safe_to_execute = False
                plan.notes.append("This workspace is already a Git repository.")
            else:
                plan.steps.append(GitStep("Initialize Git repository", "git init -b main", True))
                plan.notes.append("The initial branch will be 'main'.")

        elif action == GitAction.STATUS:
            if not repo:
                raise ValueError("This workspace is not a Git repository yet. Ask Iceman to initialize git first.")
            plan.steps.append(GitStep("Show Git status", "git status --short --branch"))

        elif action == GitAction.DIFF:
            if not repo:
                raise ValueError("This workspace is not a Git repository yet.")
            plan.steps.extend([
                GitStep("Show unstaged change summary", "git diff --stat"),
                GitStep("Show staged change summary", "git diff --cached --stat"),
            ])

        elif action == GitAction.REMOTES:
            if not repo:
                raise ValueError("This workspace is not a Git repository yet.")
            plan.steps.append(GitStep("Show Git remotes", "git remote -v"))

        elif action == GitAction.BRANCH:
            if not repo:
                raise ValueError("This workspace is not a Git repository yet.")
            plan.steps.extend([
                GitStep("Show current branch", "git branch --show-current"),
                GitStep("Show local branches", "git branch --list"),
            ])

        elif action == GitAction.STAGE_ALL:
            if not repo:
                raise ValueError("This workspace is not a Git repository yet.")
            changed = _status_paths(workspace)
            plan.changed_paths = changed
            if not changed:
                raise ValueError("There are no changes to stage.")
            sensitive, large, heavy = _scan_paths(workspace, changed)
            plan.sensitive_paths = sensitive
            plan.large_paths = large
            if sensitive:
                plan.safe_to_execute = False
                plan.notes.append("Potential secret/sensitive files detected: " + ", ".join(sensitive[:8]))
                plan.notes.append("Iceman will not stage everything until these files are removed, ignored, or explicitly handled.")
            if large:
                plan.safe_to_execute = False
                plan.notes.append("Files at or above GitHub's 100 MiB limit detected: " + ", ".join(large[:8]))
            if heavy:
                plan.safe_to_execute = False
                plan.notes.append("Generated/heavy directories are not ignored: " + ", ".join(heavy[:8]))
                plan.notes.append("Create or update .gitignore before staging the whole project.")
            if plan.safe_to_execute:
                plan.steps.extend([
                    GitStep("Check whitespace errors", "git diff --check"),
                    GitStep("Stage all approved changes", "git add -A", True),
                    GitStep("Show staged summary", "git diff --cached --stat"),
                ])

        elif action == GitAction.COMMIT:
            if not repo:
                raise ValueError("This workspace is not a Git repository yet.")
            message = _validate_message(request.message)
            staged = _staged_paths(workspace)
            if not staged:
                raise ValueError("There are no staged changes to commit. Stage the intended files first.")
            sensitive, large, _ = _scan_paths(workspace, staged)
            plan.sensitive_paths = sensitive
            plan.large_paths = large
            if sensitive or large:
                plan.safe_to_execute = False
                if sensitive:
                    plan.notes.append("Potential sensitive staged files detected: " + ", ".join(sensitive[:8]))
                if large:
                    plan.notes.append("Oversized staged files detected: " + ", ".join(large[:8]))
            if plan.safe_to_execute:
                plan.steps.extend([
                    GitStep("Check staged whitespace errors", "git diff --cached --check"),
                    GitStep("Show staged summary", "git diff --cached --stat"),
                    GitStep("Create Git commit", f"git commit -m {_quote(message)}", True),
                ])

        elif action == GitAction.PUSH:
            if not repo:
                raise ValueError("This workspace is not a Git repository yet.")
            branch = _branch(workspace)
            plan.branch = branch
            if not branch:
                raise ValueError("Git is in detached HEAD state or has no current branch; Iceman will not push.")
            remotes = _remote_names(workspace)
            if "origin" not in remotes:
                raise ValueError("No 'origin' remote is configured. Create/connect the GitHub repository first.")
            plan.remote = "origin"
            upstream = _run(workspace, "git rev-parse --abbrev-ref --symbolic-full-name @{u}")
            command = "git push" if upstream and upstream.returncode == 0 else f"git push -u origin {_quote(branch)}"
            plan.steps.extend([
                GitStep("Show branch and remote", "git status --short --branch"),
                GitStep("Push current branch to origin", command, True),
            ])

        elif action == GitAction.COMMIT_PUSH:
            if not repo:
                raise ValueError("This workspace is not a Git repository yet.")
            message = _validate_message(request.message)
            branch = _branch(workspace)
            if not branch:
                raise ValueError("Git is in detached HEAD state or has no current branch; Iceman will not push.")
            remotes = _remote_names(workspace)
            if "origin" not in remotes:
                raise ValueError("No 'origin' remote is configured. Create/connect the GitHub repository first.")
            changed = _status_paths(workspace)
            if not changed:
                raise ValueError("There are no changes to commit.")
            plan.changed_paths = changed
            sensitive, large, heavy = _scan_paths(workspace, changed)
            plan.sensitive_paths = sensitive
            plan.large_paths = large
            if sensitive or large or heavy:
                plan.safe_to_execute = False
                if sensitive:
                    plan.notes.append("Potential secret/sensitive files detected: " + ", ".join(sensitive[:8]))
                if large:
                    plan.notes.append("Files at or above GitHub's 100 MiB limit detected: " + ", ".join(large[:8]))
                if heavy:
                    plan.notes.append("Generated/heavy directories are not ignored: " + ", ".join(heavy[:8]))
                plan.notes.append("Iceman will not stage/commit/push until the pre-commit blockers are resolved.")
            if plan.safe_to_execute:
                upstream = _run(workspace, "git rev-parse --abbrev-ref --symbolic-full-name @{u}")
                push_cmd = "git push" if upstream and upstream.returncode == 0 else f"git push -u origin {_quote(branch)}"
                plan.steps.extend([
                    GitStep("Check whitespace errors", "git diff --check"),
                    GitStep("Stage all approved changes", "git add -A", True),
                    GitStep("Show staged summary", "git diff --cached --stat"),
                    GitStep("Create Git commit", f"git commit -m {_quote(message)}", True),
                    GitStep("Push current branch to origin", push_cmd, True),
                ])

        elif action == GitAction.GITHUB_CREATE_REPO:
            if not repo:
                raise ValueError("Initialize Git locally before creating the GitHub repository.")
            if "origin" in _remote_names(workspace):
                raise ValueError("An 'origin' remote already exists. Iceman will not replace it automatically.")
            if shutil.which("gh") is None:
                raise ValueError("GitHub CLI (gh) is not installed. Install/authenticate gh before creating a GitHub repository.")
            auth = _run(workspace, "gh auth status")
            if not auth or auth.returncode != 0:
                raise ValueError("GitHub CLI is not authenticated. Run 'gh auth login' first, then retry.")
            repo_name = _validate_repo_name(request.repo_name, workspace)
            visibility = _validate_visibility(request.visibility)
            if request.visibility is None:
                plan.notes.append("No visibility was specified; Iceman selected the safer default: private.")
            plan.steps.extend([
                GitStep("Confirm GitHub authentication", "gh auth status"),
                GitStep(
                    f"Create {visibility} GitHub repository {repo_name}",
                    f"gh repo create {_quote(repo_name)} --{visibility} --source=. --remote=origin",
                    True,
                ),
                GitStep("Show configured remote", "git remote -v"),
            ])

        elif action == GitAction.TAG:
            if not repo:
                raise ValueError("This workspace is not a Git repository yet.")
            tag = (request.tag or "").strip()
            if not tag or not _SAFE_TAG.fullmatch(tag):
                raise ValueError("Provide a safe Git tag such as v0.9.7.")
            plan.steps.extend([
                GitStep("Create annotated milestone tag", f"git tag -a {_quote(tag)} -m {_quote(tag)}", True),
                GitStep("Show milestone tag", f"git show --no-patch --oneline {_quote(tag)}"),
            ])

        else:
            raise ValueError("Unsupported Git/GitHub action.")

    except ValueError as exc:
        plan.safe_to_execute = False
        plan.notes.append(str(exc))

    plan.requires_approval = True
    return plan


def execute_git_plan(
    plan: GitPlan,
    step_runner: Callable[[GitStep, int, int], CommandResult] | None = None,
) -> GitExecutionResult:
    started = time.monotonic()
    if not plan.safe_to_execute:
        return GitExecutionResult(plan=plan, success=False, error="Plan is not safe to execute.")

    results: list[GitStepResult] = []
    total = len(plan.steps)
    for index, step in enumerate(plan.steps, 1):
        try:
            result = step_runner(step, index, total) if step_runner else run_command(step.command, cwd=plan.workspace)
        except KeyboardInterrupt:
            return GitExecutionResult(
                plan=plan,
                success=False,
                step_results=results,
                error="Execution interrupted by operator.",
                duration_seconds=time.monotonic() - started,
                interrupted=True,
            )
        except (FileNotFoundError, OSError) as exc:
            return GitExecutionResult(
                plan=plan,
                success=False,
                step_results=results,
                error=str(exc),
                duration_seconds=time.monotonic() - started,
            )
        results.append(GitStepResult(step=step, result=result))
        if result.interrupted or result.returncode != 0:
            return GitExecutionResult(
                plan=plan,
                success=False,
                step_results=results,
                error=(
                    f"Execution interrupted during: {step.label}"
                    if result.interrupted
                    else f"'{step.label}' exited with code {result.returncode}."
                ),
                duration_seconds=time.monotonic() - started,
                interrupted=result.interrupted,
            )

    summary: list[str] = []
    if plan.request.action == GitAction.INIT:
        summary.append("Git repository initialized on branch main.")
    elif plan.request.action == GitAction.GITHUB_CREATE_REPO:
        summary.append("GitHub repository created and connected as origin.")
    elif plan.request.action == GitAction.COMMIT:
        summary.append("Git commit created successfully.")
    elif plan.request.action == GitAction.PUSH:
        summary.append("Current branch pushed to origin successfully.")
    elif plan.request.action == GitAction.COMMIT_PUSH:
        summary.append("Changes were staged, committed, and pushed successfully.")
    elif plan.request.action == GitAction.TAG:
        summary.append(f"Milestone tag created: {plan.request.tag}")

    return GitExecutionResult(
        plan=plan,
        success=True,
        step_results=results,
        summary=summary,
        duration_seconds=time.monotonic() - started,
    )
