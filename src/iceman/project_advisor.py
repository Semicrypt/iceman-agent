"""Bounded local project evidence and conversational DevOps advice.

No subprocesses, network calls, or workspace writes. Reports contain rule
explanations and locations, never file bodies or secret values.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from iceman.redaction import redact_text


@dataclass(frozen=True)
class ScanLimits:
    max_files: int = 350
    max_entries: int = 4000
    max_depth: int = 7
    max_file_bytes: int = 131072
    max_total_bytes: int = 2097152
    max_seconds: float = 3.0


@dataclass(frozen=True)
class Evidence:
    path: str
    line: int | None
    explanation: str


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    category: str
    title: str
    explanation: str
    recommendation: str
    evidence: tuple[Evidence, ...]
    certainty: str = "observed"


@dataclass
class ProjectAssessment:
    workspace: Path
    focus: str = "review"
    stacks: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    architecture: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    scanners: dict[str, bool] = field(default_factory=dict)
    files_inspected: int = 0
    recognizable: bool = False

    def to_safe_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["workspace"] = _label(str(self.workspace))
        return result

    def _ordered(self, focus: str) -> list[Finding]:
        priority = {"high": 0, "medium": 1, "low": 2}
        items = self.findings
        if focus == "security":
            items = [f for f in items if f.category == "security"]
        if focus == "missing":
            items = [f for f in items if f.certainty == "suggestion"]
        return sorted(items, key=lambda f: (priority.get(f.severity, 3), f.certainty != "observed", f.code))

    def render(self, focus: str | None = None, detail: str = "summary") -> str:
        focus = focus or self.focus
        if not self.recognizable:
            lines = ["I couldn't identify a software or DevOps project in this folder from the files I could safely inspect.",
                     "Open a project folder and I can review application manifests, Docker/Compose, Kubernetes, Terraform, CI workflows, and documentation."]
            if self.findings:
                lines.append("I did notice: " + self.findings[0].title + ". " + self.findings[0].recommendation)
            if self.limitations:
                lines.append("Inspection limits: " + " ".join(self.limitations))
            return redact_text("\n\n".join(lines))
        lines = ["This looks like a " + ", ".join(self.stacks or ["source-code"]) + " project."]
        high = [f for f in self.findings if f.severity == "high" and f.certainty == "observed"]
        introductions = {
            "readiness": "I would address the observed high-risk configuration before deploying. Files alone cannot certify production readiness." if high else "I can't certify production readiness from files alone; runtime behavior, tests, recovery, and deployment access still need verification.",
            "improve": "Here is where I would focus improvements, based on the files in this folder.",
            "missing": "These are gaps or safeguards I couldn't establish in the inspected files; they may exist elsewhere.",
            "security": "I checked local configuration for security risks. Observed settings and recommendations are separated below.",
        }
        lines.append(introductions.get(focus, "The file structure gives me a useful view of the stack, but it doesn't establish that the application builds or runs correctly."))
        if self.architecture:
            lines.append("Structure: " + "; ".join(self.architecture[:4]) + ".")
        if self.strengths and focus != "security":
            lines.append("Good foundations: " + "; ".join(self.strengths[:6]) + ".")
        findings = self._ordered(focus)
        limit = len(findings) if detail == "deep" else 5
        for index, finding in enumerate(findings[:limit], 1):
            label = "Observed" if finding.certainty == "observed" else "Suggestion"
            location = ", ".join(e.path + (f":{e.line}" if e.line else "") for e in finding.evidence[:2])
            lines.append(f"{index}. {label} ({finding.severity}): {finding.title}. {finding.explanation} {finding.recommendation}" + (f" Evidence: {location}." if location else ""))
        if not findings:
            lines.append("I didn't find a matching issue in the inspected configuration. That is a limited static result, not a security or production-readiness guarantee.")
        elif len(findings) > limit:
            lines.append(f"There are {len(findings) - limit} more observations; ask me to go deeper or show the evidence.")
        available = ", ".join(name for name, present in self.scanners.items() if present)
        unavailable = ", ".join(name for name, present in self.scanners.items() if not present)
        note = f"I inspected {self.files_inspected} text files. This was a read-only static review; I did not run tests, Git status, containers, cluster/cloud queries, or vulnerability scanners."
        if available:
            note += f" Available but not run: {available}."
        if unavailable:
            note += f" Optional scanners not found: {unavailable}."
        lines.append(note + " No CVE verification was performed.")
        if self.limitations:
            lines.append("Inspection limits: " + " ".join(self.limitations))
        return redact_text("\n\n".join(lines))

    def follow_up(self, request: str) -> str:
        request = request.lower().strip().rstrip("?.!")
        ordered = self._ordered(self.focus)
        if "evidence" in request or request == "why":
            if not ordered:
                return "My assessment is limited to the local file types and configuration I could inspect; I haven't verified runtime health."
            return "Here is what supports my assessment:\n\n" + "\n".join(f"- {f.title}: " + "; ".join(e.path + (f":{e.line}" if e.line else "") + " — " + e.explanation for e in f.evidence) for f in ordered[:12])
        if "first" in request or "highest" in request or request.startswith("fix"):
            if not ordered:
                return "First, confirm the application's test and deployment procedures. I don't have enough file evidence to choose an automatic fix."
            first = ordered[0]
            reply = f"Start with {first.title.lower()}. {first.explanation} {first.recommendation}"
            if request.startswith("fix"):
                reply += " I haven't changed any files. I'm not programmed to apply an arbitrary review finding automatically; a supported, exact change must be planned and approved first."
            return reply
        if "else" in request:
            remaining = ordered[5:]
            if not remaining:
                return "I have no additional evidence-backed findings from this scan. The next useful step is to run the project's documented tests and validate the deployment in its intended environment."
            return "Other things to consider:\n\n" + "\n".join(f"- {f.title}: {f.recommendation}" for f in remaining)
        return self.render(detail="deep")


_SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__", ".terraform", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".iceman"}
_TEXT_SUFFIXES = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb", ".php", ".tf", ".tfvars", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".md", ".txt", ".sh", ".conf", ".hcl"}
_SAFE_NAMES = {"dockerfile", "containerfile", "makefile", "procfile", ".dockerignore", ".gitignore", "jenkinsfile", "go.mod"}
_SENSITIVE = re.compile(r"(?:^\.env(?:\.|$)|(?:^|[._-])(?:credentials|secrets?)(?:[._-]|$)|^id_(?:rsa|ed25519)|\.(?:pem|key|p12|pfx|tfstate)(?:\.|$))", re.I)
_SECRET_LITERAL = re.compile(r"(?im)^\s*(?:export\s+)?(?:[\w.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)[\w.-]*)\s*[:=]\s*['\"]?([^\s#'\"]{8,})")
_PLACEHOLDER = re.compile(r"(?:\$|\{\{|<|example|dummy|placeholder|changeme|redacted|your[_-]|process\.env|os\.environ|var\.|env\.|secrets\.|getenv|environ)", re.I)


def _label(path: str) -> str:
    return "".join(c if c.isprintable() and c not in "\r\n\t" else "?" for c in redact_text(path))[:240]


class _Collector:
    def __init__(self, root: Path, limits: ScanLimits, assessment: ProjectAssessment):
        self.root, self.limits, self.assessment = root, limits, assessment
        self.started = time.monotonic()
        self.entries = self.total_bytes = 0
        self.names: set[str] = set()
        self.texts: dict[str, str] = {}
        self.stopped = False

    def note(self, message: str) -> None:
        if message not in self.assessment.limitations:
            self.assessment.limitations.append(message)

    def expired(self) -> bool:
        if time.monotonic() - self.started > self.limits.max_seconds:
            self.note("The scan time limit was reached.")
            self.stopped = True
        return self.stopped

    def scan(self) -> None:
        if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd or os.scandir not in os.supports_fd:
            self.note("Safe descriptor-based scanning is unavailable on this platform.")
            return
        try:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            self.note("The workspace is unavailable, unreadable, or a symbolic link.")
            return
        try:
            self.walk(fd, "", 0)
        finally:
            os.close(fd)

    def walk(self, directory_fd: int, relative: str, depth: int) -> None:
        if self.expired():
            return
        try:
            with os.scandir(directory_fd) as iterator:
                entries = []
                for entry in iterator:
                    self.entries += 1
                    if self.entries > self.limits.max_entries or self.expired():
                        self.note("The directory-entry limit was reached.")
                        self.stopped = True
                        break
                    entries.append(entry.name)
            for name in sorted(entries):
                if self.expired():
                    return
                rel = f"{relative}/{name}" if relative else name
                try:
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        self.note("Symbolic links were skipped.")
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        if name in _SKIP_DIRS or (name.startswith(".") and name not in {".github", ".gitlab", ".circleci"}):
                            continue
                        if depth >= self.limits.max_depth:
                            self.note("The scan depth limit was reached.")
                            continue
                        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                        try:
                            self.walk(child, rel, depth + 1)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode):
                        self.read(directory_fd, name, rel, info.st_size)
                except OSError:
                    self.note("Some files or folders were unavailable or unreadable.")
        except OSError:
            self.note("Some folders were unavailable or unreadable.")

    def read(self, directory_fd: int, name: str, rel: str, size: int) -> None:
        if len(self.names) >= self.limits.max_files:
            self.note("The file-count limit was reached.")
            self.stopped = True
            return
        self.names.add(rel)
        if _SENSITIVE.search(name) and not name.lower().endswith((".example", ".sample", ".template")):
            self.note("Sensitive files were excluded from content inspection.")
            return
        if Path(name).suffix.lower() not in _TEXT_SUFFIXES and name.lower() not in _SAFE_NAMES and not name.lower().startswith("dockerfile."):
            return
        if size > self.limits.max_file_bytes:
            self.note("Files above the per-file size limit were skipped.")
            return
        if self.total_bytes + size > self.limits.max_total_bytes:
            self.note("The total content-size limit was reached.")
            self.stopped = True
            return
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            actual = os.fstat(fd)
            if not stat.S_ISREG(actual.st_mode) or actual.st_size > self.limits.max_file_bytes:
                return
            payload = os.read(fd, self.limits.max_file_bytes + 1)
        finally:
            os.close(fd)
        if len(payload) > self.limits.max_file_bytes or self.total_bytes + len(payload) > self.limits.max_total_bytes:
            self.note("Content growth exceeded the scan size limits.")
            return
        self.total_bytes += len(payload)
        if b"\0" not in payload:
            self.texts[rel] = payload.decode("utf-8", errors="replace")
            self.assessment.files_inspected += 1


def assess_project(workspace: str | Path, focus: str = "review", *, limits: ScanLimits | None = None) -> ProjectAssessment:
    """Inspect bounded local files without commands, network requests, or writes."""
    root = Path(workspace).expanduser().absolute()
    result = ProjectAssessment(workspace=root.resolve(), focus=focus)
    collector = _Collector(root, limits or ScanLimits(), result)
    collector.scan()
    names, texts = collector.names, collector.texts
    codes: set[tuple[str, str]] = set()

    def add(code: str, severity: str, category: str, title: str, explanation: str,
            recommendation: str, path: str, match: re.Match[str] | None = None,
            *, suggestion: bool = False) -> None:
        if (code, path) in codes:
            return
        codes.add((code, path))
        line = texts.get(path, "").count("\n", 0, match.start()) + 1 if match else None
        evidence = Evidence(_label(path), line, explanation)
        result.evidence.append(evidence)
        result.findings.append(Finding(code, severity, category, title, explanation, recommendation,
                                       (evidence,), "suggestion" if suggestion else "observed"))

    def detect(path: str, content: str, pattern: str, code: str, severity: str, category: str,
               title: str, explanation: str, recommendation: str, *, suggestion: bool = False) -> bool:
        match = re.search(pattern, content, re.I | re.M)
        if match:
            add(code, severity, category, title, explanation, recommendation, path, match, suggestion=suggestion)
        return bool(match)

    source_types = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".rb": "Ruby", ".php": "PHP"}
    for suffix, label in source_types.items():
        if any(Path(p).suffix.lower() == suffix for p in names):
            result.stacks.append(label)
    for marker, label in (("pyproject.toml", "Python"), ("requirements.txt", "Python"), ("package.json", "Node.js"), ("go.mod", "Go"), ("cargo.toml", "Rust")):
        if any(Path(p).name.lower() == marker for p in names) and label not in result.stacks:
            result.stacks.append(label)
    docker = [p for p in texts if Path(p).name.lower().startswith(("dockerfile", "containerfile"))]
    compose = [p for p in texts if re.match(r"(?:docker-)?compose(?:[.-].*)?\.ya?ml$", Path(p).name, re.I)]
    terraform = [p for p in texts if p.endswith(".tf")]
    kubernetes = [p for p, t in texts.items() if p.endswith((".yml", ".yaml")) and re.search(r"(?m)^\s*apiVersion:", t) and re.search(r"(?m)^\s*kind:\s*(?:Deployment|StatefulSet|DaemonSet|Pod|Service|Ingress|Job|CronJob|Secret|ConfigMap|Role|ClusterRole|RoleBinding|ClusterRoleBinding)\b", t)]
    workflows = [p for p in texts if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))]
    for files, label in ((docker, "Docker"), (compose, "Compose"), (terraform, "Terraform"), (kubernetes, "Kubernetes"), (workflows, "GitHub Actions")):
        if files:
            result.stacks.append(label)
            result.evidence.append(Evidence(_label(files[0]), None, label + " configuration is present."))
    tf_text = "\n".join(texts[p] for p in terraform)
    for provider, label in (("aws", "AWS IaC"), ("azurerm", "Azure IaC")):
        if re.search(rf'\b(?:resource|data|provider)\s+"{provider}(?:_|\")', tf_text):
            result.stacks.append(label)
    components = sorted({Path(p).parts[0] for p in names if len(Path(p).parts) > 1 and Path(p).parts[0] in {"src", "app", "apps", "frontend", "backend", "services", "infra", "infrastructure", "deploy", "k8s"}})
    if components:
        result.architecture.append("source/configuration directories include " + ", ".join(components))
    for p, content in texts.items():
        if Path(p).name == "package.json":
            try:
                manifest = json.loads(content)
                deps = set(manifest.get("dependencies", {})) | set(manifest.get("devDependencies", {}))
                frameworks = sorted(deps & {"react", "next", "vue", "express", "fastify", "@nestjs/core"})
                if frameworks:
                    result.architecture.append(_label(p) + " declares " + ", ".join(frameworks))
            except (ValueError, TypeError, AttributeError):
                result.limitations.append("An application manifest could not be parsed.")
    result.recognizable = bool(result.stacks)
    tests = [p for p in names if any(part in {"test", "tests", "__tests__"} for part in Path(p).parts) or Path(p).name.startswith("test_") or ".test." in p or "_test.go" in p]
    readmes = [p for p in names if Path(p).name.lower().startswith("readme")]
    for exists, description in ((tests, "test files are present (not run)"), (readmes, "a README is present"), (workflows, "CI workflows are defined"), (terraform, "infrastructure is described as code"), (docker, "a container build is defined")):
        if exists:
            result.strengths.append(description)
    if result.recognizable:
        baseline = next(iter(sorted(texts)), ".")
        for exists, code, category, title, explanation, recommendation in (
            (tests, "tests-not-found", "reliability", "Establish automated verification", "No conventional test files were found in this bounded scan.", "Document and run the project's test command, and add coverage for critical behavior."),
            (readmes, "readme-not-found", "documentation", "Document setup and operations", "No README was found in this bounded scan.", "Document setup, configuration, deployment, rollback, and incident recovery."),
            (workflows or any(p.startswith(".circleci/") or Path(p).name in {".gitlab-ci.yml", "Jenkinsfile"} for p in names), "ci-not-found", "cicd", "Verify the CI quality gates", "No recognized CI pipeline was found in the inspected files.", "Verify where CI is configured and require tests and relevant configuration checks before deployment."),
            (any(re.search(r"prometheus|grafana|opentelemetry|otelcol|alertmanager", p, re.I) for p in names), "monitoring-not-found", "observability", "Confirm monitoring and alerting", "No conventional monitoring configuration was identified by filename.", "Confirm service health, useful logs, metrics, alert ownership, and recovery procedures; these may be managed elsewhere.")):
            if not exists:
                add(code, "low", category, title, explanation, recommendation, baseline, suggestion=True)

    for path, raw in texts.items():
        if time.monotonic() - collector.started > collector.limits.max_seconds:
            collector.note("The assessment time limit was reached; some file rules were not evaluated.")
            break
        content = re.sub(r"(?m)^[ \t]*(?:#|//)[^\r\n]*", lambda match: " " * len(match.group()), raw)
        secret = next((m for m in _SECRET_LITERAL.finditer(content) if not _PLACEHOLDER.search(m.group(1))), None)
        marker = re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", content)
        if secret or marker:
            add("possible-secret", "high", "security", "A possible embedded credential needs review", "A credential-like literal or private-key marker appears in a readable project file; its value is withheld and validity is unverified.", "Inspect it locally, remove real credentials from source, and rotate them if they were exposed.", path, secret or marker, suggestion=True)
        if path in docker:
            detect(path, content, r"^\s*USER\s+(?:root|0)(?:\s|$)", "docker-root", "medium", "security", "The container selects the root user", "A Dockerfile explicitly selects root or UID 0; later stages may override it.", "Confirm the final runtime stage uses a suitable non-root user.")
            if not re.search(r"(?im)^\s*USER\s+", content):
                add("docker-user-unspecified", "medium", "security", "Confirm the container runtime user", "The Dockerfile does not specify USER; the base image determines the default.", "Verify the final image user and select a non-root user where appropriate.", path, suggestion=True)
            detect(path, content, r"^\s*FROM\s+[^\s]+:latest\b", "docker-latest", "low", "reliability", "The base image uses a mutable latest tag", "A Dockerfile FROM instruction uses :latest.", "Pin an intentional supported version or digest and maintain an update process.", suggestion=True)
            detect(path, content, r"(?:curl|wget)\b[^\n]*\|\s*(?:sh|bash)\b", "remote-shell", "high", "security", "A build executes a downloaded script", "A download command is piped directly into a shell.", "Review the source and pin or verify the downloaded artifact before executing it.")
            parent = str(Path(path).parent)
            ignore = ".dockerignore" if parent == "." else parent + "/.dockerignore"
            if ignore not in names:
                add("dockerignore-not-found", "low", "security", "Check the Docker build context exclusions", "No .dockerignore was found beside this Dockerfile; the actual build context may be elsewhere.", "Exclude secrets, VCS metadata, dependencies, and unnecessary files from the actual build context.", path, suggestion=True)
        if path in compose or path in kubernetes:
            for pattern, code, title, explanation in (
                (r"^\s*privileged:\s*true\b", "privileged", "A workload requests privileged execution", "A privileged: true setting grants broad host capabilities."),
                (r"^\s*hostNetwork:\s*true\b|^\s*network_mode:\s*['\"]?host\b", "host-network", "A workload uses the host network", "Host networking bypasses ordinary container network isolation."),
                (r"^\s*allowPrivilegeEscalation:\s*true\b", "privilege-escalation", "Privilege escalation is explicitly enabled", "The manifest explicitly permits privilege escalation."),
                (r"^\s*runAsNonRoot:\s*false\b", "nonroot-disabled", "The non-root safeguard is disabled", "The manifest explicitly disables runAsNonRoot.")):
                detect(path, content, pattern, code, "high", "security", title, explanation, "Confirm the requirement and use the least privileges the workload needs.")
            detect(path, content, r"(?:^|\s)/var/run/docker\.sock(?:\s|:|$)", "docker-socket", "high", "security", "A workload mounts the Docker socket", "The configuration references the host Docker socket, which can grant host-level control.", "Remove the socket mount unless essential and tightly isolate workloads requiring it.")
        if path in kubernetes:
            if re.search(r"(?m)^\s*kind:\s*(?:Deployment|StatefulSet|DaemonSet|Pod)\b", content):
                for pattern, code, title, explanation, advice in (
                    (r"\breadinessProbe:", "readiness-probe-not-found", "Check readiness behavior", "No readinessProbe was found in this workload file.", "Add an application-appropriate readiness probe so traffic waits for readiness."),
                    (r"\b(?:requests|limits):", "resource-bounds-not-found", "Define workload resource requirements", "No resource requests or limits were identified in this workload file.", "Measure resource needs and configure requests and suitable limits.")):
                    if not re.search(pattern, content):
                        add(code, "medium", "reliability", title, explanation, advice, path, suggestion=True)
            detect(path, content, r"^\s*(?:name|role):\s*cluster-admin\b", "cluster-admin", "high", "security", "Cluster-admin is referenced", "A Kubernetes manifest references the broad cluster-admin role; binding context needs review.", "Check the binding and scope access to the resources and operations actually required.", suggestion=True)
            if re.search(r"(?m)^\s*kind:\s*Secret\s*$", content):
                add("kubernetes-secret", "medium", "security", "Verify how Kubernetes secrets are delivered", "A Kubernetes Secret manifest is present; secret values were not included in this assessment.", "Keep real values out of source control and use a controlled, encrypted secret-delivery process.", path, suggestion=True)
        if path in terraform:
            detect(path, content, r"\bingress\s*\{[^}]*[\"'](?:0\.0\.0\.0/0|::/0)[\"']", "public-ingress", "high", "security", "An ingress block permits a public address range", "A Terraform ingress block contains the all-address IPv4 or IPv6 range; protocol and port context still need review.", "Confirm the intended exposure and restrict ingress to justified sources and services.")
            detect(path, content, r"\b(?:publicly_accessible|allow_blob_public_access|allow_nested_items_to_be_public)\s*=\s*true\b", "public-cloud-resource", "high", "security", "A cloud resource explicitly permits public access", "A known public-access setting is enabled in Terraform.", "Review whether public access is required and verify the applicable network and identity controls.")
            detect(path, content, r"\b(?:encrypt|encrypted|https_only|enable_https_traffic_only|https_traffic_only_enabled)\s*=\s*false\b", "cloud-security-disabled", "high", "security", "An encryption or HTTPS safeguard is disabled", "A recognized encryption or HTTPS-only property is explicitly false.", "Confirm provider semantics and enable the safeguard where supported.")
            detect(path, content, r'\bresource\s+"(?:aws_nat_gateway|aws_instance|aws_eks_cluster|azurerm_kubernetes_cluster|azurerm_virtual_machine|azurerm_linux_virtual_machine)"', "cloud-cost-review", "low", "cost", "Review the infrastructure cost assumptions", "The configuration declares infrastructure that can incur ongoing charges.", "Review sizing, utilization, lifecycle, egress, and ownership using actual account usage and current provider pricing; no price estimate was made.", suggestion=True)
        if path in workflows:
            detect(path, content, r"^\s*permissions:\s*write-all\b", "ci-write-all", "high", "security", "CI grants write-all token permissions", "A workflow explicitly requests write-all permissions.", "Reduce permissions to the individual scopes required by each job.")
            detect(path, content, r"\buses:\s*[^\s@]+@(?![a-f0-9]{40}(?:\s|$))[^\s#]+", "ci-mutable-action", "low", "cicd", "Third-party actions use mutable references", "An action reference is not pinned to a full commit SHA.", "Consider verified commit pins with an update process for external actions.", suggestion=True)
            if not re.search(r"(?m)^\s*permissions:", content):
                add("ci-permissions-unspecified", "medium", "security", "Make CI token permissions explicit", "No permissions block was found in this workflow; repository defaults may still restrict it.", "Declare the smallest permissions required for each job.", path, suggestion=True)
            if re.search(r"\bpull_request_target\b", content) and "actions/checkout" in content:
                add("ci-pr-target-review", "high", "security", "Review privileged pull-request checkout", "The workflow combines pull_request_target with checkout; the checked-out ref and later execution determine risk.", "Ensure untrusted pull-request code cannot run with repository secrets or write credentials.", path, suggestion=True)
    if terraform and not any(Path(p).name == ".terraform.lock.hcl" for p in names):
        add("terraform-lock-not-found", "low", "reliability", "Check Terraform provider locking", "No Terraform provider lockfile was found in the scanned tree.", "Keep an appropriate provider lockfile for each Terraform root and review dependency updates.", terraform[0], suggestion=True)
    if terraform and not re.search(r"\brequired_version\s*=", tf_text):
        add("terraform-version-not-found", "low", "reliability", "Declare Terraform compatibility", "No required_version constraint was found in the inspected Terraform files.", "Document and constrain supported Terraform versions, then validate with the intended providers.", terraform[0], suggestion=True)
    for scanner in ("trivy", "checkov", "pip-audit", "hadolint"):
        result.scanners[scanner] = shutil.which(scanner) is not None
    if not names:
        collector.note("No readable project files were discovered.")
    return result
