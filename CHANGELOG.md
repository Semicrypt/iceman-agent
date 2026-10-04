# Changelog

## 0.9.7

- Added first-class natural-language Git and GitHub workflows behind a typed `GitAction` contract; the reasoning provider still cannot supply arbitrary shell commands.
- Added trusted actions for Git status, diff summaries, repository initialization, stage-all, commit, push, combined commit+push, remotes, branch inspection, annotated milestone tags, and GitHub repository creation through authenticated `gh`.
- Added exact-command preview, approval gates, live progress, stop-on-first-failure, and workflow summaries for Git/GitHub actions.
- Added pre-commit safety inspection for `.env`, key/certificate files, Terraform state/tfvars, high-confidence secret patterns, generated/heavy directories, and files at or above GitHub's 100 MiB per-file limit.
- `stage everything` and combined commit+push refuse to proceed when sensitive, oversized, or clearly generated/unignored content is detected.
- GitHub repository creation never pushes automatically; it creates/connects `origin`, leaving publishing as a separate approved action. When visibility is omitted, the safer default is private and is shown in the plan.
- Added Git/GitHub Advisor reminders for repository visibility, remote publication, missing `.gitignore`, and combined stage/commit/push operations.
- Added language repair for common Git mistakes such as `comit`, `pus`, `gitub`, `repositry`, `stge`, `brnch`, and `chnages`.
- Added manual-command safety blocks for Git force-push, `git reset --hard`, forced `git clean`, and forced branch deletion; normal push remains allowed.
- Added GitHub CLI to `doctor` tool checks.
- Added 19 Git/GitHub regression and safety tests; full suite now passes 303 tests.

## 0.9.6

- Added command-aware Docker diagnosis for images that use entrypoint wrappers such as `docker-entrypoint.sh`.
- Docker diagnosis now captures only the executable plus the first two runtime arguments, keeps the raw startup output hidden, sanitizes sensitive-looking arguments, and reconstructs the effective application command when safe.
- The `broken-backend` regression now correlates `docker-entrypoint.sh | node | missing.js` with `MODULE_NOT_FOUND /app/missing.js` and identifies the bad startup entry file instead of suggesting generic dependency/build fixes.
- Added safe startup-command evidence such as `node missing.js` without dumping full argument arrays or environment variables.
- Added existing-resource separator normalization before intent routing so natural names like `broken backend`, `broken-backend`, and underscore/space variants can resolve to the same real resource with confirmation.
- Resource separator repair is grounded against real workspace/Docker/Kubernetes objects and is not applied to creation destinations.
- Preserved exact-command preview, approval gates, live progress, Advisor checks, and evidence-vs-inference separation.
- Added 4 v0.9.6 regression tests; full suite now passes 284 tests.

## 0.9.5

- Added evidence-aware Node.js `MODULE_NOT_FOUND` classification for Docker diagnosis.
- Missing startup/entry files (for example `node missing.js` → `/app/missing.js`) are no longer misreported as missing npm dependencies.
- Added separate diagnosis paths for missing entry files, missing local application modules/files, bare runtime package dependencies, and unclassified module targets.
- Added a bounded Docker startup-inspection step exposing only the executable and first runtime argument; full argument arrays and environment values remain hidden.
- Added concise high-signal log excerpts to the **Evidence** section so users can see the exact error lines supporting a diagnosis without dumping the full log sample.
- Recommendations now follow the established failure class: correct startup commands, inspect COPY/build-context/import paths, or verify runtime dependency installation as appropriate.
- Removed the generic “inspect errors first” recommendation when Docker diagnosis has already established a more specific log-based cause.
- Updated legacy Docker diagnosis plan coverage for the new bounded startup-inspection step.
- Added 6 evidence-aware Docker root-cause regression tests; full suite now passes 280 tests.

## 0.9.4

