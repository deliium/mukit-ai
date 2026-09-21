# Implementation Plan: Symbolic Music Transformer (PyTorch)

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-21

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: full, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: first custom decoder-only Music Transformer — model library, offline train/sample CLI, inference adapter, thin optional API façade — consuming `tokenizer.v1`; **not** replacing LangGraph LLM generate as default; **not** loading GGUF in FastAPI

## Roadmap Linkage
Milestone: "Symbolic music PyTorch Music Transformer"
Rationale: Next milestone after the completed tokenizer; delivers a train→checkpoint→generate path that emits validated Composition V2 via the shared token contract. Add as a new unchecked milestone in `.ai-factory/ROADMAP.md` during docs/implement (roadmap owner: `/aif-roadmap` or docs checkpoint).

## Goal

Design and implement the **first custom PyTorch symbolic Music Transformer** for AI Composer: a practical decoder-only Transformer that predicts `tokenizer.v1` token ids, supports conditioning and constrained decoding, trains on dataset examples (tiny fixtures for CI), saves/loads versioned checkpoints, and generates short playable `composition.v2` documents after tokenizer decode + Composition validation. Keep **model library**, **inference adapter**, and **API integration** as separate layers — never couple `nn.Module` code to FastAPI routers. Support CPU for tests and ROCm GPU for real train/infer. Quality of music is out of scope; validity and reproducibility are in scope.

## Audit Summary (current state)

### Upstream contracts this model must bind to

| Contract | Location | Model implication |
|----------|----------|-------------------|
| `tokenizer.v1` encode/decode | `backend/app/tokenizer/` | Sole token↔V2 path; same vocab train + inference |
| `TokenizerModelExpectationV1` | `tokenizer/schemas.py` + `versioning.expected_tokenizer_record` | Checkpoint metadata **must** embed `expected_tokenizer_version` + `vocab_hash` (+ config_digest/profile) |
| Vocab / special ids | `vocab.build_vocab`, `special_tokens` (`PAD=0`, BOS, EOS, …) | Embedding size = vocab size; ignore_index = PAD; stop at EOS |
| Conditioning | `TokenizerConditioningV1` + COND_* prefix tokens | Prompt prefix from key/genre/mood/instset/section; unknown → COND_*_UNK |
| Repair / validate | `validate_token_sequence` / `repair_token_sequence` / `decode_tokens` | Generation pipeline ends in repair→decode→`CompositionV2` validate |
| Dataset examples | `dataset.example.v1` under `DATASET_ROOT` | Train dataloader reads examples/splits; encode via `encode_composition` |
| Dataset version | `dataset.manifest.v1` `dataset_version_id` | Record in checkpoint card |
| Playable notes | `tracks[].events[]` only | Never invent notes from harmony/analysis |
| Local AI / weights policy | `docs/local-ai.md`, ARCHITECTURE | FastAPI never loads GGUF; this package **may** load its own `.pt` checkpoints **only** inside the music_transformer / inference adapter layer (CLI or optional opt-in service), not via llama.cpp sidecars |

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Tokenizer CLI/library | encode/decode/repair/vocab/manifest/expectation | Training collation + generation detokenization |
| Dataset pipeline | `DATASET_ROOT` examples + splits + manifest | Offline train data source |
| Compose `training` profile | `local-ai-training-stub` no-op busybox | Upgrade docs + optional image notes; keep default compose free of torch |
| Fixtures | `backend/tests/fixtures/dataset/`, V2 samples | Tiny train corpus + acceptance generate |
| Logging / secrets rules | `LOG_LEVEL`, RULES.md | Structured extras; no full token dumps at INFO |
| `ai_runtime` | HTTP chat adapters only | Optional thin future registration; **do not** put `nn.Module` in `ai_runtime/runtimes/` in v1 |

### Gaps (must build)

| Gap | Notes |
|-----|--------|
| No PyTorch dependency path | `requirements.txt` has no torch; need optional extras file |
| No Music Transformer package | Config, embeddings, blocks, LM head, causal mask, loss |
| No train / sample CLI | Offline only under `python -m app.music_transformer.cli` |
| No checkpoint card | architecture + tokenizer expectation + dataset version + train config + git/project version |
| No sampling (temp/top-k/top-p/greedy) | Needed for generate + deterministic tests |
| No constrained decoding hooks | Reduce invalid symbolic sequences before decode |
| No inference adapter | Checkpoint → tokens → repair/decode → validate Composition V2 |
| No API façade separation | Thin service/router opt-in; model stays decoupled |
| No tiny-model test fixtures | CPU CI acceptance: train → save → load → generate valid short V2 |
| Docs | Need `docs/music-transformer.md` + handoffs in tokenizer/datasets/local-ai |

