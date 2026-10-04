from __future__ import annotations

import ipaddress
import re
import shlex
import socket
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from iceman.probing import (
    ApplicationCheckReplay,
    EvidenceStability,
    HttpEvidenceSample,
    RootCauseInvestigation,
    _postgres_container,
    collect_application_replay_samples,
    replay_application_check,
    summarize_http_evidence,
)
from iceman.runner import CommandResult, run_command


_DB_SOURCE_RE = re.compile(
    r"^PostgreSQL "
    r"(?P<table>[A-Za-z_][A-Za-z0-9_]*)\."
    r"(?P<url_column>[A-Za-z_][A-Za-z0-9_]*) "
    r"\(matched by "
    r"(?P<table2>[A-Za-z_][A-Za-z0-9_]*)\."
    r"(?P<name_column>[A-Za-z_][A-Za-z0-9_]*)\)$"
)

_METADATA_HOSTS = {
    "169.254.169.254",
    "100.100.100.200",
    "metadata.google.internal",
}


@dataclass
class RemediationOption:
    number: int
    title: str
    rationale: str
    risk: str
    action: str = "manual"
    recommended: bool = False
    automatically_applicable: bool = False
    requires: str | None = None
    note: str | None = None


@dataclass
class ApplicationRemediationAdvice:
    subject: str
    cause: str
    confidence: str
    options: list[RemediationOption] = field(default_factory=list)
    ranking_basis: str | None = None
    note: str | None = None


@dataclass
class DatabaseTargetLocator:
    table: str
    name_column: str
    url_column: str
    subject: str
    old_url: str


@dataclass
class ApplicationChangePlan:
    subject: str
    option_number: int
    title: str
    cause: str
    workspace: Path
    old_url: str | None = None
    new_url: str | None = None
    locator: DatabaseTargetLocator | None = None
    preflight: ApplicationCheckReplay | None = None
    preflight_samples: list[HttpEvidenceSample] = field(default_factory=list)
    preflight_stability: EvidenceStability | None = None
    safe_to_apply: bool = False
    mutation_command: str | None = None
    verification_command: str | None = None
    rollback_command: str | None = None
    note: str | None = None


@dataclass
class ApplicationChangeResult:
    success: bool
    mutation_applied: bool
    verification_passed: bool
    message: str


def _host_http_status(
    investigation: RootCauseInvestigation,
) -> int | None:
    for step in investigation.steps:
        if step.name == "HTTP" and step.status_code is not None:
            return step.status_code
    return None


def _http_success(status: int | None) -> bool | None:
    if status is None:
        return None
    return 200 <= status < 400


def _replace_target_option(
    number: int,
    *,
    recommended: bool,
) -> RemediationOption:
    return RemediationOption(
        number=number,
        title="Replace the monitored target URL",
        rationale=(
            "Point this monitor at an endpoint that is intentionally suitable "
            "for automated health checks and returns a status accepted by the "
            "application's discovered success policy."
        ),
        risk="medium",
        action="replace_target",
        recommended=recommended,
        automatically_applicable=True,
        requires=(
            "An explicit replacement HTTP/HTTPS URL chosen by the operator. "
            "Iceman will preflight it from the emitting application container "
            "before any database write."
        ),
        note=(
            "Iceman will not invent a replacement endpoint. Only use this when "
            "the new endpoint preserves what this monitor is intended to measure."
        ),
    )


def _customize_request_option(number: int) -> RemediationOption:
    return RemediationOption(
        number=number,
        title="Customize the monitoring request",
        rationale=(
            "Keep the current endpoint but change documented request headers, "
            "authentication, redirect behavior, or other request properties only "
            "when the target's authoritative requirements justify it."
        ),
        risk="high",
        action="customize_request",
        automatically_applicable=False,
        requires=(
            "Authoritative endpoint requirements and, where applicable, a "
            "separate secrets/configuration workflow."
        ),
        note=(
            "Iceman will not guess anti-bot bypasses, credentials, cookies, "
            "or secret headers from a 401/403 response."
        ),
    )