- Added structured post-execution Docker reasoning with separate **Diagnosis**, **Evidence**, and **Recommended next actions** sections.
- Docker connectivity diagnosis now explains host/container port mismatches instead of only listing mappings.
- When a bounded localhost probe succeeds, Iceman can state that the published endpoint is reachable and distinguish that from an incorrect requested host port.
- Connectivity evidence can surface when the requested host port belongs to a different Docker container and recommend the actual local endpoint.
- Docker health reasoning now explicitly distinguishes a running process from proven application readiness when no Docker `HEALTHCHECK` exists.
- Docker diagnosis prioritizes high-signal runtime/log evidence for dependency connection refusal, `EADDRINUSE`/bind conflicts, permission errors, missing modules/files, and possible OOM pressure.
- Added bounded next-step guidance for each established failure class without auto-executing remediation.
- Unknown/weak evidence now produces an explicit non-conclusion rather than inventing a root cause.
- The reasoning layer consumes only already-collected trusted command results and safe plan metadata; it does not execute hidden commands.
- Added 5 reasoned-diagnosis regression tests; full suite now passes 274 tests.

## 0.9.3

- Moved high-confidence language repair ahead of intent routing so multiple simultaneous spelling/grammar errors can be normalized before trusted-action matching.
- Fixed the regression phrase `why cant i reac the backen on pot 5000`: `reac` → `reach`, `pot` → `port`, and the mistyped entity `backen` is then grounded to the real `backend` resource with operator confirmation.
- Expanded bounded conversational vocabulary repair for common action words such as check/show/reach/port/back/execute/deploy/validate/initialize without giving the language layer arbitrary shell authority.
- Added colloquial connectivity repair such as `backend no dey open pot 5000` → `why can't I reach backend on port 5000`.
- Added broken Kubernetes query repair such as `chec kubernete pod` → `show pods`.
- Added broken Terraform problem phrasing such as `terrafor plan no working` → `why terraform plan failing`.
- Added additional Linux repair such as `go bak one folde` and `giv deploy.sh execut permision`.
- Preserved the entity-safety boundary: creation destinations are not fuzzy-renamed, resource/entity correction still uses real discovered candidates, and destructive typo requests are not promoted into executable actions.
- Preserved the confirmation boundary: any material repair sets a corrected interpreted request and requires `Do you mean ...?` confirmation before trusted commands are planned.
- Added 10 semantic-repair regression tests; full suite now passes 269 tests.

## 0.9.2

- Added robust natural-language repair for common spelling, contraction, grammar, and sentence-shape mistakes while preserving typed trusted actions.
- Added deterministic entity grounding for mistyped Docker containers/components, Kubernetes deployment/components and contexts, and existing Linux workspace paths.
- The regression phrase `why cant i reach the backen on port 5000` now resolves `backen` to the real `backend` entity and requires operator confirmation before any diagnostic command is built.
- Added conversational grammar repairs for examples such as `why backend no open`, `mak backend 5 replica`, `giv deploy.sh execute permision`, and `go bak to project folder`.
- Added Terraform failure phrasing such as `why terrafor plan failing`, routed to the existing read-only plan workflow.
- Added confidence-based entity matching using only real discovered candidates. Strong unique matches become `Do you mean ...?` confirmations; medium-confidence ambiguity becomes a bounded numbered candidate choice; low-confidence input is not guessed.
- Creation destinations are never fuzzy-rewritten, so intentional new directory/file names remain untouched.
- Destructive requests remain outside fuzzy promotion and cannot become executable merely because a similar trusted phrase exists.
- Added bounded Docker inventory lookup for runtime-only container typo recovery, while preferring local workspace names when sufficient.
- Added local Kubernetes manifest name discovery and kubeconfig context matching without inventing cluster/resource names.
- Extended the strict AI intent contract with optional structured clarification metadata; no arbitrary shell-command field was introduced.
- Updated the external-provider instructions so advanced Docker actions and deterministic entity grounding remain within the same safety boundary.
- Added 12 robust-language regression tests; full suite now passes 259 tests.

## 0.9.1

