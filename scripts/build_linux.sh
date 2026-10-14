#!/usr/bin/env bash
set -euo pipefail
umask 022

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${1:-$ROOT/dist/linux}"
PYTHON="${PYTHON:-python3}"
MAINTAINER="${AGENT_ICEMAN_MAINTAINER:-}"

fail() { echo "ERROR: $*" >&2; exit 2; }

# CI supplies its own test-only value; ordinary builds need a real contact.
[[ -n "$MAINTAINER" ]] || fail "Set AGENT_ICEMAN_MAINTAINER to 'Name <email>'."
[[ "$MAINTAINER" != *$'\n'* && "$MAINTAINER" != *$'\r'* ]] || fail "Maintainer must be one line."
[[ "$MAINTAINER" =~ ^[^\<]+\<[^\ \<\>]+@[^\ \<\>]+\>$ ]] || fail "Maintainer must use 'Name <email>' format."
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || fail "This build targets Linux AMD64 only."
for command in "$PYTHON" dpkg dpkg-deb getconf sha256sum install timeout; do
    command -v "$command" >/dev/null || fail "Missing build dependency: $command"
done
[[ "$(dpkg --print-architecture)" == amd64 ]] || fail "An AMD64 userspace is required."

# Never label a newer host build as compatible with an older glibc.
# The Actions workflow builds on Ubuntu 22.04 (glibc 2.35).
GLIBC="$(getconf GNU_LIBC_VERSION)"
GLIBC="${GLIBC##* }"
[[ "$GLIBC" =~ ^[0-9]+\.[0-9]+$ ]] || fail "Could not determine the glibc version."

mkdir -p "$OUTPUT"
OUTPUT="$(cd "$OUTPUT" && pwd)"
[[ -z "$(find "$OUTPUT" -mindepth 1 -maxdepth 1 -print -quit)" ]] || fail "Output directory must be empty: $OUTPUT"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
unset PYTHONPATH PYTHONHOME
export PYTHONNOUSERSITE=1

echo "=== Building Agent Iceman (glibc >= $GLIBC) ==="
"$PYTHON" -m venv "$WORK/venv"
PY="$WORK/venv/bin/python"
"$PY" -m pip install 'build==1.6.1' 'pyinstaller==6.22.3'
"$PY" -m build --wheel --outdir "$WORK/wheels" "$ROOT"

shopt -s nullglob
WHEEL=("$WORK"/wheels/agent_iceman-*.whl)
[[ ${#WHEEL[@]} -eq 1 ]] || fail "Expected exactly one Agent Iceman wheel."
WHEEL_NAME="$(basename "${WHEEL[0]}")"
"$PY" -m pip install "${WHEEL[0]}"

cd "$WORK"
VERSION="$("$PY" -I -c 'from importlib.metadata import version; import iceman; v = version("agent-iceman"); assert iceman.__version__ == v, "Source and wheel versions differ"; print(v)')"
# PEP 440 prereleases need an explicit Debian version mapping.
[[ "$VERSION" =~ ^[0-9]+(\.[0-9]+)*$ ]] || fail "Expected a stable numeric version; got $VERSION."
DEB_VERSION="${VERSION}-1"
dpkg --validate-version "$DEB_VERSION"

echo "=== Checking installed wheel ==="
bash "$ROOT/scripts/smoke_linux.sh" "$VERSION" "$WORK/venv/bin/ice" "$WORK/venv/bin/iceman"

echo "=== Building Iceman executable v$VERSION ==="
"$PY" -m PyInstaller \
    --noconfirm \
    --clean \
    --onefile \
    --name ice \
    --collect-submodules iceman \
    --recursive-copy-metadata agent-iceman \
    --distpath "$WORK/standalone" \
    --workpath "$WORK/pyinstaller-work" \
    --specpath "$WORK/pyinstaller-spec" \
    "$ROOT/scripts/ice_entry.py"

bash "$ROOT/scripts/smoke_linux.sh" "$VERSION" "$WORK/standalone/ice"

echo "=== Creating Ubuntu installer ==="
STAGE="$WORK/debian"
mkdir -p "$STAGE/DEBIAN" "$STAGE/opt/agent-iceman" "$STAGE/usr/bin" "$WORK/artifacts"
install -m 755 "$WORK/standalone/ice" "$STAGE/opt/agent-iceman/ice"
ln -s /opt/agent-iceman/ice "$STAGE/usr/bin/ice"
ln -s /opt/agent-iceman/ice "$STAGE/usr/bin/iceman"
INSTALLED_SIZE="$(du -sk "$STAGE" | cut -f1)"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: agent-iceman
Version: ${DEB_VERSION}
Section: utils
Priority: optional
Architecture: amd64
Maintainer: ${MAINTAINER}
Depends: libc6 (>= ${GLIBC}), zlib1g
Installed-Size: ${INSTALLED_SIZE}
Description: Agent Iceman - Cloud and DevOps CLI assistant
 A safety-first Cloud and DevOps assistant with
 trusted execution workflows and technical knowledge.
 Optional local AI support through Ollama.
EOF
chmod 644 "$STAGE/DEBIAN/control"

DEB_NAME="agent-iceman_${DEB_VERSION}_amd64.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$WORK/artifacts/$DEB_NAME"
cp "${WHEEL[0]}" "$WORK/artifacts/$WHEEL_NAME"
install -m 755 "$WORK/standalone/ice" "$WORK/artifacts/ice"
(
    cd "$WORK/artifacts"
    sha256sum "$DEB_NAME" "$WHEEL_NAME" ice > SHA256SUMS
    sha256sum --check --strict SHA256SUMS
)
# Copy only a complete, smoke-tested set. Existing outputs are never deleted.
cp "$WORK/artifacts/"* "$OUTPUT/"
echo "=== Agent Iceman build completed ==="
ls -lh "$OUTPUT"
