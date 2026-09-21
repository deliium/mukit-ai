#!/usr/bin/env bash
# Thin wrapper: encode→decode round-trip for a Composition V2 JSON file.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INPUT="${1:?usage: tokenizer_roundtrip.sh <composition.v2.json> [out.json]}"
OUT="${2:-}"
cd "$ROOT/backend"
if [[ -n "$OUT" ]]; then
  python -m app.tokenizer.cli roundtrip --input "$INPUT" --out "$OUT"
else
  python -m app.tokenizer.cli roundtrip --input "$INPUT"
fi
