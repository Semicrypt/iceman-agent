from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from iceman.devops_actions import DevOpsAction
from iceman.linux_actions import LinuxAction
from iceman.runner import run_command


@dataclass(frozen=True)
class EntityResolution:
    original: str
    kind: str
    resolved: str | None = None
    candidates: tuple[str, ...] = ()
    confidence: str = "low"

    @property
    def changed(self) -> bool:
        return bool(self.resolved and self.resolved != self.original)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _workspace_names(workspace: Path) -> list[str]:
    try:
        return sorted(
            item.name
            for item in workspace.iterdir()
            if item.name not in {".git", ".terraform", "node_modules", ".venv"}
        )
    except OSError:
        return []


def _manifest_resource_names(workspace: Path, kind: str = "Deployment") -> list[str]:
    root = workspace / "k8s"
    if not root.exists() or not root.is_dir():
        return []
    names: list[str] = []
    for path in sorted(list(root.glob("*.yml")) + list(root.glob("*.yaml"))):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not re.search(rf"(?mi)^kind:\s*{re.escape(kind)}\s*$", text):
            continue
        match = re.search(
            r"(?ms)^metadata:\s*\n(?:^[ \t]+[^\n]*\n)*?^[ \t]+name:\s*['\"]?([A-Za-z0-9_.-]+)",
            text,
        )
        if match:
            names.append(match.group(1))
    return _dedupe(names)


def _docker_container_names(workspace: Path) -> list[str]:
    try:
        result = run_command('docker ps -a --format "{{.Names}}"', cwd=workspace)
    except Exception:
        return []
    if result.returncode != 0:
        return []
    return _dedupe(result.stdout.splitlines())


def _docker_image_names(workspace: Path) -> list[str]:
    try:
        result = run_command(
            'docker images --format "{{.Repository}}:{{.Tag}}"',
            cwd=workspace,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    return [value for value in _dedupe(result.stdout.splitlines()) if value != "<none>:<none>"]


def _kube_context_names(workspace: Path) -> list[str]:
    try:
        result = run_command("kubectl config get-contexts -o name", cwd=workspace)
    except Exception:
        return []
    if result.returncode != 0:
        return []
    return _dedupe(result.stdout.splitlines())


def _score(original: str, candidate: str) -> float:
    a = original.casefold()
    b = candidate.casefold()
    if a == b:
        return 1.0
    score = SequenceMatcher(None, a, b).ratio()
    # A near-complete prefix/suffix is a common CLI typo (missing final char).
    if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):
        score = max(score, min(len(a), len(b)) / max(len(a), len(b)))
    return score


def resolve_entity(original: str, candidates: list[str], *, kind: str) -> EntityResolution:
    original = original.strip()
    candidates = _dedupe(candidates)
    if not original or not candidates:
        return EntityResolution(original=original, kind=kind)

    exact = next((item for item in candidates if item.casefold() == original.casefold()), None)
    if exact is not None:
        return EntityResolution(
            original=original,
            kind=kind,
            resolved=exact,
            confidence="high",
        )

    ranked = sorted(
        ((_score(original, item), item) for item in candidates),
        key=lambda pair: (-pair[0], pair[1]),
    )
    top_score, top_name = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0

    # Strong, unique typo repair. The operator will still confirm the corrected
    # interpretation before the trusted action can execute.
    if top_score >= 0.84 and (top_score - second_score >= 0.07 or top_score >= 0.93):
        return EntityResolution(
            original=original,
            kind=kind,
            resolved=top_name,
            confidence="high",
        )

    likely = tuple(name for score, name in ranked[:5] if score >= 0.62)
    if likely:
        return EntityResolution(
            original=original,
            kind=kind,
            candidates=likely,
            confidence="medium",
        )
    return EntityResolution(original=original, kind=kind)


def _docker_candidates(workspace: Path, original: str) -> list[str]:
    local = _workspace_names(workspace)
    first = resolve_entity(original, local, kind="Docker container/component")
    if first.resolved and first.confidence == "high":
        return local
    return _dedupe(local + _docker_container_names(workspace))



