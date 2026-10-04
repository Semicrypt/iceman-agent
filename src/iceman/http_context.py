from __future__ import annotations

import json
import os
import platform
import shlex
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from iceman.probing import (
    ApplicationCheckProfile,
    EvidenceStability,
    RootCauseInvestigation,
    SignalEmitter,
)
from iceman.runner import run_command


_SAFE_REQUEST_HEADERS = {
    "accept",
    "accept-encoding",
    "connection",
    "host",
    "user-agent",
}

_SAFE_RESPONSE_HEADERS = {
    "alt-svc",
    "cf-cache-status",
    "cf-ray",
    "content-type",
    "location",
    "server",
    "strict-transport-security",
    "via",
    "x-amz-cf-id",
    "x-amz-cf-pop",
    "x-cache",
    "x-cache-hits",
    "x-envoy-upstream-service-time",
    "x-request-id",
    "x-served-by",
    "x-vercel-id",
}

_PROXY_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
)


@dataclass
class HttpContextSnapshot:
    context: str
    client: str
    runtime: str | None = None
    method: str = "GET"
    status_code: int | None = None
    sample_statuses: list[int | None] = field(default_factory=list)
    final_url: str | None = None
    redirect_chain: list[str] = field(default_factory=list)
    resolved_addresses: list[str] = field(default_factory=list)
    selected_remote_address: str | None = None
    address_family: str | None = None
    tls_version: str | None = None
    alpn_protocol: str | None = None
    transport_observation_source: str | None = None
    transport_event: str | None = None
    request_headers: dict[str, str] = field(default_factory=dict)
    response_headers: dict[str, str] = field(default_factory=dict)
    proxy_env_present: list[str] = field(default_factory=list)
    dns_servers: list[str] = field(default_factory=list)
    container_networks: list[str] = field(default_factory=list)
    omitted_header_names: list[str] = field(default_factory=list)
    latency_ms: int | None = None
    error: str | None = None
    note: str | None = None


@dataclass
class DifferentialFinding:
    category: str
    observation: str
    interpretation: str


@dataclass
class HypothesisAssessment:
    hypothesis: str
    status: str
    evidence: str


@dataclass
class HttpContextDifferential:
    subject: str
    target_url: str
    host: HttpContextSnapshot
    application: HttpContextSnapshot
    findings: list[DifferentialFinding] = field(default_factory=list)
    hypotheses: list[HypothesisAssessment] = field(default_factory=list)
    conclusion: str = ""
    confidence: str = "low"
    confidence_basis: str = ""
    recommended_next_action: str = ""
    safety_note: str = (
        "This workflow is read-only. It compares non-sensitive request/runtime/"
        "response metadata and does not attempt WAF/bot bypasses, browser "
        "fingerprint spoofing, credential discovery, or secret-header guessing."
    )


class _TrackingRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.chain: list[str] = []

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        self.chain.append(_safe_url_display(newurl))
        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            newurl,
        )


def _safe_url_display(url: str | None) -> str | None:
    if not url:
        return url
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return "<invalid-url>"

    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"

    path = parsed.path or "/"
    display = urllib.parse.urlunsplit(
        (parsed.scheme, host, path, "", "")
    )
    if parsed.query:
        display += "?[query omitted]"
    return display


def _headers_allowlist(
    headers,
    allowed: set[str],
) -> dict[str, str]:
    output: dict[str, str] = {}
    if headers is None:
        return output

    try:
        items = headers.items()
    except AttributeError:
        items = []

    for key, value in items:
        key_l = str(key).lower()
        if key_l not in allowed:
            continue
        value_text = str(value)
        if key_l == "location":
            value_text = _safe_url_display(value_text) or "-"
        output[key_l] = value_text
    return output


def _proxy_presence_from_names(names: set[str]) -> list[str]:
    present: list[str] = []
    for canonical in _PROXY_NAMES:
        if canonical in names or canonical.lower() in names:
            present.append(canonical)
    return present


def _host_proxy_presence() -> list[str]:
    return _proxy_presence_from_names(set(os.environ))


def _resolved_addresses(url: str) -> list[str]:
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        if not host:
            return []
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
        return sorted({
            info[4][0]
            for info in infos
            if info and len(info) >= 5 and info[4]
        })
    except (OSError, ValueError):
        return []


