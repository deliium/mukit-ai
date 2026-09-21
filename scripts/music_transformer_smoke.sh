#!/usr/bin/env bash
# Thin smoke: train tiny model then generate with seed prefix.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="${ROOT}/backend"
FIX="${BACKEND}/tests/fixtures/music_transformer"
OUT_DIR="${TMPDIR:-/tmp}/mukit_mt_smoke"
mkdir -p "${OUT_DIR}"
cd "${BACKEND}"
python -m app.music_transformer.cli train \
  --arch-config "${FIX}/tiny_arch.json" \
  --config "${FIX}/tiny_train.json" \
  --inputs "${FIX}/seed_one_bar.json" \
  --out "${OUT_DIR}/smoke.pt" \
  --steps 2 \
  --device cpu
python -m app.music_transformer.cli generate \
  --checkpoint "${OUT_DIR}/smoke.pt" \
  --prefix "${FIX}/seed_one_bar.json" \
  --out "${OUT_DIR}/out.json" \
  --greedy \
  --max-new-tokens 32 \
  --seed 7 \
  --device cpu
python -m app.music_transformer.cli inspect --checkpoint "${OUT_DIR}/smoke.pt"
echo "smoke ok: ${OUT_DIR}/out.json"
