#!/usr/bin/env bash
# Read-only CLI checks; CI additionally runs this in a network namespace.
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 EXPECTED_VERSION /absolute/path/to/ice [...]" >&2
    exit 2
fi
VERSION="$1"
shift
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
trap 'echo "CLI smoke check failed; captured output follows." >&2; cat "$WORK"/*.txt >&2' ERR
mkdir -p "$WORK/home" "$WORK/project"
printf 'FROM scratch\n' > "$WORK/project/Dockerfile"
cd "$WORK/project"

for CLI in "$@"; do
    [[ "$CLI" == /* && -x "$CLI" ]] || { echo "Expected executable absolute path: $CLI" >&2; exit 2; }
    run_cli() {
        # Avoid credentials, user config, editable imports, and Ollama preferences.
        env -i HOME="$WORK/home" PATH=/usr/bin:/bin LANG=C.UTF-8 \
            XDG_CONFIG_HOME="$WORK/home/.config" TMPDIR="$WORK" \
            ICEMAN_AI_PROVIDER=local ICEMAN_KNOWLEDGE_MODE=offline \
            NO_COLOR=1 TERM=dumb COLUMNS=200 \
            timeout --kill-after=5s 30s "$CLI" "$@" </dev/null
    }
    run_cli --version > "$WORK/version.txt"
    grep -Fx "Iceman $VERSION" "$WORK/version.txt"
    run_cli --help > "$WORK/help.txt"
    grep -Fq 'ai-status' "$WORK/help.txt"
    run_cli ask "What is Kubernetes?" > "$WORK/knowledge.txt"
    # Check the answer, not the echoed question.
    grep -Fq 'Pods are its smallest deployable units.' "$WORK/knowledge.txt"
    run_cli ask 'inspect current folder' > "$WORK/inspect.txt"
    grep -Fq 'Dockerfile' "$WORK/inspect.txt"
    echo "Offline CLI checks passed: $CLI"
done
