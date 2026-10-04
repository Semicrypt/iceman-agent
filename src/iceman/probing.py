from __future__ import annotations

import ipaddress
import json
import re
import shlex
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from iceman.runner import run_command


_TEXT_SUFFIXES = {
    ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml",
    ".env", ".txt", ".md", ".conf", ".ini", ".py",
}

_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "dist", "build", ".next",
    ".cache", "coverage", "__pycache__", ".terraform", "vendor",
}

_SKIP_FILENAMES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "pnpm-lock.yml",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
}

_NAME_COLUMNS = (
    "name",
    "label",
    "title",
    "service_name",
    "check_name",
    "target_name",
)

_URL_COLUMNS = (
    "url",
    "endpoint",
    "uri",
    "target_url",
    "health_url",
    "website",
    "address",
    "target",
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_URL_RE = re.compile(r'https?://[^\s\'"<>`]+', re.IGNORECASE)

_BLOCKED_METADATA_HOSTS = {
    "169.254.169.254",   # AWS / many clouds
    "100.100.100.200",   # Alibaba Cloud metadata
    "metadata.google.internal",
}


@dataclass
class TargetCandidate:
    subject: str
    url: str
    source_file: str
    line_number: int | None
    score: int
    context: str
    source_kind: str = "file"


@dataclass
class TargetResolution:
    subject: str
    resolved: bool
    selected: TargetCandidate | None = None
    candidates: list[TargetCandidate] = field(default_factory=list)
    confidence: str = "low"
    note: str | None = None


@dataclass
class ProbeStep:
    name: str
    success: bool | None
    detail: str
    latency_ms: int | None = None
    status_code: int | None = None


@dataclass
class SignalEmitter:
    compose_service: str
    container_id: str
    matches: int


@dataclass
class ExecutionContextProbe:
    context: str
    compose_service: str | None = None
    container_id: str | None = None
    runtime: str | None = None
    steps: list[ProbeStep] = field(default_factory=list)
    note: str | None = None

    @property
    def http_status(self) -> int | None:
        for step in self.steps:
            if step.name == "HTTP" and step.status_code is not None:
                return step.status_code
        return None


@dataclass
class ApplicationCheckProfile:
    source_file: str
    line_number: int
    client: str
    method: str
    timeout_ms: int | None
    headers: dict[str, str]
    omitted_header_names: list[str]
    follow_redirects: bool | None
    accepted_status_min: int | None
    accepted_status_max: int | None
    success_policy: str
    confidence: str
    fidelity: str
    evidence: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class ApplicationCheckReplay:
    compose_service: str
    container_id: str
    client: str
    runtime: str | None = None
    status_code: int | None = None
    latency_ms: int | None = None
    accepted_by_policy: bool | None = None
    error: str | None = None
    fidelity: str = "low"
    note: str | None = None


@dataclass
class HttpEvidenceSample:
    attempt: int
    status_code: int | None
    accepted: bool | None
    latency_ms: int | None
    detail: str


@dataclass
class EvidenceStability:
    context: str
    samples: list[HttpEvidenceSample] = field(default_factory=list)
    classification: str = "insufficient"
    success_count: int = 0
    failure_count: int = 0
    unknown_count: int = 0
    status_counts: dict[int, int] = field(default_factory=dict)
    summary: str = "No HTTP evidence."


@dataclass
class RootCauseInvestigation:
    subject: str
    workspace: Path
    resolution: TargetResolution
    url: str | None = None
    host: str | None = None
    port: int | None = None
    dns_addresses: list[str] = field(default_factory=list)
    steps: list[ProbeStep] = field(default_factory=list)
    likely_cause: str = "Unable to classify."
    confidence: str = "low"
    blocked: bool = False
    healthy_endpoint: bool = False
    signal_emitter: SignalEmitter | None = None
    container_probe: ExecutionContextProbe | None = None
    context_comparison: str | None = None
    request_replay_note: str | None = None
    check_profile: ApplicationCheckProfile | None = None
    check_replay: ApplicationCheckReplay | None = None
    reproduction_conclusion: str | None = None
    host_http_samples: list[HttpEvidenceSample] = field(default_factory=list)
    container_http_samples: list[HttpEvidenceSample] = field(default_factory=list)
    source_http_samples: list[HttpEvidenceSample] = field(default_factory=list)
    host_stability: EvidenceStability | None = None
    container_stability: EvidenceStability | None = None
    source_stability: EvidenceStability | None = None
    session_host_stability: EvidenceStability | None = None
    session_container_stability: EvidenceStability | None = None
    session_source_stability: EvidenceStability | None = None


def _iter_text_files(workspace: Path):
    count = 0
    for path in workspace.rglob("*"):
        if count >= 4000:
            break
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.name in _SKIP_FILENAMES or path.suffix.lower() == ".lock":
            continue
        if path.name.endswith(".min.js") or path.name.endswith(".min.css"):
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES and path.name not in {
            ".env", ".env.example", "Dockerfile", "docker-compose.yml",
            "docker-compose.yaml",
        }:
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
        except OSError:
            continue
        count += 1
        yield path


def _candidate_score(
    subject: str,
    lines: list[str],
    subject_index: int,
    url_index: int,
) -> int:
    score = 0
    distance = abs(subject_index - url_index)

    if distance == 0:
        score += 12
    elif distance <= 2:
        score += 8
    elif distance <= 5:
        score += 4
    else:
        score += 1

    subject_line = lines[subject_index].lower()
    if any(token in subject_line for token in (
        "name", "label", "title", "service", "check", "target",
    )):
        score += 4

    nearby = "\n".join(
        lines[max(0, min(subject_index, url_index) - 2):
              min(len(lines), max(subject_index, url_index) + 3)]
    ).lower()

    if any(token in nearby for token in (
        "url", "endpoint", "website", "health", "monitor", "check",
    )):
        score += 3

    if subject.lower() in nearby:
        score += 2

    return score



def normalize_investigation_subject(value: str) -> str:
    """
    Convert explicit-command and conversational forms into the monitored
    subject name.

    Examples:
      Openai                       -> Openai
      why Openai is down           -> Openai
      why is Openai down?          -> Openai
      investigate why Openai is down -> Openai
      check why Openai is failing  -> Openai
    """
    text = " ".join(value.strip().split()).strip()
    if not text:
        return ""

    # Strip a leading explicit command when this helper is called directly.
    text = re.sub(
        r"^(?:investigate|probe)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    patterns = (
        r"^why\s+is\s+(.+?)\s+(?:down|failing|failed|offline|unhealthy)\??$",
        r"^why\s+(.+?)\s+is\s+(?:down|failing|failed|offline|unhealthy)\??$",
        r"^check\s+why\s+(.+?)\s+is\s+(?:down|failing|failed|offline|unhealthy)\??$",
        r"^(.+?)\s+is\s+(?:down|failing|failed|offline|unhealthy)\??$",
    )

    for pattern in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" ?.")

    return text.strip(" ?.")

