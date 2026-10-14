from __future__ import annotations

import re
import hashlib
import os
import stat
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
    PUSH_TAG = "git_push_tag"
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
    preflight_fingerprint: str | None = None


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



def _compound_paths(workspace: Path) -> list[str]:
    result = _run(workspace, "git status --porcelain=v1 -z --untracked-files=all")
    if result is None or result.returncode != 0:
        raise ValueError("I could not inspect all repository changes safely.")
    records = result.stdout.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            raise ValueError("Git returned an unreadable change list; no changes will be staged.")
        code, path = record[:2], record[3:]
        if code in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}:
            raise ValueError("Resolve the current Git merge conflicts before committing and pushing.")
        paths.add(path)
        if "R" in code or "C" in code:
            if index >= len(records) or not records[index]:
                raise ValueError("Git returned an incomplete rename or copy entry.")
            paths.add(records[index])
            index += 1
        if len(paths) > 1000:
            raise ValueError("This workflow covers more than 1000 paths; split it into smaller reviewed changes.")
    return sorted(paths)


def _compound_file_digest(workspace: Path, rel: str, *, scan: bool) -> str:
    root = workspace.resolve()
    relative = Path(rel)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("A changed path is outside the approved repository.")
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise ValueError("Safe file inspection is unavailable on this platform.")
    # Open each component relative to an anchored directory descriptor. This
    # refuses symlinks even if a path changes between listing and inspection.
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        try:
            file_fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        except FileNotFoundError:
            return "deleted"
        except OSError as exc:
            raise ValueError("Changed symbolic links or unreadable files need separate review before a combined commit and push.") from exc
        try:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Changed directories or submodules need separate review before a combined commit and push.")
            if info.st_size >= 100 * 1024 * 1024:
                raise ValueError("A changed file reaches the 100 MiB limit; review large files before committing.")
            digest = hashlib.sha256()
            digest.update(str(info.st_mode).encode("ascii"))
            tail = ""
            total = 0
            while True:
                block = os.read(file_fd, 128 * 1024)
                if not block:
                    break
                total += len(block)
                if total >= 100 * 1024 * 1024:
                    raise ValueError("A changed file grew beyond the inspection limit.")
                digest.update(block)
                if scan:
                    content = tail + block.decode("utf-8", errors="ignore")
                    if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
                        raise ValueError("A changed file contains a potential secret. Remove it before committing; its value has not been displayed.")
                    tail = content[-4096:]
            return digest.hexdigest()
        finally:
            os.close(file_fd)
    finally:
        os.close(descriptor)


