"""High-confidence conversational intents; never grants execution authority."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ConversationRoute:
    kind: str
    intent: str
    normalized_text: str
    confidence: float = 1.0
    payload: dict[str, Any] = field(default_factory=dict)
    message: str = ""


_ALIASES = {"gti": "git", "dockr": "docker", "terrafom": "terraform", "kuberenetes": "kubernetes", "kubernets": "kubernetes", "stgae": "stage", "proejct": "project", "porject": "project", "prject": "project", "reveiw": "review", "anaylze": "analyze", "analize": "analyze", "imporve": "improve", "improvments": "improvements", "sugestions": "suggestions", "suggestons": "suggestions", "securty": "security", "vulnerabilites": "vulnerabilities", "prodution": "production", "redy": "ready"}
_COMMANDS = {"git": {"status", "diff", "stage", "stgae", "add", "commit", "push", "log", "branch", "remote", "tag"}, "docker": {"status", "ps", "inspect", "build", "run", "logs", "compose", "images", "containers", "restart", "health", "ports"}, "terraform": {"init", "fmt", "validate", "plan", "state", "providers", "apply", "destroy"}, "kubernetes": {"status", "pods", "services", "deployments", "logs", "contexts", "namespaces", "rollout", "scale", "restart", "delete"}}
_QUOTE = re.compile(r'''(?<![\w])(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''')
_CONNECTOR = r"(?:\s*,\s*(?:(?:and|then)\s+)?|\s+(?:and\s+then|and|then)\s+)"
_MUTATION = r"(?:stage|add|commit|push|deploy|apply|destroy|delete|remove|run|execute|restart|rebuild|scale|install|publish|release|copy|move|chmod|reset|clean|prune|create|write|fix)"
_CHAIN = re.compile(_CONNECTOR + r"(?:(?:git|docker|kubectl|terraform)\s+)?" + _MUTATION + r"\b", re.I)


def _mask(text: str) -> str:
    return _QUOTE.sub(lambda m: "\ufff0" * len(m.group()), text)


def normalize_request(text: str) -> str:
    """Correct reviewed intent typos, preserving literal messages, paths and names."""
    raw = text.strip()
    masked = _mask(raw)
    if re.match(r"^(?:please\s+)?(?:copy|move|rename|chmod|cat|find|show\s+(?:file|folder|directory))\b", masked, re.I):
        return raw
    literal = re.search(r"\b(?:commit|comit|commt|commmit)\b.*?\b(?:message|as)\s+|\b(?:commit|comit|commt|commmit)\b.*?\s-m\s+", masked, re.I)
    tokens = list(re.finditer(r"\S+", masked[:literal.end() if literal else len(raw)]))
    changes = []
    argument_start = len(tokens)
    for index, token in enumerate(tokens[:-1]):
        tool = _ALIASES.get(token.group().lower(), token.group().lower())
        if tool in _COMMANDS and tokens[index + 1].group().lower() in _COMMANDS[tool]:
            argument_start = index + 2
            break
    for i, token in enumerate(tokens):
        if i >= argument_start:
            continue
        match = re.fullmatch(r"([A-Za-z]+)([?,!:]?)", token.group())
        if not match:
            continue
        replacement = _ALIASES.get(match.group(1).lower())
        if not replacement:
            continue
        previous = tokens[i - 1].group().lower().rstrip(",") if i else ""
        following = tokens[i + 1].group().lower().rstrip(",?!") if i + 1 < len(tokens) else ""
        if replacement in _COMMANDS:
            if following not in _COMMANDS[replacement]:
                continue
            if i and previous not in {"please", "show", "check", "inspect", "review", "my", "the", "and", "then"}:
                continue
        elif replacement == "stage":
            if following not in {"all", "everything"} or (i and previous not in {"please", "git", "gti", "and", "then"}):
                continue
        elif previous in {"logs", "inspect", "describe", "copy", "move", "rename", "show", "file", "folder", "directory", "branch", "container", "service", "pod", "namespace", "context", "named"}:
            continue
        changes.append((token.start(), token.start() + len(match.group(1)), replacement))
    for start, end, replacement in reversed(changes):
        raw = raw[:start] + replacement + raw[end:]
    return raw


def _clarify(text: str, message: str) -> ConversationRoute:
    return ConversationRoute("clarify", "clarification", text, 0.0, message=message)


def _message(value: str) -> str | None:
    if any(c in value for c in "\x00\r\n"):
        return None
    value = value.strip()
    if not value:
        return None
    if value[0] in "\"'":
        quote = value[0]
        pattern = re.escape(quote) + r"(?:\\.|[^" + re.escape(quote) + r"\\])*" + re.escape(quote)
        if not re.fullmatch(pattern, value):
            return None
        return re.sub(r"\\([\\" + re.escape(quote) + r"])", r"\1", value[1:-1]) or None
    if re.search(r"\b(?:and|then)\b|[;|<>\n\r]|\x60|&&|\$\(", value, re.I):
        return None
    return value


def _git(text: str) -> ConversationRoute | None:
    actions = {a.lower() for a in re.findall(r"\b(stage|add|commit|push)\b", _mask(text), re.I)}
    if "commit" not in actions or not actions.intersection({"stage", "add", "push"}):
        return None
    error = "I need one complete stage/commit/push workflow and an exact commit message. Please separate any other actions; nothing has been executed."
    work = re.sub(r"^(?:please\s+|can you\s+|could you\s+)", "", text, flags=re.I).strip()
    # Preserve the previously supported explicit combined workflow form.
    legacy = re.fullmatch(r"(?:git\s+)?commit\s+and\s+push\s+with\s+(?:the\s+)?message\s+(.+)", work, re.I | re.S)
    if legacy:
        message = _message(legacy.group(1))
        if message:
            return ConversationRoute("git_workflow", "stage_commit_push", text, payload={"stage_all": True, "commit_message": message, "push": True})
        return _clarify(text, error)
    tail = re.search(_CONNECTOR + r"(?:git\s+)?push(?:\s+(?:(?:to\s+)?(?:github|origin|the remote)))?\s*[.!]?\s*$", _mask(work), re.I)
    if not tail:
        return _clarify(text, error)
    work = work[:tail.start()].strip()
    stage_all = False
    stage = re.match(r"^(?:git\s+)?(?:stage|add)\s+(?:everything|all(?:\s+(?:files|changes))?|\.)" + _CONNECTOR, work, re.I)
    if stage:
        stage_all = True
        work = work[stage.end():]
    elif actions.intersection({"stage", "add"}):
        return _clarify(text, error)
    commit = re.fullmatch(r"(?:git\s+)?commit(?:\s+(?P<scope>these changes|all changes|my changes|the changes|staged changes))?\s+(?:with\s+(?:the\s+)?message|message|as|-m)\s+(?P<message>.+)", work, re.I | re.S)
    if not commit:
        return _clarify(text, error)
    message = _message(commit.group("message"))
    if message is None:
        return _clarify(text, error + " Keep quotes around a message containing extra conjunctions.")
    if (commit.group("scope") or "").lower() in {"these changes", "all changes", "my changes", "the changes"}:
        stage_all = True
    return ConversationRoute("git_workflow", "stage_commit_push", text, payload={"stage_all": stage_all, "commit_message": message, "push": True})


_FOLLOWUPS = {"why": "why", "why is that": "why", "explain why": "why", "go deeper": "deeper", "tell me more": "deeper", "more detail": "deeper", "explain more": "deeper", "what else": "else", "anything else": "else", "show me the evidence": "evidence", "show the evidence": "evidence", "show evidence": "evidence", "where is the evidence": "evidence", "what should i do first": "first", "what do i do first": "first", "where should i start": "first", "fix the highest risk": "fix_highest_risk", "fix the biggest risk": "fix_highest_risk", "fix the top issue": "fix_highest_risk"}


def route_conversation(text: str) -> ConversationRoute | None:
    """Clarifications are terminal. Followups need caller-validated session evidence."""
    normalized = normalize_request(text)
    lowered = re.sub(r"\s+", " ", normalized.lower()).strip(" .?!")
    followup = _FOLLOWUPS.get(re.sub(r"^please\s+", "", lowered))
    if followup:
        return ConversationRoute("followup", followup, normalized)
    workflow = _git(normalized)
    if workflow:
        return workflow
    masked = _mask(lowered)
    if re.search(r"\b(?:(?:terraform|terrafom)\s+(?:apply|destroy|aply|destory)|kubectl\s+(?:delete|delte)|git\s+(?:push\b.*(?:--force|-f\b)|reset\s+--hard|clean\b)|docker\s+(?:system\s+prune|restrat))\b", masked):
        return _clarify(normalized, "I'm not programmed to carry out that destructive or ambiguous operation. I can inspect the current configuration and help plan a supported alternative.")
    if re.fullmatch(r"(?:please\s+)?rebuild\s+and\s+restart\s+[a-z0-9_.-]+", masked):
        return None
    # Reject unknown suffixes on mutation requests as well as known action chains.
    mutation_head = re.search(r"\b" + _MUTATION + r"\b", masked)
    if _CHAIN.search(masked) or re.search(r"(?:&&|;|\|)\s*\w", masked) or (
        mutation_head and re.search(r"\s+(?:and|then)\s+", masked)
        and not re.search(r"\b(?:deployment|requests)\s+and\s+(?:service|limits)\b", masked)
    ):
        return _clarify(normalized, "That combines actions I can't safely plan as one workflow yet. Please specify one supported workflow; nothing has been executed.")
    if re.match(r"^(?:please\s+)?(?:gti\s+|git\s+)?(?:stge|stga|psuh|delte|delet|destory|aply)\b", masked):
        return _clarify(normalized, "That action looks misspelled and could change state. Please spell out the intended action so I can show an exact plan.")
    words = set(re.findall(r"[a-z]+", masked))
    project = bool(words & {"project", "workspace", "repository", "repo", "codebase"}) or bool(re.search(r"\b(?:current|this|my)\s+(?:folder|directory)\b", masked))
    bare = bool(re.fullmatch(r"(?:please )?(?:(?:what (?:do you think )?(?:i |we )?should (?:i |we )?(?:improve|do better))|(?:any (?:good )?(?:suggestion|suggestions|recommendations))|(?:what(?:'s| is| do you think is) missing)|(?:(?:is (?:this|it|this project) )?production ready)|(?:(?:review|check|scan) (?:security|vulnerabilities)))", masked))
    assessment = project or bare
    if words & {"security", "vulnerability", "vulnerabilities", "secure", "audit"} and assessment:
        intent = "security"
    elif "production" in words and words & {"ready", "readiness"} and assessment:
        intent = "readiness"
    elif words & {"missing", "lacking", "lack"} and assessment:
        intent = "missing"
    elif words & {"improve", "improvements", "improvement", "optimize", "optimise"} and assessment:
        intent = "improve"
    elif words & {"suggest", "suggestion", "suggestions", "recommend", "recommendation", "recommendations"} and assessment:
        intent = "suggestions"
    elif project and words & {"review", "assess", "assessment", "analyze", "analyse", "inspect", "think", "health"}:
        intent = "review"
    else:
        return None
    if re.search(r"\b" + _MUTATION + r"\b", masked) and not re.search(r"\b(?:should|could|would|before|without|readiness|ready)\b", masked):
        return _clarify(normalized, "I can review the project and recommend changes. Please request a specific supported change so I can preview it for approval.")
    return ConversationRoute("assessment", intent, normalized)
