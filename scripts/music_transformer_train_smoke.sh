#!/usr/bin/env bash
# Tiny CPU smoke for Music Transformer experiment train (optional torch extra).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
FIXTURES=tests/fixtures/music_transformer
OUT="${TMPDIR:-/tmp}/mt_smoke_$$"
mkdir -p "$OUT"
export MUSIC_TRANSFORMER_EXPERIMENT_ROOT="$OUT"
export MUSIC_TRANSFORMER_DEVICE=cpu
python - <<PY
import json
from pathlib import Path
from app.music_transformer.experiment_schemas import (
    MusicTransformerExperimentV1,
    MusicTransformerEvalConfigV1,
    MusicTransformerListeningConfigV1,
)
from app.music_transformer.schemas import MusicTransformerTrainConfigV1, tiny_test_config
from app.music_transformer.train import train_experiment
from app.tokenizer.schemas import default_tokenizer_config
from app.tokenizer.vocab import build_vocab

fixtures = Path("$FIXTURES")
vocab = build_vocab(default_tokenizer_config())
arch = tiny_test_config(vocab_size=vocab.size)
train = MusicTransformerTrainConfigV1.model_validate(
    json.loads((fixtures / "tiny_train.json").read_text())
)
train = train.model_copy(update={
    "steps": 4,
    "grad_accum_steps": 2,
    "checkpoint_interval": 2,
    "eval_interval": 0,
    "precision": "fp32",
})
exp = MusicTransformerExperimentV1(
    experiment_id="smoke-cpu-001",
    seed=7,
    device="cpu",
    architecture=arch,
    train=train,
    eval=MusicTransformerEvalConfigV1(enabled=False, max_samples=1),
    listening=MusicTransformerListeningConfigV1(
        enabled=True,
        set_path=str(fixtures / "listening_set.v1.json"),
        run_on_train_end=True,
    ),
)
paths = train_experiment(
    exp,
    force=True,
    inputs=[fixtures / "seed_one_bar.json"],
    run_listening_on_end=True,
)
print(json.dumps({"ok": True, "experiment_id": paths.root.name, "latest": paths.latest_checkpoint.name}))
PY
echo "smoke ok under $OUT"
