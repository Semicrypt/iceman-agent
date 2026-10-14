# Iceman v0.9.7

Iceman is a safety-first Cloud/DevOps CLI agent with pluggable AI reasoning.

## Installed commands and one-shot requests

The package installs two equivalent commands: `iceman` and `ice`.
Run either with no arguments to open the interactive shell, or give it a
natural-language request to run one workflow and return to your terminal:

```bash
iceman
ice --version
ice analyze plan
ice git push
ice stage everything
ice status
ice git status
ice show git status
ice list my folders
ice show files here
ice analyze my current folder and files
ice inspect current folder
ice "show me the containers"
ice commit my changes with message "feat: add health checks"
iceman ask "analyze terraform plan"
```

`analyze plan` (or `analyze the plan`) means the existing Terraform plan
analysis workflow. It discovers Terraform configuration in the current workspace,
previews the trusted commands, and reports planned resource changes; it does not
apply infrastructure changes.

`ice status` shows the current workspace and fresh Iceman session context,
matching `status` inside the interactive shell. This exact shortcut is handled
locally without an AI call, external commands, or an approval prompt. It works
outside a Git repository and does not restore earlier interactive session history.
Use `ice git status` for Git working-tree status; its command preview and explicit
execution approval are unchanged. `iceman` with no arguments still opens the shell.

`ice list my folders`, `ice list folders`, `ice list my files`, and
`ice show files here` list the current directory contents, including both files
and folders, through the existing read-only Linux listing action. Iceman shows
the exact command and asks for approval before running it.

`ice analyze my current folder and files`, `ice analyze this folder`,
`ice inspect current folder`, and `ice analyze current directory` reuse the
read-only project inspector to detect technologies and report scanned files.
They do not run shell commands or change files.

One-shot requests use the directory where you invoke the command as their
workspace. They use the same configured reasoning provider, typed trusted actions,
Advisor, safety checks, typo/entity clarification, exact-command preview, and
approval gates as the interactive shell. For example, `ice stage everything`
runs the existing pre-commit safety review, and `ice git push` requires approval
before pushing. Requests are never executed as arbitrary shell text.

"One-shot" means no Iceman shell loop. When a workflow needs approval or
clarification, it still asks. Declining or reaching end-of-input stops the request
with a nonzero exit status. Natural-language requests, including `ask`, do not
offer `--yes`/`-y`; this replaces the older planner-only `ask --yes` behavior.
The explicit `exec` command retains its existing safety-checked `--yes` option.

Built-in commands (`doctor`, `inspect`, `check`, `investigate`, `diagnose`,
`ai-status`, `version`, `exec`, and `ask`) retain their own argument parsing.
The complete multi-word phrase `inspect current folder` (and close current-folder
variants) also works directly; a single path argument still means `inspect [PATH]`.
Use `ask` for other natural-language requests that start with a built-in command name:

```bash
ice inspect /path/to/project
ice ask check this project and tell me if anything is wrong
ice ask diagnose pod backend-abc
ice ask "investigate why the backend is down"
```

Both quoted requests and multiple words work. Quote shell metacharacters and
wildcards so your terminal does not interpret them before Iceman sees them.
Each one-shot invocation starts fresh session context. Use the interactive shell
for follow-ups such as `fix it`, `apply file plan`, or `rollback file changes`.
A one-shot request such as `ice change folder to Works` cannot change the parent
terminal's directory and does not persist a workspace for future invocations.
Iceman explains this and exits without changing workspace. Run `cd -- Works` in
your terminal before the next `ice` request, or open `iceman` and change workspace
inside that interactive session. Interactive workspace changes apply to later
requests in that session; they still do not change the parent terminal's directory.

Successful requests exit with status 0; unknown requests, blocked plans,
declined approvals, missing context, or failed workflows exit nonzero.
Invalid CLI arguments exit with status 2.

### Refresh the development installation

From this local checkout, install or refresh the editable pipx package:

```bash
pipx install --force --editable ~/iceman-agent
pipx list
command -v iceman
command -v ice
cd /tmp
iceman --version
ice --version
ice --help
```

Ensure `~/.local/bin` is on PATH (use `pipx ensurepath` and reopen your terminal
if needed). Source edits remain linked to the checkout; refresh the installation
when packaging entry points change so pipx exposes the new command.

This change does not publish a release. A future wheel/package installation will
install both entry points; public installation instructions depend on a separate
package release.

### Run the tests

```bash
source .venv/bin/activate
python -m pytest -q
git diff --check
```



## v0.9.7 — Git & GitHub Natural-Language Intelligence

v0.9.7 adds a dedicated Git/GitHub action engine so Iceman can manage everyday repository workflows using the same safety model already used for Docker, Kubernetes, Terraform, and Linux. The AI/provider selects only typed Git actions; Iceman constructs the deterministic commands, shows them exactly, and requires approval before execution.

Natural-language examples include:

```text
initialize git
show git status
show what changed
stage everything
commit my changes with message "feat: add Git intelligence"
create a public github repo called iceman-agent
push to github
commit and push with message "feat: milestone"
tag this as v0.9.7
```

Before broad staging or commit+push, Iceman performs a bounded pre-commit safety review. It blocks obvious sensitive files such as real `.env` files, private keys, Terraform state/tfvars, high-confidence secret/token patterns, unignored virtual environments/caches, and files at or above GitHub's 100 MiB file limit. It recommends creating/updating `.gitignore` rather than blindly running `git add .`.

GitHub repository creation uses authenticated GitHub CLI (`gh`). Iceman checks authentication, shows the exact `gh repo create` command, and creates/configures `origin` only after approval. Repository creation deliberately does **not** push commits automatically; pushing remains a separate reviewable action. If the user does not specify public/private visibility, Iceman chooses the safer `private` default and tells the operator before execution.

The manual-command safety layer also rejects force-push, hard reset, forced Git clean, and forced branch deletion. Normal Git push is supported.

This release is designed so the Iceman project itself can be used as the first end-to-end Git/GitHub validation: generate a `.gitignore`, initialize the repository, inspect/stage/commit the v0.9.7 milestone, create its GitHub repository, and push it using natural language.


## v0.9.6 — Command-Aware Docker Diagnosis + Resource Name Normalization