### Coupling risks to avoid

1. Importing FastAPI / routers from `music_transformer` model or train code.
2. Writing checkpoints or corpora into `PROJECT_DB_PATH` / revision history.
3. Bypassing tokenizer decode (emitting ad-hoc V2 JSON from logits).
4. Loading torch weights inside `ai_runtime` LocalLanguageModel path or default `docker compose up`.
5. Making default backend image/CI hard-require multi-GB ROCm torch when CPU tiny tests suffice with optional install.
6. Growing unrelated logic in `main.py`; API façade is a thin router → service → adapter only.
7. Logging full logits, full token-id sequences at INFO, API keys, or raw MIDI/MusicXML.
8. Dual token schemes or a second “model tokenizer”.

## Scope And Decisions

### In scope
- Package `backend/app/music_transformer/` (sibling of `dataset/` / `tokenizer/`).
- External architecture config: layers, hidden size, heads, dropout, context length, vocab size (from tokenizer).
- Decoder-only Transformer LM: token + (optional) position embeddings, stacked blocks, causal attention, LM head, cross-entropy loss (ignore PAD).
- Conditioning via tokenizer COND_* / structure prefix already in sequences (no parallel modality tower in v1).
- Sampling: temperature, top-k, top-p, greedy/deterministic.
- Constrained decoding **hooks** (pluggable mask/filter API + at least one practical constraint set: e.g. ban PAD after BOS except padding region; force structural legality helpers; optional vocab-family FSM stub).
- Offline CLI: `train`, `sample`/`generate`, `checkpoint inspect`, optionally `export-card`.
- Checkpoint save/load + `music_transformer.checkpoint.v1` / card metadata.
- Inference adapter: load checkpoint → generate ids → validate/repair → decode → CompositionV2 (+ integrity validate).
- Thin API integration layer (optional env-gated router + service) that calls the adapter only — **no** `torch` imports in the router module.
- CPU execution for tests; device selection `cpu` | `cuda` | `mps` | `hip`/`rocm` via settings (torch device string).
- Tiny fixtures + unit/acceptance tests + reproducibility (seed) + verbose logging + docs.
- Optional deps: `backend/requirements-music-transformer.txt` (torch); pytest mark `music_transformer` / skip if torch missing **or** install in CI job that opts in — prefer install in backend test path when practical, else clear skip with docs.

### Out of scope
- Research architectures (relative attention music papers, diffusion, RWKV, etc.) beyond a standard causal Transformer.
- High-quality musicality / human eval / large corpus training in CI.
- Replacing default LangGraph / remote LLM generate workflow.
- HuggingFace Hub upload, GGUF conversion, llama.cpp serving of this model.
- Frontend SPA training UI.
- Changing Composition V2 schema or tokenizer vocab families (consume as-is; if tokenizer bump needed, stop and escalate).
- NPU / Ryzen AI EP.
- Full production ROCm Docker training image (document + stub upgrade; may ship a thin Dockerfile.music-transformer later if needed — not required for acceptance if host/venv train works).

### Architecture decisions (locked)

**1. Subsystem boundary (mandatory)**

```text
dataset.example.v1 / Composition V2
        ↓
app.tokenizer encode/decode/repair   ← shared train+inference contract
        ↓
app.music_transformer (library)      ← nn.Module, train loop, sample, checkpoint
        ↓
inference adapter (package module)   ← generate → repair → decode → validate V2
        ↓
optional API façade (service/router) ← HTTP DTO only; imports adapter, not nn
        ✗ never → project_store / PROJECT_DB_PATH as training store
        ✗ never → ai_runtime weight load for GGUF / LocalLanguageModel path
```