def discover_target(
    workspace: Path,
    subject: str,
) -> TargetResolution:
    workspace = workspace.resolve()
    subject = normalize_investigation_subject(subject)
    subject_l = subject.lower().strip()
    candidates: list[TargetCandidate] = []

    if not subject_l:
        return TargetResolution(
            subject=subject,
            resolved=False,
            note="No application-signal subject was supplied.",
        )

    for path in _iter_text_files(workspace):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        lines = text.splitlines()
        subject_indexes = [
            i for i, line in enumerate(lines)
            if subject_l in line.lower()
        ]
        if not subject_indexes:
            continue

        url_lines: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            for match in _URL_RE.findall(line):
                url_lines.append((i, match.rstrip("),.;]}")))

        if not url_lines:
            continue

        for subject_index in subject_indexes:
            nearby_urls = [
                (url_index, url)
                for url_index, url in url_lines
                if abs(url_index - subject_index) <= 8
            ]
            for url_index, url in nearby_urls:
                start = max(0, min(subject_index, url_index) - 2)
                end = min(len(lines), max(subject_index, url_index) + 3)
                context = "\n".join(lines[start:end]).strip()
                candidates.append(
                    TargetCandidate(
                        subject=subject,
                        url=url,
                        source_file=str(path.relative_to(workspace)),
                        line_number=url_index + 1,
                        score=_candidate_score(
                            subject,
                            lines,
                            subject_index,
                            url_index,
                        ),
                        context=context[:800],
                    )
                )

    # Deduplicate same URL/file combination and retain best score.
    best_by_key: dict[tuple[str, str], TargetCandidate] = {}
    for candidate in candidates:
        key = (candidate.url, candidate.source_file)
        existing = best_by_key.get(key)
        if existing is None or candidate.score > existing.score:
            best_by_key[key] = candidate

    ranked = sorted(
        best_by_key.values(),
        key=lambda item: (-item.score, item.source_file, item.line_number),
    )

    if not ranked:
        return TargetResolution(
            subject=subject,
            resolved=False,
            candidates=[],
            note=(
                f'Iceman could not find a nearby HTTP/HTTPS target for "{subject}" '
                "in the active workspace."
            ),
        )

    top = ranked[0]
    second_score = ranked[1].score if len(ranked) > 1 else -999

    if top.score >= 12 and top.score - second_score >= 3:
        confidence = "high"
        resolved = True
    elif top.score >= 9 and top.score - second_score >= 2:
        confidence = "medium"
        resolved = True
    elif len(ranked) == 1 and top.score >= 8:
        confidence = "medium"
        resolved = True
    else:
        return TargetResolution(
            subject=subject,
            resolved=False,
            candidates=ranked[:5],
            confidence="low",
            note=(
                "Multiple plausible URLs were found and Iceman will not probe "
                "one automatically without stronger evidence."
            ),
        )

    return TargetResolution(
        subject=subject,
        resolved=resolved,
        selected=top,
        candidates=ranked[:5],
        confidence=confidence,
    )



def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_identifier(value: str) -> str | None:
    if not _IDENTIFIER_RE.fullmatch(value):
        return None
    return '"' + value.replace('"', '""') + '"'


def _docker_compose_services(workspace: Path) -> list[str]:
    try:
        result = run_command(
            "docker compose config --services",
            cwd=workspace,
        )
    except (OSError, PermissionError):
        return []

    if result.returncode != 0:
        return []

    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _postgres_container(
    workspace: Path,
) -> tuple[str, dict[str, str]] | None:
    """
    Find a running Compose PostgreSQL container without modifying anything.
    Returns (container_id, env).
    """
    for service in _docker_compose_services(workspace):
        try:
            ps = run_command(
                f"docker compose ps -q {shlex.quote(service)}",
                cwd=workspace,
            )
        except (OSError, PermissionError):
            return None

        container_id = ps.stdout.strip()
        if ps.returncode != 0 or not container_id:
            continue

        image = run_command(
            (
                f"docker inspect {shlex.quote(container_id)} "
                "--format '{{.Config.Image}}'"
            ),
            cwd=workspace,
        )
        env_result = run_command(
            (
                f"docker inspect {shlex.quote(container_id)} "
                "--format '{{range .Config.Env}}{{println .}}{{end}}'"
            ),
            cwd=workspace,
        )

        env: dict[str, str] = {}
        if env_result.returncode == 0:
            for line in env_result.stdout.splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key] = value

        image_name = image.stdout.strip().lower()
        postgres_like = (
            "postgres" in image_name
            or "POSTGRES_USER" in env
            or "POSTGRES_DB" in env
        )
        if postgres_like:
            return container_id, env

    return None


def _psql(
    workspace: Path,
    container_id: str,
    user: str,
    database: str,
    sql: str,
):
    command = " ".join(
        shlex.quote(part)
        for part in (
            "docker",
            "exec",
            container_id,
            "psql",
            "-U",
            user,
            "-d",
            database,
            "-At",
            "-F",
            "|",
            "-c",
            sql,
        )
    )
    return run_command(command, cwd=workspace)


def discover_postgres_target(
    workspace: Path,
    subject: str,
) -> TargetResolution:
    """
    Read-only fallback for apps whose monitored targets are stored in
    PostgreSQL rather than source/config files.

    Iceman introspects public text columns, then only queries tables that have
    both a name-like field and a URL-like field.
    """
    workspace = workspace.resolve()
    subject = normalize_investigation_subject(subject)

    resolved_container = _postgres_container(workspace)
    if resolved_container is None:
        return TargetResolution(
            subject=subject,
            resolved=False,
            note="No running Compose PostgreSQL service was discovered.",
        )

    container_id, env = resolved_container
    user = env.get("POSTGRES_USER") or "postgres"
    database = env.get("POSTGRES_DB") or user

    schema_sql = """
SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND data_type IN (
    'text',
    'character varying',
    'character'
  )
ORDER BY table_name, ordinal_position;
""".strip()

    schema = _psql(
        workspace,
        container_id,
        user,
        database,
        schema_sql,
    )
    if schema.returncode != 0:
        return TargetResolution(
            subject=subject,
            resolved=False,
            note=(
                "PostgreSQL was detected, but read-only schema discovery failed."
            ),
        )

    table_columns: dict[str, set[str]] = {}
    for line in schema.stdout.splitlines():
        if "|" not in line:
            continue
        table, column = line.split("|", 1)
        table = table.strip()
        column = column.strip()
        if not _IDENTIFIER_RE.fullmatch(table):
            continue
        if not _IDENTIFIER_RE.fullmatch(column):
            continue
        table_columns.setdefault(table, set()).add(column)

    candidates: list[TargetCandidate] = []
    subject_literal = _sql_literal(subject)
    contains_literal = _sql_literal(f"%{subject}%")

    for table, columns in table_columns.items():
        name_columns = [
            column for column in _NAME_COLUMNS
            if column in columns
        ]
        url_columns = [
            column for column in _URL_COLUMNS
            if column in columns
        ]
        if not name_columns or not url_columns:
            continue

        table_id = _sql_identifier(table)
        if table_id is None:
            continue

        for name_column in name_columns:
            name_id = _sql_identifier(name_column)
            if name_id is None:
                continue

            for url_column in url_columns:
                url_id = _sql_identifier(url_column)
                if url_id is None:
                    continue

                query = f"""
SELECT
  COALESCE({name_id}::text, ''),
  COALESCE({url_id}::text, '')
FROM {table_id}
WHERE
  lower(COALESCE({name_id}::text, '')) = lower({subject_literal})
  OR lower(COALESCE({name_id}::text, '')) LIKE lower({contains_literal})
LIMIT 10;
""".strip()

                result = _psql(
                    workspace,
                    container_id,
                    user,
                    database,
                    query,
                )
                if result.returncode != 0:
                    continue

                for row in result.stdout.splitlines():
                    if "|" not in row:
                        continue
                    matched_name, url = row.split("|", 1)
                    matched_name = matched_name.strip()
                    url = url.strip()

                    parsed = urllib.parse.urlparse(url)
                    if (
                        parsed.scheme not in {"http", "https"}
                        or not parsed.hostname
                    ):
                        continue

                    exact = matched_name.casefold() == subject.casefold()
                    score = 100 if exact else 80
                    candidates.append(
                        TargetCandidate(
                            subject=subject,
                            url=url,
                            source_file=(
                                f"PostgreSQL {table}.{url_column} "
                                f"(matched by {table}.{name_column})"
                            ),
                            line_number=None,
                            score=score,
                            context=f"{matched_name} -> {url}",
                            source_kind="database",
                        )
                    )

    best_by_url: dict[str, TargetCandidate] = {}
    for candidate in candidates:
        existing = best_by_url.get(candidate.url)
        if existing is None or candidate.score > existing.score:
            best_by_url[candidate.url] = candidate

    ranked = sorted(
        best_by_url.values(),
        key=lambda candidate: (-candidate.score, candidate.url),
    )

    if not ranked:
        return TargetResolution(
            subject=subject,
            resolved=False,
            note=(
                f'PostgreSQL was inspected read-only, but no monitored HTTP/HTTPS '
                f'target matching "{subject}" was found in a table with '
                "name-like and URL-like columns."
            ),
        )

    top = ranked[0]
    same_score = [
        candidate for candidate in ranked
        if candidate.score == top.score
    ]
    if len(same_score) > 1 and len({
        candidate.url for candidate in same_score
    }) > 1:
        return TargetResolution(
            subject=subject,
            resolved=False,
            candidates=ranked[:5],
            confidence="low",
            note=(
                "Multiple database-backed targets match this subject. "
                "Iceman will not choose one automatically."
            ),
        )

    return TargetResolution(
        subject=subject,
        resolved=True,
        selected=top,
        candidates=ranked[:5],
        confidence="high" if top.score >= 100 else "medium",
        note="Resolved from PostgreSQL using read-only schema/data queries.",
    )