- Fixed Docker natural-language routing for component-aware connectivity requests such as `why can't I reach backend on port 5000`.
- Added trusted `docker_connectivity` action so Iceman retains both the target container and the user-requested host port instead of reducing the problem to a generic port listing.
- Expanded grammar coverage for phrases such as `I can't connect to backend`, `backend isn't reachable`, `backend isn't opening`, `why is backend not responding`, `check why backend connectivity is failing`, and `port 5000 isn't working`.
- Added a bounded connectivity workflow: runtime-state inspection, published-port inspection, requested host-port ownership/listener checks, and a localhost-only HTTP GET probe when exactly one published host port is unambiguous.
- The HTTP probe reports connection failure as diagnostic evidence instead of aborting the workflow, so Iceman can explain an unreachable application rather than stopping at the expected failed probe.
- Connectivity summaries now explain container-port to host-port mappings and surface the host-accessible localhost endpoint candidate.
- Preserved the safety boundary: no arbitrary AI shell generation, no remote-host probing, no port scanning, and no mutating network action.
- Added conversational replay support for the new connectivity action.
- Extended Docker Advisor coverage to connectivity diagnosis.
- Added 14 regression tests for contractions, grammar variants, component+port preservation, plan construction, bounded localhost probing, summaries, and conversational replay; full suite now passes 247 tests.

## 0.9.0

- Added Advanced Docker Intelligence trusted actions for runtime diagnosis, health checks, port troubleshooting, security review, image optimization review, safe rebuild/restart, and Compose status.
- Added natural-language routing for requests such as `why did backend stop`, `why is backend restarting`, `check if backend is healthy`, `show important errors in backend logs`, and `why can't I reach port 5000`.
- Docker runtime diagnosis now summarizes status, health, restart count, exit code, Docker runtime errors, and high-signal recent-log patterns.
- Diagnostic log samples can be analyzed without dumping all sampled logs into the live terminal; ordinary `show <container> logs` remains available for raw logs.
- Docker run now detects existing deterministic container names: running containers are verified rather than duplicated, and stopped containers can be started/verified.
- New Docker containers are followed by runtime-state and published-port verification steps.
- Added bounded host-port ownership troubleshooting with Docker owner identification and local-listener fallback.
- Added Docker security review of static Dockerfiles and bounded runtime settings without exposing container environment values.
- Expanded Docker Advisor checks for non-root user, HEALTHCHECK, mutable `:latest` base images, remote `ADD` URLs, secret-like ENV literals, missing `.dockerignore`, and deterministic npm installs.
- Added image optimization review with total image size and layer IDs/sizes; Docker `CreatedBy` history is intentionally hidden because it can leak build arguments.
- Added safe standalone `rebuild and restart <component>` workflow. Iceman preserves the previous container as `<name>-iceman-backup` and refuses automatic recreation when mounts, custom networks, runtime env overrides, or command overrides are detected.
- Added deterministic `docker_compose` workspace template and natural-language `create docker compose for <component> port <port>`. Existing Compose definitions are never overwritten automatically.
- Added trusted Docker Compose status action.
- Fixed the latent `docker_images` planner gap so image listing remains a first-class trusted action.
- Added Docker conversation memory/typo vocabulary for health, Compose, security, rebuild, optimization, ports, and errors.
- Added 20 new Advanced Docker Intelligence tests; full suite now passes 233 tests.

## 0.8.6

- Added live execution progress for natural-language Docker, Kubernetes, Terraform, and Linux workflows.
- Added real-time stdout/stderr streaming so long-running commands expose their own progress instead of appearing frozen.
- Added measurable `[step/total · percent]` workflow progress; percentages reflect completed workflow steps only and are never fabricated for opaque commands.
- Added elapsed timers and per-step duration reporting.
- Added silent-process heartbeats every few seconds with elapsed time and time since last output.
- Docker BuildKit, Terraform, kubectl rollout, and other native tool progress is surfaced as soon as the tool emits it.
- Added total workflow duration to success and failure summaries.
- Added safe Ctrl+C handling: the active child process is terminated, the workflow is marked interrupted, and later state-changing steps are not attempted.
- Added interruption summaries showing completed steps, interrupted step, and remaining unexecuted steps.
- Added streaming progress to explicit `!command` execution as well as natural-language trusted workflows.
- Kept normal stderr uncolored while a process is running because many DevOps CLIs use stderr for non-error progress output.
- Added streaming-runner, heartbeat, cwd, workflow-hook, and interruption tests; full suite now passes 213 tests.