def _change_policy_option(number: int) -> RemediationOption:
    return RemediationOption(
        number=number,
        title="Change the monitor's accepted-status policy",
        rationale=(
            "Keep the current endpoint but change what HTTP responses count as "
            "healthy."
        ),
        risk="high",
        action="change_policy",
        automatically_applicable=False,
        requires=(
            "A deliberate application-policy decision confirming that the "
            "currently rejected status genuinely represents health."
        ),
        note=(
            "This is not automatically applied because accepting an error "
            "status can hide a real outage."
        ),
    )


def _investigate_context_option(
    number: int,
    investigation: RootCauseInvestigation,
) -> RemediationOption:
    service = (
        investigation.signal_emitter.compose_service
        if investigation.signal_emitter is not None
        else "application container"
    )
    return RemediationOption(
        number=number,
        title="Investigate the backend-specific HTTP rejection",
        rationale=(
            "The host succeeds while the application container fails. Preserve "
            "the current monitored target and investigate why the request or "
            f"execution context inside {service} is treated differently."
        ),
        risk="low",
        action="investigate_context",
        recommended=True,
        automatically_applicable=False,
        requires=(
            "Read-only comparison of request/runtime characteristics, proxy "
            "environment, redirect behavior, DNS/IP selection, and relevant "
            "non-sensitive response metadata."
        ),
        note=(
            "This is a diagnostic next step, not a write. Replacing the target "
            "before understanding the context mismatch could hide the actual "
            "problem or change what the monitor measures."
        ),
    )


def _investigate_check_logic_option(
    number: int,
) -> RemediationOption:
    return RemediationOption(
        number=number,
        title="Investigate check logic or transient state",
        rationale=(
            "Both current probes satisfy the endpoint, so the recorded DOWN "
            "signal is not explained by present network/status evidence alone."
        ),
        risk="low",
        action="investigate_check_logic",
        recommended=True,
        automatically_applicable=False,
        requires=(
            "Read-only review of recent failure timing, response-content checks, "
            "dynamic request behavior, and application state."
        ),
        note="No configuration write is justified by the current evidence.",
    )


def _investigate_stability_option(
    number: int,
    investigation: RootCauseInvestigation,
) -> RemediationOption:
    return RemediationOption(
        number=number,
        title="Investigate unstable/intermittent HTTP behavior",
        rationale=(
            "Repeated evidence is mixed across one or more contexts. Preserve "
            "the monitored target and characterize the instability before "
            "changing configuration."
        ),
        risk="low",
        action="investigate_stability",
        recommended=True,
        automatically_applicable=False,
        requires=(
            "Additional read-only observations of host, application-container, "
            "and source-derived check behavior."
        ),
        note=(
            "A write based on one favorable or unfavorable sample could hide an "
            "intermittent failure."
        ),
    )


def _effective_stability(
    investigation: RootCauseInvestigation,
    context: str,
) -> EvidenceStability | None:
    if context == "host":
        return (
            investigation.session_host_stability
            or investigation.host_stability
        )
    if context == "container":
        return (
            investigation.session_container_stability
            or investigation.container_stability
        )
    if context == "source":
        return (
            investigation.session_source_stability
            or investigation.source_stability
        )
    return None


def _has_success(stability: EvidenceStability | None) -> bool:
    return bool(stability and stability.success_count > 0)


def _is(stability: EvidenceStability | None, classification: str) -> bool:
    return bool(
        stability
        and stability.classification == classification
    )


