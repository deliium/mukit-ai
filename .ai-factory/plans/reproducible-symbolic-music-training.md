# Implementation Plan: Reproducible Symbolic Music Training & Evaluation

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-21

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: full, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: turn experimental `music_transformer` training into a **repeatable experiment capability** — structured train config, seeding, resume, device/AMP/grad-accum/clip, metrics, symbolic eval (not musical quality), listening set, experiment IDs, checkpoint compare, optional early stopping, offline CLI/Docker training profile — **without** blocking the FastAPI web process

## Roadmap Linkage
Milestone: "Reproducible symbolic music training and evaluation"
Rationale: Music Transformer v1 delivered a minimal train→checkpoint→generate path; this milestone makes two runs comparable via reproducible configs, metrics, checkpoints, and fixed evaluation generations. Add as a new unchecked milestone in `.ai-factory/ROADMAP.md` during docs/implement (roadmap owner: `/aif-roadmap` or docs checkpoint). Prior milestone "Symbolic music PyTorch Music Transformer" is already complete.

## Goal

Upgrade `backend/app/music_transformer/` from a short ad-hoc train loop into a **filesystem-backed experiment runner**: versioned experiment configs, deterministic seeding, checkpoint resume, CPU/ROCm device selection, gradient accumulation, safe mixed precision, gradient clipping, recorded train/val metrics, deterministic symbolic-music evaluation metrics (explicitly *not* musical quality), fixed listening/evaluation generations, and CLI commands to compare two experiments/checkpoints. Jobs run only via offline CLI / Docker `training` profile — never inside the main web API process.

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Package `app/music_transformer/` | Model, loss, sampling, constraints, inference adapter, CLI train/generate/inspect | Extend in place; do not fork a second training package |
| `MusicTransformerTrainConfigV1` | `lr`, `batch_size`, `steps`/`epochs`, `seed`, `max_seq_len`, `grad_clip`, `log_every`, `dataset_dir`, `inputs_glob` | Expand fields; keep schema version bump disciplined |
| `seed_everything` | Python/`random`/`numpy`/torch (+ cudnn deterministic best-effort) | Keep; strengthen docs + optional CUDA deterministic flag |
| `resolve_device` | `cpu` / `cuda` / `mps` / `rocm`→`cuda`; optional fallback | Keep; train loop must pass through it |
| Checkpoint card `music_transformer.checkpoint.v1` | Architecture + tokenizer expectation + `dataset_version_id` + training seed/steps/lr/device + optimizer state optional | Extend training card + embed experiment_id; resume loads optimizer |
| Tokenizer expectation | `expected_tokenizer_record` / `verify_expectation` | Record tokenizer version + vocab_hash in experiment metadata |
| Dataset splits | `app/dataset/split.py` train/validation/test | Train loop should prefer split files when present under dataset version dir |
| Inference adapter | `generate_composition` → repair → decode → `CompositionV2` | Listening set + eval decode rate |
| Composition analysis helpers | Tonality, density, repetition, range warnings | Prefer **lightweight deterministic metrics** in `music_transformer/eval_*` that read `tracks[].events[]` only; optionally call shared pure helpers — **never** persist `composition.analysis.v1` as training data |
| Arrangement catalog ranges | `instrument_catalog` absolute/preferred ranges | Optional source for instrument-range violation counts (by program/name match); advisory metric only |
| Compose `training` profile | `local-ai-training-stub` busybox no-op | Upgrade docs + command/entrypoint to invoke CLI; keep default `docker compose up` free of torch |
| Tests | `test_music_transformer_core.py`, `test_music_transformer_acceptance.py` | Extend with train resume, metrics, eval, compare acceptance |
| Docs | `docs/music-transformer.md` | Expand training/eval/experiment sections; cross-link datasets/local-ai |

### Gaps (must build)