## 0.8.5

- Added guided Advisor remediations: Advisor findings can now expose a bounded optional fix instead of only warning the operator.
- Kubernetes deploy warnings for missing CPU/memory resources can offer a starter resource patch before deployment.
- Added deterministic `kubernetes_resources` workspace template with no arbitrary AI-provided YAML.
- Starter resource patch uses requests of 100m CPU / 128Mi memory and limits of 500m CPU / 512Mi memory, clearly labeled as values to tune from observed workload usage.
- Guided fixes use the existing file-safety pipeline: exact unified diff preview, explicit write approval, backup, atomic write, static validation, SHA-256 verification, and session rollback.
- After a guided fix is written, Iceman rebuilds the DevOps plan from disk and re-runs Advisor checks so execution never relies on stale pre-fix evidence.
- Kubernetes deployment still performs `kubectl apply --dry-run=client` before any real apply, providing post-edit manifest validation against the selected cluster.
- Automatic resource insertion is deliberately conservative: ambiguous or multi-container Deployment manifests are refused rather than guessed.
- Added direct natural-language routing for requests such as `add kubernetes resource requests and limits for backend`.
- Preserved operator choice: declining/cancelling an Advisor fix never silently changes files and the final deployment still requires its normal exact-command approval.
- Added guided-fix, diff, rollback, ambiguity, Advisor metadata, and routing tests; full suite now passes 206 tests.

## 0.8.4

- Fixed Kubernetes platform/context confusion: phrases such as `deploy backend to kubernetes` now mean deploy to Kubernetes using the current kubeconfig context, not a context literally named `kubernetes`.
- Added kubeconfig context inventory checks before Kubernetes deployment planning. A requested context must actually exist before an executable plan can be produced.
- Added explicit target-context resolution. When no cluster/context is named, Iceman resolves the current context and asks the operator to confirm that destination before the exact-command approval gate.
- Added target namespace awareness from kubeconfig context configuration for Advisor output and deployment destination notes.
- Kubernetes deploy workflows no longer mutate the user's active kubeconfig context. Commands are scoped with `kubectl --context <target>` instead of `kubectl config use-context`.
- Added a read-only API reachability preflight (`kubectl --context <target> cluster-info`) before validation or apply.
- Added explicit context scoping to manifest validation, apply, rollout verification, pod listing, and service listing.
- Added Advisor warnings for likely remote/non-local Kubernetes destinations and reminders when the current context was inferred.
- Nonexistent contexts are rejected during planning with the available context names shown; no deployment commands are executed.
- Preserved stop-on-first-failure execution semantics and exact-command `y/N` approval.
- Added Kubernetes context-intelligence, remote-target Advisor, target-confirmation, and namespace tests; full suite now passes 201 tests.

## 0.8.3

- Added a typed Natural-Language Linux Action Engine with persistent workspace navigation.
- Added trusted Linux actions for pwd, listing/hidden files, cd, mkdir, touch, cat, copy, move, chmod +x, permission inspection, disk/filesystem usage, file search, text search, port ownership, and process listing.
- Linux file-changing operations are constrained to the active Iceman workspace; navigation may intentionally change the active workspace.
- Every Linux action previews its exact deterministic command and requires operator `y/N` confirmation, including read-only actions.
- Added typo repair for Linux vocabulary such as `permions` → `permissions`, with clarification before execution.
- Added Iceman Advisor foundation with BLOCKER/WARNING/RECOMMENDATION/REMINDER findings shown before execution.
- Added Docker advice for missing non-root USER and HEALTHCHECK.
- Added Kubernetes advice for missing resource requests/limits, `:latest` images, missing probes, and implicit namespace use.
- Added Terraform advice for missing remote state, potential secret-bearing tfvars, and plan-review reminders.
- Added Linux least-privilege advice around executable permissions.
- Added trusted `docker_images` action so `show me the docker images` no longer falls back to the legacy command planner.
- Preserved the strict AI boundary: no AI-provided arbitrary shell command or arbitrary editor command is exposed.
- Added 22 Linux/Advisor tests; full suite now passes 192 tests.