v0.9.6 improves two real-world Docker failure cases found during live testing.

First, Docker diagnosis can now reconstruct a bounded application startup command even when an image uses an entrypoint wrapper. Instead of stopping at a runtime shape such as `docker-entrypoint.sh | node`, Iceman captures only `Path + Args[0] + Args[1]`, keeps that raw inspection output hidden, sanitizes sensitive-looking arguments, and can safely report an effective command such as `node missing.js`. It never dumps the full argument array or container environment.

That lets Iceman correlate a startup command with the logs. For example, when a container exits with code 1, its safe startup command is `node missing.js`, and logs contain `Cannot find module '/app/missing.js'`, the diagnosis can identify an incorrect/missing startup entry file and recommend correcting the command before recreating the container.

Second, existing resource names are more conversational. If Docker contains `broken-backend`, a request such as `why did broken backend stop` can be normalized to the real resource name before intent routing and confirmed with the operator. Space, hyphen, and underscore variants are grounded only against resources that actually exist; creation destinations are not silently renamed.

All existing safety behavior remains: corrected interpretations require confirmation, exact trusted commands are shown before execution, diagnostic startup output is bounded and sanitized, and remediation is never auto-executed.

## v0.9.5 — Evidence-Aware Docker Root Cause

v0.9.5 makes Docker failure diagnosis more precise when Node.js reports `MODULE_NOT_FOUND`. Iceman no longer treats every missing module as an uninstalled package. It classifies the missing target using the sampled logs plus a deliberately bounded startup hint (only the container executable and first runtime argument; full runtime arguments are not exposed).

For example, a container started as `node missing.js` whose logs contain `Cannot find module '/app/missing.js'` is now diagnosed as a missing startup/entry file, not as a generic npm dependency problem. A bare package such as `express` is treated as a runtime dependency, while a relative target such as `./config/local.js` is treated as a missing local application file/module.

Docker diagnosis now also surfaces a very small set of the high-signal log lines that actually support the conclusion. The full 120-line sample stays hidden unless the operator explicitly asks to show logs. This keeps diagnosis readable while making the evidence visible.

The `docker_diagnose` plan includes a new read-only startup-inspection step. To reduce credential exposure, it uses only Docker's runtime executable plus the first argument instead of dumping the full entrypoint/argument array or environment.

Recommended next actions are tied to the established failure class: correct a bad startup command for a missing entry file, inspect COPY/build-context/import paths for a missing local module, or verify package installation for a missing runtime dependency. When the evidence cannot classify the missing target, Iceman explicitly says so instead of guessing.

All exact-command preview, approval gates, semantic language repair, Advisor checks, live progress, and stop-on-first-failure behavior remain unchanged.

## v0.9.4 — Reasoned Docker Diagnosis

Iceman now turns trusted Docker evidence into a structured operator-facing conclusion instead of ending with raw state/port facts. After a Docker health, connectivity, or diagnosis workflow completes, Iceman can present three bounded sections:

```text
Diagnosis
Evidence
Recommended next actions
```

For the validated port-mismatch scenario, the output can explain that the target container is running and responding successfully, that container port 5000 is published on host port 5001, that the requested host port 5000 belongs to another Docker container, and that the correct local endpoint is `http://localhost:5001/`.

The reasoning layer only consumes evidence already collected by trusted steps plus safe plan metadata; it does not run hidden commands or make additional changes. It distinguishes runtime state from application readiness, avoids claiming a root cause when evidence is weak, and prioritizes common high-signal Docker failures including dependency connection refusal, bind-port conflicts, permission errors, missing modules/files, and possible memory pressure.

Examples of recommended next actions include checking dependency host/port health after `ECONNREFUSED`, identifying an existing listener after `EADDRINUSE`, reviewing container-user/file permissions after permission errors, rebuilding after missing runtime dependencies, and adding an application-aware Docker `HEALTHCHECK` when Docker can only prove that the process is running.

All exact-command preview, approval gates, Advisor checks, semantic language repair, live progress, and stop-on-first-failure behavior remain unchanged.


## v0.9.3 — Semantic Language Repair

Iceman now performs a bounded language-repair pass **before** trusted intent routing. This lets several spelling/grammar mistakes be repaired together instead of requiring the sentence to match an intent pattern first. The repair stage only normalizes known conversational/DevOps control vocabulary; project/resource names are still grounded later against real local entities.

The regression that motivated this release now resolves safely:

```text
why cant i reac the backen on pot 5000

→ why can't i reach the backend on port 5000
```

Iceman then asks for confirmation before building any Docker commands. Other supported examples include:

```text
backend no dey open pot 5000
chec kubernete pod
terrafor plan no working
go bak one folde
giv deploy.sh execut permision
mak backen 5 replca
shw me the contaners
```

Material corrections still produce a `Do you mean ...?` gate. Entity names such as `backen` are not guessed from a generic dictionary: they are resolved against real workspace/Docker/Kubernetes objects after the intent becomes clear. If several real resources are plausible, Iceman presents bounded choices instead of selecting one silently.

Creation destinations remain literal, and destructive typo requests are deliberately not fuzzy-promoted into executable delete/destroy operations. Exact trusted-command preview, Advisor checks, live progress, and normal approval gates remain unchanged.

## v0.9.2 — Robust Language Understanding

Iceman now tolerates substantially more spelling, grammar, and sentence-shape errors while preserving the trusted-action boundary. The language layer can repair high-confidence DevOps terminology, normalize common conversational grammar, and ground mistyped resource names against real local project/Docker/Kubernetes/filesystem entities before execution.

Examples that now route safely include:

```text
why cant i reach the backen on port 5000
why backend no open
deploy backen to kubernete
mak backend 5 replica
why terrafor plan failing
giv deploy.sh execute permision
go bak to project folder
```

For a unique high-confidence match, Iceman does not silently guess. It shows the corrected interpretation and asks for confirmation, for example:

```text
Iceman interpreted your request as:
"why can't i reach the backend on port 5000"

Do you mean this? [y/N]
```