def build_application_remediation_advice(
    investigation: RootCauseInvestigation,
) -> ApplicationRemediationAdvice:
    cause = investigation.likely_cause
    subject = investigation.subject

    host = _effective_stability(investigation, "host")
    container = _effective_stability(investigation, "container")
    source = _effective_stability(investigation, "source")

    host_status = _host_http_status(investigation)
    container_status = (
        investigation.container_probe.http_status
        if investigation.container_probe is not None
        else None
    )
    source_status = (
        investigation.check_replay.status_code
        if investigation.check_replay is not None
        else None
    )

    stability_available = any(
        stability is not None
        for stability in (host, container, source)
    )

    if stability_available:
        persistent_app_failure = (
            _is(container, "stable_failure")
            or _is(source, "stable_failure")
        )
        host_has_success = _has_success(host)
        any_unstable = any(
            _is(stability, "unstable_mixed")
            for stability in (host, container, source)
        )

        if persistent_app_failure and host_has_success:
            options = [
                _investigate_context_option(1, investigation),
                _replace_target_option(2, recommended=False),
                _customize_request_option(3),
                _change_policy_option(4),
            ]
            ranking_basis = (
                "Repeated evidence shows persistent application-context failure "
                "while at least one host sample succeeds. "
                f"Host={host.summary if host else 'unknown'}; "
                f"container={container.summary if container else 'unknown'}; "
                f"source={source.summary if source else 'unknown'}."
            )
        elif any_unstable:
            options = [
                _investigate_stability_option(1, investigation),
                _investigate_context_option(2, investigation),
                _replace_target_option(3, recommended=False),
                _customize_request_option(4),
                _change_policy_option(5),
            ]
            ranking_basis = (
                "Repeated evidence is unstable/mixed, so remediation is ranked "
                "behind further read-only characterization. "
                f"Host={host.summary if host else 'unknown'}; "
                f"container={container.summary if container else 'unknown'}; "
                f"source={source.summary if source else 'unknown'}."
            )
        elif (
            _is(host, "stable_success")
            and _is(container, "stable_success")
            and (
                source is None
                or _is(source, "stable_success")
            )
        ):
            options = [
                _investigate_check_logic_option(1),
                _replace_target_option(2, recommended=False),
                _customize_request_option(3),
                _change_policy_option(4),
            ]
            ranking_basis = (
                "Repeated current/session evidence is stable-success in the "
                "available contexts, so no configuration write is justified first. "
                f"Host={host.summary if host else 'unknown'}; "
                f"container={container.summary if container else 'unknown'}; "
                f"source={source.summary if source else 'unknown'}."
            )
        else:
            options = [
                _replace_target_option(1, recommended=True),
                _customize_request_option(2),
                _change_policy_option(3),
            ]
            ranking_basis = (
                "Repeated evidence does not show a successful-host/persistent-app "
                "mismatch or mixed instability. "
                f"Host={host.summary if host else f'HTTP {host_status}'}; "
                f"container={container.summary if container else f'HTTP {container_status}'}; "
                f"source={source.summary if source else f'HTTP {source_status}'}."
            )
    else:
        # Compatibility fallback for imported/legacy/synthetic investigation
        # objects that predate v0.7.2 evidence series. Real v0.7.2 network
        # investigations populate stability fields before recommendations.
        host_ok = _http_success(host_status)
        container_ok = _http_success(container_status)

        execution_context_mismatch = (
            host_ok is True
            and container_ok is False
        )

        both_currently_healthy = (
            host_ok is True
            and container_ok is True
            and (
                investigation.check_replay is None
                or investigation.check_replay.accepted_by_policy is True
            )
        )

        if execution_context_mismatch:
            options = [
                _investigate_context_option(1, investigation),
                _replace_target_option(2, recommended=False),
                _customize_request_option(3),
                _change_policy_option(4),
            ]
            ranking_basis = (
                f"Legacy single-sample fallback: host HTTP {host_status} succeeds, "
                f"while "
                f"{investigation.signal_emitter.compose_service if investigation.signal_emitter else 'application container'} "
                f"HTTP {container_status} fails"
                + (
                    f"; source-derived replay also returns HTTP {source_status}"
                    if source_status is not None
                    else ""
                )
                + "."
            )
        elif both_currently_healthy:
            options = [
                _investigate_check_logic_option(1),
                _replace_target_option(2, recommended=False),
                _customize_request_option(3),
                _change_policy_option(4),
            ]
            ranking_basis = (
                f"Legacy single-sample fallback: host HTTP {host_status} and "
                f"application-container HTTP {container_status} both succeed."
            )
        else:
            options = [
                _replace_target_option(1, recommended=True),
                _customize_request_option(2),
                _change_policy_option(3),
            ]
            ranking_basis = (
                "Legacy single-sample fallback because no v0.7.2 stability "
                "series was present. "
                f"Host HTTP {host_status}; application-container HTTP "
                f"{container_status}."
            )

    replacement = next(
        (
            option
            for option in options
            if option.action == "replace_target"
        ),
        None,
    )

    selected = investigation.resolution.selected
    if replacement is not None and (
        selected is None
        or selected.source_kind != "database"
        or not _parse_database_locator(investigation)
    ):
        replacement.automatically_applicable = False
        replacement.note = (
            "The current target is not backed by a uniquely identified PostgreSQL "
            "name/URL row, so Iceman can recommend replacement but will not write "
            "it automatically."
        )

    if replacement is not None and (
        investigation.signal_emitter is None
        or investigation.check_profile is None
    ):
        replacement.automatically_applicable = False
        replacement.note = (
            "Iceman needs both the emitting application container and a discovered "
            "application-check profile before it can safely preflight a replacement."
        )

    return ApplicationRemediationAdvice(
        subject=subject,
        cause=cause,
        confidence=investigation.confidence,
        options=options,
        ranking_basis=ranking_basis,
        note=(
            "Recommendations are ranked from repeated current-run evidence and, "
            "when available, bounded evidence accumulated in this interactive "
            "Iceman session. No option is applied from a recommendation alone."
        ),
    )