## 0.8.2

- Added operator confirmation for every natural-language DevOps execution, including read-only actions.
- Exact trusted Docker/kubectl/Terraform command(s) are displayed before the `y/N` approval prompt.
- Added bounded conversational prompt normalization for common DevOps spelling mistakes.
- Added explicit clarification prompts such as `Do you mean: 'validate my terraform'?` when a technical typo or ambiguous conversational reference is repaired.
- Added conversational follow-ups including `show pods again`, `show them again`, `again`, `refresh that`, and Kubernetes scale follow-ups such as `make it 3 instead`.
- Added polite/grammar filler tolerance for requests such as `please show me pods`.
- Fuzzy repair is restricted to a trusted DevOps vocabulary and never rewrites arbitrary project names, images, URLs, paths, or destructive requests.
- Preserved the strict AI boundary: normalized text is display/routing context only and cannot become arbitrary shell input.
- Added 14 tests covering conversational repair, typo tolerance, grammar tolerance, follow-up context, and universal command confirmation; full suite now passes 170 tests.

## 0.8.1

- Added typed Natural Language DevOps Action Engine.
- Added Docker build/run/list/logs/stop/restart trusted actions.
- Added Docker host-port conflict detection and bounded alternative-port selection.
- Added container-port inference from Dockerfile EXPOSE or image metadata.
- Added Kubernetes context/list/validate/deploy/pods/deployments/services/rollout/scale/logs/describe/restart actions.
- Added bounded Minikube deployment workflow with local image loading when applicable.
- Added Terraform init/fmt/validate/plan/plan-summary/state actions.
- Terraform validate can include local provider initialization when required, behind approval.
- Added one approval gate per state-changing natural-language workflow.
- Read-only natural-language actions run without requiring the user to type CLI commands.
- Executor stops at the first failed step.
- Added DevOps session memory for the latest component and image so follow-ups such as `run it on port 5050` can work.
- Added ANSI stripping and Rich-markup escaping for tool output.
- Removed stale v0.4/v0.2 fallback/safety wording.
- Terraform apply/destroy and Kubernetes delete remain outside the v0.8.1 trusted action set.


# Changelog

## 0.8.0

- Added Workspace & File Action Engine.
- Added strict trusted workspace template enum; AI cannot return arbitrary file
  contents or arbitrary generated paths.
- Added deterministic Node.js Dockerfile + `.dockerignore` generation.
- Added deterministic `.gitignore` generation.
- Added Kubernetes Deployment + ClusterIP Service generation with explicit port,
  bounded replicas, safe image-reference parsing, and visible image placeholder.
- Added Terraform AWS provider starter generation.
- Added Terraform AzureRM provider starter generation.
- Added unified-diff previews before writes.
- Added explicit approval gate for workspace writes.
- Added traversal/symlink/workspace-boundary checks.
- Added basic generated-secret detection.
- Added atomic writes and post-write SHA-256 verification.
- Added backups for overwritten files under `~/.cache/iceman`.
- Added rollback of the most recent successful Iceman workspace-file write.
- Added session memory for file plans and applied writes.


# Changelog

## 0.7.5

- Added passive live Node ClientRequest/socket transport capture for the
  source-derived Axios differential replay.
- Native `http.request` and `https.request` are instrumented before Axios loads
  and restored afterward.
- Added socket/connect/secureConnect observation without modifying request
  options, headers, agents, TLS settings, or redirects.
- Added application transport event reporting.
- Kept post-response request-graph inspection as a fallback only.
- Added selected-address-to-container-DNS correlation as an explicit
  differential control.
- Added hypothesis assessment for unrelated-remote-address routing.
- Preserved all v0.7.4 safety boundaries and read-only behavior.


# Changelog

## 0.7.4

- Recovered application-side transport metadata from the actual Axios /
  follow-redirects ClientRequest socket graph.
- Added independent target DNS resolution inside the emitting container.
- Added application resolved addresses, selected remote address, address family,
  TLS version, ALPN, and transport-observation source.