Entity repair is grounded against resources that actually exist. Docker container/component names are matched against the active workspace and Docker container inventory; Kubernetes deployment/component names are matched against local manifests/workspace components; Kubernetes contexts are matched against kubeconfig; existing Linux paths are matched against the active workspace. Creation targets are intentionally not fuzzy-rewritten because the user may be creating a new name.

When more than one real entity is plausible, Iceman presents a bounded numbered choice instead of choosing one silently. Low-confidence matches are left unresolved. Destructive requests are still never fuzzy-promoted into executable actions.

The local/free reasoning path provides these corrections without requiring an external AI API. Exact trusted commands, approval gates, Advisor checks, live progress, and stop-on-first-failure behavior remain unchanged.

## v0.9.1 — Advanced Docker Intelligence

Iceman now goes beyond Docker command translation and can perform bounded runtime
diagnosis, health inspection, port troubleshooting, security review, image-size
review, safe standalone rebuild/restart planning, and deterministic Compose
scaffolding. Exact commands are still shown before execution, and live progress,
Advisor checks, approvals, and stop-on-first-failure behavior remain active.

Natural-language examples:

```text
why did backend stop
why is backend restarting
check if backend is healthy
show important errors in backend logs
why can't I reach port 5000
check docker security for backend
make backend image smaller
rebuild and restart backend
show docker compose status
create docker compose for backend port 5000
```

### Runtime diagnosis

Docker diagnosis uses bounded, read-only evidence: runtime state, restart count,
health status, published ports, and a recent log sample. Iceman summarizes common
high-signal failures such as `ECONNREFUSED`, port conflicts, permission errors,
missing modules/files, syntax errors, and possible OOM failures. Raw diagnostic
log samples are retained for analysis but are not dumped live unless the user asks
for ordinary container logs.

### Safer `run` behavior

`run backend ...` now checks whether the deterministic container name already
exists. If it is already running, Iceman verifies it instead of failing with a
duplicate-name error. If it is stopped, Iceman can start and verify the existing
container. Explicit port-remapping requests are refused when they conflict with an
existing container's published ports. New containers are verified immediately
after `docker run`.

### Safe rebuild + restart

For a simple standalone container, `rebuild and restart backend` can rebuild the
image and replace the container while preserving the old container as
`<name>-iceman-backup` instead of deleting it. Automatic recreation is refused when
Iceman detects mounts/binds, custom networks, runtime environment overrides, or
command/entrypoint overrides. This avoids silently losing storage, secrets, or
runtime configuration.

### Docker Advisor

The Advisor can now flag Docker-specific issues such as:

- no non-root `USER`;
- no `HEALTHCHECK`;
- mutable `:latest` base images;
- remote URL downloads through `ADD`;
- secret-like literals baked into `ENV`;
- `COPY . .` without `.dockerignore`; and
- `npm install` despite an authoritative `package-lock.json`.

Image optimization output intentionally avoids Docker `CreatedBy` history because
that metadata can contain build arguments. Iceman exposes layer IDs/sizes and total
image size instead.

### Docker Compose generation

`create docker compose for backend port 5000` creates a conservative single-service
`compose.yaml` from an existing Dockerfile. It never invents databases, credentials,
or secret values. If a Compose file already exists, Iceman refuses to replace it
automatically; multi-service Compose editing needs explicit future support.

## v0.8.6 — Live Execution Progress

Iceman no longer waits silently for long-running Docker, Kubernetes, Terraform,
or Linux commands. Trusted workflows stream command output as it arrives and show
measurable workflow progress, elapsed time, silent-process heartbeats, per-step
duration, and a final workflow duration.

Example:

```text
[3/8 · 25%] ▶ Load local image iceman-test-backend:0.1 into Minikube
⠋ Load local image iceman-test-backend:0.1 into Minikube · elapsed 00:02 · no new output for 00:02
⠙ Load local image iceman-test-backend:0.1 into Minikube · elapsed 00:04 · no new output for 00:04
✓ [3/8 · 37%] completed in 00:05
```

The percentage is explicitly based on completed workflow steps. Iceman does not
invent command-level percentages when a tool cannot report them. When a tool does
produce real progress (for example Docker BuildKit stages or `kubectl rollout
status`), that output is streamed live.

Silent commands emit a heartbeat every few seconds so the terminal does not look
frozen. Manual `!command` execution also streams output and shows elapsed time.

`Ctrl+C` now stops the active child process, marks the workflow interrupted, and
prevents later state-changing steps from running. The summary reports completed
steps, the interrupted step, remaining steps, and total elapsed time.

## v0.8.5 — Guided Advisor Fixes

The Advisor can now turn selected findings into optional, bounded fixes while keeping
the operator in control. The first guided remediation is Kubernetes CPU/memory
resource requests and limits.

Example:

```text
iceman > deploy backend to kubernetes

Iceman Advisor
⚠ WARNING  No Kubernetes CPU/memory requests or limits were detected.
   Guided fix available.

Would you like Iceman to prepare starter CPU/memory requests and limits for this Deployment? [y/N]
```

If accepted, Iceman does **not** let the AI write arbitrary YAML. It selects the
trusted `kubernetes_resources` template, prepares a deterministic file plan, and
shows the exact unified diff. The write then uses the existing backup, atomic-write,
verification, and rollback engine.

The starter values are deliberately visible and conservative:

```yaml
resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 512Mi
```

These are starting points, not workload-specific production sizing. Iceman reminds
the operator to tune them using observed usage. Multi-container or otherwise
ambiguous manifests are refused instead of guessed.

After the verified file write, Iceman rebuilds the deployment plan from the current
workspace, re-runs Advisor checks, and still performs the Kubernetes dry-run before
any real apply.

The same bounded resource change can also be requested directly:

```text
add kubernetes resource requests and limits for backend
```

## v0.8.4 — Kubernetes Context Intelligence

Kubernetes deployment requests now resolve the real kubeconfig destination before
Iceman creates an executable plan. Generic platform language is not treated as a
context name.

For example:

```text
iceman > deploy backend to kubernetes
```

means "deploy the backend to Kubernetes". If the current context is `minikube`,
Iceman resolves the target to `minikube`, shows that destination, and asks:

```text
Deploy to current Kubernetes context 'minikube'? [y/N]
```

Only after target confirmation does the normal exact-command approval gate run.

