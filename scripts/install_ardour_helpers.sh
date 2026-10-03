#!/usr/bin/env bash
# Copy operator-installed Ardour Lua helpers into a user-supplied scripts directory.
# Never starts Ardour, never writes .ardour session XML, never remote-executes Lua.
#
# Usage:
#   ./scripts/install_ardour_helpers.sh /path/to/ardour/scripts
#   ARDOUR_SCRIPTS_DIR=/path/to/ardour/scripts ./scripts/install_ardour_helpers.sh
#   ./scripts/install_ardour_helpers.sh --help

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${ROOT}/backend/examples/ardour"
DEST="${1:-${ARDOUR_SCRIPTS_DIR:-}}"

usage() {
  cat <<'EOF'
install_ardour_helpers.sh — copy Mukit Ardour Lua recipes (operator install only)

Usage:
  ./scripts/install_ardour_helpers.sh <ardour-scripts-directory>
  ARDOUR_SCRIPTS_DIR=<dir> ./scripts/install_ardour_helpers.sh

Refuses destinations that look like a .ardour session file path.
Does not start Ardour and does not edit session XML.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -z "${DEST}" ]]; then
  echo "ERROR: destination directory required (arg or ARDOUR_SCRIPTS_DIR)." >&2
  usage >&2
  exit 2
fi

# Refuse if DEST itself is a .ardour session file or ends with .ardour.
if [[ -f "${DEST}" && "${DEST}" == *.ardour ]]; then
  echo "ERROR: destination looks like an Ardour session file: ${DEST}" >&2
  exit 2
fi
if [[ "${DEST}" == *.ardour ]]; then
  echo "ERROR: refusing destination path ending in .ardour: ${DEST}" >&2
  exit 2
fi
case "${DEST}" in
  *.ardour/*|*/.ardour|*/.ardour/*)
    echo "ERROR: destination looks inside a .ardour session path: ${DEST}" >&2
    exit 2
    ;;
esac

if [[ ! -d "${SRC}" ]]; then
  echo "ERROR: source recipes missing: ${SRC}" >&2
  exit 1
fi

mkdir -p "${DEST}"
copied=0
for lua in "${SRC}"/*.lua; do
  [[ -f "${lua}" ]] || continue
  base="$(basename "${lua}")"
  cp -f "${lua}" "${DEST}/${base}"
  copied=$((copied + 1))
  echo "copied ${base}"
done

if [[ "${copied}" -eq 0 ]]; then
  echo "ERROR: no .lua files found under ${SRC}" >&2
  exit 1
fi

cat <<EOF

Install checklist
-----------------
1. Lua recipes copied to: ${DEST} (${copied} files)
2. In Ardour: Window → Scripting / Script Manager → load scripts from that directory
   (optional: register as Editor Action if your Ardour build supports it)
3. Pair Compose bind-mount with the same host path as ARDOUR_EXCHANGE_ROOT
4. Enable flags:
     ARDOUR_COMPANION_ENABLED=1
     ARDOUR_EXCHANGE_ENABLED=1
     ARDOUR_EXCHANGE_ROOT=<same host directory as Lua packages>
5. Connected workflow:
     export selected MIDI region (Lua) → Studio Ardour tab Send → Realize → Apply
     → Prepare outbound → import package (Lua) at start_samples
6. Mukit never edits .ardour XML and never remote-executes Lua.

See: backend/examples/ardour/README.md
     docs/ardour-companion.md
     docs/ardour-session-exchange.md
EOF