| Gap | Notes |
|-----|--------|
| Incomplete train config | Missing: dataset_version_id ref, tokenizer_version binding, optimizer name, scheduler, precision, checkpoint_interval, eval_interval, grad_accum, early_stopping, experiment_id, output root |
| No train/val split loading | `data.py` loads all `examples/**/*.json`; ignores dataset split manifests |
| No checkpoint resume | Optimizer state can be saved but train always starts from step 0 / fresh model |
| No gradient accumulation | One backward → one optimizer step |
| No mixed precision | Full fp32 only; need AMP where CUDA/ROCm safely supports it; CPU stays fp32 |
| Metrics incomplete | Only step loss + token_accuracy logged; no val loss, lr, tokens/sec, memory |
| No symbolic eval suite | No valid-token / decode-rate / pitch-class / density / rhythm / repetition / interval / range / tonal metrics |
| No listening set | No fixed prompts/seeds → deterministic sample compositions for human compare |
| No experiment IDs / run dirs | Checkpoints are single `--out` paths; no metadata sidecar for full config digests |
| No compare command | Cannot diff two runs’ metrics/cards/listening outputs |
| No early stopping | Steps always run to completion |
| Training profile stub | Docker `training` profile does not run real train CLI |
| Risk of blocking API | Must keep train/eval CLI-only; HTTP façade remains generate-only and opt-in |

### Coupling risks to avoid

1. Importing FastAPI / routers from train/eval modules.
2. Writing experiments/checkpoints into `PROJECT_DB_PATH` or revision history.
3. Calling `POST /analysis/composition` from the training process (use local pure metrics instead).
4. Claiming symbolic metrics equal musical quality in docs, logs, or UI copy.
5. Loading torch inside the default backend image or making CI hard-require ROCm.
6. Logging full token dumps, logits, event arrays, or home-absolute paths at INFO.
7. Dual token schemes — always `tokenizer.v1` encode/decode.
8. Blocking uvicorn with long train jobs (even if API enabled).

## Scope And Decisions

### In scope
- Expand train/experiment Pydantic schemas + YAML/JSON load.
- Experiment directory layout under a configurable root (default basename `experiments/music_transformer/`; never `PROJECT_DB_PATH`).
- Reproducible seeding (already present; wire into every train/eval/listen path).
- Checkpoint periodic save + **resume** (model + optimizer + scheduler + step/epoch + AMP scaler state).
- Device selection via existing `resolve_device` (CPU tests; ROCm/HIP → cuda API).
- Gradient accumulation (`grad_accum_steps`).
- Mixed precision: `precision: fp32 | amp_fp16 | amp_bf16` with safe gates (AMP only when CUDA available; bf16 only when hardware supports; else hard fail or documented fallback flag).
- Gradient clipping (already present; keep + apply after accum).
- Metrics recorder: train loss, val loss, lr, tokens/sec, memory (CUDA max allocated when available; RSS best-effort on CPU).
- Symbolic evaluation metrics module + CLI (deterministic properties listed in requirements).
- Explicit documentation that metrics ≠ musical quality.
- Listening/eval set: fixture of fixed prompts + seeds → greedy (or fixed-sample) generations stored per experiment.
- Experiment ID + metadata card (`music_transformer.experiment.v1`).
- `compare` CLI for two experiment dirs or two checkpoints.
- Optional early stopping on validation loss (patience / min_delta).
- Offline CLI + Docker training-profile execution path (non-blocking vs web API).
- Tests, verbose structured logging, docs updates.

### Out of scope
- Musicality / aesthetic scoring / human-in-the-loop labeling UI.
- Frontend SPA for training or experiment dashboards.
- Replacing LangGraph LLM generate as default.
- W&B / MLflow / cloud experiment tracking SaaS (filesystem JSON/JSONL only in v1).
- Distributed multi-GPU / DeepSpeed / FSDP.
- Changing tokenizer vocab or Composition V2 schema.
- Full production ROCm training image build (document host/venv + upgrade stub command; optional thin Dockerfile later).
- Training-from-HTTP endpoints.