Before a Kubernetes deploy plan is considered executable, Iceman now:

- enumerates the kubeconfig contexts;
- verifies an explicitly requested context actually exists;
- resolves the current context when no specific cluster is named;
- reads the context's configured namespace for destination/advisor output;
- checks Kubernetes API reachability before apply;
- scopes every kubectl deployment command with `--context <target>`; and
- refuses a nonexistent target before any apply step is possible.

Iceman does **not** change the user's active kubeconfig context during a deploy.
For a target other than the current context, commands remain explicitly scoped:

```text
kubectl --context prod-cluster apply --dry-run=client -f k8s/
kubectl --context prod-cluster apply -f k8s/
```

The Advisor also calls out likely remote contexts and asks the operator to verify
cloud/account, cluster, namespace, and environment before approval.

## The v0.6 security boundary

The AI model is **not a shell executor**.

It cannot return arbitrary commands to run. The AI output is validated into a
strict Pydantic object whose action must be one of Iceman's trusted actions:

- `assess_project`
- `prioritize`
- `diagnose_container`
- `explain_context`
- `session_status`
- `repair_last_diagnosis`
- `inspect_project`
- `doctor`
- `fallback`

There is intentionally no `command`, `shell`, `script`, or `args` field in the
AI contract.

Iceman itself maps trusted actions to its existing deterministic diagnostic and
remediation functions. Write actions still pass through the existing plan,
approval, safety, and verification gates.

## Providers

v0.6 includes:

### Local provider

The structured local v0.5 reasoner. No network connection or API key required.

### OpenAI provider

Uses the OpenAI Responses API with Pydantic structured outputs.

The default OpenAI model is:

```text
gpt-5.6-luna
```

Override it with:

```bash
export ICEMAN_OPENAI_MODEL="gpt-5.6-terra"
```

or another compatible model.

## Provider selection

Default:

```bash
export ICEMAN_AI_PROVIDER=auto
```

`auto` chooses OpenAI when `OPENAI_API_KEY` is present, otherwise local
reasoning.

Explicit local mode:

```bash
export ICEMAN_AI_PROVIDER=local
```

Explicit OpenAI mode:

```bash
export ICEMAN_AI_PROVIDER=openai
export OPENAI_API_KEY="..."
```

Iceman reads the API key from the environment. It does not write the API key to
its project files or session state.

Check configuration:

```bash
iceman ai-status
```

or inside Iceman:

```text
ai status
```

## Safe fallback

When auto/OpenAI mode is active but the API request fails, Iceman falls back to
the local structured reasoner. The fallback does not bypass any safety rule.

## Example

```text
iceman > inspect ~/hybrid-cloud-monitor-v2

iceman (hybrid-cloud-monitor-v2) >
the frontend says it is running but users cannot reach it; investigate
```

The provider may return a validated intent such as:

```text
action: diagnose_container
target: minerva-frontend
confidence: high
```

It cannot return:

```text
docker exec ...
rm ...
terraform destroy ...
```

because no shell-command field exists in the schema.

## Architecture

```text
User request
    |
    v
Reasoning provider
(OpenAI or local)
    |
    v
Strict AIIntent schema
    |
    v
TrustedAction allow-list
    |
    +--> read-only assessment/diagnosis
    |
    +--> bounded remediation builder
              |
              v
         explicit approval
              |
              v
        deterministic executor
              |
              v
          verification
```


## v0.6.1 presentation update

Normal interactive output now uses:

```text
Advanced Intelligence
Intent: assess_project
Reasoning: ...
```

Provider/model details remain available through:

```bash
iceman ai-status
```


## v0.6.2 — Application Log Intelligence

Iceman now distinguishes three different layers of health:

```text
Container/runtime health
Application-reported health
Monitored dependency/target health
```

For example, a backend can be:

```text
Container: HEALTHY / running
Application warnings:
  Openai: DOWN ×4
  Broken Website: DOWN ×4
```

without Iceman incorrectly claiming that the Docker container itself is down.

Repeated `DOWN`, `FAILED`, `ERROR`, `UNHEALTHY`, and `OFFLINE` statuses written
in the supported `Subject | STATUS` log format are aggregated into structured
application signals.

These warnings are deliberately not promoted to deployment/runtime failures
without stronger evidence because monitored targets may be synthetic,
intentionally failing, misconfigured, unreachable, or genuinely unavailable.

Follow-up questions such as:

```text
what caused the problems you found?
```

now prefer meaningful application warnings over the generic
`Container is running` informational finding.


## v0.6.3 — Root-Cause Probing

Iceman can now move from detecting an application warning to bounded read-only
investigation.

Example:

```text
iceman > investigate why Openai is down
```

Workflow:

```text
Application warning
    ↓
Resolve configured target from project files
    ↓
DNS resolution
    ↓
TCP connection
    ↓
TLS handshake (HTTPS)
    ↓
HTTP status/reachability
    ↓
Likely-cause classification
```

The probe distinguishes likely causes including:

- DNS/name-resolution failures
- TCP/network connectivity failures
- TLS/certificate failures
- HTTP timeout
- HTTP 401/403 authentication or authorization
- HTTP 404 path mismatch
- HTTP 429 quota/rate limiting
- HTTP 5xx remote/upstream failures
- endpoint currently healthy, suggesting application-check configuration,
  expected-response, authentication, timeout, or container-vs-host network
  differences

Safety:

- read-only only
- HTTP/HTTPS only
- embedded URL credentials are rejected
- cloud metadata/link-local endpoints are blocked
- ambiguous configuration matches are not probed automatically
- no remediation is performed by this workflow

CLI form:

```bash
iceman investigate "Openai" --path ~/hybrid-cloud-monitor-v2
```


## v0.6.4 — Investigation Phrase Normalization

The explicit `investigate` command now accepts both command-style and natural
phrasing:

```text
investigate Openai
investigate why Openai is down
why is Openai down?
check why Openai is failing
```

All of these normalize to the application-signal subject:

```text
Openai
```

This fixes a v0.6.3 bug where:

```text
investigate why Openai is down
```

was interpreted literally as a target named `why Openai is down`.


## v0.6.5 — Target Resolution Hardening