def _parse_database_locator(
    investigation: RootCauseInvestigation,
) -> DatabaseTargetLocator | None:
    selected = investigation.resolution.selected
    if (
        selected is None
        or selected.source_kind != "database"
        or not investigation.url
    ):
        return None

    match = _DB_SOURCE_RE.fullmatch(selected.source_file.strip())
    if not match:
        return None

    if match.group("table") != match.group("table2"):
        return None

    return DatabaseTargetLocator(
        table=match.group("table"),
        name_column=match.group("name_column"),
        url_column=match.group("url_column"),
        subject=investigation.subject,
        old_url=investigation.url,
    )


def _sql_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Unsafe SQL identifier")
    return f'"{value}"'


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _psql_command(
    container_id: str,
    user: str,
    database: str,
    sql: str,
) -> str:
    return " ".join(
        shlex.quote(part)
        for part in (
            "docker",
            "exec",
            container_id,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            database,
            "-At",
            "-c",
            sql,
        )
    )


def _validate_replacement_url(url: str) -> tuple[bool, str | None]:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        return False, f"Invalid URL: {exc}"

    if parsed.scheme not in {"http", "https"}:
        return False, "Replacement target must use HTTP or HTTPS."

    if not parsed.hostname:
        return False, "Replacement target must contain a hostname."

    if parsed.username or parsed.password:
        return False, "Embedded URL credentials are not allowed."

    host = parsed.hostname.lower().strip("[]")
    if host in _METADATA_HOSTS:
        return False, "Cloud metadata endpoints cannot be used as replacement targets."

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if ip is not None and ip.is_link_local:
        return False, "Link-local replacement targets are blocked."

    # Also block a hostname that resolves to a link-local metadata-style address.
    try:
        infos = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        # Let the application-container preflight provide the operational failure.
        infos = []

    for info in infos:
        address = info[4][0]
        try:
            resolved_ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if resolved_ip.is_link_local:
            return False, (
                "Replacement hostname resolves to a link-local address; "
                "automatic probing/writing is blocked."
            )

    return True, None


