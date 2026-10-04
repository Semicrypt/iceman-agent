from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from iceman.devops_actions import DevOpsAction
from iceman.session import SessionState


@dataclass(frozen=True)
class PromptInterpretation:
    original: str
    normalized: str
    corrections: tuple[tuple[str, str], ...] = ()
    used_context: bool = False
    requires_clarification: bool = False

    @property
    def changed(self) -> bool:
        return self.normalized.strip() != self.original.strip()


# Deliberately limited to Iceman's trusted DevOps vocabulary. We do not fuzzy
# correct arbitrary project names, image names, URLs, paths, or shell syntax.
_TECHNICAL_WORDS = {
    "docker",
    "container",
    "containers",
    "kubernetes",
    "deployment",
    "deployments",
    "service",
    "services",
    "pod",
    "pods",
    "replica",
    "replicas",
    "manifest",
    "manifests",
    "terraform",
    "validate",
    "initialize",
    "format",
    "context",
    "contexts",
    "restart",
    "scale",
    "build",
    "logs",
    "state",
    "plan",
    "minikube",
    "linux",
    "directory",
    "folder",
    "file",
    "files",
    "permission",
    "permissions",
    "executable",
    "process",
    "processes",
    "health",
    "healthy",
    "security",
    "compose",
    "rebuild",
    "optimize",
    "image",
    "images",
    "ports",
    "errors",
    "connectivity",
    "reachable",
    "accessible",
    "responding",
    "listener",
    "git",
    "github",
    "repository",
    "repositories",
    "commit",
    "push",
    "stage",
    "branch",
    "remote",
    "tag",
}

# High-confidence conversational/action vocabulary that is safe to repair before
# intent routing. These are operator-language words, never project/entity names.
# Entity names are resolved later against real Docker/Kubernetes/filesystem objects.
_INTENT_WORDS = {
    "show",
    "list",
    "check",
    "why",
    "reach",
    "access",
    "connect",
    "open",
    "working",
    "failing",
    "failed",
    "error",
    "errors",
    "make",
    "give",
    "change",
    "changes",
    "create",
    "generate",
    "add",
    "remove",
    "stop",
    "start",
    "restart",
    "run",
    "build",
    "deploy",
    "describe",
    "wait",
    "ready",
    "healthy",
    "format",
    "validate",
    "initialize",
    "explain",
    "back",
    "again",
    "execute",
    "using",
    "port",
    "commit",
    "push",
    "stage",
    "repository",
    "github",
    "branch",
    "remote",
    "tag",
}

_COMMON_MISSPELLINGS = {
    "terrafor": "terraform",
    "terrafom": "terraform",
    "terrafomr": "terraform",
    "terrafprm": "terraform",
    "kuberentes": "kubernetes",
    "kuberenetes": "kubernetes",
    "kubernete": "kubernetes",
    "kubernets": "kubernetes",
    "dockre": "docker",
    "docer": "docker",
    "dokcer": "docker",
    "deploymet": "deployment",
    "deplyment": "deployment",
    "deploymnet": "deployment",
    "contianer": "container",
    "contianers": "containers",
    "conatiner": "container",
    "repilca": "replica",
    "repilcas": "replicas",
    "permions": "permissions",
    "permisions": "permissions",
    "permision": "permission",
    "permissons": "permissions",
    "direcotry": "directory",
    "directry": "directory",
    "foler": "folder",
    "excutable": "executable",
    "replicaas": "replicas",
    "manfiest": "manifest",
    "manfiests": "manifests",
    "manifets": "manifests",
    "servcies": "services",
    "podds": "pods",
    "shwo": "show",
    "agian": "again",
    "agani": "again",
    "pleae": "please",
    "helath": "health",
    "healty": "healthy",
    "compsoe": "compose",
    "securty": "security",
    "rebuid": "rebuild",
    "optmize": "optimize",
    "mak": "make",
    "giv": "give",
    "bak": "back",
    "bakc": "back",
    "bac": "back",
    "cant": "can't",
    "cnt": "can't",
    "isnt": "isn't",
    "doesnt": "doesn't",
    "wont": "won't",
    "comit": "commit",
    "commt": "commit",
    "commmit": "commit",
    "pus": "push",
    "puhs": "push",
    "gitub": "github",
    "githb": "github",
    "gihub": "github",
    "repositry": "repository",
    "repoitory": "repository",
    "stge": "stage",
    "brnch": "branch",
    "chnages": "changes",
    "chages": "changes",
    "reac": "reach",
    "rech": "reach",
    "raech": "reach",
    "pot": "port",
    "prt": "port",
    "porrt": "port",
    "chec": "check",
    "chek": "check",
    "chck": "check",
    "contaner": "container",
    "containr": "container",
    "conainer": "container",
    "folde": "folder",
    "foldr": "folder",
    "execut": "execute",
    "execte": "execute",
    "replca": "replica",
    "replicas": "replicas",
    "deply": "deploy",
    "deplo": "deploy",
    "valdate": "validate",
    "intialize": "initialize",
    "initailize": "initialize",
    "shw": "show",
}