def resolve_target(
    workspace: Path,
    subject: str,
) -> TargetResolution:
    """
    Prefer project source/config. If no trustworthy file-backed target is
    resolved, fall back to read-only PostgreSQL discovery.
    """
    file_resolution = discover_target(workspace, subject)
    if file_resolution.resolved and file_resolution.selected is not None:
        return file_resolution

    db_resolution = discover_postgres_target(workspace, subject)
    if db_resolution.resolved:
        return db_resolution

    # Preserve any useful ambiguity from source files before the generic DB miss.
    if file_resolution.candidates:
        return file_resolution

    if db_resolution.note and "No running Compose PostgreSQL" not in db_resolution.note:
        return db_resolution

    return file_resolution



_CODE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".py"}

_REQUEST_MARKERS = (
    "axios.get(",
    "axios.head(",
    "axios.request(",
    "fetch(",
    "http.get(",
    "https.get(",
    ".get(service.url",
    ".get(check.url",
    ".get(target.url",
)

_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
}


def _code_files(workspace: Path):
    for path in _iter_text_files(workspace):
        if path.suffix.lower() not in _CODE_SUFFIXES:
            continue

        lowered_parts = {part.lower() for part in path.parts}
        if "tests" in lowered_parts or "__tests__" in lowered_parts:
            continue
        if any(
            part.endswith(".test") or part.endswith(".spec")
            for part in lowered_parts
        ):
            continue
        yield path


def _implementation_score(path: Path, text: str) -> int:
    lowered = text.lower()
    score = 0

    if "monitoring cycle started" in lowered:
        score += 25
    if re.search(r"\bdown\b", lowered):
        score += 16
    if "❌" in text:
        score += 8
    if any(marker in lowered for marker in _REQUEST_MARKERS):
        score += 20
    if "service.url" in lowered or "check.url" in lowered or "target.url" in lowered:
        score += 12
    if any(word in lowered for word in ("monitor", "health check", "checkservice", "check_service")):
        score += 8
    if "backend" in {part.lower() for part in path.parts}:
        score += 5
    if "controller" in path.name.lower() or "service" in path.name.lower():
        score += 3

    return score


def _request_line(lines: list[str]) -> tuple[int, str] | None:
    patterns = (
        r"axios\.get\s*\(",
        r"axios\.head\s*\(",
        r"axios\.request\s*\(",
        r"\bfetch\s*\(",
        r"\bhttps?\.get\s*\(",
        r"\bhttps?\.request\s*\(",
    )
    for index, line in enumerate(lines):
        if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in patterns):
            return index, line
    return None


def _extract_client_and_method(text: str, request_line: str) -> tuple[str, str]:
    line = request_line.lower()

    if re.search(r"axios\.head\s*\(", line):
        return "axios", "HEAD"
    if re.search(r"axios\.get\s*\(", line):
        return "axios", "GET"
    if re.search(r"axios\.request\s*\(", line):
        method_match = re.search(
            r"method\s*:\s*['\"]([A-Za-z]+)['\"]",
            text,
            flags=re.IGNORECASE,
        )
        return "axios", (
            method_match.group(1).upper()
            if method_match
            else "GET"
        )

    if re.search(r"\bfetch\s*\(", line):
        method_match = re.search(
            r"method\s*:\s*['\"]([A-Za-z]+)['\"]",
            text,
            flags=re.IGNORECASE,
        )
        return "fetch", (
            method_match.group(1).upper()
            if method_match
            else "GET"
        )

    if re.search(r"\bhttps?\.get\s*\(", line):
        return "node-http", "GET"

    if re.search(r"\bhttps?\.request\s*\(", line):
        method_match = re.search(
            r"method\s*:\s*['\"]([A-Za-z]+)['\"]",
            text,
            flags=re.IGNORECASE,
        )
        return "node-http", (
            method_match.group(1).upper()
            if method_match
            else "GET"
        )

    return "unknown", "GET"


