# Symbolic Music Transformer

Practical GPT-style **pre-norm decoder-only** Transformer that predicts `tokenizer.v1` token ids, trains offline on dataset examples / Composition V2 JSON, and generates short playable `composition.v2` documents via the shared tokenizer decode path.

Music quality is out of scope; **validity and reproducibility** are in scope.

## Isolation rules

| Allowed | Forbidden |
|---------|-----------|
| `python -m app.music_transformer.cli` | Writing corpora/checkpoints to `PROJECT_DB_PATH` |
| Load `.pt` inside this package / inference adapter | Inventing notes from harmony / analysis |
| Optional env-gated HTTP façade | FastAPI imports in model/train modules |
| Optional torch extra | Default `docker compose up` pulling ROCm torch |
| Same tokenizer vocab train + inference | Dual token schemes / GGUF in FastAPI |

## Architecture

```text
dataset.example.v1 / Composition V2
        ↓
app.tokenizer encode/decode/repair
        ↓
app.music_transformer (LM, train, sample, checkpoint)
        ↓
inference adapter → repair → decode → CompositionV2 validate
        ↓
optional API (service/router; no torch in router)
        ↓
hybrid LangGraph generate (`options.pipeline=hybrid_plan_symbolic`) via
`app.services.symbolic_composition_generate` (see docs/hybrid-generation.md)
```

- Token + learned (or sinusoidal) positions, stacked pre-norm MHA+MLP, LM head (weight-tied by default).
- Conditioning = tokenizer `COND_*` / structure prefix (no separate encoder).
- Loss: next-token CE with `ignore_index=PAD` (0).
- Sampling order: temperature → top-k → top-p → multinomial; greedy/temp≤0 → argmax.
- Constraints (hooks): ban PAD, BOS prefix guard, heuristic family transition (no inventing pitches).

## Install (optional)

Core `requirements.txt` does **not** include torch.

```bash
pip install -r backend/requirements-music-transformer.txt
# or CPU wheel:
# pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu
```

ROCm: install the matching PyTorch ROCm wheel, then set `MUSIC_TRANSFORMER_DEVICE=cuda` (HIP builds use the CUDA device API) or `rocm` (mapped inside the package). No silent GPU→CPU fallback unless `MUSIC_TRANSFORMER_DEVICE_FALLBACK=cpu`.

## Config

Pydantic schemas:

| Schema | Role |
|--------|------|
| `music_transformer.config.v1` | layers, d_model, heads, dropout, max_seq_len, vocab_size, tie_embeddings, pos_encoding |
| `music_transformer.train_config.v1` | lr, batch, steps, seed, optimizer, scheduler, precision, grad_accum, checkpoint/eval intervals, early_stopping |
| `music_transformer.experiment.v1` | experiment_id, architecture, train/eval/listening, seed, device |
| `music_transformer.eval_config.v1` / `listening_config.v1` | symbolic eval + fixed listening set |
| `music_transformer.sample_config.v1` | temperature, top_k, top_p, greedy, max_new_tokens, constraint flags |
| `music_transformer.checkpoint.v1` | architecture + tokenizer expectation + dataset_version_id + training card (`experiment_id`, `global_step`, precision, …) |

Tiny fixtures: `backend/tests/fixtures/music_transformer/`.

## Experiments (reproducible runs)

Filesystem layout under `MUSIC_TRANSFORMER_EXPERIMENT_ROOT` (default `experiments/music_transformer/`; **never** `PROJECT_DB_PATH`):

```text
experiments/<experiment_id>/
  config.json          # frozen experiment contract
  metadata.json        # digests, tokenizer expectation, git/project version
  metrics.jsonl        # train/val snapshots
  metrics_summary.json
  checkpoints/step_*.pt (+ .card.json) + latest.pt
  eval/                # symbolic metric reports
  listening/           # fixed-prompt Composition V2 JSON
  compare/             # optional compare CLI outputs
```

Training supports resume (model + optimizer + scheduler + scaler + step), gradient accumulation, optional AMP (`fp32` default; `amp_fp16`/`amp_bf16` only on CUDA/ROCm), gradient clipping, and optional early stopping on `val_loss`.

Validation data: prefer dataset `splits/validation`; for flat fixtures use `val_fraction` in train config or CLI `--val-inputs path.json …`. Empty val → skip val metrics with WARN (no fake val loss).

**Training never runs inside the FastAPI web process.** Use offline CLI or the Compose `training` profile (stub / host torch image).

## Symbolic evaluation (not musical quality)

Eval metrics are **validity / distributional diagnostics only**. Every report sets `musical_quality_claim: false`. They do **not** score musicality or aesthetics.

| Metric | Meaning (intent) |
|--------|------------------|
| `valid_token_rate` | Pre-repair tokenizer validate success rate |
| `valid_composition_decode_rate` | repair→decode→CompositionV2(+integrity) success rate |
| `pitch_class_distribution` | pitch % 12 histogram over `tracks[].events[]` |
| `note_density_distribution` | notes-per-bar summary |
| `rhythmic_distribution` | duration / onset-modulus summary |
| `repetition` | lightweight n-gram self-similarity |
| `interval_distribution` | consecutive pitch intervals |
| `instrument_range_violations` | catalog absolute-range violations (or `unavailable`) |
| `tonal_consistency` | simple diatonic PC-set overlap (limited) |