### Architecture decisions (locked)

**1. Experiment as the unit of reproducibility**

```text
music_transformer.experiment.v1  (config + digests + ids)
        ↓
experiments/<experiment_id>/
  config.json | config.yaml          # frozen resolved config
  metadata.json                      # experiment card
  metrics.jsonl                      # per-log-interval / per-eval rows
  metrics_summary.json               # final aggregates
  checkpoints/step_*.pt (+ .card.json)
  checkpoints/latest.pt              # symlink or copy for resume
  eval/                              # symbolic metric reports
  listening/                         # fixed-prompt generations (V2 JSON + report)
  compare/                           # optional outputs from compare CLI
```

`experiment_id`: user-provided slug or auto `YYYYMMDD-HHMMSS-<short_config_digest>` (filesystem-safe). Recorded in checkpoint training card.

**2. Schema layering (do not collapse)**

| Schema | Role |
|--------|------|
| `music_transformer.config.v1` | Architecture (unchanged contract; already exists) |
| `music_transformer.train_config.v1` | **Expanded** training hyperparameters (optimizer, scheduler, precision, intervals, accum, early stop, …) |
| `music_transformer.experiment.v1` | experiment_id, paths, dataset_version_id, tokenizer expectation binding, train_config, eval_config, listening_config, architecture ref |
| `music_transformer.eval_config.v1` | Which symbolic metrics to run, max samples, decode/repair policy |
| `music_transformer.listening_config.v1` | Prompt fixture path, seeds, sample knobs (default greedy) |
| `music_transformer.metrics_row.v1` | One JSONL metrics row |
| `music_transformer.eval_report.v1` | Aggregated symbolic eval |
| `music_transformer.compare_report.v1` | Diff of two experiments/checkpoints |
| `music_transformer.checkpoint.v1` | Extend training card: `experiment_id`, `global_step`, `optimizer`, `scheduler`, `precision`, `grad_accum_steps`, tokenizer/dataset versions already present |

Bump only nested fields with validators; keep `extra=forbid`. If a breaking change to train_config shape is required, keep loader accepting prior minimal fields with defaults (backward compatible with existing `tiny_train.json`).

**3. Train loop ownership**  
Refactor `train.py` into a clearer loop (`Trainer` or functions) that:
- Loads train + optional validation corpora (split-aware).
- Seeds once at start (and again before eval/listening if needed for determinism).
- Supports resume from `latest.pt` / `--resume`.
- Applies grad accum + clip + optional AMP.
- Steps scheduler per config (step or epoch).
- Writes metrics JSONL; periodic checkpoint; optional early stop.
- Never imports FastAPI.

**4. Validation data**  
When `dataset_dir` points at a dataset version with `splits/validation` (or equivalent layout from dataset pipeline), use it for val loss + symbolic eval sample pool. When only flat examples exist (fixtures), allow `val_fraction` or explicit `--val-inputs` for CI. Empty val → skip val metrics with WARN (do not invent fake val loss).

**5. Mixed precision policy**  
- `fp32`: default; always safe (CPU + GPU).
- `amp_fp16`: `torch.autocast` + `GradScaler` on CUDA/ROCm only.
- `amp_bf16`: autocast bf16 when `torch.cuda.is_bf16_supported()` (or documented ROCm equivalent); else error unless `precision_fallback=fp32` explicitly set.
- Never silently run AMP on CPU.

**6. Symbolic metrics (deterministic; not quality)**  
Compute from **decoded `CompositionV2`** (`tracks[].events[]` only) and/or raw token sequences before decode:

