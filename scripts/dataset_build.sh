#!/usr/bin/env bash
# Thin wrapper around the offline dataset build CLI.
# Usage: scripts/dataset_build.sh --config path/to/pipeline.yaml [--out datasets]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}/backend"
exec python -m app.dataset.cli build "$@"