def prepare_application_change_plan(
    investigation: RootCauseInvestigation,
    option_number: int,
    replacement_url: str | None,
    workspace: Path,
) -> ApplicationChangePlan:
    workspace = workspace.resolve()

    advice = build_application_remediation_advice(investigation)
    selected_option = next(
        (
            option
            for option in advice.options
            if option.number == option_number
        ),
        None,
    )

    if selected_option is None:
        return ApplicationChangePlan(
            subject=investigation.subject,
            option_number=option_number,
            title=f"Unknown remediation option {option_number}",
            cause=investigation.likely_cause,
            workspace=workspace,
            safe_to_apply=False,
            note="That remediation option is not present in the current evidence-ranked plan.",
        )

    if selected_option.action != "replace_target":
        return ApplicationChangePlan(
            subject=investigation.subject,
            option_number=option_number,
            title=selected_option.title,
            cause=investigation.likely_cause,
            workspace=workspace,
            safe_to_apply=False,
            note=(
                "This option is not a bounded automatic database write. "
                "No changes were prepared. "
                + (selected_option.note or "")
            ).strip(),
        )

    locator = _parse_database_locator(investigation)
    plan = ApplicationChangePlan(
        subject=investigation.subject,
        option_number=option_number,
        title=selected_option.title,
        cause=investigation.likely_cause,
        workspace=workspace,
        old_url=investigation.url,
        new_url=replacement_url,
        locator=locator,
    )

    if not replacement_url:
        plan.note = (
            f"Option {option_number} requires an explicit replacement URL. "
            "No change was prepared. Use: "
            f"apply option {option_number} with https://your-intended-health-endpoint"
        )
        return plan

    valid, reason = _validate_replacement_url(replacement_url)
    if not valid:
        plan.note = reason
        return plan

    if locator is None:
        plan.note = (
            "Iceman cannot map the current target to a unique PostgreSQL "
            "name/URL location, so it will not prepare an automatic database write."
        )
        return plan

    emitter = investigation.signal_emitter
    profile = investigation.check_profile
    if emitter is None or profile is None:
        plan.note = (
            "A unique emitting application container and source-derived check "
            "profile are required before a replacement can be preflighted."
        )
        return plan

    if profile.fidelity != "high":
        plan.note = (
            "The discovered application-check reproduction fidelity is not high "
            "enough for automatic target replacement. No write was prepared."
        )
        return plan

    preflight_replays, preflight_samples = collect_application_replay_samples(
        workspace,
        emitter,
        replacement_url,
        profile,
        sample_count=3,
    )
    plan.preflight = (
        preflight_replays[0]
        if preflight_replays
        else None
    )
    plan.preflight_samples = preflight_samples
    plan.preflight_stability = summarize_http_evidence(
        "replacement-source-derived-preflight",
        preflight_samples,
    )

    replay_errors = [
        replay.error
        for replay in preflight_replays
        if replay.error
    ]
    if replay_errors:
        plan.note = (
            "Replacement preflight failed inside the emitting application "
            f"container: {replay_errors[0]}"
        )
        return plan

    if plan.preflight_stability.classification != "stable_success":
        plan.note = (
            "Replacement preflight is not stable-success across three "
            "source-derived samples and does not pass the v0.7.2 write "
            "requirement "
            f"({plan.preflight_stability.summary}). "
            "The database will not be changed."
        )
        return plan

    postgres = _postgres_container(workspace)
    if postgres is None:
        plan.note = (
            "A running Compose PostgreSQL service could not be resolved for the "
            "write plan. No change was prepared."
        )
        return plan

    container_id, env = postgres
    user = env.get("POSTGRES_USER") or "postgres"
    database = env.get("POSTGRES_DB") or user

    table = _sql_identifier(locator.table)
    name_col = _sql_identifier(locator.name_column)
    url_col = _sql_identifier(locator.url_column)
    subject = _sql_literal(locator.subject)
    old_url = _sql_literal(locator.old_url)
    new_url = _sql_literal(replacement_url)

    where_old = (
        f"lower(COALESCE({name_col}::text, '')) = lower({subject}) "
        f"AND COALESCE({url_col}::text, '') = {old_url}"
    )
    where_new = (
        f"lower(COALESCE({name_col}::text, '')) = lower({subject}) "
        f"AND COALESCE({url_col}::text, '') = {new_url}"
    )

    mutation_sql = f"""
DO $iceman$
DECLARE
  matches integer;
BEGIN
  SELECT count(*) INTO matches
  FROM {table}
  WHERE {where_old};

  IF matches <> 1 THEN
    RAISE EXCEPTION 'Iceman precondition failed: expected exactly one matching row, found %', matches;
  END IF;

  UPDATE {table}
  SET {url_col} = {new_url}
  WHERE {where_old};

  IF NOT EXISTS (
    SELECT 1 FROM {table} WHERE {where_new}
  ) THEN
    RAISE EXCEPTION 'Iceman verification failed inside transaction';
  END IF;
END
$iceman$;
""".strip()

    verify_sql = f"""
SELECT count(*)
FROM {table}
WHERE {where_new};
""".strip()

    rollback_sql = f"""
DO $iceman$
DECLARE
  matches integer;
BEGIN
  SELECT count(*) INTO matches
  FROM {table}
  WHERE {where_new};

  IF matches <> 1 THEN
    RAISE EXCEPTION 'Iceman rollback precondition failed: expected exactly one matching row, found %', matches;
  END IF;

  UPDATE {table}
  SET {url_col} = {old_url}
  WHERE {where_new};
END
$iceman$;
""".strip()

    plan.mutation_command = _psql_command(
        container_id,
        user,
        database,
        mutation_sql,
    )
    plan.verification_command = _psql_command(
        container_id,
        user,
        database,
        verify_sql,
    )
    plan.rollback_command = _psql_command(
        container_id,
        user,
        database,
        rollback_sql,
    )
    plan.safe_to_apply = True
    plan.note = (
        "The replacement passed the application's source-derived check policy "
        "from the emitting container. The write is guarded by an exact old-value "
        "precondition and must affect exactly one database row."
    )
    return plan