| Metric | Definition (locked intent) |
|--------|----------------------------|
| `valid_token_rate` | Fraction of generated token ids that pass tokenizer `validate_token_sequence` (pre-repair) |
| `valid_composition_decode_rate` | Fraction of samples that repair→decode→`CompositionV2` (+ integrity) succeed |
| `pitch_class_distribution` | Histogram of pitch % 12 over note events |
| `note_density_distribution` | Notes per bar / ticks occupied histogram summary (mean/std + coarse bins) |
| `rhythmic_distribution` | Duration / onset-modulus histogram from event timing |
| `repetition` | Simple n-gram / pitch-interval self-similarity score (lightweight; not full motif analysis service) |
| `interval_distribution` | Melodic consecutive-pitch interval histogram |
| `instrument_range_violations` | Count of notes outside catalog absolute range when program/name resolves; else `unavailable` |
| `tonal_consistency` | Fraction of pitch classes in declared/inferred diatonic set (simple PC-set overlap; document limits) |

Every report MUST include a fixed disclaimer field: `musical_quality_claim: false` and docs banner: metrics measure **validity/distributional diagnostics**, not musical quality.

**7. Listening / evaluation set**  
Fixture under `backend/tests/fixtures/music_transformer/listening_set.v1.json` (and docs example): list of `{ id, seed, conditioning?, prefix_path?, sample_overrides? }`. CLI `listen` / train-end hook generates deterministic outputs into `experiments/<id>/listening/`. Same fixture used for compare.

**8. Compare**  
`python -m app.music_transformer.cli compare --a <exp_or_ckpt> --b <exp_or_ckpt> --out …` produces `music_transformer.compare_report.v1`: config digests, metric deltas, eval metric deltas, listening file hashes / note counts. Does not rank “better music.”

**9. Early stopping**  
Optional: `early_stopping: { enabled, metric: val_loss, patience, min_delta, mode: min }`. Disabled by default for short CI trains.

**10. Non-blocking API**  
- No new train HTTP routes.
- Existing opt-in generate API stays generate-only.
- Docs + Compose: training runs in `training` profile / separate container / host CLI.
- AGENTS/RULES reminder: never start train loops from request handlers.

**11. Docker training profile**  
Upgrade `local-ai-training-stub` documentation and command to either:
- echo clear install + CLI invocation instructions, **or**
- run a documented smoke `python -m app.music_transformer.cli train …` when an image with torch is used.
Default compose stack unchanged; torch remains optional extra.

**12. Logging**  
Verbose structured extras: experiment_id, step, epoch, loss, val_loss, lr, tokens_per_sec, mem_mb, checkpoint basename, metric names/counts. Never full sequences at INFO.

## Acceptance criteria mapping

| Criterion | Tasks |
|----------|-------|
| Structured training configuration | 1–2 |
| Reproducible random seeding | 2, 4, 10 |
| Checkpoint resume | 3–4 |
| CPU and ROCm device selection | 4, 11 |
| Gradient accumulation | 4 |
| Mixed precision where safe | 4 |
| Gradient clipping | 4 |
| Train metrics (loss, val loss, lr, tokens/sec, memory) | 5 |
| Symbolic-music evaluation metrics | 6–7 |
| Do not pretend metrics = quality | 6, 12 |
| Listening/evaluation set | 8 |
| Experiment IDs and metadata | 1–2, 5 |
| Compare checkpoints/models | 9 |
| Optional early stopping | 4 |
| Training does not block web API | 11–12 |
| CLI/job execution for Docker training profile | 10–12 |
| Two runs comparable (configs, metrics, ckpts, fixed gens) | 10–12 |
| Tests, logging, documentation | 10–12 |

## Commit Plan
- **Commit 1** (tasks 1–3): `feat(music-transformer): experiment schemas, run dirs, and checkpoint resume metadata`
- **Commit 2** (tasks 4–5): `feat(music-transformer): reproducible trainer with AMP, accum, metrics, and early stop`
- **Commit 3** (tasks 6–9): `feat(music-transformer): symbolic eval, listening set, and experiment compare CLI`
- **Commit 4** (tasks 10–12): `feat(music-transformer): training job CLI, acceptance tests, and docs`

## Tasks

### Phase 1: Experiment contracts, run layout, resume metadata