**2. Practical decoder-only Transformer (GPT-style)**  
Pre-norm or post-norm Transformer decoder stack (lock **pre-norm** for training stability at tiny scale): token embedding, learned absolute positional embedding (sinusoidal optional via config flag; default **learned**), `n_layers` blocks with multi-head causal self-attention + MLP, final LayerNorm, linear LM head (weight-tied to token embedding by default). Causal mask via `torch.nn.functional.scaled_dot_product_attention` is_causal or explicit tril mask. This matches “practical, not novel.”

**3. External config (`music_transformer.config.v1`)**  
Pydantic strict model + YAML/JSON load. Required fields: `n_layers`, `d_model`, `n_heads`, `d_ff` (or `ff_mult`), `dropout`, `max_seq_len` (context), `vocab_size`, `tie_embeddings`, `norm` (`pre`), `pos_encoding` (`learned`|`sinusoidal`). Reject `d_model % n_heads != 0`. Context length must be ≤ training truncation length used in collate.

**4. Conditioning strategy**  
Do **not** add a separate encoder. Rely on tokenizer prefix tokens already emitted by `encode_composition(..., conditioning=...)`. Training sequences include COND_* when labels/args present. Inference adapter accepts the same `TokenizerConditioningV1` and builds a prompt prefix (BOS + COND_* + optional structure) before autoregressive continuation. Musical prefix = token ids from encoding an existing short V2 / partial sequence (teacher-forced continuation).

**5. Loss**  
Standard next-token cross-entropy: shift labels by 1; `ignore_index = PAD id (0)`. Log mean loss + token accuracy (non-PAD) at INFO periodically; never log full batch token ids at INFO.

**6. Sampling**  
Shared `sample_logits(logits, *, temperature, top_k, top_p, greedy)` utility. Greedy = argmax (temperature ignored / forced 0). Temperature ≤ 0 or greedy flag → deterministic. Top-k / top-p applied on last-step logits only; document interaction order: temperature → top-k → top-p → multinomial (standard).

**7. Constrained decoding hooks**  
Interface: `ConstraintFn` / `DecodingConstraint` protocol: `allowed_mask(prefix_ids, vocab) -> BoolTensor[vocab]` or `filter_logits(prefix_ids, logits) -> logits`. Built-ins for v1:
- `EosAfterMaxNewTokens` / max length stop
- `BanPadInContent` (PAD not sampleable during free generation)
- `RequireBosPrefix` (generation starts from BOS or provided prefix containing BOS)
- Optional lightweight `FamilyTransitionHook` stub that can ban illegal next families when enabled (e.g. VEL without preceding PITCH) — may be heuristic; must not invent pitches
Wire constraints in the generate loop **before** sampling. Keep hooks extensible without changing the core model class.

**8. Checkpoint metadata (`music_transformer.checkpoint.v1`)**  
Sidecar JSON (and/or embedded dict in `.pt` under key `card`) must include:
- `schema_version`
- `architecture` (full config dump + digest)
- `tokenizer` / `TokenizerModelExpectationV1` fields (`expected_tokenizer_version`, `vocab_hash`, `config_digest`, `profile`)
- `dataset_version_id` (and dataset name if known)
- `training` (seed, steps/epochs, batch size, lr, max_seq_len, device string, git commit / project version if available)
- `created_at` (wall-clock OK in card; exclude from architecture digest)
Load path verifies tokenizer expectation via `tokenizer.versioning.verify_expectation` when `require_version=True` (default for generate).

**9. Device policy**  
`MUSIC_TRANSFORMER_DEVICE` default `cpu`. Map `rocm`/`hip` → CUDA-compatible torch HIP device as documented for the installed wheel. Tests force `cpu`. Fail soft with clear error if requested GPU unavailable (fallback only when `MUSIC_TRANSFORMER_DEVICE_FALLBACK=cpu` explicitly set — default: no silent fallback for `rocm`).

**10. Dependency isolation**  
- Core app (`requirements.txt`) stays free of torch if packaging friction on Python 3.14 requires it; ship `requirements-music-transformer.txt` and document install.
- Package imports `torch` lazily inside model/train/sample modules so importing `app.music_transformer.schemas` does not require torch.
- Router/API façade must not import torch.
- Default Docker backend image: unchanged. Training profile docs point to optional torch+ROCm image later; stub may echo install hint.