_DESTRUCTIVE_TERMS = {
    "delete",
    "destroy",
    "terminate",
    "prune",
    "wipe",
    "erase",
    "remove",
}

_REPEAT_PHRASES = {
    "again",
    "do it again",
    "do that again",
    "show it again",
    "show them again",
    "repeat it",
    "repeat that",
    "repeat the last one",
    "refresh it",
    "refresh that",
    "check again",
    "same again",
}


def _strip_polite_fillers(text: str) -> str:
    value = " ".join(text.strip().split())
    prefixes = (
        r"^can\s+you\s+please\s+",
        r"^could\s+you\s+please\s+",
        r"^would\s+you\s+please\s+",
        r"^can\s+you\s+",
        r"^could\s+you\s+",
        r"^would\s+you\s+",
        r"^please\s+",
    )
    changed = True
    while changed:
        changed = False
        for pattern in prefixes:
            updated = re.sub(pattern, "", value, flags=re.IGNORECASE)
            if updated != value:
                value = updated.strip()
                changed = True
                break
    value = re.sub(r"\s+(?:please|for\s+me)$", "", value, flags=re.IGNORECASE)
    return value.strip()


def _best_technical_word(token: str) -> str | None:
    lower = token.lower()
    vocabulary = _TECHNICAL_WORDS | _INTENT_WORDS
    if lower in vocabulary:
        return None
    if lower in _COMMON_MISSPELLINGS:
        return _COMMON_MISSPELLINGS[lower]

    # Fuzzy repair is intentionally restricted to Iceman control/DevOps
    # vocabulary. Custom resource names are grounded in a later entity pass.
    # Four-character intent words need a stricter threshold; shorter words are
    # only repaired through the explicit high-confidence misspelling table.
    if len(lower) < 4:
        return None

    scored = sorted(
        (
            (SequenceMatcher(None, lower, candidate).ratio(), candidate)
            for candidate in vocabulary
            if abs(len(candidate) - len(lower)) <= 3
        ),
        reverse=True,
    )
    if not scored:
        return None
    threshold = 0.90 if len(lower) == 4 else 0.86
    if scored[0][0] < threshold:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.05:
        return None
    return scored[0][1]