def normalize_existing_resource_separators(text: str, workspace: Path) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Normalize spacing/hyphen/underscore variants for *existing* resources.

    This is intentionally grounded against resources that actually exist. It is
    used before intent routing so a phrase like ``broken backend`` can match an
    existing Docker container named ``broken-backend``. Creation requests are
    excluded because a user may intentionally be introducing a new name.
    """
    value = " ".join(text.strip().split())
    lower = value.lower()
    if re.search(r"\b(?:create|make|generate|new)\b", lower):
        return value, ()

    candidates: list[str] = []
    # Only query the runtime sources that are plausibly relevant to the request.
    dockerish = any(
        token in lower
        for token in (
            "docker", "container", "backend", "frontend", "stop",
            "restart", "health", "healthy", "logs", "reach",
            "connect", "port", "crash", "fail", "running",
        )
    )
    k8sish = any(
        token in lower
        for token in (
            "kubernetes", "k8s", "deployment", "pod", "service",
            "replica", "scale", "rollout", "context", "minikube",
        )
    )

    candidates.extend(_workspace_names(workspace))
    if dockerish:
        candidates.extend(_docker_container_names(workspace))
    if k8sish:
        candidates.extend(_manifest_resource_names(workspace, "Deployment"))
        candidates.extend(_kube_context_names(workspace))

    # Prefer longer/more specific names so ``minerva backend`` resolves before
    # the shorter ``backend`` candidate. Single-token candidates do not need
    # separator normalization and are handled by the normal entity matcher.
    unique = sorted(_dedupe(candidates), key=lambda item: (-len(item), item))
    corrections: list[tuple[str, str]] = []
    for candidate in unique:
        tokens = [part for part in re.split(r"[-_\s]+", candidate) if part]
        if len(tokens) < 2:
            continue
        pattern = (
            r"(?<![A-Za-z0-9_.-])"
            + r"[\s_-]+".join(re.escape(part) for part in tokens)
            + r"(?![A-Za-z0-9_.-])"
        )
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if not match:
            continue
        original = match.group(0)
        if original.casefold() == candidate.casefold():
            continue
        value = value[: match.start()] + candidate + value[match.end() :]
        corrections.append((original, candidate))
        # One grounded resource repair per prompt is enough and avoids changing
        # unrelated prose that happens to contain similar words.
        break
    return value, tuple(corrections)

def candidates_for_devops_entity(
    workspace: Path,
    action: DevOpsAction,
    original: str,
) -> EntityResolution:
    if action in {
        DevOpsAction.DOCKER_LOGS,
        DevOpsAction.DOCKER_STOP,
        DevOpsAction.DOCKER_RESTART,
        DevOpsAction.DOCKER_DIAGNOSE,
        DevOpsAction.DOCKER_HEALTH,
        DevOpsAction.DOCKER_CONNECTIVITY,
        DevOpsAction.DOCKER_REBUILD_RESTART,
    }:
        return resolve_entity(
            original,
            _docker_candidates(workspace, original),
            kind="Docker container/component",
        )

    if action in {DevOpsAction.DOCKER_BUILD, DevOpsAction.DOCKER_RUN, DevOpsAction.DOCKER_OPTIMIZE, DevOpsAction.DOCKER_SECURITY}:
        return resolve_entity(
            original,
            _workspace_names(workspace),
            kind="project component",
        )

    if action in {
        DevOpsAction.K8S_DEPLOY,
        DevOpsAction.K8S_ROLLOUT,
        DevOpsAction.K8S_SCALE,
        DevOpsAction.K8S_LOGS,
        DevOpsAction.K8S_DESCRIBE,
        DevOpsAction.K8S_RESTART,
    }:
        names = _dedupe(_manifest_resource_names(workspace, "Deployment") + _workspace_names(workspace))
        return resolve_entity(original, names, kind="Kubernetes deployment/component")

    return EntityResolution(original=original, kind="resource")


def candidates_for_context(workspace: Path, original: str) -> EntityResolution:
    return resolve_entity(
        original,
        _kube_context_names(workspace),
        kind="Kubernetes context",
    )


def candidates_for_linux_path(
    workspace: Path,
    action: LinuxAction,
    original: str,
) -> EntityResolution:
    # Never rewrite names for creation destinations; the user may intentionally
    # be introducing a new path.
    if action in {LinuxAction.MAKE_DIR, LinuxAction.CREATE_FILE}:
        return EntityResolution(original=original, kind="path")

    # Explicit paths are handled by the normal workspace/path-safety layer.
    # Do not fuzzy-rewrite path components.
    if "/" in original or original in {".", ".."}:
        return EntityResolution(original=original, kind="path")

    value = original.strip()
    folded = value.casefold()
    names = _workspace_names(workspace)

    # First prefer an exact name ignoring case. This safely resolves things like
    # `dockerfile` -> `Dockerfile` only when the workspace contains one unique
    # match. A case-sensitive filesystem can legally contain both variants, so
    # multiple matches must remain ambiguous.
    exact_matches = [
        name for name in names
        if name.casefold() == folded
    ]

    if len(exact_matches) == 1:
        resolved = exact_matches[0]
        return EntityResolution(
            original=original,
            kind="workspace path",
            resolved=resolved,
            confidence="high",
        )

    if len(exact_matches) > 1:
        return EntityResolution(
            original=original,
            kind="workspace path",
            candidates=tuple(exact_matches[:5]),
            confidence="high",
        )

    # Extension-aware shorthand is deliberately limited to top-level files and
    # only when the user supplied a bare filename token. For example:
    #
    #   readme -> README.md
    #
    # If README.md and README.txt both exist, refuse to guess.
    if not Path(value).suffix:
        try:
            excluded = {".git", ".terraform", "node_modules", ".venv"}
            file_names = sorted(
                item.name
                for item in workspace.iterdir()
                if item.name not in excluded and item.is_file()
            )
        except OSError:
            file_names = []

        stem_matches = [
            name for name in file_names
            if Path(name).stem.casefold() == folded
        ]

        if len(stem_matches) == 1:
            return EntityResolution(
                original=original,
                kind="workspace file",
                resolved=stem_matches[0],
                confidence="high",
            )

        if len(stem_matches) > 1:
            return EntityResolution(
                original=original,
                kind="workspace file",
                candidates=tuple(stem_matches[:5]),
                confidence="high",
            )

    # Fall back to the existing bounded entity matcher for ordinary typo/fuzzy
    # handling. It still operates only on real entries in the current workspace.
    return resolve_entity(
        original,
        names,
        kind="workspace path",
    )


def candidates_for_image(workspace: Path, original: str) -> EntityResolution:
    return resolve_entity(original, _docker_image_names(workspace), kind="Docker image")


def candidates_for_workspace_component(workspace: Path, original: str) -> EntityResolution:
    return resolve_entity(original, _workspace_names(workspace), kind="project component")