- [x] Task 1: Expand train config + add experiment / eval / listening / metrics schemas
  Deliverable: Strict Pydantic models (extend `schemas.py` or split `experiment_schemas.py` if file grows too large):
  - Expand `music_transformer.train_config.v1` with: `dataset_version_id` (optional explicit), `tokenizer_version` / expectation binding fields (or digest), `optimizer` (`adamw` default), `weight_decay`, `scheduler` (`none`|`cosine`|`linear_warmup`), `warmup_steps`, `precision`, `grad_accum_steps`, `checkpoint_interval`, `eval_interval`, `early_stopping` block, keep existing fields with defaults so `tiny_train.json` still validates.
  - Add `music_transformer.experiment.v1` (experiment_id, output_root, architecture, train, eval, listening, seed, device).
  - Add `eval_config.v1`, `listening_config.v1`, `metrics_row.v1`, `eval_report.v1`, `compare_report.v1`.
  - Closed issue Literals for new errors (`experiment_exists`, `resume_mismatch`, `precision_unsupported`, `val_empty`, …).
  - Config digests for experiment + train (stable JSON, exclude wall-clock).
  LOGGING: DEBUG validation field names; INFO digest prefixes + experiment_id when computed.
  Files: `backend/app/music_transformer/schemas.py` (and optional `experiment_schemas.py`), `errors.py`.

- [x] Task 2: Experiment store — create/load run directory + freeze config
  Deliverable: `experiments.py` (or `experiment_store.py`) that creates `experiments/<experiment_id>/`, writes frozen `config.json` + `metadata.json` (`music_transformer.experiment.v1` card with created_at, git/project version, tokenizer expectation, dataset_version_id). Refuse overwrite unless `--force`. Resolve relative paths; log basenames only. Settings knob `MUSIC_TRANSFORMER_EXPERIMENT_ROOT` (default under checkpoint/experiments basename).
  LOGGING: INFO create/load experiment_id, root basename, config digest prefix; ERROR on collision; never log username home paths.
  Files: `backend/app/music_transformer/experiments.py`, `settings.py`, `.env.example` commented keys.

- [x] Task 3: Checkpoint resume protocol
  Deliverable: Extend save/load to persist/restore `optimizer_state_dict`, optional `scheduler_state_dict`, optional `scaler_state_dict`, `global_step`, `epoch`, `experiment_id`, RNG state best-effort (torch/python). `resume_checkpoint(path) -> ResumeState` verifying architecture digest + tokenizer expectation match current experiment (fail closed on mismatch). Update `MusicTransformerTrainingCardV1` fields. Keep sidecar `.card.json`.
  LOGGING: INFO resume step/epoch, digest prefixes; ERROR on mismatch codes; WARN if optimizer missing (weights-only resume).
  Files: `backend/app/music_transformer/checkpoint.py`, schemas training card.

### Phase 2: Trainer capabilities + metrics

- [x] Task 4: Reproducible trainer — device, accum, AMP, clip, scheduler, early stop, resume
  Deliverable: Refactor `train.py` into a production-grade offline loop:
  - `seed_everything` at start; honor experiment seed.
  - Split-aware data load (train vs validation); fixture-friendly fallbacks.
  - Micro-batch loop with `grad_accum_steps`; clip after unscale (AMP) / before step.
  - Optimizer from config; scheduler step; AMP per precision policy.
  - Periodic checkpoint to experiment `checkpoints/`; update `latest`.
  - Optional early stopping on val_loss.
  - Resume continues `global_step` without resetting experiment_id.
  - Device via `resolve_device` (`cpu` for tests; `rocm` mapped).
  LOGGING: INFO step/epoch loss, lr, accum boundary, checkpoint saves, early-stop trigger; DEBUG batch shapes; never full ids at INFO.
  Files: `backend/app/music_transformer/train.py`, maybe `trainer.py` / `optim.py` / `precision.py`, `data.py` (split loading).