def _socket_metadata(response) -> tuple[str | None, str | None, str | None, str | None]:
    candidates = [
        getattr(response, "fp", None),
        getattr(getattr(response, "fp", None), "raw", None),
        getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None),
    ]

    sock = None
    for candidate in reversed(candidates):
        if candidate is not None and hasattr(candidate, "getpeername"):
            sock = candidate
            break

    if sock is None:
        return None, None, None, None

    remote = None
    family = None
    tls_version = None
    alpn = None

    try:
        peer = sock.getpeername()
        remote = str(peer[0]) if isinstance(peer, tuple) and peer else str(peer)
    except OSError:
        pass

    sock_family = getattr(sock, "family", None)
    if sock_family == socket.AF_INET:
        family = "IPv4"
    elif sock_family == socket.AF_INET6:
        family = "IPv6"

    try:
        if hasattr(sock, "version"):
            tls_version = sock.version()
    except Exception:
        pass

    try:
        if hasattr(sock, "selected_alpn_protocol"):
            alpn = sock.selected_alpn_protocol()
    except Exception:
        pass

    return remote, family, tls_version, alpn


def _capture_host_once(
    url: str,
    timeout: float,
) -> HttpContextSnapshot:
    redirect_handler = _TrackingRedirectHandler()
    opener = urllib.request.build_opener(redirect_handler)
    request_headers = {
        "User-Agent": "Iceman/0.7.5 context-differential",
        "Accept": "*/*",
    }
    request = urllib.request.Request(
        url,
        headers=request_headers,
        method="GET",
    )

    started = time.monotonic()
    snapshot = HttpContextSnapshot(
        context="host",
        client="Python urllib",
        runtime=(
            f"Python {platform.python_version()} "
            f"({platform.system()})"
        ),
        method="GET",
        resolved_addresses=_resolved_addresses(url),
        request_headers={
            key.lower(): value
            for key, value in request_headers.items()
            if key.lower() in _SAFE_REQUEST_HEADERS
        },
        proxy_env_present=_host_proxy_presence(),
    )

    try:
        with opener.open(request, timeout=timeout) as response:
            response.read(256)
            snapshot.status_code = int(response.status)
            snapshot.final_url = _safe_url_display(response.geturl())
            snapshot.redirect_chain = list(redirect_handler.chain)
            snapshot.response_headers = _headers_allowlist(
                response.headers,
                _SAFE_RESPONSE_HEADERS,
            )
            (
                snapshot.selected_remote_address,
                snapshot.address_family,
                snapshot.tls_version,
                snapshot.alpn_protocol,
            ) = _socket_metadata(response)
            if snapshot.selected_remote_address or snapshot.tls_version:
                snapshot.transport_observation_source = (
                    "urllib response socket"
                )
    except urllib.error.HTTPError as exc:
        snapshot.status_code = int(exc.code)
        snapshot.final_url = _safe_url_display(exc.geturl())
        snapshot.redirect_chain = list(redirect_handler.chain)
        snapshot.response_headers = _headers_allowlist(
            exc.headers,
            _SAFE_RESPONSE_HEADERS,
        )
        try:
            (
                snapshot.selected_remote_address,
                snapshot.address_family,
                snapshot.tls_version,
                snapshot.alpn_protocol,
            ) = _socket_metadata(exc)
            if snapshot.selected_remote_address or snapshot.tls_version:
                snapshot.transport_observation_source = (
                    "urllib HTTPError response socket"
                )
        except Exception:
            pass
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        snapshot.error = str(exc)
    finally:
        snapshot.latency_ms = int(
            (time.monotonic() - started) * 1000
        )

    return snapshot


def capture_host_context(
    url: str,
    *,
    timeout: float = 5.0,
    attempts: int = 3,
) -> HttpContextSnapshot:
    snapshots: list[HttpContextSnapshot] = []
    for _ in range(max(1, min(attempts, 3))):
        snapshots.append(_capture_host_once(url, timeout))

    statuses = [item.status_code for item in snapshots]

    successful = next(
        (
            item
            for item in snapshots
            if item.status_code is not None
            and 200 <= item.status_code < 400
        ),
        None,
    )
    selected = successful or snapshots[-1]
    selected.sample_statuses = statuses

    if successful is not None and len(set(statuses)) > 1:
        selected.note = (
            "A successful host sample was selected for metadata comparison; "
            "the bounded host attempts were mixed."
        )
    elif len(set(statuses)) > 1:
        selected.note = "Bounded host attempts returned mixed statuses."

    return selected