def _extract_timeout_ms(text: str) -> int | None:
    patterns = (
        r"\btimeout\s*:\s*(\d{2,8})\b",
        r"AbortSignal\.timeout\s*\(\s*(\d{2,8})\s*\)",
        r"\btimeoutMs\s*[:=]\s*(\d{2,8})\b",
        r"\bTIMEOUT\s*[:=]\s*(\d{2,8})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _extract_static_headers(text: str) -> tuple[dict[str, str], list[str]]:
    """
    Extract only literal headers. Sensitive or dynamic headers are never copied
    into a replay.
    """
    headers: dict[str, str] = {}
    omitted: set[str] = set()

    # Limit parsing to header object-like regions to avoid harvesting arbitrary
    # constants elsewhere in the file.
    for block_match in re.finditer(
        r"headers\s*:\s*\{(?P<body>[^{}]{0,2500})\}",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        body = block_match.group("body")

        # Literal key/value pair.
        for pair in re.finditer(
            r"(?P<key>['\"]?[A-Za-z0-9_-]+['\"]?)\s*:\s*"
            r"(?P<value>['\"][^'\"]{0,500}['\"])",
            body,
        ):
            key = pair.group("key").strip("'\"")
            value = pair.group("value")[1:-1]
            key_l = key.lower()

            if key_l in _SENSITIVE_HEADER_NAMES:
                omitted.add(key)
                continue

            headers[key] = value

        # Dynamic values: record the header name but never evaluate/copy them.
        for pair in re.finditer(
            r"(?P<key>['\"]?[A-Za-z0-9_-]+['\"]?)\s*:\s*"
            r"(?P<value>(?!['\"])[^,\n}]+)",
            body,
        ):
            key = pair.group("key").strip("'\"")
            key_l = key.lower()
            if key_l in _SENSITIVE_HEADER_NAMES:
                omitted.add(key)
            else:
                value = pair.group("value").strip()
                if value and key not in headers:
                    omitted.add(key)

    return headers, sorted(omitted)


def _extract_redirect_policy(text: str, client: str) -> bool | None:
    max_redirects = re.search(
        r"maxRedirects\s*:\s*(\d+)",
        text,
        flags=re.IGNORECASE,
    )
    if max_redirects:
        return int(max_redirects.group(1)) > 0

    fetch_redirect = re.search(
        r"redirect\s*:\s*['\"](follow|manual|error)['\"]",
        text,
        flags=re.IGNORECASE,
    )
    if fetch_redirect:
        return fetch_redirect.group(1).lower() == "follow"

    if client in {"axios", "fetch"}:
        return True

    return None


def _extract_success_policy(
    text: str,
    client: str,
) -> tuple[int | None, int | None, str]:
    # Exact 200 checks.
    if re.search(
        r"(?:response\.)?status(?:Code)?\s*={2,3}\s*200\b",
        text,
        flags=re.IGNORECASE,
    ):
        return 200, 200, "HTTP 200 only"

    # Common 2xx checks.
    if (
        re.search(r"\.ok\b", text)
        or (
            re.search(r"status\s*>=\s*200", text)
            and re.search(r"status\s*<\s*300", text)
        )
    ):
        return 200, 299, "HTTP 2xx"

    # Static validateStatus patterns.
    less_than = re.search(
        r"validateStatus\s*:\s*[^,\n]*status[^,\n]*<\s*(\d{3})",
        text,
        flags=re.IGNORECASE,
    )
    if less_than:
        upper_exclusive = int(less_than.group(1))
        return 100, upper_exclusive - 1, f"HTTP status < {upper_exclusive}"

    # Axios rejects non-2xx by default. This is particularly useful for code
    # that logs DOWN from catch(error).
    if client == "axios" and "validatestatus" not in text.lower():
        return 200, 299, "Axios default HTTP 2xx"

    return None, None, "Not statically determined"


def _profile_fidelity(
    text: str,
    client: str,
    method: str,
    omitted_headers: list[str],
    policy_min: int | None,
    policy_max: int | None,
) -> tuple[str, str, list[str]]:
    evidence: list[str] = []
    lowered = text.lower()

    if client != "unknown":
        evidence.append(f"HTTP client: {client}")
    evidence.append(f"Method: {method}")

    custom_instance = bool(
        re.search(r"\baxios\.create\s*\(", text, flags=re.IGNORECASE)
        or "interceptors." in lowered
    )
    if custom_instance:
        evidence.append("Custom Axios instance/interceptor detected")

    dynamic_request = bool(omitted_headers)
    if omitted_headers:
        evidence.append(
            "Dynamic/sensitive headers omitted: "
            + ", ".join(omitted_headers)
        )

    content_check = bool(
        re.search(
            r"(response\.(?:data|text)|await\s+\w+\.json\s*\(|await\s+\w+\.text\s*\()",
            text,
            flags=re.IGNORECASE,
        )
        and re.search(
            r"\bif\s*\(",
            text,
            flags=re.IGNORECASE,
        )
    )
    if content_check:
        evidence.append("Possible response-content check detected")

    supported_method = method in {"GET", "HEAD"}
    has_policy = policy_min is not None and policy_max is not None

    if (
        client in {"axios", "fetch", "node-http"}
        and supported_method
        and has_policy
        and not custom_instance
        and not dynamic_request
        and not content_check
    ):
        return (
            "high",
            "high",
            evidence,
        )

    if (
        client in {"axios", "fetch", "node-http"}
        and supported_method
        and has_policy
    ):
        return (
            "medium",
            "medium",
            evidence,
        )

    return (
        "low",
        "low",
        evidence,
    )


def discover_application_check_profile(
    workspace: Path,
) -> ApplicationCheckProfile | None:
    """
    Locate the source implementation most likely responsible for the recurring
    monitoring log output and derive only statically observable request policy.

    This does not execute or import project code.
    """
    workspace = workspace.resolve()
    ranked: list[tuple[int, Path, str]] = []

    for path in _code_files(workspace):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        score = _implementation_score(path, text)
        if score >= 30:
            ranked.append((score, path, text))

    if not ranked:
        return None

    ranked.sort(
        key=lambda item: (
            -item[0],
            str(item[1]),
        )
    )

    top_score, path, text = ranked[0]
    if len(ranked) > 1 and ranked[1][0] == top_score:
        # Avoid picking between equally strong unrelated implementations.
        return None

    lines = text.splitlines()
    request = _request_line(lines)
    if request is None:
        return None

    request_index, request_text = request
    client, method = _extract_client_and_method(
        text,
        request_text,
    )
    timeout_ms = _extract_timeout_ms(text)
    headers, omitted_headers = _extract_static_headers(text)
    follow_redirects = _extract_redirect_policy(text, client)
    policy_min, policy_max, success_policy = _extract_success_policy(
        text,
        client,
    )

    confidence, fidelity, evidence = _profile_fidelity(
        text,
        client,
        method,
        omitted_headers,
        policy_min,
        policy_max,
    )

    if timeout_ms is not None:
        evidence.append(f"Timeout: {timeout_ms} ms")
    if follow_redirects is not None:
        evidence.append(
            "Redirects: " + ("follow" if follow_redirects else "do not follow")
        )
    evidence.append(f"Success policy: {success_policy}")

    relative = str(path.relative_to(workspace))
    note = None
    if fidelity != "high":
        note = (
            "The implementation contains dynamic/custom behavior that cannot be "
            "reproduced exactly by static analysis. Replay results are therefore "
            "source-derived rather than claimed as byte-for-byte identical."
        )

    return ApplicationCheckProfile(
        source_file=relative,
        line_number=request_index + 1,
        client=client,
        method=method,
        timeout_ms=timeout_ms,
        headers=headers,
        omitted_header_names=omitted_headers,
        follow_redirects=follow_redirects,
        accepted_status_min=policy_min,
        accepted_status_max=policy_max,
        success_policy=success_policy,
        confidence=confidence,
        fidelity=fidelity,
        evidence=evidence,
        note=note,
    )


def _policy_accepts(
    profile: ApplicationCheckProfile,
    status: int,
) -> bool | None:
    if (
        profile.accepted_status_min is None
        or profile.accepted_status_max is None
    ):
        return None
    return (
        profile.accepted_status_min
        <= status
        <= profile.accepted_status_max
    )


_SOURCE_REPLAY_NODE = r"""
const profile = JSON.parse(process.argv[1]);
const target = process.argv[2];
const started = Date.now();

function done(payload, code = 0) {
  console.log(JSON.stringify(payload));
  process.exitCode = code;
}

const timeout = profile.timeout_ms || 5000;
const headers = profile.headers || {};
const method = profile.method || "GET";
const follow = profile.follow_redirects !== false;

(async () => {
  try {
    if (profile.client === "axios") {
      let axios;
      try {
        axios = require("axios");
      } catch (err) {
        return done({
          error: "axios is not installed in the emitting container",
          runtime: "node"
        }, 2);
      }

      const response = await axios.request({
        url: target,
        method,
        timeout,
        headers,
        maxRedirects: follow ? 5 : 0,
        validateStatus: () => true
      });

      return done({
        status: response.status,
        latency_ms: Date.now() - started,
        runtime: `axios/${axios.VERSION || "unknown"} on ${process.version}`
      });
    }

    if (profile.client === "fetch") {
      const response = await fetch(target, {
        method,
        headers,
        redirect: follow ? "follow" : "manual",
        signal: AbortSignal.timeout(timeout)
      });
      if (method !== "HEAD" && response.body) {
        try {
          await response.body.cancel();
        } catch (_) {}
      }
      return done({
        status: response.status,
        latency_ms: Date.now() - started,
        runtime: `fetch on ${process.version}`
      });
    }

    // node-http fallback using built-in fetch for safe GET/HEAD reproduction.
    const response = await fetch(target, {
      method,
      headers,
      redirect: follow ? "follow" : "manual",
      signal: AbortSignal.timeout(timeout)
    });
    if (method !== "HEAD" && response.body) {
      try {
        await response.body.cancel();
      } catch (_) {}
    }
    return done({
      status: response.status,
      latency_ms: Date.now() - started,
      runtime: `node-http-compatible replay on ${process.version}`
    });
  } catch (err) {
    return done({
      error: String(err && (err.message || err)),
      latency_ms: Date.now() - started,
      runtime: process.version
    }, 2);
  }
})();
""".strip()


def replay_application_check(
    workspace: Path,
    emitter: SignalEmitter,
    url: str,
    profile: ApplicationCheckProfile,
) -> ApplicationCheckReplay:
    replay = ApplicationCheckReplay(
        compose_service=emitter.compose_service,
        container_id=emitter.container_id,
        client=profile.client,
        fidelity=profile.fidelity,
    )

    if profile.method not in {"GET", "HEAD"}:
        replay.note = (
            f"Automatic replay is disabled for {profile.method} because Iceman "
            "only reproduces read-only GET/HEAD checks."
        )
        return replay

    if profile.omitted_header_names:
        replay.note = (
            "Dynamic or sensitive headers were intentionally omitted: "
            + ", ".join(profile.omitted_header_names)
            + "."
        )

    payload = {
        "client": profile.client,
        "method": profile.method,
        "timeout_ms": profile.timeout_ms or 5000,
        "headers": profile.headers,
        "follow_redirects": (
            True
            if profile.follow_redirects is None
            else profile.follow_redirects
        ),
    }

    try:
        result = _docker_exec(
            workspace,
            emitter.container_id,
            [
                "node",
                "-e",
                _SOURCE_REPLAY_NODE,
                json.dumps(payload, separators=(",", ":")),
                url,
            ],
        )
    except (OSError, PermissionError) as exc:
        replay.error = f"Container replay could not start: {exc}"
        return replay

    structured = None
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            structured = json.loads(line)
            break
        except json.JSONDecodeError:
            continue

    if structured is None:
        replay.error = (
            "Source-derived replay did not return parseable structured output."
        )
        if result.stderr.strip():
            replay.error += f" stderr: {result.stderr.strip()[:300]}"
        return replay

    replay.runtime = structured.get("runtime")
    replay.latency_ms = structured.get("latency_ms")

    if structured.get("error"):
        replay.error = str(structured["error"])
        return replay

    status = structured.get("status")
    if status is not None:
        replay.status_code = int(status)
        replay.accepted_by_policy = _policy_accepts(
            profile,
            replay.status_code,
        )

    return replay


def apply_source_reproduction_conclusion(
    investigation: RootCauseInvestigation,
) -> None:
    profile = investigation.check_profile
    replay = investigation.check_replay

    if profile is None or replay is None:
        return

    if replay.error:
        investigation.reproduction_conclusion = (
            "The source-derived check profile was found, but replay failed: "
            f"{replay.error}"
        )
        return

    if replay.status_code is None:
        investigation.reproduction_conclusion = (
            replay.note
            or "The source-derived check could not be replayed automatically."
        )
        return

    if replay.accepted_by_policy is False:
        investigation.reproduction_conclusion = (
            f"The source-derived {profile.client} {profile.method} replay from "
            f"{replay.compose_service} returned HTTP {replay.status_code}, while "
            f"the discovered application policy accepts {profile.success_policy}. "
            "That HTTP-status mismatch is sufficient to explain the current "
            "DOWN classification for this check."
        )
        if profile.fidelity == "high":
            investigation.likely_cause = (
                f"Minerva's source-derived monitoring request receives HTTP "
                f"{replay.status_code}, which fails its discovered "
                f"{profile.success_policy} success policy. The target is reachable, "
                "but the HTTP response is rejected by the monitor's status policy."
            )
            investigation.confidence = "high"
        return

    if replay.accepted_by_policy is True:
        investigation.reproduction_conclusion = (
            f"The source-derived replay returned HTTP {replay.status_code}, which "
            f"passes the discovered {profile.success_policy} policy. The current "
            "DOWN signal is therefore not explained by HTTP status alone."
        )
        investigation.likely_cause = (
            "The source-derived request currently passes the discovered HTTP "
            "status policy. Investigate response-content checks, dynamic headers, "
            "transient failures, or state that differs from this replay."
        )
        investigation.confidence = (
            "medium"
            if profile.fidelity == "high"
            else "low"
        )
        return

    investigation.reproduction_conclusion = (
        f"The replay returned HTTP {replay.status_code}, but Iceman could not "
        "statically determine the application's accepted status policy."
    )

def discover_signal_emitter(
    workspace: Path,
    subject: str,
    tail: int = 250,
) -> SignalEmitter | None:
    """
    Find the running Compose service whose recent logs contain the application
    signal. This is read-only and is used to identify the application execution
    context that actually produced the DOWN/FAILED event.
    """
    subject = normalize_investigation_subject(subject)
    if not subject:
        return None

    signal_re = re.compile(
        rf"{re.escape(subject)}\s*\|\s*"
        r"(?:DOWN|FAILED|ERROR|UNHEALTHY|OFFLINE)\b",
        flags=re.IGNORECASE,
    )

    emitters: list[SignalEmitter] = []

    for service in _docker_compose_services(workspace):
        try:
            ps = run_command(
                f"docker compose ps -q {shlex.quote(service)}",
                cwd=workspace,
            )
        except (OSError, PermissionError):
            return None

        container_id = ps.stdout.strip()
        if ps.returncode != 0 or not container_id:
            continue

        try:
            logs = run_command(
                (
                    f"docker logs --tail {int(tail)} "
                    f"{shlex.quote(container_id)}"
                ),
                cwd=workspace,
            )
        except (OSError, PermissionError):
            continue

        combined = "\n".join(
            part for part in (logs.stdout, logs.stderr) if part
        )
        matches = len(signal_re.findall(combined))
        if matches:
            emitters.append(
                SignalEmitter(
                    compose_service=service,
                    container_id=container_id,
                    matches=matches,
                )
            )

    if not emitters:
        return None

    emitters.sort(
        key=lambda emitter: (
            -emitter.matches,
            emitter.compose_service,
        )
    )

    # If two services have exactly the same evidence, do not guess.
    if (
        len(emitters) > 1
        and emitters[0].matches == emitters[1].matches
    ):
        return None

    return emitters[0]


_NODE_CONTEXT_PROBE = r"""
const target = process.argv[1];
const timeoutMs = Number(process.argv[2] || "5000");
const u = new URL(target);
const isHttps = u.protocol === "https:";
const mod = isHttps ? require("https") : require("http");
const started = Date.now();
const result = {
  dns: null,
  tcp: null,
  tls: null,
  http: null,
  error: null
};
let printed = false;

function output() {
  if (printed) return;
  printed = true;
  console.log(JSON.stringify(result));
}

const req = mod.request(
  u,
  {
    method: "GET",
    headers: {
      "User-Agent": "Iceman/0.7.5 execution-context-probe",
      "Accept": "*/*"
    },
    agent: false
  },
  (res) => {
    result.http = {
      status: res.statusCode,
      latency_ms: Date.now() - started
    };
    res.resume();
    res.on("end", output);
  }
);

req.on("socket", (sock) => {
  sock.on("lookup", (err, address) => {
    result.dns = {
      success: !err,
      address: address || null,
      error: err ? String(err.message || err) : null,
      latency_ms: Date.now() - started
    };
  });

  sock.on("connect", () => {
    result.tcp = {
      success: true,
      latency_ms: Date.now() - started
    };
  });

  sock.on("secureConnect", () => {
    result.tls = {
      success: true,
      protocol: typeof sock.getProtocol === "function"
        ? sock.getProtocol()
        : null,
      latency_ms: Date.now() - started
    };
  });
});

req.setTimeout(timeoutMs, () => {
  req.destroy(new Error("timeout"));
});

req.on("error", (err) => {
  result.error = String(err.message || err);
  output();
  process.exitCode = 2;
});

req.end();
""".strip()


def _docker_exec(
    workspace: Path,
    container_id: str,
    argv: list[str],
):
    command = " ".join(
        shlex.quote(part)
        for part in (
            "docker",
            "exec",
            container_id,
            *argv,
        )
    )
    return run_command(command, cwd=workspace)


def probe_from_container(
    workspace: Path,
    emitter: SignalEmitter,
    url: str,
    timeout: float = 5.0,
) -> ExecutionContextProbe:
    """
    Replay a neutral HTTP GET from the container that emitted the monitored
    signal. Node is preferred because Minerva's backend is Node-based; curl is
    only a fallback. No file or process state is modified.
    """
    probe = ExecutionContextProbe(
        context="application-container",
        compose_service=emitter.compose_service,
        container_id=emitter.container_id,
    )

    try:
        node_version = _docker_exec(
            workspace,
            emitter.container_id,
            ["node", "--version"],
        )
    except (OSError, PermissionError) as exc:
        probe.note = f"Container probe could not start: {exc}"
        return probe

    if node_version.returncode == 0:
        probe.runtime = (
            f"node {node_version.stdout.strip()}"
            if node_version.stdout.strip()
            else "node"
        )
        result = _docker_exec(
            workspace,
            emitter.container_id,
            [
                "node",
                "-e",
                _NODE_CONTEXT_PROBE,
                url,
                str(int(timeout * 1000)),
            ],
        )

        payload = None
        for line in reversed(result.stdout.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

        if payload is None:
            probe.note = (
                "Node was available in the application container, but the "
                "probe did not return parseable structured output."
            )
            if result.stderr.strip():
                probe.note += f" stderr: {result.stderr.strip()[:300]}"
            return probe

        dns = payload.get("dns")
        if isinstance(dns, dict):
            success = bool(dns.get("success"))
            address = dns.get("address")
            error = dns.get("error")
            probe.steps.append(
                ProbeStep(
                    name="DNS",
                    success=success,
                    detail=(
                        f"Resolved inside container to {address}."
                        if success and address
                        else (
                            "DNS resolved inside container."
                            if success
                            else f"Container DNS failed: {error or 'unknown error'}"
                        )
                    ),
                    latency_ms=dns.get("latency_ms"),
                )
            )

        tcp = payload.get("tcp")
        if isinstance(tcp, dict):
            success = bool(tcp.get("success"))
            probe.steps.append(
                ProbeStep(
                    name="TCP",
                    success=success,
                    detail=(
                        "TCP connection succeeded inside container."
                        if success
                        else "TCP connection failed inside container."
                    ),
                    latency_ms=tcp.get("latency_ms"),
                )
            )

        tls = payload.get("tls")
        if isinstance(tls, dict):
            success = bool(tls.get("success"))
            protocol = tls.get("protocol")
            probe.steps.append(
                ProbeStep(
                    name="TLS",
                    success=success,
                    detail=(
                        f"TLS handshake succeeded inside container"
                        + (f" ({protocol})." if protocol else ".")
                        if success
                        else "TLS handshake failed inside container."
                    ),
                    latency_ms=tls.get("latency_ms"),
                )
            )

        http = payload.get("http")
        if isinstance(http, dict) and http.get("status") is not None:
            status = int(http["status"])
            probe.steps.append(
                ProbeStep(
                    name="HTTP",
                    success=200 <= status < 400,
                    detail=f"HTTP {status} from application container.",
                    latency_ms=http.get("latency_ms"),
                    status_code=status,
                )
            )
        elif payload.get("error"):
            probe.steps.append(
                ProbeStep(
                    name="HTTP",
                    success=False,
                    detail=(
                        "Container HTTP request failed: "
                        f"{payload.get('error')}"
                    ),
                )
            )

        return probe

    # curl fallback for non-Node application images.
    try:
        curl_version = _docker_exec(
            workspace,
            emitter.container_id,
            ["curl", "--version"],
        )
    except (OSError, PermissionError):
        curl_version = None

    if curl_version is not None and curl_version.returncode == 0:
        probe.runtime = "curl"
        result = _docker_exec(
            workspace,
            emitter.container_id,
            [
                "curl",
                "-sS",
                "-L",
                "-o",
                "/dev/null",
                "--max-time",
                str(int(timeout)),
                "-A",
                "Iceman/0.7.5 execution-context-probe",
                "-w",
                "%{http_code}",
                url,
            ],
        )
        status_text = result.stdout.strip()
        if status_text.isdigit():
            status = int(status_text)
            probe.steps.append(
                ProbeStep(
                    name="HTTP",
                    success=200 <= status < 400,
                    detail=f"HTTP {status} from application container.",
                    status_code=status,
                )
            )
        else:
            probe.note = (
                "curl was available but did not return an HTTP status."
            )
        return probe

    probe.note = (
        "The emitting container has neither Node nor curl available for the "
        "bounded HTTP replay."
    )
    return probe


def _http_status(steps: list[ProbeStep]) -> int | None:
    for step in steps:
        if step.name == "HTTP" and step.status_code is not None:
            return step.status_code
    return None


def _first_failed_layer(
    steps: list[ProbeStep],
) -> ProbeStep | None:
    for step in steps:
        if step.success is False:
            return step
    return None


DEFAULT_HTTP_SAMPLE_COUNT = 3
DEFAULT_SAMPLE_INTERVAL_SECONDS = 0.15


def summarize_http_evidence(
    context: str,
    samples: list[HttpEvidenceSample],
) -> EvidenceStability:
    success_count = sum(sample.accepted is True for sample in samples)
    failure_count = sum(sample.accepted is False for sample in samples)
    unknown_count = sum(sample.accepted is None for sample in samples)

    status_counts: dict[int, int] = {}
    for sample in samples:
        if sample.status_code is not None:
            status_counts[sample.status_code] = (
                status_counts.get(sample.status_code, 0) + 1
            )

    observed = success_count + failure_count
    if observed < 2:
        classification = "insufficient"
    elif success_count == observed and unknown_count == 0:
        classification = "stable_success"
    elif failure_count == observed and unknown_count == 0:
        classification = "stable_failure"
    elif success_count > 0 and failure_count > 0:
        classification = "unstable_mixed"
    elif unknown_count > 0:
        classification = "incomplete"
    else:
        classification = "insufficient"

    statuses = ", ".join(
        (
            str(sample.status_code)
            if sample.status_code is not None
            else "no-status"
        )
        for sample in samples
    ) or "none"

    labels = {
        "stable_success": "STABLE SUCCESS",
        "stable_failure": "STABLE FAILURE",
        "unstable_mixed": "UNSTABLE / MIXED",
        "incomplete": "INCOMPLETE",
        "insufficient": "INSUFFICIENT",
    }

    return EvidenceStability(
        context=context,
        samples=list(samples),
        classification=classification,
        success_count=success_count,
        failure_count=failure_count,
        unknown_count=unknown_count,
        status_counts=status_counts,
        summary=(
            f"{labels.get(classification, classification)} — "
            f"samples [{statuses}]"
        ),
    )


def _sleep_between_samples(interval_seconds: float) -> None:
    if interval_seconds > 0:
        time.sleep(interval_seconds)


def _sample_from_http_step(
    step: ProbeStep | None,
    attempt: int,
) -> HttpEvidenceSample:
    if step is None:
        return HttpEvidenceSample(
            attempt=attempt,
            status_code=None,
            accepted=None,
            latency_ms=None,
            detail="No HTTP result.",
        )
    return HttpEvidenceSample(
        attempt=attempt,
        status_code=step.status_code,
        accepted=step.success if step.status_code is not None else None,
        latency_ms=step.latency_ms,
        detail=step.detail,
    )


def _http_step_from_execution_probe(
    probe: ExecutionContextProbe,
) -> ProbeStep | None:
    for step in probe.steps:
        if step.name == "HTTP":
            return step
    return None


def _http_step_from_investigation(
    investigation: RootCauseInvestigation,
) -> ProbeStep | None:
    for step in investigation.steps:
        if step.name == "HTTP":
            return step
    return None


def probe_host_http_once(
    url: str,
    timeout: float,
    attempt: int,
) -> HttpEvidenceSample:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Iceman/0.7.5 evidence-sample",
            "Accept": "*/*",
        },
        method="GET",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            response.read(256)
            status = int(response.status)
            latency = int((time.monotonic() - started) * 1000)
            return HttpEvidenceSample(
                attempt=attempt,
                status_code=status,
                accepted=200 <= status < 400,
                latency_ms=latency,
                detail=f"HTTP {status}",
            )
    except urllib.error.HTTPError as exc:
        latency = int((time.monotonic() - started) * 1000)
        status = int(exc.code)
        return HttpEvidenceSample(
            attempt=attempt,
            status_code=status,
            accepted=False,
            latency_ms=latency,
            detail=f"HTTP {status}: {exc.reason}",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        latency = int((time.monotonic() - started) * 1000)
        return HttpEvidenceSample(
            attempt=attempt,
            status_code=None,
            accepted=None,
            latency_ms=latency,
            detail=f"HTTP sample failed: {exc}",
        )


def collect_host_http_samples(
    investigation: RootCauseInvestigation,
    *,
    timeout: float,
    sample_count: int = DEFAULT_HTTP_SAMPLE_COUNT,
    interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
) -> list[HttpEvidenceSample]:
    first = _sample_from_http_step(
        _http_step_from_investigation(investigation),
        1,
    )
    samples = [first]

    if first.status_code is None:
        return samples

    for attempt in range(2, max(2, sample_count) + 1):
        _sleep_between_samples(interval_seconds)
        samples.append(
            probe_host_http_once(
                investigation.url or "",
                timeout,
                attempt,
            )
        )
    return samples


def collect_container_http_samples(
    investigation: RootCauseInvestigation,
    *,
    timeout: float,
    sample_count: int = DEFAULT_HTTP_SAMPLE_COUNT,
    interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
) -> list[HttpEvidenceSample]:
    probe = investigation.container_probe
    emitter = investigation.signal_emitter

    if probe is None or emitter is None:
        return []

    samples = [
        _sample_from_http_step(
            _http_step_from_execution_probe(probe),
            1,
        )
    ]

    if samples[0].status_code is None:
        return samples

    for attempt in range(2, max(2, sample_count) + 1):
        _sleep_between_samples(interval_seconds)
        repeated = probe_from_container(
            investigation.workspace,
            emitter,
            investigation.url or "",
            timeout=timeout,
        )
        samples.append(
            _sample_from_http_step(
                _http_step_from_execution_probe(repeated),
                attempt,
            )
        )

    return samples


def sample_from_application_replay(
    replay: ApplicationCheckReplay,
    attempt: int,
) -> HttpEvidenceSample:
    return HttpEvidenceSample(
        attempt=attempt,
        status_code=replay.status_code,
        accepted=replay.accepted_by_policy,
        latency_ms=replay.latency_ms,
        detail=(
            f"HTTP {replay.status_code}"
            if replay.status_code is not None
            else (replay.error or replay.note or "No replay HTTP result.")
        ),
    )


def collect_application_replay_samples(
    workspace: Path,
    emitter: SignalEmitter,
    url: str,
    profile: ApplicationCheckProfile,
    *,
    first_replay: ApplicationCheckReplay | None = None,
    sample_count: int = DEFAULT_HTTP_SAMPLE_COUNT,
    interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
) -> tuple[list[ApplicationCheckReplay], list[HttpEvidenceSample]]:
    replays: list[ApplicationCheckReplay] = []
    samples: list[HttpEvidenceSample] = []

    if first_replay is not None:
        replays.append(first_replay)
        samples.append(
            sample_from_application_replay(first_replay, 1)
        )

    start_attempt = len(replays) + 1
    for attempt in range(start_attempt, sample_count + 1):
        if replays:
            _sleep_between_samples(interval_seconds)
        replay = replay_application_check(
            workspace,
            emitter,
            url,
            profile,
        )
        replays.append(replay)
        samples.append(
            sample_from_application_replay(replay, attempt)
        )

    return replays, samples


def apply_multi_sample_conclusion(
    investigation: RootCauseInvestigation,
) -> None:
    host = investigation.host_stability
    container = investigation.container_stability
    source = investigation.source_stability

    if source is None:
        return

    if source.classification == "stable_failure":
        if (
            host is not None
            and host.classification == "stable_success"
        ):
            investigation.likely_cause = (
                "The host is consistently successful while the source-derived "
                "application check consistently fails. The failure is specific "
                "to the monitored application/request execution context rather "
                "than basic endpoint availability."
            )
            investigation.confidence = "high"
            return

        if (
            host is not None
            and host.classification == "unstable_mixed"
        ):
            investigation.likely_cause = (
                "Host access is intermittent, while the source-derived "
                "application check fails consistently. The monitor's DOWN state "
                "is stable in the application context even though host behavior "
                "varies."
            )
            investigation.confidence = "high"
            return

        if (
            container is not None
            and container.classification == "stable_failure"
        ):
            investigation.likely_cause = (
                "The application container and source-derived monitoring check "
                "consistently receive responses that fail the monitor policy. "
                "This is a stable application-context HTTP failure, not a "
                "single-sample anomaly."
            )
            investigation.confidence = "high"
            return

    if source.classification == "unstable_mixed":
        investigation.likely_cause = (
            "The source-derived monitoring check is unstable across repeated "
            "samples: both accepted and rejected responses were observed. "
            "Intermittent endpoint/request behavior can explain recurring DOWN "
            "events, so a configuration write should not be based on one sample."
        )
        investigation.confidence = "medium"
        return

    if source.classification == "stable_success":
        investigation.likely_cause = (
            "The source-derived monitoring check is currently stable-success "
            "across repeated samples. Recent DOWN logs are therefore more likely "
            "historical/transient or caused by behavior not represented by the "
            "current status-only replay."
        )
        investigation.confidence = "medium"


def _http_failure_cause(status: int) -> str:
    if status in {401, 403}:
        return (
            f"Endpoint returned HTTP {status}; the request is being rejected "
            "at the HTTP layer. Possible causes include access policy/WAF or "
            "bot protection, authentication/authorization, request "
            "headers/User-Agent, or an expected-status mismatch."
        )
    if status == 404:
        return (
            "Endpoint returned HTTP 404; the configured path may be wrong or "
            "no longer exist."
        )
    if status == 429:
        return (
            "Endpoint returned HTTP 429; rate limiting or quota exhaustion "
            "is likely."
        )
    if 500 <= status <= 599:
        return (
            f"Endpoint returned HTTP {status}; the remote/upstream service is "
            "reporting a server-side failure."
        )
    return f"Endpoint is reachable but returned HTTP {status}."


def compare_execution_contexts(
    investigation: RootCauseInvestigation,
) -> None:
    """
    Compare host and application-container evidence and refine the likely cause
    without claiming more than the probes establish.
    """
    container = investigation.container_probe
    if container is None:
        return

    host_status = _http_status(investigation.steps)
    container_status = container.http_status

    if host_status is not None and container_status is not None:
        if host_status == container_status:
            if 200 <= host_status < 400:
                investigation.context_comparison = (
                    f"Host and {container.compose_service} both receive HTTP "
                    f"{host_status}. Execution-context networking is unlikely "
                    "to explain the recorded DOWN signal."
                )
                investigation.likely_cause = (
                    "The endpoint is currently healthy from both the host and "
                    "the application container. The recorded DOWN signal is "
                    "more likely related to Minerva's check criteria, request "
                    "differences, timeout/content expectations, or a transient "
                    "condition that is no longer present."
                )
                investigation.confidence = "medium"
            else:
                investigation.context_comparison = (
                    f"Host and {container.compose_service} both receive HTTP "
                    f"{host_status}. A host-vs-container network difference is "
                    "unlikely."
                )
                investigation.likely_cause = _http_failure_cause(host_status)
                investigation.confidence = "high"
            return

        host_ok = 200 <= host_status < 400
        container_ok = 200 <= container_status < 400

        if host_ok and not container_ok:
            investigation.context_comparison = (
                f"Host receives HTTP {host_status}, but "
                f"{container.compose_service} receives HTTP "
                f"{container_status}."
            )
            investigation.likely_cause = (
                "The failure is execution-context specific to the application "
                "container. Investigate source-IP/access policy, proxy settings, "
                "container DNS/networking, or request/runtime differences."
            )
            investigation.confidence = "high"
            return

        if not host_ok and container_ok:
            investigation.context_comparison = (
                f"Host receives HTTP {host_status}, but "
                f"{container.compose_service} receives HTTP "
                f"{container_status}."
            )
            investigation.likely_cause = (
                "The application container can currently reach the endpoint "
                "successfully. If Minerva still reports DOWN, investigate its "
                "actual check logic/request parameters or whether the log signal "
                "was historical/transient."
            )
            investigation.confidence = "high"
            return

        investigation.context_comparison = (
            f"Host receives HTTP {host_status}; "
            f"{container.compose_service} receives HTTP {container_status}. "
            "Both are HTTP-layer failures, but the responses differ."
        )
        investigation.likely_cause = (
            "The endpoint is reachable in both contexts but responds "
            "differently. Request/runtime headers, access policy, WAF/bot "
            "protection, proxying, or source-network differences may matter."
        )
        investigation.confidence = "medium"
        return

    # If the host passed to HTTP but the container failed earlier, that is a
    # strong execution-context signal.
    container_failure = _first_failed_layer(container.steps)
    if host_status is not None and 200 <= host_status < 400 and container_failure:
        investigation.context_comparison = (
            f"Host reaches HTTP successfully, but "
            f"{container.compose_service} fails at "
            f"{container_failure.name}."
        )
        investigation.likely_cause = (
            "The problem is specific to the application container's execution "
            f"context at the {container_failure.name} layer."
        )
        investigation.confidence = "high"
        return

    if container.note:
        investigation.context_comparison = (
            "The emitting application container was identified, but Iceman "
            f"could not complete the replay: {container.note}"
        )

def _blocked_target(host: str) -> str | None:
    host_l = host.lower().strip("[]")
    if host_l in _BLOCKED_METADATA_HOSTS:
        return "Cloud metadata endpoints are blocked from automatic probing."

    try:
        ip = ipaddress.ip_address(host_l)
    except ValueError:
        return None

    # Link-local addresses are risky for automatic probes. Normal localhost and
    # private application networks remain usable for DevOps troubleshooting.
    if ip.is_link_local:
        return "Link-local addresses are blocked from automatic probing."

    return None


def _timed_call(fn):
    start = time.monotonic()
    result = fn()
    elapsed = int((time.monotonic() - start) * 1000)
    return result, elapsed


def investigate_target(
    workspace: Path,
    subject: str,
    timeout: float = 5.0,
) -> RootCauseInvestigation:
    workspace = workspace.resolve()
    subject = normalize_investigation_subject(subject)
    resolution = resolve_target(workspace, subject)
    investigation = RootCauseInvestigation(
        subject=subject,
        workspace=workspace,
        resolution=resolution,
    )

    if not resolution.resolved or resolution.selected is None:
        investigation.likely_cause = (
            resolution.note
            or "The target configuration could not be resolved safely."
        )
        investigation.confidence = "low"
        return investigation

    url = resolution.selected.url
    investigation.url = url
    investigation.request_replay_note = (
        "Host/container comparison replays a neutral HTTP GET with Accept */* "
        "and an Iceman User-Agent. It does not claim to reproduce Minerva's "
        "exact request headers or application code unless those are separately "
        "observed."
    )

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        investigation.blocked = True
        investigation.likely_cause = (
            "Only HTTP/HTTPS targets with a valid hostname can be probed."
        )
        return investigation

    if parsed.username or parsed.password:
        investigation.blocked = True
        investigation.likely_cause = (
            "Iceman will not automatically probe URLs containing embedded credentials."
        )
        return investigation

    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    investigation.host = host
    investigation.port = port

    blocked_reason = _blocked_target(host)
    if blocked_reason:
        investigation.blocked = True
        investigation.likely_cause = blocked_reason
        return investigation

    # Identify the application context that produced the monitored DOWN event.
    investigation.signal_emitter = discover_signal_emitter(
        workspace,
        subject,
    )

    # -------------------------
    # Host DNS
    # -------------------------
    try:
        infos, latency = _timed_call(
            lambda: socket.getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
            )
        )
        addresses = sorted({
            info[4][0]
            for info in infos
            if info and len(info) >= 5 and info[4]
        })
        investigation.dns_addresses = addresses
        investigation.steps.append(
            ProbeStep(
                name="DNS",
                success=True,
                detail=(
                    f"Resolved {host} to "
                    + ", ".join(addresses[:6])
                    if addresses
                    else f"Resolved {host}."
                ),
                latency_ms=latency,
            )
        )
    except socket.gaierror as exc:
        investigation.steps.append(
            ProbeStep(
                name="DNS",
                success=False,
                detail=f"DNS resolution failed: {exc}",
            )
        )
        investigation.likely_cause = "DNS/name-resolution failure on the host."
        investigation.confidence = "high"
        _attach_container_context(investigation, timeout)
        return investigation

    # -------------------------
    # Host TCP
    # -------------------------
    try:
        def connect():
            return socket.create_connection(
                (host, port),
                timeout=timeout,
            )

        sock, latency = _timed_call(connect)
        investigation.steps.append(
            ProbeStep(
                name="TCP",
                success=True,
                detail=f"Connected to {host}:{port}.",
                latency_ms=latency,
            )
        )
    except (OSError, TimeoutError) as exc:
        investigation.steps.append(
            ProbeStep(
                name="TCP",
                success=False,
                detail=f"TCP connection failed: {exc}",
            )
        )
        investigation.likely_cause = (
            f"Network/TCP connectivity failure to {host}:{port} on the host."
        )
        investigation.confidence = "high"
        _attach_container_context(investigation, timeout)
        return investigation

    # -------------------------
    # Host TLS
    # -------------------------
    if parsed.scheme == "https":
        try:
            context = ssl.create_default_context()

            def tls_handshake():
                with sock:
                    with context.wrap_socket(
                        sock,
                        server_hostname=host,
                    ) as tls_sock:
                        return tls_sock.version()

            tls_version, latency = _timed_call(tls_handshake)
            investigation.steps.append(
                ProbeStep(
                    name="TLS",
                    success=True,
                    detail=f"TLS handshake succeeded ({tls_version}).",
                    latency_ms=latency,
                )
            )
        except ssl.SSLError as exc:
            try:
                sock.close()
            except OSError:
                pass
            investigation.steps.append(
                ProbeStep(
                    name="TLS",
                    success=False,
                    detail=f"TLS handshake failed: {exc}",
                )
            )
            investigation.likely_cause = (
                "TLS/certificate negotiation failure on the host."
            )
            investigation.confidence = "high"
            _attach_container_context(investigation, timeout)
            return investigation
        except OSError as exc:
            investigation.steps.append(
                ProbeStep(
                    name="TLS",
                    success=False,
                    detail=f"TLS connection failed: {exc}",
                )
            )
            investigation.likely_cause = (
                "TLS connection failed after TCP connectivity succeeded on the host."
            )
            investigation.confidence = "medium"
            _attach_container_context(investigation, timeout)
            return investigation
    else:
        try:
            sock.close()
        except OSError:
            pass

    # -------------------------
    # Host HTTP
    # -------------------------
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Iceman/0.7.5 execution-context-probe",
            "Accept": "*/*",
        },
        method="GET",
    )

    try:
        def http_request():
            with urllib.request.urlopen(
                request,
                timeout=timeout,
            ) as response:
                response.read(512)
                return response.status, response.geturl()

        (status, final_url), latency = _timed_call(http_request)
        investigation.steps.append(
            ProbeStep(
                name="HTTP",
                success=200 <= status < 400,
                detail=(
                    f"HTTP {status}"
                    + (
                        f" (final URL: {final_url})"
                        if final_url != url
                        else ""
                    )
                ),
                latency_ms=latency,
                status_code=status,
            )
        )

        if 200 <= status < 400:
            investigation.healthy_endpoint = True
            investigation.likely_cause = (
                "The endpoint is currently reachable from the host. If the "
                "application still reports it as DOWN, compare the application "
                "container execution context and Minerva's actual check logic."
            )
            investigation.confidence = "medium"
        else:
            investigation.likely_cause = _http_failure_cause(status)
            investigation.confidence = "high"

    except urllib.error.HTTPError as exc:
        status = exc.code
        investigation.steps.append(
            ProbeStep(
                name="HTTP",
                success=False,
                detail=f"HTTP {status}: {exc.reason}",
                status_code=status,
            )
        )
        investigation.likely_cause = _http_failure_cause(status)
        investigation.confidence = "high"

    except urllib.error.URLError as exc:
        reason = exc.reason
        investigation.steps.append(
            ProbeStep(
                name="HTTP",
                success=False,
                detail=f"HTTP request failed: {reason}",
            )
        )

        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            investigation.likely_cause = (
                "HTTP request timed out on the host after lower network layers "
                "succeeded."
            )
            investigation.confidence = "high"
        else:
            investigation.likely_cause = (
                "HTTP/application-layer request failed on the host after lower "
                "network layers succeeded."
            )
            investigation.confidence = "medium"

    except TimeoutError:
        investigation.steps.append(
            ProbeStep(
                name="HTTP",
                success=False,
                detail=f"HTTP request timed out after {timeout:g}s.",
            )
        )
        investigation.likely_cause = (
            "HTTP request timed out on the host after lower network layers "
            "succeeded."
        )
        investigation.confidence = "high"

    investigation.host_http_samples = collect_host_http_samples(
        investigation,
        timeout=timeout,
    )
    investigation.host_stability = summarize_http_evidence(
        "host-neutral",
        investigation.host_http_samples,
    )

    _attach_container_context(investigation, timeout)
    apply_multi_sample_conclusion(investigation)
    return investigation