- [x] Task 5: Metrics recorder (train/val loss, lr, tokens/sec, memory)
  Deliverable: `metrics.py` writing append-only `metrics.jsonl` (`metrics_row.v1`) and final `metrics_summary.json`. Measure tokens/sec from non-PAD tokens processed / wall time; memory via `torch.cuda.max_memory_allocated` when CUDA else best-effort RSS (`resource`/`psutil` if already available — do not add heavy deps; skip with `null` + WARN). Include learning rate from scheduler/optimizer. Validation loss at `eval_interval`.
  LOGGING: INFO periodic metric snapshot keys (not huge histograms); WARN when memory unavailable.
  Files: `backend/app/music_transformer/metrics.py`.

### Phase 3: Symbolic eval, listening set, compare

- [x] Task 6: Symbolic evaluation metrics engine
  Deliverable: `eval_metrics.py` (name flexible) computing the locked metric set from token sequences and/or decoded V2. Pure functions + aggregate report (`eval_report.v1`) with `musical_quality_claim: false`. Instrument-range violations via arrangement catalog lookup when resolvable; otherwise mark metric status `unavailable` without failing the whole report. Do not call HTTP analysis API; may reuse pure helpers from analysis services if imports stay free of FastAPI/routers.
  LOGGING: INFO sample counts, decode success rate, violation counts; DEBUG per-metric status; never dump full compositions at INFO.
  Files: `backend/app/music_transformer/eval_metrics.py`, schemas for report.

- [x] Task 7: Eval runner wired to checkpoints / validation pool
  Deliverable: `evaluate_checkpoint(...)` loads model, generates or teacher-forces according to eval_config (prefer generate-from-prefix on val examples for decode metrics; allow teacher-forced valid_token_rate on held-out labels). Writes `eval/step_*.json` + `eval/latest.json` under experiment dir. Invoked from trainer at `eval_interval` and from CLI `eval`.
  LOGGING: INFO eval start/end, step, rates; ERROR on hard failures.
  Files: `backend/app/music_transformer/evaluate.py`.

- [x] Task 8: Listening / evaluation set (fixed prompts + seeds)
  Deliverable: Fixture `listening_set.v1.json` + loader; `run_listening_set(checkpoint, listening_config, out_dir)` generates deterministic Composition V2 JSON files named by prompt id + seed. Default greedy. Train completion optionally runs listening when configured. Document human comparison workflow (export MusicXML/MIDI separately via existing tools — out of scope to automate playback UI).
  LOGGING: INFO each prompt id, seed, notes_out, status; WARN on decode reject.
  Files: `backend/app/music_transformer/listening.py`, `backend/tests/fixtures/music_transformer/listening_set.v1.json` (+ small prefixes if needed).

- [x] Task 9: Compare experiments / checkpoints CLI
  Deliverable: `compare_experiments(a, b) -> CompareReport` joining config digests, metrics_summary deltas, eval report deltas, listening output digests/note counts. CLI `compare --a … --b … --out …`. No “winner” field for musical quality — only numeric deltas and equality flags for reproducibility checks (`configs_equal`, `seeds_equal`, etc.).
  LOGGING: INFO digests compared, metric keys present/missing; WARN on schema skew.
  Files: `backend/app/music_transformer/compare.py`, CLI wiring.

### Phase 4: CLI/jobs, tests, docs

- [x] Task 10: CLI surface for experiment train / resume / eval / listen / compare
  Deliverable: Extend `python -m app.music_transformer.cli`:
  - `train --experiment-config …` (or `--config` experiment YAML) creating run dir
  - `train --resume experiments/<id>/checkpoints/latest.pt`
  - `eval --checkpoint … --out …`
  - `listen --checkpoint … --listening-set … --out …`
  - `compare --a … --b … --out …`
  Keep existing simple `train --out` path working for tiny smoke (may auto-wrap ephemeral experiment id).
  Optional `scripts/music_transformer_train_smoke.sh` for Docker/docs.
  LOGGING: INFO command, experiment_id, elapsed_ms, basenames; JSON stdout summary `{ok, experiment_id, …}`.
  Files: `backend/app/music_transformer/cli.py`, optional script.