Root-cause probing now rejects dependency metadata as monitored-target evidence.

The following files are excluded from target URL discovery:

```text
package-lock.json
npm-shrinkwrap.json
yarn.lock
pnpm-lock.yaml
poetry.lock
Pipfile.lock
Cargo.lock
go.sum
composer.lock
*.lock
```

This prevents package registry/download URLs from being mistaken for application
monitoring endpoints.

### Database-backed monitored targets

If no trustworthy source/config URL is found, Iceman now performs a read-only
PostgreSQL fallback when the active project has a running Compose PostgreSQL
service.

The fallback:

1. discovers the running PostgreSQL Compose container,
2. reads its `POSTGRES_USER` / `POSTGRES_DB` configuration,
3. queries `information_schema.columns`,
4. identifies tables containing both name-like and URL-like text columns,
5. searches those tables for the application warning subject,
6. only accepts valid HTTP/HTTPS values,
7. refuses ambiguous multi-target matches.

All PostgreSQL operations are `SELECT` queries. No database writes are used.

Example output:

```text
Configured target: https://actual-monitored-service.example
Source: PostgreSQL services.url (matched by services.name)
Source type: database
Resolution confidence: high
```


## v0.6.6 — Execution-Context Diagnostics

Root-cause probing now correlates the monitored target with the Compose service
that actually emits its `DOWN`/`FAILED` log signal.

Workflow:

```text
Resolve monitored target
    ↓
Probe from host
    ↓
Find Compose service emitting Subject | DOWN
    ↓
Replay from that application container
    ↓
Compare host vs container
    ↓
Refine likely cause
```

The application-container replay is read-only and uses a neutral GET request
with the same Iceman probe User-Agent in both contexts.

Iceman explicitly states that this replay does **not** claim to reproduce the
application's exact headers, authentication, or request code unless those are
separately observed.

Examples:

```text
Host:      HTTP 200
backend:   HTTP 403
```

is classified as an execution-context-specific failure.

```text
Host:      HTTP 403
backend:   HTTP 403
```

makes a host-vs-container network difference unlikely and points instead to an
HTTP-layer rejection such as access policy/WAF/bot protection,
authentication/authorization, request headers/User-Agent, or response-policy
mismatch.

A `403` is no longer described narrowly as authentication/authorization.


## v0.6.7 — Source-Derived Application Check Reproduction

Root-cause investigation now continues from network evidence into the
application's monitoring implementation.

Iceman statically locates the most likely monitoring source file by correlating:

- recurring `DOWN` logging
- `Monitoring Cycle Started` style monitoring markers
- HTTP-client usage
- `service.url` / `check.url` / `target.url`
- backend/monitoring source location

It derives supported request properties without importing or executing project
source code:

```text
HTTP client
GET / HEAD method
timeout
literal non-sensitive headers
redirect behavior
HTTP success-status policy
```

Supported clients include direct Axios, Fetch, and Node HTTP/HTTPS patterns.

The discovered request is then replayed read-only from the Compose service that
actually emitted the `DOWN` signal. Axios replays use the Axios installation
inside that application container when available.

### Safety and fidelity

- only GET and HEAD are replayed automatically
- POST/PUT/PATCH/DELETE are never replayed automatically
- Authorization, Cookie, API-key, and other sensitive headers are never copied
- dynamic header expressions are omitted rather than evaluated
- project modules are never imported or executed
- custom Axios instances/interceptors and response-content checks reduce replay
  fidelity instead of being treated as exact
- output reports `high`, `medium`, or `low` reproduction fidelity

Example:

```text
Application check implementation
  Source: backend/services/monitor.js:42
  Client: axios
  Method: GET
  Timeout: 5000 ms
  Success policy: Axios default HTTP 2xx
  Reproduction fidelity: high

Source-derived application check replay
  Context: backend
  Runtime: axios/...
  HTTP status: 403
  Application policy result: FAIL

Check reproduction conclusion:
The source-derived Axios GET replay returned HTTP 403 while the discovered
application policy accepts only HTTP 2xx. That mismatch explains the current
DOWN classification at the HTTP-status layer.
```

Iceman calls this "source-derived reproduction" rather than claiming
byte-for-byte identity when dynamic/custom behavior cannot be established.


## v0.7.0 — Remediation Intelligence

Iceman now continues from established root cause into bounded remediation
recommendations and a guarded application-change workflow.

After an application investigation:

```text
how should I fix it?
```

produces numbered remediation options. For an HTTP target that fails the
application's discovered success policy, the default options are:

1. replace the monitored target URL with an operator-chosen endpoint intended
   for automated health checks,
2. change the accepted-status policy (manual semantic decision),
3. customize the monitoring request according to authoritative endpoint
   requirements (manual/security-sensitive decision).

Only option 1 is exposed as an automatic write in v0.7, and only when all of
these conditions hold:

- the current target is uniquely database-backed,
- the emitting Compose application container is known,
- the source-derived check profile has high reproduction fidelity,
- the operator explicitly supplies the replacement URL,
- the replacement URL passes the application's discovered check policy from
  inside the emitting application container,
- PostgreSQL can be resolved through the active Compose project.

Example:

```text
apply option 1
```

does **not** write anything because no replacement URL was supplied.

```text
apply option 1 with https://your-intended-health-endpoint
```

first performs the source-derived application preflight. Only a passing
replacement becomes write-ready.

The PostgreSQL update then has an exact old-value precondition, requires exactly
one matching row, verifies the new value by read-back, and retains a bounded
inverse command for immediate verification failure.

Iceman never invents a replacement URL.

Automatic changes to success policy, authentication, cookies, API keys,
anti-bot/WAF behavior, or arbitrary application code are deliberately outside
the v0.7 automatic write boundary.


## v0.7.1 — Context-Aware Remediation Ranking

Remediation ordering now uses execution-context evidence instead of always
placing target replacement first.

When the host succeeds but the application container fails, for example:

```text
Host:    HTTP 200
Backend: HTTP 403
Axios source-derived replay: HTTP 403
```

Iceman now ranks:

```text
1. Investigate the backend-specific HTTP rejection  (recommended)
2. Replace the monitored target URL
3. Customize the monitoring request
4. Change the accepted-status policy
```