**11. API integration (thin, opt-in)**  
- Service: `services/music_transformer_generate.py` (or package `inference_api.py` called by service) wrapping adapter → CompositionV2 DTO.
- Router: `routers/music_transformer.py` behind env `MUSIC_TRANSFORMER_API_ENABLED=0` default; POST generate with conditioning + sampling knobs; 503 if checkpoint missing/torch missing.
- Do **not** register as default `AiOperation.GENERATE` replacement in this milestone.
- Optional discovery note in docs only; full `ai_runtime` capability for symbolic LM is a **follow-up**.

**12. Reproducibility**  
`seed_everything(seed)` sets Python/`random`/`numpy`/torch (+ cuda deterministic flags best-effort). Record seed in checkpoint card. Greedy generate tests must be deterministic on CPU.

**13. Tiny fixtures**  
Under `backend/tests/fixtures/music_transformer/`: micro config (e.g. 2 layers, d_model=64, 2 heads, max_seq_len=128), 1–N tiny encoded sequences or V2 JSON windows, optional pre-init random checkpoint for load tests. Acceptance: train ≥1 step on fixture → save → load → greedy generate short continuation → decode → `CompositionV2` validates.

## Acceptance criteria mapping

| Criterion | Tasks |
|----------|-------|
| PyTorch decoder-only Transformer | 2–4 |
| Consume versioned tokenizer | 1, 5, 8, 11 |
| Conditioning (key/instruments/genre/section/prefix) | 5, 8 |
| Config external; embeddings; pos; blocks; LM head; causal mask; loss | 2–4 |
| Sampling temp/top-k/top-p/greedy | 6 |
| Constrained decoding hooks | 7 |
| Decode + Composition validation before return | 8, 11 |
| Separate library / adapter / API | 1, 8, 9 |
| CPU tests + ROCm-capable device knob | 3, 10, 12 |
| Tiny fixtures | 10, 12 |
| Checkpoint save/load + metadata | 3, 11 |
| Train on test data → save → load → valid short V2 | 12, 13 |
| Unit tests, reproducibility, logging, docs | 10–14 |

## Commit Plan
- **Commit 1** (tasks 1–3): `feat(music-transformer): add package skeleton, config, and checkpoint card`
- **Commit 2** (tasks 4–7): `feat(music-transformer): transformer LM, loss, sampling, and decode constraints`
- **Commit 3** (tasks 8–10): `feat(music-transformer): train/generate CLI, inference adapter, and tiny fixtures`
- **Commit 4** (tasks 11–14): `feat(music-transformer): optional API façade, acceptance tests, and docs`

## Tasks

### Phase 1: Package boundary, config, checkpoint card

- [x] Task 1: Create `music_transformer` package skeleton + isolation rules
  Deliverable: `backend/app/music_transformer/` with `__init__.py`, `settings.py`, `errors.py`, `__main__.py` documenting hard rules: offline library/CLI; may load `.pt` checkpoints inside this package/adapter only; never `PROJECT_DB_PATH`; never invent notes from harmony; no FastAPI imports in model/train modules; consume `tokenizer.v1` only. Env knobs `MUSIC_TRANSFORMER_*` (device, seed, checkpoint dir, api enabled). Confirm default Compose unchanged.
  LOGGING: INFO on settings load (device, seed, version string); WARN on missing optional config; never log absolute home paths with usernames if avoidable (basename OK).
  Files: `backend/app/music_transformer/{__init__,settings,errors,__main__}.py`, `.env.example` (commented keys).

- [x] Task 2: Lock architecture + train/generate schemas
  Deliverable: Strict Pydantic models:
  - `music_transformer.config.v1` (layers, d_model, heads, dropout, max_seq_len, vocab_size, tie_embeddings, pos_encoding, …)
  - `music_transformer.train_config.v1` (lr, batch_size, steps/epochs, seed, dataset path refs, max_seq_len truncate)
  - `music_transformer.sample_config.v1` (temperature, top_k, top_p, greedy, max_new_tokens, constraints enabled flags)
  - `music_transformer.checkpoint.v1` card (architecture, tokenizer expectation, dataset_version_id, training, git/project version)
  Closed error/issue Literals. Module docstring states GPT-style pre-norm decoder-only choice.
  LOGGING: DEBUG validation field names; INFO config digest prefix when computed.
  Files: `backend/app/music_transformer/schemas.py`.