- Added transport correlation between independently resolved addresses and the
  actual selected application remote address.
- Added explicit differential hypothesis assessment:
  ruled down/unsupported, still plausible, and not established.
- Added evidence-based confidence basis that distinguishes confidence in the
  failure layer/context from confidence in a specific remote rejection mechanism.
- Kept request differences as candidates rather than causal claims.
- Preserved all v0.7.3 safety restrictions against WAF/bot bypasses, browser
  spoofing, credentials, secret headers, and proxy-value disclosure.


# Changelog

## 0.7.3

- Added trusted `analyze_http_context` reasoning action.
- Added `investigate option N` for read-only diagnostic remediation options.
- Added natural-language routing for deeper backend HTTP rejection analysis.
- Added host/application HTTP context snapshots with safe request/response
  header allowlists.
- Added representative redirect/final-URL, DNS/address, TLS/ALPN, runtime,
  proxy-presence, container DNS, and Docker-network evidence.
- Added explicit differential findings that distinguish observations from
  causal claims.
- Added safety boundary against WAF/bot bypasses, browser spoofing, credential
  guessing, secret headers, and proxy-value disclosure.
- Added differential results to session context and AI reasoning context.
- Fixed duplicated startup guidance text.


# Changelog

## 0.7.2

- Added three-sample HTTP evidence collection for host, application-container,
  and source-derived application checks.
- Added stable-success, stable-failure, unstable/mixed, incomplete, and
  insufficient evidence classification.
- Added bounded interactive-session evidence accumulation keyed by workspace,
  monitored subject, and configured URL.
- Added current-run and session evidence tables to root-cause output.
- Remediation ranking now uses evidence stability instead of the most recent
  single HTTP status.
- Persistent application failure plus any host success prioritizes
  execution-context investigation.
- Mixed evidence prioritizes stability investigation ahead of writes.
- Replacement-target preflight now requires three source-derived application
  checks and STABLE SUCCESS before a write can become approval-ready.
- New investigations clear stale application-remediation advice/change plans so
  recommendations are recalculated from current evidence.


# Changelog

## 0.7.1

- Added context-aware remediation ranking from host/container HTTP evidence.
- Host-success/container-failure now prioritizes investigating the execution
  context rather than replacing the monitored URL.
- Added semantic remediation action types so write behavior follows the action,
  not a fixed option number.
- Database target replacement can move from Option 1 to Option 2 when evidence
  ranking changes.
- Added a ranking-basis explanation to Remediation Intelligence output.
- Both-currently-healthy probes now prioritize check-logic/transient-state
  investigation ahead of writes.
- Preserved all existing replacement preflight, one-row database guards,
  approval, verification, and rollback protections.


# Changelog

## 0.7.0

- Added root-cause-aware Remediation Intelligence for application investigations.
- Added numbered remediation recommendations with risk and applicability.
- Added trusted AI actions for remediation recommendation and applying a numbered
  bounded option.
- Added explicit replacement URL field to the strict AI intent contract; the AI
  is forbidden from inventing a replacement URL.
- Added source-derived replacement preflight from the actual emitting container.
- Added automatic-write gating on high reproduction fidelity and passing
  application policy.
- Added guarded PostgreSQL URL update for uniquely identified database-backed
  monitor targets.
- Added exact old-value/one-row SQL preconditions, read-back verification, and a
  bounded inverse command for immediate verification failure.
- Added post-change root-cause re-investigation.
- Kept success-policy changes, auth/header customization, and application-code
  changes outside the automatic write boundary.
- Fixed an accidental duplicate `Advanced Intelligence` startup label.


# Changelog

## 0.6.7

- Added static discovery of the monitoring implementation that emits recurring
  application DOWN signals.
- Added supported extraction of HTTP client, method, timeout, literal headers,
  redirect behavior, and accepted HTTP status policy.
- Added source-derived replay from the actual Compose service that emitted the
  signal.
- Axios replays use the application's installed Axios package when available.
- Added explicit high/medium/low reproduction fidelity.
- Sensitive and dynamic headers are never copied automatically.
- Automatic application-check replay is restricted to read-only GET/HEAD.
- Added direct explanation when the reproduced HTTP status fails the discovered
  application success policy.