def _attach_container_context(
    investigation: RootCauseInvestigation,
    timeout: float,
) -> None:
    emitter = investigation.signal_emitter
    if emitter is None:
        investigation.context_comparison = (
            "Iceman could not identify a unique running Compose service whose "
            "recent logs emitted this application signal, so no container replay "
            "was attempted."
        )
        return

    investigation.container_probe = probe_from_container(
        investigation.workspace,
        emitter,
        investigation.url or "",
        timeout=timeout,
    )
    investigation.container_http_samples = collect_container_http_samples(
        investigation,
        timeout=timeout,
    )
    if investigation.container_http_samples:
        investigation.container_stability = summarize_http_evidence(
            "application-container-neutral",
            investigation.container_http_samples,
        )

    compare_execution_contexts(investigation)

    investigation.check_profile = discover_application_check_profile(
        investigation.workspace,
    )
    if investigation.check_profile is not None:
        investigation.check_replay = replay_application_check(
            investigation.workspace,
            emitter,
            investigation.url or "",
            investigation.check_profile,
        )
        _, investigation.source_http_samples = collect_application_replay_samples(
            investigation.workspace,
            emitter,
            investigation.url or "",
            investigation.check_profile,
            first_replay=investigation.check_replay,
        )
        if investigation.source_http_samples:
            investigation.source_stability = summarize_http_evidence(
                "source-derived-application-check",
                investigation.source_http_samples,
            )
        apply_source_reproduction_conclusion(investigation)
