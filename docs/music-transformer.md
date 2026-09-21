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
| `music_transformer.train_config.v1` | lr, batch_size, steps, seed, max_seq_len |
| `music_transformer.sample_config.v1` | temperature, top_k, top_p, greedy, max_new_tokens, constraint flags |
| `music_transformer.checkpoint.v1` | architecture + tokenizer expectation + dataset_version_id + training card |

Tiny fixture config: `backend/tests/fixtures/music_transformer/tiny_arch.json`.

## CLI

```bash
python -m app.music_transformer.cli train \
  --arch-config tests/fixtures/music_transformer/tiny_arch.json \
  --inputs tests/fixtures/music_transformer/seed_one_bar.json \
  --out /tmp/mt.pt --steps 3 --device cpu

python -m app.music_transformer.cli generate \
  --checkpoint /tmp/mt.pt \
  --prefix tests/fixtures/music_transformer/seed_one_bar.json \
  --out /tmp/out.json --greedy --max-new-tokens 48 --seed 7

python -m app.music_transformer.cli inspect --checkpoint /tmp/mt.pt
python -m app.music_transformer.cli export-card --checkpoint /tmp/mt.pt --out /tmp/card.json
```

Thin wrapper: `scripts/music_transformer_smoke.sh`.

## Checkpoint card

Each `.pt` embeds a card (plus `.pt.card.json` sidecar) with:

- Architecture dump + digest
- `TokenizerModelExpectationV1` (`expected_tokenizer_version`, `vocab_hash`, …)
- Optional `dataset_version_id`
- Training seed/steps/lr/device + best-effort git SHA / `MUKIT_VERSION`
- `created_at` (wall-clock; excluded from architecture digest)

Generate verifies the tokenizer expectation by default (`require_tokenizer_version=True`).

## Optional HTTP API

Disabled by default (`MUSIC_TRANSFORMER_API_ENABLED=0`).

When enabled:

- `POST /music-transformer/generate` — DTO only; router does **not** import torch
- Service calls the inference adapter
- `GET /ready` exposes soft `music_transformer` block (non-blocking)

This does **not** replace default LangGraph / remote LLM generate.

## Env knobs

| Variable | Default | Meaning |
|----------|---------|---------|
| `MUSIC_TRANSFORMER_DEVICE` | `cpu` | `cpu` / `cuda` / `mps` / `rocm` |
| `MUSIC_TRANSFORMER_DEVICE_FALLBACK` | empty | Set to `cpu` to allow fallback |
| `MUSIC_TRANSFORMER_SEED` | `42` | Default seed |
| `MUSIC_TRANSFORMER_CHECKPOINT_DIR` | `checkpoints/music_transformer` | Default dir basename |
| `MUSIC_TRANSFORMER_CHECKPOINT` | unset | Default checkpoint for API |
| `MUSIC_TRANSFORMER_API_ENABLED` | `0` | Opt-in router |

## Logging / secrets

Structured `extra={...}` via `LOG_LEVEL`. Log: package version, device, digests/hash prefixes, step loss, note/token counts, issue codes, basenames, elapsed_ms.

**Never** log: API keys, full prompts, raw MIDI/MusicXML/WAV, full event arrays, full token-id dumps or logits at INFO (DEBUG may log ≤64 token ids).

## Tests

```bash
cd backend
python -m pytest tests/test_music_transformer_core.py tests/test_music_transformer_acceptance.py -q
```

Tests `pytest.importorskip("torch")` when the optional extra is missing.

Acceptance: train ≥1 step on fixture → save → load → greedy generate with one-bar prefix → `CompositionV2` + integrity OK with `notes_out >= 1`.

## See also

- [Symbolic tokenizer](tokenizer.md) — train/inference token contract
- [Datasets](datasets.md) — offline `DATASET_ROOT` examples
- [Local AI](local-ai.md) — llama.cpp/vLLM sidecars (separate from this package)