- [x] Task 3: Checkpoint save/load + metadata card + optional torch extras file
  Deliverable: `save_checkpoint(path, model, optimizer?, card)` / `load_checkpoint(path, map_location)` writing `.pt` + sidecar `.card.json` (or embedded card). Include `TokenizerModelExpectationV1` via `expected_tokenizer_record`. Capture `dataset_version_id` when training from a dataset dir. Capture git SHA / project version best-effort (`git rev-parse` or env `MUKIT_VERSION`) without failing offline. Add `backend/requirements-music-transformer.txt` with torch pin guidance for CPU and note ROCm wheel URL in comments/docs. Lazy torch import.
  LOGGING: INFO save/load with basename, architecture digest prefix, tokenizer_version, vocab_hash prefix, dataset_version_id prefix; ERROR on verify mismatch.
  Files: `backend/app/music_transformer/checkpoint.py`, `requirements-music-transformer.txt`, maybe `version_info.py`.

### Phase 2: Model, loss, sampling, constraints

- [x] Task 4: Implement decoder-only Transformer LM modules
  Deliverable: `MusicTransformerLM` (or equivalent) with embedding, positional representation, `n_layers` pre-norm blocks (MHA + MLP + dropout), final norm, output projection (tied weights optional). Forward(`input_ids`, `attention_mask?`) → logits `[B,T,V]`. Causal masking enforced. Build from `music_transformer.config.v1`. Tiny default factory for tests.
  LOGGING: INFO model param count + config summary on init; DEBUG shape on forward when verbose; never dump weights.
  Files: `backend/app/music_transformer/model.py`, `blocks.py` (optional split).

- [x] Task 5: Data collation from tokenizer + conditioning/prefix support
  Deliverable: Dataset utilities that load `dataset.example.v1` / V2 JSON / tokenized JSONL, call `encode_composition` with conditioning from labels/args, truncate/pad to `max_seq_len` with PAD=0, produce `input_ids` + `labels` for LM. Support “musical prefix” mode: encode existing composition then train/generate continuation after prefix length. Do not mutate source files.
  LOGGING: INFO sequence count, avg length, truncate count; DEBUG conditioning UNK counts; no full id dumps at INFO.
  Files: `backend/app/music_transformer/data.py`, maybe `collate.py`.

- [x] Task 6: Loss + sampling strategies
  Deliverable: `language_modeling_loss(logits, labels)` with ignore_index=PAD. `sample_next_token` / `generate_ids` supporting temperature, top-k, top-p, greedy. Stop on EOS or max_new_tokens. Seed-respecting on CPU greedy path.
  LOGGING: DEBUG sampling knobs; INFO generate length + stop reason (`eos`|`max_new_tokens`); WARN on empty/invalid knobs clamped.
  Files: `backend/app/music_transformer/loss.py`, `sampling.py`, `generate.py`.

- [x] Task 7: Constrained decoding hooks
  Deliverable: Protocol + registry for constraints; integrate into generate loop. Ship BanPadInContent, max length, BOS prefix guard, and at least one symbolic family heuristic hook (document limitations). Constraints must not invent pitch tokens to “fix” bars.
  LOGGING: DEBUG which constraints active; INFO count of masked logits steps; never log full masks.
  Files: `backend/app/music_transformer/constraints.py`.

### Phase 3: Train CLI, inference adapter, fixtures

- [x] Task 8: Inference adapter (library) — generate valid Composition V2
  Deliverable: `generate_composition(checkpoint, *, conditioning, prefix_composition?, sample_config, require_tokenizer_version=True) -> (CompositionV2, GenerateReport)`. Pipeline: load+verify card → build prompt tokens → constrained generate → `validate`/`repair` → `decode_tokens` → `CompositionV2` validation (`model_validate` + `validate_composition_integrity` or project equivalent) → return only if accepted; else typed error with issue codes. No FastAPI types.
  LOGGING: INFO status, note/bar counts, tokenizer_version, vocab_hash prefix, repair result; ERROR on reject; DEBUG bounded token prefix (≤64).
  Files: `backend/app/music_transformer/inference.py`, report fields in schemas.