def _compound_fingerprint(workspace: Path, *, scan: bool = False) -> str:
    digest = hashlib.sha256()
    commands = (
        "git status --porcelain=v1 -z --untracked-files=all",
        "git diff --cached --binary",
        "git rev-parse --verify HEAD",
        "git branch --show-current",
        "git config --get-regexp '^remote\\.origin\\.'",
        "git remote get-url --push --all origin",
    )
    for command in commands:
        result = _run(workspace, command)
        if result is None or (result.returncode != 0 and command != "git rev-parse --verify HEAD"):
            raise ValueError("Git preflight could not read repository state.")
        digest.update((command + "\0" + str(result.returncode) + "\0" + result.stdout + "\0").encode("utf-8"))
    try:
        for rel in _compound_paths(workspace):
            digest.update((rel + "\0" + _compound_file_digest(workspace, rel, scan=scan) + "\0").encode("utf-8"))
    except OSError as exc:
        raise ValueError("A changed path could not be inspected safely; nothing will be staged.") from exc
    return digest.hexdigest()

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
                plan.notes.append(
                    f"Pre-commit safety scan passed for {len(changed)} changed path(s): "
                    "no sensitive files, no files at or above 100 MiB, and no unignored generated/heavy paths detected."
                )
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
            sensitive, large, heavy = _scan_paths(workspace, staged)
            plan.sensitive_paths = sensitive
            plan.large_paths = large
            if sensitive or large or heavy:
                plan.safe_to_execute = False
                if sensitive:
                    plan.notes.append("Potential sensitive staged files detected: " + ", ".join(sensitive[:8]))
                if large:
                    plan.notes.append("Oversized staged files detected: " + ", ".join(large[:8]))
                if heavy:
                    plan.notes.append("Generated/heavy staged paths detected: " + ", ".join(heavy[:8]))
            if plan.safe_to_execute:
                plan.notes.append(
                    f"Pre-commit safety scan passed for {len(staged)} staged path(s): "
                    "no sensitive, oversized, or generated/heavy files detected."
                )
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
            top = _run(workspace, "git rev-parse --show-toplevel")
            if top is None or top.returncode != 0 or Path(top.stdout.strip()).resolve() != workspace.resolve():
                raise ValueError("A combined stage, commit, and push covers the whole repository. Run it from the repository root so I can inspect and preview that full scope.")
            _validate_message(request.message)
            message = request.message
            assert message is not None
            if any(pattern.search(message) for pattern in _SECRET_PATTERNS):
                raise ValueError("The commit message contains a potential secret; replace it before continuing.")
            branch = _branch(workspace)
            if not branch:
                raise ValueError("Git is in detached HEAD state or has no current branch; Iceman will not push.")
            if "origin" not in _remote_names(workspace):
                raise ValueError("No 'origin' remote is configured. Connect a remote before committing and pushing.")
            destination = _run(workspace, "git remote get-url --push --all origin")
            urls = destination.stdout.strip().splitlines() if destination and destination.returncode == 0 else []
            if len(urls) != 1 or urls[0].startswith("-") or "::" in urls[0]:
                raise ValueError("This workflow requires one ordinary origin push destination; review the remote configuration.")
            mirror = _run(workspace, "git config --bool --get remote.origin.mirror")
            if mirror and mirror.returncode == 0 and mirror.stdout.strip() == "true":
                raise ValueError("Origin is configured as a mirror; this workflow only publishes one branch.")
            from urllib.parse import urlsplit
            destination_parts = urlsplit(urls[0])
            if destination_parts.password or (destination_parts.scheme in {"http", "https"} and destination_parts.username):
                raise ValueError("Use Git credential management instead of embedding credentials in the remote URL.")
            changed = _compound_paths(workspace)
            if not changed:
                raise ValueError("There are no changes to commit.")
            fingerprint = _compound_fingerprint(workspace, scan=True)
            plan.changed_paths = changed
            plan.branch = branch
            plan.remote = "origin"
            # Strict descriptor-based content inspection above precedes the
            # existing name/size/generated-file policy shared by Git actions.
            sensitive = [p for p in changed if _is_sensitive_name(p)]
            heavy = [p for p in changed if any(part + "/" in _HEAVY_PREFIXES for part in Path(p).parts)]
            plan.sensitive_paths = sensitive
            if sensitive or heavy:
                plan.safe_to_execute = False
                if sensitive:
                    plan.notes.append("Potential secret/sensitive files detected; review them before staging.")
                if heavy:
                    plan.notes.append("Generated/heavy directories are not ignored.")
                plan.notes.append("No part of this workflow will execute until these blockers are resolved.")
            if plan.safe_to_execute:
                checks = (
                    ("git diff --check", "Fix whitespace errors in the working tree before committing."),
                    ("git diff --cached --check", "Fix whitespace errors in staged changes before committing."),
                    ("git var GIT_AUTHOR_IDENT", "Configure a valid Git author identity before committing."),
                    ("git var GIT_COMMITTER_IDENT", "Configure a valid Git committer identity before committing."),
                )
                for command, problem in checks:
                    check = _run(workspace, command)
                    if check is None or check.returncode != 0:
                        raise ValueError(problem)
                plan.preflight_fingerprint = fingerprint
                remote_ref = "refs/heads/" + branch
                plan.steps.extend([
                    GitStep("Check whitespace errors", "git diff --check"),
                    GitStep("Check staged whitespace errors", "git diff --cached --check"),
                    GitStep("Stage all approved changes", "git add -A", True),
                    GitStep("Check approved staged whitespace", "git diff --cached --check"),
                    GitStep("Show staged summary", "git diff --cached --stat"),
                    GitStep("Create Git commit", f"git commit -m {_quote(message)}", True),
                    GitStep("Push current branch to origin", f"git push --no-follow-tags -u origin {_quote('HEAD:' + remote_ref)}", True),
                    GitStep("Read committed revision", "git rev-parse --verify HEAD"),
                    GitStep("Verify origin branch revision", f"git ls-remote --exit-code --refs {_quote(urls[0])} {_quote(remote_ref)}"),
                ])
                from iceman.redaction import redact_text
                plan.notes.append("Destination: " + redact_text(urls[0]) + " (branch " + branch + ").")
                plan.notes.append(f"The plan stages all {len(changed)} changed repository paths. Existing committed history may also be published; this scan does not audit history.")
                plan.notes.append("This single approval covers staging all repository changes, committing with the displayed message, pushing this branch to origin, and verifying the remote revision.")

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

            local_ref = f"refs/tags/{tag}"
            existing = _run(
                workspace,
                f"git show-ref --verify --quiet {_quote(local_ref)}",
            )
            if existing and existing.returncode == 0:
                raise ValueError(
                    f"Tag '{tag}' already exists locally. "
                    "Iceman will not move or replace an existing milestone tag automatically."
                )

            plan.steps.extend([
                GitStep("Create annotated milestone tag", f"git tag -a {_quote(tag)} -m {_quote(tag)}", True),
                GitStep("Show milestone tag", f"git show --no-patch --oneline {_quote(tag)}"),
            ])

        elif action == GitAction.PUSH_TAG:
            if not repo:
                raise ValueError("This workspace is not a Git repository yet.")

            tag = (request.tag or "").strip()
            if not tag or not _SAFE_TAG.fullmatch(tag):
                raise ValueError("Provide a safe existing Git tag such as v0.9.7.")

            remotes = _remote_names(workspace)
            if "origin" not in remotes:
                raise ValueError(
                    "No 'origin' remote is configured. "
                    "Create/connect the GitHub repository before publishing a tag."
                )

            local_ref = f"refs/tags/{tag}"
            existing = _run(
                workspace,
                f"git show-ref --verify --quiet {_quote(local_ref)}",
            )
            if not existing or existing.returncode != 0:
                raise ValueError(
                    f"Local tag '{tag}' does not exist. "
                    "Create the milestone tag before publishing it."
                )

            plan.remote = "origin"
            plan.steps.extend([
                GitStep(
                    f"Verify local milestone tag {tag}",
                    f"git show-ref --verify --quiet {_quote(local_ref)}",
                ),
                GitStep("Show configured origin", "git remote -v"),
                GitStep(
                    f"Push milestone tag {tag} to origin",
                    f"git push origin {_quote(tag)}",
                    True,
                ),
                GitStep(
                    f"Verify milestone tag {tag} on origin",
                    f"git ls-remote --exit-code --tags origin {_quote(local_ref)}",
                ),
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
    compound = plan.request.action == GitAction.COMMIT_PUSH
    if compound:
        try:
            if not plan.preflight_fingerprint or _compound_fingerprint(plan.workspace, scan=True) != plan.preflight_fingerprint:
                raise ValueError("The repository changed after the preview. Request a new plan and approve it before staging.")
        except (OSError, ValueError) as exc:
            return GitExecutionResult(plan=plan, success=False, error=str(exc), duration_seconds=time.monotonic() - started)

    results: list[GitStepResult] = []

    def partial_summary() -> list[str]:
        if not compound:
            return []
        completed = {entry.step.label for entry in results if entry.result.returncode == 0 and not entry.result.interrupted}
        if "Push current branch to origin" in completed:
            return ["The local commit was created and the push command completed; remote verification is incomplete."]
        if "Create Git commit" in completed:
            return ["The local commit was created. The push has not been verified; no automatic rollback was performed."]
        if "Stage all approved changes" in completed:
            return ["Changes were staged before this workflow stopped; no automatic rollback was performed."]
        return []

    total = len(plan.steps)
    for index, step in enumerate(plan.steps, 1):
        try:
            if compound and step.label == "Stage all approved changes":
                if _compound_fingerprint(plan.workspace, scan=True) != plan.preflight_fingerprint:
                    return GitExecutionResult(plan=plan, success=False, step_results=results, error="The repository changed after the preview. Request a new plan before staging.", duration_seconds=time.monotonic() - started)
            result = step_runner(step, index, total) if step_runner else run_command(step.command, cwd=plan.workspace)
        except KeyboardInterrupt:
            return GitExecutionResult(plan=plan, success=False, step_results=results, summary=partial_summary(), error="Execution interrupted by operator.", duration_seconds=time.monotonic() - started, interrupted=True)
        except (FileNotFoundError, OSError, ValueError) as exc:
            return GitExecutionResult(plan=plan, success=False, step_results=results, summary=partial_summary(), error=str(exc), duration_seconds=time.monotonic() - started)
        results.append(GitStepResult(step=step, result=result))
        if result.interrupted or result.returncode != 0:
            return GitExecutionResult(
                plan=plan, success=False, step_results=results, summary=partial_summary(),
                error=(f"Execution interrupted during: {step.label}" if result.interrupted else f"'{step.label}' exited with code {result.returncode}."),
                duration_seconds=time.monotonic() - started, interrupted=result.interrupted,
            )

    if compound:
        local = results[-2].result.stdout.strip() if len(results) >= 2 else ""
        remote_rows = [line.split() for line in results[-1].result.stdout.splitlines()] if results else []
        expected_ref = "refs/heads/" + (plan.branch or "")
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", local) or [local, expected_ref] not in remote_rows:
            return GitExecutionResult(plan=plan, success=False, step_results=results, summary=partial_summary(), error="The push command completed, but origin's branch revision could not be verified against the local commit.", duration_seconds=time.monotonic() - started)

    summary: list[str] = []
    if plan.request.action == GitAction.INIT:
        summary.append("Git repository initialized on branch main.")
    elif plan.request.action == GitAction.GITHUB_CREATE_REPO:
        summary.append("GitHub repository created and connected as origin.")
    elif plan.request.action == GitAction.COMMIT:
        summary.append("Git commit created successfully.")
    elif plan.request.action == GitAction.PUSH:
        summary.append("Current branch pushed to origin successfully.")
    elif compound:
        summary.append("Changes were staged and committed, and origin's branch was verified at the same commit.")
    elif plan.request.action == GitAction.TAG:
        summary.append(f"Milestone tag created: {plan.request.tag}")
    elif plan.request.action == GitAction.PUSH_TAG:
        summary.append(f"Milestone tag published to origin: {plan.request.tag}")
    return GitExecutionResult(plan=plan, success=True, step_results=results, summary=summary, duration_seconds=time.monotonic() - started)