_CONTAINER_CONTEXT_SCRIPT = r"""
const target = process.argv[1];
const profile = JSON.parse(process.argv[2]);
const dns = require("dns");
const http = require("http");
const https = require("https");
const { URL } = require("url");

const SAFE_REQ = new Set([
  "accept",
  "accept-encoding",
  "connection",
  "host",
  "user-agent"
]);

const SAFE_RESP = new Set([
  "alt-svc",
  "cf-cache-status",
  "cf-ray",
  "content-type",
  "location",
  "server",
  "strict-transport-security",
  "via",
  "x-amz-cf-id",
  "x-amz-cf-pop",
  "x-cache",
  "x-cache-hits",
  "x-envoy-upstream-service-time",
  "x-request-id",
  "x-served-by",
  "x-vercel-id"
]);

function keepHeaders(input, allowed) {
  const out = {};
  if (!input) return out;
  for (const [key, value] of Object.entries(input)) {
    const lower = String(key).toLowerCase();
    if (!allowed.has(lower)) continue;
    out[lower] = String(value);
  }
  return out;
}

function proxyPresence() {
  const names = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"];
  return names.filter((name) => {
    return Object.prototype.hasOwnProperty.call(process.env, name) ||
      Object.prototype.hasOwnProperty.call(process.env, name.toLowerCase());
  });
}

function normalizeFamily(value) {
  if (value === 4 || value === "4" || value === "IPv4") return "IPv4";
  if (value === 6 || value === "6" || value === "IPv6") return "IPv6";
  if (value === undefined || value === null || value === "") return null;
  return String(value);
}

// Passive live transport capture. No request options, headers, agent, TLS,
// redirect, or socket settings are changed.
let liveTransport = {
  selected_remote_address: null,
  address_family: null,
  tls_version: null,
  alpn_protocol: null,
  source: null,
  event: null
};

function captureSocket(socket, source, eventName) {
  if (!socket) return;

  const remoteAddress = socket.remoteAddress || null;
  const family = normalizeFamily(socket.remoteFamily);

  let tlsVersion = null;
  try {
    if (typeof socket.getProtocol === "function") {
      tlsVersion = socket.getProtocol() || null;
    }
  } catch (_) {}

  let alpn = null;
  try {
    alpn = socket.alpnProtocol || null;
  } catch (_) {}

  // Prefer observations that contain live connection metadata. Later redirect
  // requests can legitimately replace earlier observations.
  if (
    remoteAddress ||
    family ||
    tlsVersion ||
    alpn ||
    !liveTransport.source
  ) {
    liveTransport = {
      selected_remote_address: remoteAddress,
      address_family: family,
      tls_version: tlsVersion,
      alpn_protocol: alpn,
      source,
      event: eventName
    };
  }
}

function attachSocketObservers(request, protocolName) {
  if (!request || typeof request.once !== "function") return request;

  request.once("socket", (socket) => {
    captureSocket(
      socket,
      `live ${protocolName}.request socket event`,
      "socket"
    );

    // A reused keep-alive socket may already be connected at the socket event.
    if (socket && socket.remoteAddress) {
      captureSocket(
        socket,
        `live ${protocolName}.request socket event`,
        "socket-connected"
      );
    }

    if (socket && typeof socket.once === "function") {
      socket.once("connect", () => {
        captureSocket(
          socket,
          `live ${protocolName}.request connect event`,
          "connect"
        );
      });

      socket.once("secureConnect", () => {
        captureSocket(
          socket,
          `live ${protocolName}.request secureConnect event`,
          "secureConnect"
        );
      });
    }
  });

  return request;
}

function instrumentRequest(moduleObject, protocolName) {
  const original = moduleObject.request;
  moduleObject.request = function instrumentedRequest(...args) {
    const request = original.apply(moduleObject, args);
    return attachSocketObservers(request, protocolName);
  };
  return original;
}

// Instrument native request creation BEFORE Axios/follow-redirects is loaded.
// The originals are restored in finally.
const originalHttpRequest = instrumentRequest(http, "http");
const originalHttpsRequest = instrumentRequest(https, "https");

(async () => {
  const started = Date.now();
  let resolvedAddresses = [];

  try {
    const parsed = new URL(target);
    const resolved = await dns.promises.lookup(
      parsed.hostname,
      { all: true }
    );
    resolvedAddresses = resolved.map((item) => ({
      address: item.address,
      family: normalizeFamily(item.family)
    }));
  } catch (_) {
    resolvedAddresses = [];
  }

  try {
    // Deliberately loaded after native request instrumentation.
    const axios = require("axios");

    const response = await axios.request({
      url: target,
      method: profile.method || "GET",
      timeout: profile.timeout_ms || 5000,
      headers: profile.headers || {},
      maxRedirects: profile.follow_redirects === false ? 0 : 5,
      validateStatus: () => true
    });

    const request = response.request;
    const requestHeaders =
      request && typeof request.getHeaders === "function"
        ? request.getHeaders()
        : (
          request && request._currentRequest &&
          typeof request._currentRequest.getHeaders === "function"
            ? request._currentRequest.getHeaders()
            : (
              request && request._redirectable &&
              request._redirectable._currentRequest &&
              typeof request._redirectable._currentRequest.getHeaders === "function"
                ? request._redirectable._currentRequest.getHeaders()
                : {}
            )
        );

    let finalUrl = target;
    let redirectCount = 0;
    if (request && request._redirectable) {
      finalUrl = request._redirectable._currentUrl || finalUrl;
      redirectCount = request._redirectable._redirectCount || 0;
    } else if (request && request.res && request.res.responseUrl) {
      finalUrl = request.res.responseUrl;
    }

    // Fallback only: if live capture somehow produced no transport metadata,
    // inspect the response-side request graph without changing the request.
    if (!liveTransport.selected_remote_address && request) {
      const fallbackCandidates = [
        ["response.request.socket", request.socket],
        ["response.request.res.socket", request.res && request.res.socket],
        [
          "response.request.res.req.socket",
          request.res && request.res.req && request.res.req.socket
        ],
        [
          "response.request._currentRequest.socket",
          request._currentRequest && request._currentRequest.socket
        ],
        [
          "response.request._redirectable._currentRequest.socket",
          request._redirectable &&
          request._redirectable._currentRequest &&
          request._redirectable._currentRequest.socket
        ]
      ];

      for (const [source, socket] of fallbackCandidates) {
        if (!socket) continue;
        captureSocket(socket, source, "post-response-fallback");
        if (liveTransport.selected_remote_address) break;
      }
    }

    const payload = {
      status: response.status,
      latency_ms: Date.now() - started,
      runtime: `axios/${axios.VERSION || "unknown"} on ${process.version}`,
      final_url: finalUrl,
      redirect_count: redirectCount,
      resolved_addresses: resolvedAddresses,
      request_headers: keepHeaders(requestHeaders, SAFE_REQ),
      response_headers: keepHeaders(
        response.headers && typeof response.headers.toJSON === "function"
          ? response.headers.toJSON()
          : response.headers,
        SAFE_RESP
      ),
      selected_remote_address: liveTransport.selected_remote_address,
      address_family: liveTransport.address_family,
      tls_version: liveTransport.tls_version,
      alpn_protocol: liveTransport.alpn_protocol,
      transport_observation_source: liveTransport.source,
      transport_event: liveTransport.event,
      dns_servers: dns.getServers(),
      proxy_env_present: proxyPresence()
    };

    console.log(JSON.stringify(payload));
  } catch (err) {
    console.log(JSON.stringify({
      error: String(err && (err.message || err)),
      latency_ms: Date.now() - started,
      runtime: process.version,
      resolved_addresses: resolvedAddresses,
      selected_remote_address: liveTransport.selected_remote_address,
      address_family: liveTransport.address_family,
      tls_version: liveTransport.tls_version,
      alpn_protocol: liveTransport.alpn_protocol,
      transport_observation_source: liveTransport.source,
      transport_event: liveTransport.event,
      dns_servers: dns.getServers(),
      proxy_env_present: proxyPresence()
    }));
    process.exitCode = 2;
  } finally {
    http.request = originalHttpRequest;
    https.request = originalHttpsRequest;
  }
})();
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


def _container_metadata(
    workspace: Path,
    container_id: str,
) -> tuple[list[str], list[str]]:
    try:
        result = run_command(
            " ".join(
                shlex.quote(part)
                for part in ("docker", "inspect", container_id)
            ),
            cwd=workspace,
        )
    except (OSError, PermissionError):
        return [], []

    if result.returncode != 0:
        return [], []

    try:
        payload = json.loads(result.stdout)
        info = payload[0]
    except (json.JSONDecodeError, IndexError, TypeError):
        return [], []

    env_names: set[str] = set()
    for item in (
        info.get("Config", {}).get("Env", [])
        if isinstance(info, dict)
        else []
    ):
        if not isinstance(item, str):
            continue
        env_names.add(item.split("=", 1)[0])

    proxy_presence = _proxy_presence_from_names(env_names)

    networks = []
    try:
        networks = sorted(
            info.get("NetworkSettings", {})
            .get("Networks", {})
            .keys()
        )
    except AttributeError:
        networks = []

    return proxy_presence, networks


def capture_application_context(
    workspace: Path,
    emitter: SignalEmitter,
    url: str,
    profile: ApplicationCheckProfile,
) -> HttpContextSnapshot:
    snapshot = HttpContextSnapshot(
        context="application-container",
        client=profile.client,
        method=profile.method,
        omitted_header_names=list(profile.omitted_header_names),
    )

    proxy_from_inspect, networks = _container_metadata(
        workspace,
        emitter.container_id,
    )
    snapshot.container_networks = networks

    profile_payload = {
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
                _CONTAINER_CONTEXT_SCRIPT,
                url,
                json.dumps(profile_payload, separators=(",", ":")),
            ],
        )
    except (OSError, PermissionError) as exc:
        snapshot.error = str(exc)
        snapshot.proxy_env_present = proxy_from_inspect
        return snapshot

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
        snapshot.error = (
            "Container differential probe returned no parseable structured output."
        )
        snapshot.proxy_env_present = proxy_from_inspect
        return snapshot

    snapshot.runtime = structured.get("runtime")
    snapshot.status_code = (
        int(structured["status"])
        if structured.get("status") is not None
        else None
    )
    snapshot.sample_statuses = [snapshot.status_code]
    snapshot.final_url = _safe_url_display(
        structured.get("final_url")
    )
    snapshot.redirect_chain = (
        [f"{int(structured.get('redirect_count', 0))} redirect(s)"]
        if structured.get("redirect_count")
        else []
    )
    snapshot.request_headers = {
        str(key).lower(): str(value)
        for key, value in (
            structured.get("request_headers") or {}
        ).items()
        if str(key).lower() in _SAFE_REQUEST_HEADERS
    }
    snapshot.response_headers = {
        str(key).lower(): (
            _safe_url_display(str(value))
            if str(key).lower() == "location"
            else str(value)
        )
        for key, value in (
            structured.get("response_headers") or {}
        ).items()
        if str(key).lower() in _SAFE_RESPONSE_HEADERS
    }
    snapshot.resolved_addresses = sorted({
        str(item.get("address"))
        for item in (structured.get("resolved_addresses") or [])
        if isinstance(item, dict) and item.get("address")
    })
    snapshot.selected_remote_address = structured.get(
        "selected_remote_address"
    )
    snapshot.address_family = structured.get("address_family")
    snapshot.tls_version = structured.get("tls_version")
    snapshot.alpn_protocol = structured.get("alpn_protocol")
    snapshot.transport_observation_source = structured.get(
        "transport_observation_source"
    )
    snapshot.transport_event = structured.get("transport_event")
    snapshot.dns_servers = [
        str(value)
        for value in structured.get("dns_servers") or []
    ]
    snapshot.proxy_env_present = sorted(set(
        proxy_from_inspect
        + [
            str(value)
            for value in structured.get("proxy_env_present") or []
        ]
    ))
    snapshot.container_networks = networks
    snapshot.latency_ms = structured.get("latency_ms")
    snapshot.error = structured.get("error")
    return snapshot


def _effective_stability(
    investigation: RootCauseInvestigation,
    kind: str,
) -> EvidenceStability | None:
    if kind == "host":
        return (
            investigation.session_host_stability
            or investigation.host_stability
        )
    if kind == "container":
        return (
            investigation.session_container_stability
            or investigation.container_stability
        )
    if kind == "source":
        return (
            investigation.session_source_stability
            or investigation.source_stability
        )
    return None


def _header(snapshot: HttpContextSnapshot, name: str) -> str | None:
    return snapshot.response_headers.get(name.lower())


def build_differential_findings(
    investigation: RootCauseInvestigation,
    host: HttpContextSnapshot,
    application: HttpContextSnapshot,
) -> list[DifferentialFinding]:
    findings: list[DifferentialFinding] = []

    host_stability = _effective_stability(investigation, "host")
    source_stability = _effective_stability(investigation, "source")

    if (
        host_stability is not None
        and host_stability.success_count > 0
        and source_stability is not None
        and source_stability.classification == "stable_failure"
    ):
        findings.append(
            DifferentialFinding(
                category="stability",
                observation=(
                    f"Host evidence includes {host_stability.success_count} "
                    f"successful sample(s), while the source-derived application "
                    f"check is {source_stability.summary}."
                ),
                interpretation=(
                    "The failure is persistent in the monitored application "
                    "context even though the target is sometimes reachable from "
                    "the host."
                ),
            )
        )

    host_ua = host.request_headers.get("user-agent")
    app_ua = application.request_headers.get("user-agent")
    if host_ua and app_ua and host_ua != app_ua:
        findings.append(
            DifferentialFinding(
                category="request",
                observation=(
                    f"User-Agent differs: host='{host_ua}', "
                    f"application='{app_ua}'."
                ),
                interpretation=(
                    "Request identity differs. This is a candidate difference, "
                    "not proof that changing/spoofing User-Agent is appropriate."
                ),
            )
        )

    if host.final_url and application.final_url and host.final_url != application.final_url:
        findings.append(
            DifferentialFinding(
                category="redirects",
                observation=(
                    f"Final URL differs: host={host.final_url}; "
                    f"application={application.final_url}."
                ),
                interpretation=(
                    "Redirect handling or request context may be producing "
                    "different destinations."
                ),
            )
        )
    elif host.final_url and application.final_url:
        findings.append(
            DifferentialFinding(
                category="redirects",
                observation=f"Both contexts end at {host.final_url}.",
                interpretation=(
                    "A different final destination is not indicated by this "
                    "comparison."
                ),
            )
        )

    host_server = _header(host, "server")
    app_server = _header(application, "server")
    if host_server and app_server:
        if host_server.lower() == app_server.lower():
            findings.append(
                DifferentialFinding(
                    category="response",
                    observation=(
                        f"Both responses identify server/edge '{host_server}'."
                    ),
                    interpretation=(
                        "Both requests appear to reach the same named edge/server "
                        "family, though individual edge nodes and policies can vary."
                    ),
                )
            )
        else:
            findings.append(
                DifferentialFinding(
                    category="response",
                    observation=(
                        f"Server metadata differs: host='{host_server}', "
                        f"application='{app_server}'."
                    ),
                    interpretation=(
                        "The contexts may be reaching different response paths "
                        "or intermediaries."
                    ),
                )
            )

    if host.proxy_env_present != application.proxy_env_present:
        findings.append(
            DifferentialFinding(
                category="proxy",
                observation=(
                    "Proxy environment presence differs: "
                    f"host={host.proxy_env_present or ['none']}; "
                    f"application={application.proxy_env_present or ['none']}."
                ),
                interpretation=(
                    "Proxy configuration may alter routing or request handling. "
                    "Only variable presence is reported; values are not exposed."
                ),
            )
        )
    else:
        findings.append(
            DifferentialFinding(
                category="proxy",
                observation=(
                    "No proxy-environment presence difference was detected "
                    f"({host.proxy_env_present or ['none']})."
                ),
                interpretation=(
                    "A simple proxy-environment mismatch is not indicated."
                ),
            )
        )

    if (
        host.tls_version
        and application.tls_version
        and host.tls_version != application.tls_version
    ):
        findings.append(
            DifferentialFinding(
                category="tls",
                observation=(
                    f"TLS version differs: host={host.tls_version}; "
                    f"application={application.tls_version}."
                ),
                interpretation=(
                    "TLS negotiation differs, although both may still be valid."
                ),
            )
        )

    if application.selected_remote_address:
        findings.append(
            DifferentialFinding(
                category="network",
                observation=(
                    "Application request selected remote address "
                    f"{application.selected_remote_address} "
                    f"({application.address_family or 'family unknown'})."
                ),
                interpretation=(
                    "DNS/TCP/TLS had already succeeded; this records which "
                    "resolved edge address handled the detailed application request."
                ),
            )
        )

    if (
        application.selected_remote_address
        and application.resolved_addresses
    ):
        member = (
            application.selected_remote_address
            in application.resolved_addresses
        )
        findings.append(
            DifferentialFinding(
                category="transport-correlation",
                observation=(
                    f"Application selected address "
                    f"{application.selected_remote_address} "
                    + (
                        "is present in"
                        if member
                        else "is not present in"
                    )
                    + " the DNS addresses independently resolved inside "
                    "the container."
                ),
                interpretation=(
                    "This correlates the source-derived Axios request with the "
                    "container's own target resolution without changing request "
                    "behavior. When the source is a live request event, the selected "
                    "address was observed while the request was active."
                ),
            )
        )

    if host.tls_version and application.tls_version:
        if host.tls_version == application.tls_version:
            findings.append(
                DifferentialFinding(
                    category="transport",
                    observation=(
                        f"Both representative requests negotiated "
                        f"{host.tls_version}."
                    ),
                    interpretation=(
                        "A TLS-version mismatch is not indicated."
                    ),
                )
            )

    if host.alpn_protocol and application.alpn_protocol:
        if host.alpn_protocol == application.alpn_protocol:
            findings.append(
                DifferentialFinding(
                    category="transport",
                    observation=(
                        f"Both representative requests negotiated ALPN "
                        f"{host.alpn_protocol}."
                    ),
                    interpretation=(
                        "An HTTP protocol-negotiation mismatch is not indicated "
                        "by the representative samples."
                    ),
                )
            )
        else:
            findings.append(
                DifferentialFinding(
                    category="transport",
                    observation=(
                        f"ALPN differs: host={host.alpn_protocol}; "
                        f"application={application.alpn_protocol}."
                    ),
                    interpretation=(
                        "Protocol negotiation differs. This is observable context, "
                        "not by itself proof of the HTTP rejection mechanism."
                    ),
                )
            )

    return findings


def _probe_layer_passed(
    investigation: RootCauseInvestigation,
    layer: str,
    *,
    container: bool = False,
) -> bool:
    steps = (
        investigation.container_probe.steps
        if container and investigation.container_probe is not None
        else investigation.steps
    )
    for step in steps:
        if step.name == layer:
            return step.success is True
    return False


def assess_differential_hypotheses(
    investigation: RootCauseInvestigation,
    host: HttpContextSnapshot,
    application: HttpContextSnapshot,
) -> list[HypothesisAssessment]:
    assessments: list[HypothesisAssessment] = []

    lower_layers = ("DNS", "TCP", "TLS")
    host_lower_ok = all(
        _probe_layer_passed(investigation, layer)
        for layer in lower_layers
    )
    app_lower_ok = all(
        _probe_layer_passed(
            investigation,
            layer,
            container=True,
        )
        for layer in lower_layers
    )

    if host_lower_ok and app_lower_ok:
        assessments.append(
            HypothesisAssessment(
                hypothesis="Basic DNS/TCP/TLS reachability failure",
                status="ruled_down",
                evidence=(
                    "DNS, TCP, and TLS passed in both host and application-"
                    "container probes."
                ),
            )
        )

    if (
        host.final_url
        and application.final_url
        and host.final_url == application.final_url
    ):
        assessments.append(
            HypothesisAssessment(
                hypothesis="Different final redirect destination",
                status="unsupported",
                evidence=(
                    f"Both representative requests end at {host.final_url}."
                ),
            )
        )

    if host.proxy_env_present == application.proxy_env_present:
        assessments.append(
            HypothesisAssessment(
                hypothesis="Simple proxy-environment mismatch",
                status="unsupported",
                evidence=(
                    "The same proxy-variable presence was observed in both "
                    "contexts; values were not exposed."
                ),
            )
        )

    if (
        application.selected_remote_address
        and application.resolved_addresses
        and application.selected_remote_address
        in application.resolved_addresses
    ):
        assessments.append(
            HypothesisAssessment(
                hypothesis="Application request reached an unrelated remote address",
                status="unsupported",
                evidence=(
                    f"The live application request selected "
                    f"{application.selected_remote_address}, which is one of the "
                    "addresses independently resolved for the configured target "
                    "inside the container."
                ),
            )
        )

    if (
        host.tls_version
        and application.tls_version
        and host.tls_version == application.tls_version
    ):
        assessments.append(
            HypothesisAssessment(
                hypothesis="TLS-version mismatch",
                status="unsupported",
                evidence=(
                    f"Both representative requests negotiated "
                    f"{host.tls_version}."
                ),
            )
        )

    if (
        host.alpn_protocol
        and application.alpn_protocol
        and host.alpn_protocol == application.alpn_protocol
    ):
        assessments.append(
            HypothesisAssessment(
                hypothesis="ALPN/protocol-negotiation mismatch",
                status="unsupported",
                evidence=(
                    f"Both representative requests negotiated "
                    f"{host.alpn_protocol}."
                ),
            )
        )

    request_differences = []
    for name in sorted(
        set(host.request_headers)
        | set(application.request_headers)
    ):
        if host.request_headers.get(name) != application.request_headers.get(name):
            request_differences.append(name)

    if request_differences:
        assessments.append(
            HypothesisAssessment(
                hypothesis="HTTP request-characteristic treatment",
                status="plausible",
                evidence=(
                    "Observable non-sensitive request headers differ: "
                    + ", ".join(request_differences)
                    + ". Correlation is not proof of causation."
                ),
            )
        )

    host_stability = _effective_stability(investigation, "host")
    source_stability = _effective_stability(investigation, "source")
    if (
        source_stability is not None
        and source_stability.classification == "stable_failure"
    ):
        assessments.append(
            HypothesisAssessment(
                hypothesis="Endpoint/edge policy or automated-monitor suitability",
                status="plausible",
                evidence=(
                    "The source-derived application request is a stable HTTP-layer "
                    "failure across repeated samples."
                ),
            )
        )

    assessments.extend([
        HypothesisAssessment(
            hypothesis="Specific WAF/bot rule",
            status="not_established",
            evidence=(
                "Edge/CDN metadata may be visible, but no specific rule or "
                "decision mechanism is exposed by these safe probes."
            ),
        ),
        HypothesisAssessment(
            hypothesis="User-Agent is the root cause",
            status="not_established",
            evidence=(
                "User-Agent may differ between contexts, but Iceman does not "
                "change/spoof it to infer causation."
            ),
        ),
        HypothesisAssessment(
            hypothesis="Authentication or secret-header requirement",
            status="not_established",
            evidence=(
                "No authoritative endpoint requirement was established by this "
                "local differential, and sensitive headers are deliberately omitted."
            ),
        ),
    ])

    return assessments


def differential_confidence_basis(
    investigation: RootCauseInvestigation,
    hypotheses: list[HypothesisAssessment],
) -> tuple[str, str]:
    ruled_or_unsupported = sum(
        item.status in {"ruled_down", "unsupported"}
        for item in hypotheses
    )
    plausible = sum(
        item.status == "plausible"
        for item in hypotheses
    )

    source_stability = _effective_stability(investigation, "source")
    stable_source_failure = bool(
        source_stability
        and source_stability.classification == "stable_failure"
    )

    if stable_source_failure and ruled_or_unsupported >= 2:
        return (
            "high",
            (
                "High confidence in the failure layer/context: repeated "
                "source-derived HTTP failure is established and multiple lower-"
                "level/context hypotheses are ruled down or unsupported. "
                "Confidence in the specific remote rejection mechanism remains "
                "lower because the safe evidence does not expose that decision."
            ),
        )

    if stable_source_failure or plausible:
        return (
            "medium",
            (
                "Repeated HTTP evidence exists, but too few competing hypotheses "
                "have been eliminated to call the differential highly constrained."
            ),
        )

    return (
        "low",
        (
            "The available evidence is insufficient to constrain the failure "
            "mechanism beyond a broad HTTP-context observation."
        ),
    )


def analyze_http_context_differential(
    investigation: RootCauseInvestigation,
) -> HttpContextDifferential:
    if not investigation.url:
        raise ValueError(
            "The latest investigation has no resolved HTTP/HTTPS target."
        )
    if investigation.signal_emitter is None:
        raise ValueError(
            "The latest investigation has no uniquely identified emitting container."
        )
    if investigation.check_profile is None:
        raise ValueError(
            "The latest investigation has no source-derived application check profile."
        )

    host = capture_host_context(
        investigation.url,
        timeout=5.0,
        attempts=3,
    )
    application = capture_application_context(
        investigation.workspace,
        investigation.signal_emitter,
        investigation.url,
        investigation.check_profile,
    )

    findings = build_differential_findings(
        investigation,
        host,
        application,
    )
    hypotheses = assess_differential_hypotheses(
        investigation,
        host,
        application,
    )

    host_stability = _effective_stability(investigation, "host")
    source_stability = _effective_stability(investigation, "source")

    if (
        host_stability is not None
        and host_stability.success_count > 0
        and source_stability is not None
        and source_stability.classification == "stable_failure"
    ):
        conclusion = (
            "The strongest evidence remains an HTTP/request-context difference: "
            "host access succeeds at least intermittently, while the actual "
            "source-derived application check fails consistently. DNS/TCP/TLS "
            "reachability is not the primary failure layer."
        )
    elif (
        host_stability is not None
        and host_stability.classification == "stable_failure"
        and source_stability is not None
        and source_stability.classification == "stable_failure"
    ):
        conclusion = (
            "Both host and application evidence are consistently rejected at "
            "the HTTP layer. A backend-only network difference is not supported; "
            "the target's treatment of automated requests or endpoint suitability "
            "is a stronger area to verify."
        )
    else:
        conclusion = (
            "The differential identifies observable request/runtime/transport "
            "differences, but the current evidence is not strong enough to assign "
            "a specific remote HTTP rejection mechanism."
        )

    confidence, confidence_basis = differential_confidence_basis(
        investigation,
        hypotheses,
    )

    next_action = (
        "Verify whether this URL is intended/documented for automated availability "
        "monitoring. If it is, compare only the endpoint's documented request/"
        "authentication requirements against Minerva's source-derived check. "
        "Do not treat HTTP 403 as healthy merely to silence the alert."
    )

    return HttpContextDifferential(
        subject=investigation.subject,
        target_url=_safe_url_display(investigation.url) or investigation.url,
        host=host,
        application=application,
        findings=findings,
        hypotheses=hypotheses,
        conclusion=conclusion,
        confidence=confidence,
        confidence_basis=confidence_basis,
        recommended_next_action=next_action,
    )