def execute_application_change(
    plan: ApplicationChangePlan,
) -> ApplicationChangeResult:
    if (
        not plan.safe_to_apply
        or not plan.mutation_command
        or not plan.verification_command
    ):
        return ApplicationChangeResult(
            success=False,
            mutation_applied=False,
            verification_passed=False,
            message="The application change plan is not safe/applicable.",
        )

    mutation = run_command(
        plan.mutation_command,
        cwd=plan.workspace,
    )
    if mutation.returncode != 0:
        return ApplicationChangeResult(
            success=False,
            mutation_applied=False,
            verification_passed=False,
            message=(
                "Database mutation failed and PostgreSQL reported an error: "
                + (mutation.stderr.strip() or mutation.stdout.strip() or "unknown error")
            ),
        )

    verification = run_command(
        plan.verification_command,
        cwd=plan.workspace,
    )
    verified = (
        verification.returncode == 0
        and verification.stdout.strip() == "1"
    )

    if verified:
        return ApplicationChangeResult(
            success=True,
            mutation_applied=True,
            verification_passed=True,
            message="Database target update verified exactly once.",
        )

    # A failed read-back after a successful mutation is exceptional. Attempt
    # the bounded inverse only when it was prepared from the exact old/new pair.
    rollback_message = ""
    if plan.rollback_command:
        rollback = run_command(
            plan.rollback_command,
            cwd=plan.workspace,
        )
        if rollback.returncode == 0:
            rollback_message = " Automatic rollback completed."
        else:
            rollback_message = (
                " Automatic rollback also failed; manual database review is required."
            )

    return ApplicationChangeResult(
        success=False,
        mutation_applied=True,
        verification_passed=False,
        message=(
            "The database mutation ran, but read-back verification did not "
            f"confirm exactly one updated row.{rollback_message}"
        ),
    )