def _correct_technical_typos(text: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    # Do not fuzzy-normalize destructive requests. Those must reach the normal
    # safety/fallback path without Iceman guessing what destructive verb meant.
    words = {item.lower() for item in re.findall(r"[A-Za-z]+", text)}
    if words & _DESTRUCTIVE_TERMS:
        return text, ()

    corrections: list[tuple[str, str]] = []

    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        replacement = _best_technical_word(token)
        if replacement is None:
            return token
        corrections.append((token, replacement))
        return replacement

    normalized = re.sub(r"\b[A-Za-z]{3,}\b", repl, text)
    return normalized, tuple(corrections)




def _repair_common_grammar(text: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Repair a small set of high-confidence conversational grammar patterns.

    These rewrites only produce trusted natural-language intent text; they never
    produce shell syntax. Material rewrites are confirmed by the operator.
    """
    value = " ".join(text.strip().split())
    lower = value.lower().strip().rstrip("?!.")
    repairs: list[tuple[str, str]] = []

    patterns: tuple[tuple[str, str], ...] = (
        # Colloquial/broken connectivity phrasing, with an optional port.
        (r"^(?:why\s+)?([A-Za-z0-9_.-]+)\s+no\s+(?:dey\s+)?(?:open|opening|respond|responding)(?:\s+(?:on\s+)?port\s+(\d{1,5}))?$", r"why can't I reach \1 on port \2"),
        (r"^why\s+([A-Za-z0-9_.-]+)\s+no\s+(?:dey\s+)?work(?:ing)?$", r"why is \1 not working"),
        (r"^(?:make|set|change)\s+([A-Za-z0-9_.-]+)\s+(\d{1,3})\s+replicas?$", r"scale \1 to \2 replicas"),
        (r"^give\s+([^\s]+)\s+(?:execute|executable)\s+permissions?$", r"make \1 executable"),
        (r"^go\s+back\s+(?:to\s+)?(?:the\s+)?(?:project\s+)?folder$", r"go back one folder"),
        (r"^(?:check|show)\s+(?:the\s+)?(?:kubernetes|k8s)\s+pods?$", r"show pods"),
        (r"^terraform\s+plan\s+(?:no\s+working|not\s+working|broken)$", r"why terraform plan failing"),
    )
    for pattern, replacement in patterns:
        if re.match(pattern, lower, flags=re.IGNORECASE):
            updated = re.sub(pattern, replacement, lower, flags=re.IGNORECASE)
            # Optional regex groups in replacement strings become empty text;
            # remove dangling "on port" when no port was supplied.
            updated = re.sub(r"\s+on\s+port\s*$", "", updated, flags=re.IGNORECASE).strip()
            if updated != lower:
                repairs.append((value, updated))
                return updated, tuple(repairs)
    return value, ()

def _previous_devops_prompt(state: SessionState) -> str | None:
    plan = state.last_devops_plan
    if plan is None:
        return None

    request = plan.request
    action = request.action
    component = request.component or plan.inferred_component or state.last_devops_component
    image = request.image or plan.inferred_image or state.last_devops_image

    simple = {
        DevOpsAction.DOCKER_LIST: "show me the containers",
        DevOpsAction.DOCKER_IMAGES: "show me the docker images",
        DevOpsAction.DOCKER_COMPOSE_STATUS: "show docker compose status",
        DevOpsAction.K8S_CONTEXT: "show kubernetes context",
        DevOpsAction.K8S_CONTEXTS: "show kubernetes contexts",
        DevOpsAction.K8S_VALIDATE: "validate my kubernetes manifests",
        DevOpsAction.K8S_PODS: "show pods",
        DevOpsAction.K8S_DEPLOYMENTS: "show deployments",
        DevOpsAction.K8S_SERVICES: "show services",
        DevOpsAction.TERRAFORM_INIT: "initialize terraform",
        DevOpsAction.TERRAFORM_FMT: "format my terraform",
        DevOpsAction.TERRAFORM_VALIDATE: "validate my terraform",
        DevOpsAction.TERRAFORM_PLAN: "show me the terraform plan",
        DevOpsAction.TERRAFORM_EXPLAIN_PLAN: "explain terraform plan",
        DevOpsAction.TERRAFORM_STATE: "show terraform state",
    }
    if action in simple:
        return simple[action]

    if action == DevOpsAction.K8S_SCALE and component and request.replicas:
        return f"scale {component} to {request.replicas} replicas"
    if action == DevOpsAction.K8S_ROLLOUT and component:
        return f"wait for {component} ready"
    if action == DevOpsAction.K8S_RESTART and component:
        return f"restart {component} deployment"
    if action == DevOpsAction.K8S_LOGS and component:
        return f"show kubernetes logs for {component}"
    if action == DevOpsAction.K8S_DESCRIBE and component:
        return f"describe deployment {component}"
    if action == DevOpsAction.K8S_DEPLOY and component:
        suffix = f" to {request.context}" if request.context else ""
        return f"deploy {component}{suffix}"

    if action == DevOpsAction.DOCKER_BUILD and component:
        suffix = f" image {image}" if image else ""
        return f"build {component}{suffix}"
    if action == DevOpsAction.DOCKER_RUN:
        subject = component or image or "it"
        suffix = f" on port {request.port}" if request.port else ""
        return f"run {subject}{suffix}"
    if action == DevOpsAction.DOCKER_LOGS and component:
        return f"show {component} logs"
    if action == DevOpsAction.DOCKER_STOP and component:
        return f"stop container {component}"
    if action == DevOpsAction.DOCKER_RESTART and component:
        return f"restart container {component}"
    if action == DevOpsAction.DOCKER_DIAGNOSE and component:
        return f"diagnose {component}"
    if action == DevOpsAction.DOCKER_HEALTH and component:
        return f"check {component} healthy"
    if action == DevOpsAction.DOCKER_PORTS and request.port:
        return f"why can't I reach port {request.port}"
    if action == DevOpsAction.DOCKER_CONNECTIVITY and component:
        suffix = f" on port {request.port}" if request.port else ""
        return f"why can't I reach {component}{suffix}"
    if action == DevOpsAction.DOCKER_SECURITY:
        suffix = f" {component}" if component else ""
        return f"check docker security{suffix}"
    if action == DevOpsAction.DOCKER_OPTIMIZE and component:
        return f"make {component} image smaller"
    if action == DevOpsAction.DOCKER_REBUILD_RESTART and component:
        return f"rebuild and restart {component}"

    return None


def interpret_prompt(text: str, state: SessionState) -> PromptInterpretation:
    original = text
    normalized = _strip_polite_fillers(text)
    normalized, corrections = _correct_technical_typos(normalized)
    normalized, grammar_repairs = _repair_common_grammar(normalized)
    corrections = corrections + grammar_repairs
    lower = normalized.lower().strip().rstrip("?!. ")
    used_context = False
    requires_clarification = bool(corrections)

    # A harmless explicit "again" suffix is deterministic: "show pods again"
    # can be treated as "show pods" without needing a pronoun resolution.
    if lower.endswith(" again") and lower not in _REPEAT_PHRASES:
        normalized = re.sub(r"\s+again[?!. ]*$", "", normalized, flags=re.IGNORECASE).strip()
        lower = normalized.lower().strip().rstrip("?!. ")

    if lower in _REPEAT_PHRASES:
        previous = _previous_devops_prompt(state)
        if previous:
            normalized = previous
            used_context = True
            requires_clarification = True
            lower = normalized.lower()

    # Follow-up to a previous Kubernetes scale request.
    scale_followup = re.match(
        r"^(?:make|change)\s+(?:it|that)\s+(?:to\s+)?(\d{1,3})(?:\s+replicas?)?\s+instead$",
        lower,
    )
    if scale_followup and state.last_devops_plan is not None:
        previous = state.last_devops_plan
        if previous.request.action == DevOpsAction.K8S_SCALE:
            component = (
                previous.request.component
                or previous.inferred_component
                or state.last_devops_component
            )
            if component:
                normalized = f"scale {component} to {int(scale_followup.group(1))} replicas"
                used_context = True
                requires_clarification = True

    # Common conversational continuation that does not need previous parameters.
    lower_now = normalized.lower().strip().rstrip("?!. ")
    what_about = re.match(r"^what\s+about\s+(pods|deployments|services)$", lower_now)
    if what_about:
        normalized = f"show {what_about.group(1)}"

    return PromptInterpretation(
        original=original,
        normalized=normalized,
        corrections=corrections,
        used_context=used_context,
        requires_clarification=requires_clarification,
    )