- Added reproduction evidence to conversational follow-up context.


# Changelog

## 0.6.6

- Added read-only Compose log correlation to identify the service that emitted
  an application `DOWN`/`FAILED` signal.
- Added application-container HTTP replay using Node, with curl fallback.
- Added host-vs-container execution-context comparison.
- Added structured HTTP status codes to probe steps.
- Improved HTTP 401/403 wording to include access policy, WAF/bot protection,
  authentication/authorization, request headers/User-Agent, and response-policy
  mismatch as possibilities.
- Added transparent replay limitation text: the neutral probe is not claimed to
  be the application's exact request.
- Added container evidence and context comparison to session follow-up answers.


# Changelog

## 0.6.5

- Excluded package/dependency lockfiles from monitored-target URL resolution.
- Prevented NPM registry tarballs and similar dependency URLs from receiving
  target-resolution confidence.
- Added read-only PostgreSQL target discovery for database-backed monitoring
  systems.
- PostgreSQL fallback introspects schema and only selects tables with both
  name-like and URL-like text fields.
- Database lookup accepts only HTTP/HTTPS target values.
- Ambiguous database matches are refused instead of guessed.
- Root-cause output now distinguishes file-backed versus database-backed target
  sources.


# Changelog

## 0.6.4

- Fixed explicit `investigate` parsing for natural-language phrases.
- `investigate why X is down` now resolves to subject `X`.
- Added shared investigation-subject normalization at the probe boundary so
  shell, CLI, local reasoning, and future callers cannot diverge.
- Added visible normalized-subject feedback when an explicit command is
  rewritten.
- Added regression tests for command and conversational investigation forms.


# Changelog

## 0.6.3

- Added read-only root-cause probing for application warnings.
- Added project configuration target discovery for monitored subjects.
- Added DNS, TCP, TLS, and HTTP probe layers.
- Added likely-cause classification for DNS/network/TLS/HTTP failures.
- Added explicit handling for 401/403, 404, 429, 5xx, and timeouts.
- Added metadata/link-local safety blocking.
- Added ambiguous-target refusal rather than guessing.
- Added `investigate <subject>` interactive command.
- Added `iceman investigate <subject> --path <workspace>` CLI command.
- Added `investigate_application_signal` to the trusted AI action contract.
- Added investigation state to session memory and follow-up explanations.


# Changelog

## 0.6.2

- Added structured application-level log signal extraction.
- Aggregates repeated `Subject | DOWN/FAILED/ERROR/UNHEALTHY/OFFLINE` events.
- Separates Docker/container health from application/dependency warnings.
- Added application signal table to Docker diagnosis output.
- Added project-level application warning count without incorrectly marking
  a healthy deployment as failed.
- Improved follow-up explanation so application warnings outrank generic
  `Container is running` informational findings.
- Added application warning context to the AI intent router.
- Avoids claiming a root cause for monitored-target failures when logs only
  establish that a target is DOWN.


# Changelog

## 0.6.1

- Replaced normal provider/model display with `Advanced Intelligence`.
- Kept `Intent` and `Reasoning` visible.
- Provider/model details remain available through `iceman ai-status`.
- Updated interactive startup branding.
- Local fallback now uses a product-facing fallback message.

## 0.6.0

- Added pluggable reasoning-provider architecture.
- Added OpenAI Responses API provider with Pydantic structured outputs.
- Added strict trusted-action AI contract with no arbitrary shell-command field.
- Added local provider retaining the v0.5 structured reasoner.
- Added `auto`, `local`, and `openai` provider selection.
- Added automatic local fallback when the external reasoning call fails.
- Added `iceman ai-status` and interactive `ai status`.
- Added session context packaging for AI intent routing.
- Preserved deterministic safety, approval, remediation, and verification
  layers underneath AI reasoning.
- Default OpenAI model is `gpt-5.6-luna`, configurable via
  `ICEMAN_OPENAI_MODEL`.

## 0.5.0

- Added stateful session reasoning.
- Added project-wide health assessments and issue prioritization.