The ranking explicitly warns that replacing a URL before understanding an
execution-context mismatch may hide the real problem or change what the monitor
actually measures.

The bounded PostgreSQL replacement workflow is now attached to the semantic
`replace_target` action rather than hard-coded to Option 1. Therefore in a
context-mismatch case the safe write moves with Option 2:

```text
apply option 2 with https://your-intended-health-endpoint
```

while:

```text
apply option 1
```

prepares no write because Option 1 is diagnostic.

If both host and container are currently healthy, Iceman also ranks further
investigation ahead of configuration changes.


## v0.7.2 — Evidence Stability & Multi-Sample Diagnosis

Iceman no longer bases application remediation ranking on one HTTP response.

A root-cause investigation now retains a bounded three-sample HTTP series for:

```text
host neutral probe
application-container neutral probe
source-derived application check
```

Example:

```text
Current-run evidence stability

Host
  403, 200, 403
  UNSTABLE / MIXED

Backend
  403, 403, 403
  STABLE FAILURE

Source-derived Axios check
  403, 403, 403
  STABLE FAILURE
```

Classifications are:

```text
STABLE SUCCESS
STABLE FAILURE
UNSTABLE / MIXED
INCOMPLETE
INSUFFICIENT
```

Within one interactive Iceman session, evidence for the same workspace,
subject, and configured URL is accumulated with a bounded maximum of 18 samples
per context. Remediation Intelligence prefers this session evidence when it is
available.

Evidence does not silently carry across Iceman process restarts.

### Remediation ranking

Persistent application failure plus any observed host success prioritizes
execution-context investigation.

Mixed/unstable evidence prioritizes further stability investigation ahead of
writes.

Only stable evidence with no stronger context mismatch falls back to target
replacement as the leading bounded remediation.

### Safer target replacement

A proposed replacement target now has to pass **three** source-derived
application-check samples from the emitting container. Automatic database
replacement is blocked unless the preflight classification is:

```text
STABLE SUCCESS
```

One passing request is no longer sufficient to make a database change ready for
approval.


## v0.7.3 — HTTP Context Differential Analysis

A recommended `investigate_context` remediation option is now executable as a
read-only workflow.

Examples:

```text
investigate option 1
go deeper on the backend rejection
compare host and backend HTTP context
```

The analysis compares supported non-sensitive context such as:

```text
HTTP method
bounded host statuses
representative current status
final URL and redirect count/chain
resolved/selected addresses
IPv4/IPv6 family
TLS version and ALPN when observable
safe request headers such as User-Agent and Accept
safe response metadata such as Server, Via, Content-Type, CF-Ray, X-Cache
Axios and Node runtime
proxy environment-variable PRESENCE (never values)
container DNS servers
Docker network names
```

The application-side request is source-derived from Iceman's existing static
monitor implementation analysis. Sensitive/dynamic source headers remain
omitted.

### Safety boundary

Iceman v0.7.3 does not:

- attempt WAF or bot-protection bypasses,
- spoof a browser fingerprint,
- guess cookies, credentials, API keys, Authorization headers, or secret
  request data,
- display proxy values,
- execute project source code,
- change Docker/network/application configuration.

Observable differences are presented as candidates, not automatically claimed
as the cause.

When session evidence shows intermittent host success but stable application
failure, the differential can establish that the persistent problem is in the
application/request HTTP context rather than basic DNS/TCP/TLS reachability.


## v0.7.4 — Transport Correlation & Differential Confidence

The HTTP context differential now correlates the actual Axios/follow-redirects
request with container DNS and transport evidence.

Application-side differential output can now include:

```text
Resolved addresses
Selected remote address
Address family
TLS version
ALPN
Transport observation source
```

The implementation does not add a different network client to imitate the
application. It observes the real ClientRequest/socket reachable from the
source-derived Axios replay and independently resolves the target hostname
inside the same container.

### Differential hypothesis assessment

Iceman now separates three categories:

```text
Ruled down / unsupported
Still plausible
Not established
```

Examples of hypotheses it can rule down or mark unsupported when evidence is
available:

```text
basic DNS/TCP/TLS reachability failure
different final redirect destination
simple proxy-environment mismatch
TLS-version mismatch
ALPN/protocol-negotiation mismatch
```

Examples that can remain plausible:

```text
HTTP request-characteristic treatment
endpoint/edge policy or automated-monitor suitability
```

Examples deliberately kept as not established without stronger evidence:

```text
a specific WAF/bot rule
User-Agent as the root cause
an authentication or secret-header requirement
```

### Confidence

`Confidence: high` now means high confidence in the **failure layer and context**,
not proof of a particular remote security rule. The output includes a
`Confidence basis` explaining which competing hypotheses were constrained.

Iceman still does not attempt WAF/bot bypasses, browser fingerprint spoofing,
credential discovery, or secret-header guessing.


## v0.7.5 — Live Transport Capture

The application differential now observes Node's normal request lifecycle while
the source-derived Axios request is active.

Before Axios/follow-redirects loads, Iceman temporarily wraps the native:

```text
http.request
https.request
```

functions. The wrappers call the original functions unchanged and attach passive
listeners to the returned `ClientRequest` and its socket:

```text
socket
connect
secureConnect
```

No request options, headers, HTTP method, redirect behavior, agent, TLS settings,
or socket configuration are changed.

The originals are restored after the bounded replay completes.

This allows the application-side differential to capture, when the runtime
exposes them:

```text
Selected remote address
Address family
TLS version
ALPN
Transport observation source
Transport event
```

Example:

```text
Selected remote address  104.18.33.45
Address family           IPv4
TLS                      TLSv1.3
ALPN                     http/1.1
Transport observed via   live https.request secureConnect event
Transport event          secureConnect
```

The selected address is correlated against a separate DNS resolution performed
inside the same application container.

A post-response socket walk remains only as a fallback if the live observation
does not expose transport metadata.

### Safety

Live capture is passive instrumentation around the bounded diagnostic replay. It
does not:

- change headers or User-Agent,
- inject an HTTP/TLS agent,
- alter certificate validation,
- force IPv4/IPv6,
- alter redirects,
- retry with browser-like behavior,
- bypass WAF/bot protections,
- inspect credentials or secret headers.