Listening set fixture: `listening_set.v1.json` (fixed prompts + seeds → deterministic greedy generations for human A/B). `compare` diffs configs/metrics/eval/listening digests — **no quality winner**.

## CLI

```bash
# Simple smoke (single checkpoint)
python -m app.music_transformer.cli train \
  --arch-config tests/fixtures/music_transformer/tiny_arch.json \
  --inputs tests/fixtures/music_transformer/seed_one_bar.json \
  --out /tmp/mt.pt --steps 3 --device cpu

# Experiment train / resume
python -m app.music_transformer.cli train --experiment-config exp.json --force
python -m app.music_transformer.cli train --experiment-config exp.json \
  --inputs train_a.json --val-inputs val_a.json --force
python -m app.music_transformer.cli train --resume experiments/.../checkpoints/latest.pt \
  --experiment-id my-run --extra-steps 10 --inputs train_a.json --val-inputs val_a.json

python -m app.music_transformer.cli generate \
  --checkpoint /tmp/mt.pt \
  --prefix tests/fixtures/music_transformer/seed_one_bar.json \
  --out /tmp/out.json --greedy --max-new-tokens 48 --seed 7

python -m app.music_transformer.cli eval --checkpoint … --out …/eval
python -m app.music_transformer.cli listen --checkpoint … \
  --listening-set tests/fixtures/music_transformer/listening_set.v1.json --out …/listening
python -m app.music_transformer.cli compare --a exp_a --b exp_b --out compare.json

python -m app.music_transformer.cli inspect --checkpoint /tmp/mt.pt
python -m app.music_transformer.cli export-card --checkpoint /tmp/mt.pt --out /tmp/card.json
```

Wrappers: `scripts/music_transformer_smoke.sh`, `scripts/music_transformer_train_smoke.sh`.

## Checkpoint card

Each `.pt` embeds a card (plus `.pt.card.json` sidecar) with:

- Architecture dump + digest
- `TokenizerModelExpectationV1` (`expected_tokenizer_version`, `vocab_hash`, …)
- Optional `dataset_version_id`
- Training: seed/steps/lr/device, `experiment_id`, `global_step`, optimizer/scheduler/precision/grad_accum, best-effort git SHA / `MUKIT_VERSION`
- `created_at` (wall-clock; excluded from architecture digest)

Generate verifies the tokenizer expectation by default (`require_tokenizer_version=True`). Resume fails closed on architecture/tokenizer mismatch.

## Optional HTTP API

Disabled by default (`MUSIC_TRANSFORMER_API_ENABLED=0`).

When enabled:

- `POST /music-transformer/generate` — DTO only; router does **not** import torch
- Service calls the inference adapter
- `GET /ready` exposes soft `music_transformer` block (non-blocking)

This does **not** replace default LangGraph / remote LLM generate. **No train HTTP routes.**

## Env knobs

| Variable | Default | Meaning |
|----------|---------|---------|
| `MUSIC_TRANSFORMER_DEVICE` | `cpu` | `cpu` / `cuda` / `mps` / `rocm` |
| `MUSIC_TRANSFORMER_DEVICE_FALLBACK` | empty | Set to `cpu` to allow fallback |
| `MUSIC_TRANSFORMER_SEED` | `42` | Default seed |
| `MUSIC_TRANSFORMER_CHECKPOINT_DIR` | `checkpoints/music_transformer` | Default dir basename |
| `MUSIC_TRANSFORMER_EXPERIMENT_ROOT` | `experiments/music_transformer` | Experiment run root |
| `MUSIC_TRANSFORMER_CHECKPOINT` | unset | Default checkpoint for API |
| `MUSIC_TRANSFORMER_API_ENABLED` | `0` | Opt-in generate router only |

## Logging / secrets

Structured `extra={...}` via `LOG_LEVEL`. Log: experiment_id, step, loss, val_loss, lr, tokens/sec, mem_mb, digests/hash prefixes, note/token counts, issue codes, basenames, elapsed_ms.

**Never** log: API keys, full prompts, raw MIDI/MusicXML/WAV, full event arrays, full token-id dumps or logits at INFO (DEBUG may log ≤64 token ids).

## Tests

```bash
cd backend
python -m pytest tests/test_music_transformer_core.py \
  tests/test_music_transformer_acceptance.py \
  tests/test_music_transformer_train_experiments.py -q
```

Tests `pytest.importorskip("torch")` when the optional extra is missing.

Acceptance: train ≥1 step on fixture → save → load → greedy generate with one-bar prefix → `CompositionV2` + integrity OK with `notes_out >= 1`. Experiment acceptance: two runs → `compare` + resume continues `global_step`.

## See also

- [Symbolic tokenizer](tokenizer.md) — train/inference token contract
- [Datasets](datasets.md) — offline `DATASET_ROOT` examples / splits
- [Local AI](local-ai.md) — llama.cpp/vLLM sidecars; Compose `training` profile points at this offline CLI