- [x] Task 9: Offline CLI — train / generate / inspect
  Deliverable: `python -m app.music_transformer.cli` with:
  - `train --config … --dataset-dir|… --out checkpoint.pt`
  - `generate --checkpoint … --out composition.json` (conditioning flags, sampling flags)
  - `inspect --checkpoint …` (print card summary)
  Mirror dataset/tokenizer CLI style. Optional thin `scripts/music_transformer_smoke.sh`.
  LOGGING: INFO command start/end + elapsed_ms + basenames; train step loss periodically; never log full batches.
  Files: `backend/app/music_transformer/cli.py`, `train.py`, optional script.

- [x] Task 10: Tiny-model fixtures + reproducibility helpers
  Deliverable: Fixtures under `backend/tests/fixtures/music_transformer/` (micro YAML config, tiny V2/examples, expected card schema sample). `seed_everything` helper. Document how to run CPU smoke train.
  LOGGING: N/A for static fixtures; seed helper logs INFO seed once.
  Files: fixtures + `backend/app/music_transformer/reproducibility.py`.

### Phase 4: API façade, tests, docs

- [x] Task 11: Thin API integration (opt-in) without coupling torch to routers
  Deliverable: Service wrapper calling inference adapter; router registered only when `MUSIC_TRANSFORMER_API_ENABLED=1`; request/response DTOs in `music_transformer_schemas.py` or package schemas re-export; map domain errors to HTTP. Router module must not import torch/model classes. Default off; `/ready` may expose soft `music_transformer` block (optional, non-blocking).
  LOGGING: INFO enabled/disabled at startup; WARN when enabled but checkpoint missing; no weight paths with usernames.
  Files: `backend/app/services/music_transformer_generate.py`, `backend/app/routers/music_transformer.py`, wire in `main.py` minimally, `.env.example`.

- [x] Task 12: Unit tests (CPU) — model, sampling, checkpoint, constraints
  Deliverable: Pytest modules covering config validation, forward shapes, causal property smoke, loss ignore PAD, greedy determinism, top-k/p shapes, checkpoint round-trip metadata, constraint masks, skip/xfail policy if torch not installed (prefer documenting required extra for full gate).
  LOGGING: Tests may set LOG_LEVEL=DEBUG; assert no secrets in logged extras where relevant.
  Files: `backend/tests/test_music_transformer_*.py`.

- [x] Task 13: Acceptance test — train → save → load → valid short Composition V2
  Deliverable: End-to-end test on tiny fixture: short train loop (≥1 optimizer step) → save checkpoint with full card → load → greedy generate → adapter decode/validate → assert `CompositionV2` + integrity OK and at least one note event **or** explicitly allow empty-with-repair only if fixture guarantees notes (prefer assert `notes_out >= 1` by priming prefix with a one-bar seed composition). Reproducibility: fixed seed.
  LOGGING: INFO acceptance markers via logger in adapter (already); test prints minimal assertion context.
  Files: `backend/tests/test_music_transformer_acceptance.py`.

- [x] Task 14: Documentation + roadmap/docs handoffs
  Deliverable: `docs/music-transformer.md` (architecture, config, train/generate CLI, checkpoint card, conditioning, sampling, constraints, CPU vs ROCm, optional API, isolation rules). Update `docs/tokenizer.md` (model binding), `docs/datasets.md` (training consumes examples), `docs/local-ai.md` (training profile vs this package), `AGENTS.md` / ARCHITECTURE folder map, `.ai-factory/DESCRIPTION.md` one-liner, ROADMAP new milestone checkbox (unchecked until verify). Mentions logging/secrets policy.
  LOGGING: N/A (docs).
  Files: `docs/music-transformer.md`, cross-links, `AGENTS.md`, `ROADMAP.md`, `DESCRIPTION.md`, `ARCHITECTURE.md` as needed.

## Implementation Notes For `/aif-implement`

1. Prefer extending patterns from `app.tokenizer` and `app.dataset` (schemas, CLI, settings, structured logging) over inventing a new style.
2. If PyTorch wheels for the project’s Python 3.14 are unavailable, document the supported Python minor for the extra requirements file and gate tests with a clear skip message — do not weaken Composition validation.
3. Do not change tokenizer vocab or quantization rules in this plan; bind via expectation record only.
4. Keep default `docker compose up` free of torch/ROCm pulls.
5. Commit checkpoints and large tensors are gitignored; fixtures stay tiny.