- [x] Task 11: Docker training profile + API isolation guardrails
  Deliverable: Update `compose.local-ai.yml` training service docs/command to point at offline CLI (smoke or install-hint). Confirm no train routes added; document that web API process must not run training. Soft note in `ready.py` / docs only if useful. Env examples for `MUSIC_TRANSFORMER_EXPERIMENT_ROOT`, precision, device.
  LOGGING: N/A for compose YAML; CLI logs as above.
  Files: `compose.local-ai.yml`, `.env.example`, maybe `docs/local-ai.md` section.

- [x] Task 12: Tests + documentation + roadmap handoff
  Deliverable:
  - Unit tests: config validation, digest stability, metrics row schema, AMP skip on CPU, grad accum step math (mock/tiny), resume restores step, eval metrics on fixture V2 (deterministic histograms), listening determinism (same seed → same token ids / composition digest on CPU greedy), compare report deltas.
  - Acceptance: **two** tiny train runs with different seeds or lr → distinct experiment dirs → `compare` succeeds; each run has metrics.jsonl, checkpoint card with experiment_id, listening outputs from fixed set; resume run continues from checkpoint.
  - Docs: expand `docs/music-transformer.md` (experiment layout, train config table, metrics meanings + **non-quality disclaimer**, listening set, compare, resume, AMP/ROCm, Docker training profile). Update `docs/datasets.md`, `docs/local-ai.md`, `AGENTS.md`, `DESCRIPTION.md`, `ARCHITECTURE.md` as needed. Add ROADMAP milestone checkbox (unchecked until verify).
  LOGGING: Tests may use LOG_LEVEL=DEBUG; assert disclaimer field present on eval reports.
  Files: `backend/tests/test_music_transformer_train_experiments.py` (name flexible), fixtures, docs, `ROADMAP.md`.

## Implementation Notes For `/aif-implement`

1. Prefer extending `app/music_transformer/` and mirroring `app/dataset` / `app/tokenizer` CLI+schema patterns over inventing a new top-level package.
2. Keep default backend image and `docker compose up` free of torch/ROCm; optional `requirements-music-transformer.txt` remains the install path.
3. Backward compatibility: existing acceptance test `train_model(... out_checkpoint=...)` should keep working (thin wrapper around experiment trainer or preserved simple path).
4. Validation split missing in fixtures is OK — use explicit val inputs or skip val with WARN in unit tests; acceptance for compare must still record train metrics + listening.
5. Do not change tokenizer vocab or V2 schema in this plan.
6. If AMP/bf16 support detection is messy on ROCm wheels, fail with clear `precision_unsupported` and document `fp32` as the portable default.
7. Commit large checkpoints/experiments to `.gitignore`; keep fixtures tiny.
8. Every user-facing string about eval metrics must state they are **not** musical quality scores.

## Suggested experiment config sketch (non-normative)

```yaml
schema_version: music_transformer.experiment.v1
experiment_id: tiny-cpu-smoke-001
seed: 7
device: cpu
architecture: # or path to arch JSON
  schema_version: music_transformer.config.v1
  n_layers: 2
  d_model: 64
  n_heads: 2
  max_seq_len: 128
train:
  schema_version: music_transformer.train_config.v1
  lr: 0.001
  batch_size: 2
  grad_accum_steps: 2
  steps: 20
  optimizer: adamw
  scheduler: none
  precision: fp32
  grad_clip: 1.0
  checkpoint_interval: 10
  eval_interval: 10
  seed: 7
eval:
  schema_version: music_transformer.eval_config.v1
  max_samples: 4
listening:
  schema_version: music_transformer.listening_config.v1
  set_path: tests/fixtures/music_transformer/listening_set.v1.json
```