## v0.8.0 — Workspace & File Action Engine

v0.8.0 begins Iceman's transition from diagnosis-only workflows toward natural
language DevOps work.

The new workspace file engine can safely **preview, create, update, verify, and
roll back** deterministic project files.

Initial trusted templates:

```text
Dockerfile + .dockerignore for a Node.js component
.dockerignore
.gitignore
Kubernetes Deployment + ClusterIP Service
Terraform AWS provider scaffolding
Terraform AzureRM provider scaffolding
```

Examples:

```text
create a Dockerfile for backend port 5000
create a .gitignore
create Kubernetes deployment and service for backend port 5000 replicas 3
create Kubernetes deployment and service for backend port 5000 image myrepo/backend:1.0
create Terraform starter for AWS
create Terraform starter for Azure
rollback file changes
```

### Write workflow

Natural-language file generation follows:

```text
request
  ↓
trusted typed template
  ↓
deterministic generator
  ↓
path/security validation
  ↓
unified diff preview
  ↓
explicit operator approval
  ↓
backup existing file under ~/.cache/iceman
  ↓
atomic file write
  ↓
post-write validation + SHA-256 verification
  ↓
session rollback available
```

The AI provider never supplies arbitrary file contents or arbitrary paths.

### Path safety

v0.8.0 rejects:

- absolute generated paths,
- `..` traversal,
- writes that escape the active workspace,
- replacement of symlinks,
- replacement of directories,
- non-UTF-8 existing files,
- generated files over 1 MB,
- generated content resembling AWS access keys/private keys/static secret
  assignments.

### Dockerfile generation

The first Dockerfile generator targets Node.js projects and deliberately refuses
to guess a production entrypoint when the selected component has no `npm start`
or `npm run serve` script.

A port may be supplied naturally:

```text
create a Dockerfile for backend port 5000
```

If no port is supplied, Iceman tries bounded static source detection and clearly
labels a fallback port in the preview.

### Kubernetes generation

v0.8.0 requires an explicit port rather than inventing one:

```text
create Kubernetes deployment and service for backend port 5000 replicas 3
```

If no image is provided, Iceman uses a visible `your-registry/...` placeholder
and warns before approval. It does not deploy anything in v0.8.0.

### Terraform generation

The first Terraform templates create provider scaffolding only. They do not
invent infrastructure or store cloud credentials.

AWS:

```text
terraform/versions.tf
terraform/providers.tf
terraform/variables.tf
terraform/terraform.tfvars.example
```

Azure:

```text
terraform/versions.tf
terraform/providers.tf
```

Resource-level AWS/Azure generation is planned for the Terraform intelligence
milestone.

### Git

v0.8.0 starts Git support with deterministic `.gitignore` generation. Git
status/diff/stage/commit/push workflows remain separate trusted actions for the
planned Git workflow milestone.


## v0.8.1 — Natural Language DevOps Actions

v0.8.1 adds the first conversational execution layer for Iceman's three core
DevOps domains: Docker, Kubernetes, and Terraform. The `!command` escape hatch
still exists, but ordinary work no longer requires the operator to type the
underlying CLI command.

Examples:

```text
build the backend
run it on port 5050
show me the containers
backend logs
stop backend
restart backend

validate my kubernetes manifests
deploy backend to minikube
show me the pods
show me the deployments
show me the services
wait until backend is ready
scale backend to 5 replicas
restart backend deployment
kubernetes logs for backend
describe backend deployment

initialize terraform
format my terraform
validate my terraform
show me what terraform will create
explain terraform plan
show terraform state
```

### Trusted execution model

Natural-language operational requests use a strict `DevOpsAction` enum. The AI
provider may choose the action and structured parameters such as component,
image, port, replica count, Kubernetes context, or Terraform directory. It has
no shell-command field.

Iceman itself builds the actual Docker/kubectl/Terraform commands.

All natural-language DevOps workflows display the exact deterministic command(s)
before execution and require one explicit operator confirmation. This includes
read-only requests such as `show pods`, as well as state-changing workflows.

The executor stops on the first failed step and does not continue to later
state-changing operations.

### Docker

Initial natural-language operations:

```text
docker_build
docker_run
docker_list
docker_logs
docker_stop
docker_restart
```

Docker build discovers a component Dockerfile and chooses a deterministic image
name when one was not supplied. Docker run reads the exposed container port from
the Dockerfile or image metadata. If the requested host port is occupied, Iceman
performs a bounded free-port search and shows the replacement mapping before
approval.

### Kubernetes

Initial natural-language operations:

```text
k8s_context
k8s_contexts
k8s_validate
k8s_deploy
k8s_pods
k8s_deployments
k8s_services
k8s_rollout
k8s_scale
k8s_logs
k8s_describe
k8s_restart
```

`deploy backend to minikube` can switch context after approval, load a locally
available image into Minikube when appropriate, run a client dry-run, apply the
manifests, wait for rollout, and show pods/services as one bounded workflow.

Kubernetes delete is not exposed in v0.8.1.

### Terraform

Initial natural-language operations:

```text
terraform_init
terraform_fmt
terraform_validate
terraform_plan
terraform_explain_plan
terraform_state
```

`validate my terraform` auto-detects `terraform/` or root-level `.tf` files. If
providers have not yet been initialized, Iceman proposes a local
`init -backend=false` step and requires approval before that local change.

Terraform plans are run with input disabled and color disabled. Iceman extracts
the standard add/change/destroy count when available. Terraform apply and destroy
remain outside the v0.8.1 trusted action set.

### Terminal output polish

ANSI escape sequences from tools such as Terraform are stripped before display,
and untrusted command output is escaped before Rich renders it. This also fixes
literal strings such as `*.py[cod]` being accidentally interpreted as terminal
markup.



## v0.8.3 — Natural-Language Linux + Iceman Advisor

v0.8.3 adds the first trusted Linux operations layer and a pre-execution Advisor.
The AI still cannot send arbitrary shell text to the executor: it selects typed
Linux/DevOps actions and Iceman constructs the exact command deterministically.
Every supported Linux action shows the exact command and requires `y/N` approval.

Natural-language Linux examples:

```text
where am I
show me the files
show hidden files
go into backend
go back one folder
create a folder called configs
create an empty file called app.log
show me package.json
copy Dockerfile into backend
move app.log into logs
make deploy.sh executable
show permissions for deploy.sh
show disk space
show what is using port 5000
show running processes
find files matching *.yaml
find DB_HOST in the project
```

File-changing Linux actions are constrained to the active Iceman workspace.
Navigation can intentionally move the active workspace, but Iceman keeps that
state itself so `cd` persists across later requests. `chmod +x` is exposed as a
least-privilege action; broad arbitrary chmod modes are not part of this release.

The Iceman Advisor runs before relevant Docker/Kubernetes/Terraform/Linux plans
and surfaces only contextual findings, using four levels: BLOCKER, WARNING,
RECOMMENDATION, and REMINDER. Initial checks include Docker root/HEALTHCHECK
awareness, Kubernetes resource requests/limits, image `:latest`, probes and
namespace reminders, Terraform remote-state/secret-bearing tfvars checks, and
Linux least-privilege permission reminders. Findings never silently rewrite the
operator's plan.

v0.8.3 also promotes `show me the docker images` to a first-class trusted
`docker_images` action rather than falling back to the legacy command planner.

Free-form arbitrary text editing is intentionally not implemented as raw `sed`
or AI-authored shell in this release. Existing deterministic workspace-file
writes remain previewed/diffed/backed up/rollback-capable; broader safe
natural-language file editing will build on that mechanism instead of giving the
AI unrestricted editor commands.

## v0.8.2 — Conversational DevOps Context & Command Confirmation

v0.8.2 makes the natural-language layer more forgiving while making execution
more transparent. Every supported DevOps request now follows:

```text
User request
    ↓
trusted intent
    ↓
exact deterministic command preview
    ↓
Execute the exact command(s) shown above? [y/N]
    ↓
execution only after approval
```

This applies even to read-only requests such as:

```text
show pods
show deployments
validate my terraform
```

Iceman also repairs common DevOps spelling mistakes using a deliberately narrow
technical vocabulary. A corrected technical term requires confirmation before
the trusted action is dispatched:

```text
iceman > validate my terrafor
Iceman interpreted your request as: 'validate my terraform'
Do you mean: 'validate my terraform'? [y/N]
```

Conversational follow-ups include:

```text
show pods again
show them again
again
refresh that
make it 3 instead
what about services?
please show me pods
```

Pronoun/context resolution reuses the previous **trusted DevOps intent and
structured parameters**, never the previous raw shell command. Ambiguous context
repairs ask for confirmation.

Fuzzy correction does not rewrite arbitrary component names, image references,
URLs, filesystem paths, or destructive requests. Terraform destroy/apply and
Kubernetes delete remain outside this trusted action set.

## Docker connectivity language (v0.9.1)

Iceman accepts ordinary connectivity descriptions without requiring the operator to remember a special diagnostic phrase:

```text
why can't I reach backend on port 5000
I can't connect to backend
backend isn't reachable
backend isn't opening
why is backend not responding
check why backend connectivity is failing
port 5000 isn't working
```

Component-aware requests use the trusted `docker_connectivity` workflow. Iceman inspects runtime state and published ports, checks ownership of a specifically mentioned host port, and may perform one bounded HTTP GET against `127.0.0.1` when exactly one published host port is unambiguous. A failed localhost probe is treated as diagnostic evidence rather than a reason to abandon the rest of the explanation. Iceman does not scan ports, probe arbitrary remote hosts, or allow the AI provider to construct shell commands.

## Conversational project reviews

Run `ice what do you think about this project?` from a project directory, or start
`iceman` for an interactive conversation. Other examples:

```text
ice what do you think i should improve?
ice any good suggestion?
ice what do you think is missing?
ice is this project production ready?
ice review this project for security risks
ice stage everything, commit with the message 'example' and push to github
```

Project advice works locally without an API key. It inspects bounded source and
configuration evidence for stack/structure, Docker/Compose, Kubernetes,
Terraform AWS/Azure definitions, CI, documentation, and operational safeguards.
Findings include file locations and distinguish observed configuration from
suggestions or missing evidence. Static heuristics do not certify production
readiness or identify confirmed CVEs. Optional scanner availability is reported;
scanners, tests, cloud queries, and live runtime diagnostics are not automatically
run as part of this file review. Existing trusted diagnostic actions remain
available separately with their normal previews and approvals.

In interactive `iceman`, follow a review with `why?`, `go deeper`, `what else?`,
`show me the evidence`, or `what should I do first?`. `fix the highest risk`
explains the recommended change; it does not apply arbitrary edits. Request a
specific supported file plan or action to preview and approve changes. Changing
workspace clears previous targets and plans. Each one-shot `ice` process starts
with fresh context; review follow-ups across separate processes are not persisted.

The combined Git workflow prepares one plan and asks once before staging,
committing, pushing the current branch to its configured `origin`, and checking
that the remote revision matches. The phrase “to github” uses the configured
origin; it does not create or replace a remote. It refuses ambiguous or unsupported
chains before executing a prefix, blocks common sensitive paths/secret signatures,
and rechecks repository state before staging. It must be run at the repository
root and conservatively refuses changed symlinks/submodules. Existing committed
history can also be published; the preflight scan is not a complete history audit.
If a later step fails, earlier completed steps are reported and are not silently
rolled back. Staged-only commit-and-push requests require clarification because
this combined workflow stages all changes.

Common spelling corrections are bounded, with confirmation before a repaired
state-changing request. Quoted text, paths, resource arguments, and commit
messages are treated as literal data. Quote shell metacharacters as usual; an
interactive request can retain quotation marks around a complex commit message.
OpenAI remains optional for semantic interpretation using the strict trusted-action
schema and redacted structured context. AI output cannot supply shell commands.

Developer validation for this redesign includes `tests/test_conversational_router.py`,
`tests/test_project_advisor.py`, `tests/test_conversation_integration.py`,
`tests/test_git_compound_workflow.py`, and `tests/test_cli_live_conversation.py`.
The live tests use the installed global `ice` in temporary directories and only
push to a disposable local bare remote.
