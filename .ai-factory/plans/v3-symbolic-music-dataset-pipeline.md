# Implementation Plan: Symbolic Music Dataset Pipeline

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-21

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: full, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: data foundation only (ingest → normalize → segment → split → stats → versioned artifact); **not** a tokenizer or training loop

## Roadmap Linkage
Milestone: "Symbolic music training dataset pipeline"
Rationale: Builds the licensed, reproducible Composition-V2 training corpus that the optional local-AI / future custom Composer training path needs; follows completed optional local AI inference (AMD/ROCm) whose `training` profile remains a stub. Add as a new unchecked milestone in `.ai-factory/ROADMAP.md` during docs/implement (roadmap owner: `/aif-roadmap` or docs checkpoint).

## Goal

Create a **dedicated dataset subsystem** (separate from user projects / `PROJECT_DB_PATH`) that converts licensed MIDI, MusicXML, and canonical Composition V2 JSON into a **versioned, normalized, provenance-gated** training corpus with deterministic train/validation/test splits, statistics, CLI tooling, tests, fixtures, structured logging, and documentation — ready for a future tokenizer/model training step without coupling the FastAPI app to training loops or weight loading.

## Audit Summary (current state)

### What already exists (reuse)
| Area | Today | Reuse for dataset pipeline |
|------|--------|----------------------------|
| MIDI parse | `services/composition_midi_import.py` | Batch ingest of `.mid`/`.midi` → `ParsedSourceScore` |
| MusicXML/MXL parse | `services/composition_musicxml_import.py` | Batch ingest of `.musicxml`/`.xml`/`.mxl` |
| Shared canonicalize | `services/composition_import.py` | PPQ scale, bar pad, instruments, V2 validate |
| JSON normalize | `services/composition_normalizer.py` | Accept Composition V2 JSON sources; V1→V2 migrate if needed |
| Timeline / bars | `services/composition_timeline.py`, `composition_schemas.bar_duration_ticks` | Bar/section segmentation boundaries |
| Fingerprints | `composition_fingerprint.py`, `composition_edit_fingerprint.py` | Exact/near-dup identity projections |
| Import limits | `import_settings.py` (`IMPORT_*`) | Pattern for `DATASET_*` limits (batch-oriented, higher caps) |
| Import fixtures | `backend/tests/fixtures/` + `build_import_fixtures.py` | Pattern for tiny licensed fixture corpus |
| Analysis (key/meter) | `composition_analysis*` / tonality helpers | Optional **stats-only** projections; never mutate playable V2 |
| CLI pattern | `backend/tests/fixtures/build_import_fixtures.py` argparse `__main__` | Prefer `python -m app.dataset.cli` |

### Gaps (must build)
| Gap | Notes |
|-----|--------|
| No dataset package | No `dataset/` module, manifests, version IDs, or provenance statuses |
| No training IR envelope | Import produces bare V2 for workspace; training needs metadata sidecar + item IDs |
| No batch CLI / jobs | Only HTTP multipart import for user projects |
| No license gate | User import does not track redistributability |
| No splits / dedup / corpus stats | Fingerprints exist but are for edit/analysis identity, not corpus leakage control |
| `training` Compose profile | Stub only (`echo … sleep infinity`); no dataset volume or preprocess job |
| Projects vs datasets | `PROJECT_DB_PATH` / Alembic must **not** store training corpora |

### Coupling risks to avoid
1. Writing dataset items into `project_store` / revision history / `PROJECT_DB_PATH`.
2. Changing the playable `composition.v2` contract or inventing notes from `harmony` / analysis.
3. Auto-including `unknown` (or `restricted`) provenance in **train** splits.
4. Logging raw MIDI/MusicXML bytes, full event arrays, absolute host usernames in public docs dumps, or license file full text when huge.
5. Shipping large third-party corpora in git; fixtures must be tiny and license-clear.
6. Implementing tokenizer / Music Transformer training loop in this plan (out of scope — handoff contract only).
7. Making default `docker compose up` depend on dataset volumes or GPU.

## Scope And Decisions

### In scope
- Dedicated filesystem dataset root + schemas (`dataset.manifest.v1`, `dataset.item.v1`, `dataset.example.v1`, stats, splits).
- Ingest sources: MIDI, MusicXML/MXL, Composition V2 JSON (and V1 JSON only via existing migrate→V2 path).
- Normalize to Composition V2 IR with deterministic timing/track/note/tempo/dup policies (reuse import canonicalize; add dataset-specific post-pass where needed).
- Segmentation: bars, sections, fixed token-budget proxies (note/event count or tick span), phrases **when available** (markers / non-`unsectioned` sections / motif spans).
- Mandatory provenance status per item; train eligibility filter.
- Deterministic dataset version IDs from config + content digests.
- Leakage-resistant train/val/test splits + exact/near duplicate handling.
- Corpus statistics JSON.
- CLI (primary) + optional documented offline job entry compatible with `--profile training` docs (no full PyTorch loop).
- Reproducible rebuild from manifest/config.
- Tests, fixture mini-corpus, structured logging, `docs/datasets.md` (+ README / AGENTS / ARCHITECTURE touchpoints).

### Out of scope
- Model training, fine-tuning UI, tokenizer implementation, weight export.
- Changing user-facing import HTTP API behavior (except optional shared helper extraction with zero behavior change).
- Persisting analysis sidecars as composition data.
- Scraping the public web for MIDI; operators supply local licensed directories.
- Multi-tenant cloud dataset hosting / auth.
- Frontend dataset browser (CLI + docs first; API optional only if needed for operator jobs — default **no SPA work**).

### Architecture decisions (locked)

**1. Subsystem boundary (mandatory)**  
```text
datasets/  (DATASET_ROOT, filesystem)
    ↑
backend/app/dataset/   ← CLI / optional job entry
    ↓ reuses (read-only call)
services/composition_*_import.py + composition_import.py + composition_normalizer.py
    ✗ never → project_store / PROJECT_DB_PATH / revision history
```

Place new code under `backend/app/dataset/` (schemas, settings, store, ingest, normalize, segment, dedup, split, stats, cli). Prefer thin wrappers over import services rather than duplicating MIDI/MusicXML parsers. Do **not** grow unrelated logic in `main.py`; if an operator HTTP job is added later, use `routers/` — this plan’s default is **CLI-only**.

**2. Intermediate representation**  
- Musical body: strict **`composition.v2`** (same playable contract; `tracks[].events[]` only audible source).
- Envelope: versioned **`dataset.item.v1`** wrapping:
  - `item_id`, `source_id`, `dataset_name`
  - `provenance` (status, license, source_url/reference, composer/author if legally available)
  - `labels` (genre/style optional)
  - `instruments` summary (from tracks)
  - `composition` (V2 document) **or** content-addressed pointer to normalized V2 blob
  - `ingest_report` codes/counts (sanitized; not raw bytes)
- Training examples: **`dataset.example.v1`** = segmented slice + offsets + parent `item_id` + deterministic `example_id`.

**3. Provenance statuses (closed enum)**  

| Status | Meaning | Train-eligible? |
|--------|---------|-----------------|
| `verified_redistributable` | License verified OK for training redistribution under project policy | yes |
| `user_owned` | Operator attests ownership / rights to train | yes (explicit attest flag required) |
| `public_domain` | PD attestation with reference | yes |
| `restricted` | Known license forbids or limits training/redistribution | **no** (may appear in corpus inventory only) |
| `unknown` | Missing or unverified license | **no** |

Default train filter: **only** `{verified_redistributable, user_owned, public_domain}`. Val/test may optionally include the same set only (never `unknown`/`restricted` unless a documented `include_non_trainable_in_eval=true` escape hatch — default **false**). Items with `unknown`/`restricted` are retained in the dataset inventory with `train_eligible=false` for auditability but **never** written into `splits/train.*`.

**4. Dataset layout (filesystem)**  
```text
$DATASET_ROOT/<dataset_name>/<dataset_version_id>/
  config.snapshot.yaml          # frozen pipeline config used for this build
  manifest.json                 # dataset.manifest.v1
  sources/                      # optional mirrored refs / sidecars (not required to copy raw bytes)
  items/<item_id>.json          # dataset.item.v1 (+ embedded or hashed V2)
  blobs/v2/<content_hash>.json  # optional CAS for normalized V2
  examples/<example_id>.json    # dataset.example.v1
  splits/train.jsonl
  splits/validation.jsonl
  splits/test.jsonl
  stats.json                    # dataset.stats.v1
  BUILD_ID                      # human-readable echo of dataset_version_id
```

`dataset_version_id` = stable short hex/base32 of SHA-256 over canonical JSON of `{pipeline_version, config_digest, item_content_digests_sorted, split_seed, schema_versions}`. Rebuilding with identical inputs **must** reproduce the same ID and bit-identical split membership (allow JSON key-order canonicalization).

**5. Normalization policy (deterministic)**  
Reuse import canonicalize as the primary path, then apply a dataset post-pass (idempotent):

| Concern | Policy |
|---------|--------|
| Timing resolution | Target PPQ default **480** (`DATASET_TARGET_PPQ`, align with `IMPORT_DEFAULT_TARGET_PPQ`); record rescale issues |
| Track/instrument | Existing GM/role resolution; stable track ids; document drum-channel handling |
| Note validation | Drop/clamp invalid pitches/durations per import issue codes; refuse non-representable scores |
| Malformed events | Same dangling note-on / unmatched note-off policies as import; count in ingest report |
| Tempo | Preserve tempo map when representable; default 120 with `tempo_defaulted` |
| Duplicate notes | Collapse exact same-track duplicates `(onset, duration, pitch, velocity)` when `DATASET_COLLAPSE_DUP_NOTES=1` (default on for training IR); log `duplicate_notes_collapsed` count |
| Harmony | Raw MIDI/MusicXML → `harmony: []` (unchanged). V2 JSON sources keep declared harmony metadata (non-audible) |

Do **not** run musical analysis to invent key/sections for the playable document. Stats may compute **derived** key/time-signature **distributions** from declared fields (+ optional non-persisted analysis helpers) into `stats.json` only.

**6. Segmentation**  

| Mode | Behavior |
|------|----------|
| `bars` | Slice by compiled bar boundaries (`compile_timeline` / bar ticks) |
| `sections` | One example per `sections[]` span (skip trivial full-score `unsectioned` when other modes requested, or emit single example) |
| `token_limit` | Pack contiguous bars until projected token proxy ≥ limit (proxy = note events or `(notes + control)`; document formula in config; **not** a real BPE tokenizer) |
| `phrases` | Use markers with phrase-like kinds/labels **or** section boundaries when sections are musically typed; if unavailable → skip with `phrase_unavailable` warning and fall back per config |

Each example stores `start_tick`, `end_tick`, `parent_item_id`, and a sliced V2 **or** note-index projection that remains valid Composition V2 for that window (prefer full mini-documents for trainer simplicity).

**7. Dedup / leakage control**  
1. **Exact source bytes** hash (when raw file available).  
2. **Normalized V2 content hash** (canonical JSON of analysis-relevant or edit fingerprint projection — prefer a new `DATASET_FINGERPRINT_PROFILE` dedicated projection focused on notes/timing/instruments, documented separately from analysis/edit profiles).  
3. **Near-dup (practical):** bucket by `(bar_count, note_count, primary_time_signature, pitch-class histogram coarse)`; within bucket compare quantized onset-pitch sequences (fixed grid); mark `near_duplicate_of` and keep one canonical item.  
4. **Split rule:** all items sharing a duplicate cluster ID go to the **same** split; split assignment is by cluster, not by example. Examples inherit parent cluster split.  
5. Seeded RNG (`split_seed`) with deterministic sort by `item_id` before assignment.

**8. Reproducibility**  
- Pipeline config YAML/JSON (`dataset_pipeline.v1`) checked into operator tree or passed via `--config`.  
- Build writes `config.snapshot.yaml` + digests into manifest.  
- CLI `dataset rebuild --config … --out …` must be idempotent for identical inputs.  
- Record `pipeline_version` string constant in code (bump when algorithms change).

**9. CLI surface (primary tooling)**  
```text
python -m app.dataset.cli ingest   --config pipeline.yaml
python -m app.dataset.cli normalize --config pipeline.yaml
python -m app.dataset.cli segment  --config pipeline.yaml
python -m app.dataset.cli dedup    --config pipeline.yaml
python -m app.dataset.cli split    --config pipeline.yaml
python -m app.dataset.cli stats    --config pipeline.yaml
python -m app.dataset.cli build    --config pipeline.yaml   # full pipeline
python -m app.dataset.cli verify   --dataset-dir <versioned dir>
```
Optional thin shell wrapper `scripts/dataset_build.sh` calling the module. Compose `--profile training` docs may mention mounting `DATASET_ROOT` and running `build` — **no** training loop required.

**10. Stats (`dataset.stats.v1`)**  
Report at least: file/item/example counts; approximate duration hours (from tempo map + duration_ticks); total bars; note count; instrument/program distribution; key distribution (declared); time signature distribution; example sequence-length histogram (notes and/or ticks); provenance status counts; train-eligible vs excluded counts; duplicate-cluster counts.

**11. Logging / secrets**  
Structured `extra={...}` via existing `LOG_LEVEL`. Log: dataset_name, version_id prefixes, item_id, source_id, provenance_status, counts, issue codes, elapsed_ms, split sizes. **Never** log: raw MIDI/MusicXML/WAV bytes, full prompts, API keys, full event arrays, full license file bodies (path + license **spdx/id** only).

## Capability / handoff (future trainers)

| Artifact | Consumer |
|----------|----------|
| `splits/*.jsonl` of `example_id` + path | Tokenizer / collate scripts (future plan) |
| `examples/*.json` V2 windows | Symbolic model input |
| `manifest.json` + `stats.json` | Experiment tracking |
| Provenance filter already applied | Trainer must still refuse to override without explicit flag |

This plan stops at **versioned normalized dataset ready for tokenizer/model training**.

## Config surface (env / pipeline YAML)

Document in `.env.example` and `docs/datasets.md` (all optional for normal app up):

```text
DATASET_ROOT=./datasets
DATASET_PIPELINE_VERSION=1          # code constant; env override discouraged
DATASET_TARGET_PPQ=480
DATASET_COLLAPSE_DUP_NOTES=1
DATASET_MAX_SOURCE_BYTES=50000000   # batch limit distinct from IMPORT_MAX_UPLOAD_BYTES
DATASET_MAX_NOTES=500000
DATASET_MAX_BARS=4096
DATASET_SPLIT_SEED=20260921
DATASET_TRAIN_RATIO=0.8
DATASET_VAL_RATIO=0.1
DATASET_TEST_RATIO=0.1
# Train eligibility: verified_redistributable|user_owned|public_domain only (hard-coded default)
```

Pipeline YAML also lists source roots, default provenance, per-source license overrides, segmentation modes, and near-dup thresholds.

## Acceptance criteria mapping

| Criterion | Tasks |
|-----------|-------|
| Dedicated subsystem ≠ user projects | 1, 2, 12 |
| MIDI / MusicXML / V2 JSON sources | 3, 4 |
| Normalize to Composition V2 IR | 4, 5 |
| Deterministic normalization policies | 5 |
| Segmentation (bars/sections/token/phrases) | 6 |
| Metadata + mandatory provenance | 2, 3, 7 |
| Exclude unknown from training | 7, 8 |
| Manifests + deterministic version IDs | 2, 9 |
| Leakage-resistant splits | 8 |
| Dedup / similarity | 8 |
| Statistics | 10 |
| CLI / jobs + reproducible from config | 9, 11 |
| Licensed MIDI dir → versioned dataset | 11, 13 (acceptance test) |
| Tests, fixtures, logging, docs | 5–8, 10, 12–14 |

## Commit Plan
- **Commit 1** (tasks 1–3): `feat(dataset): add schemas, settings, and filesystem store`
- **Commit 2** (tasks 4–6): `feat(dataset): ingest, normalize, and segment training sources`
- **Commit 3** (tasks 7–9): `feat(dataset): provenance gates, dedup/split, and versioned manifests`
- **Commit 4** (tasks 10–11): `feat(dataset): stats CLI and reproducible build entrypoint`
- **Commit 5** (tasks 12–14): `test(dataset): fixtures and docs for symbolic corpus pipeline`

## Tasks

### Phase 1: Boundaries, contracts, storage

- [x] Task 1: Define dataset subsystem layout and project isolation
  Deliverable: Create `backend/app/dataset/` package skeleton (`__init__.py`, `settings.py` stub, `errors.py`) and document the hard rule that dataset I/O uses `DATASET_ROOT` only — never `PROJECT_DB_PATH`, `project_store`, or revision APIs. Add `datasets/.gitkeep` + gitignore patterns for large corpora while allowing `backend/tests/fixtures/dataset/`. Confirm Compose default up unchanged. Optionally note in `compose.local-ai.yml` training stub comments that preprocess is CLI against a mounted `DATASET_ROOT`.
  LOGGING: INFO on settings load with `dataset_root` basename only (not full home path if avoidable — prefer resolved path in DEBUG); WARN on missing root (create-on-build policy documented).
  Files: `backend/app/dataset/__init__.py`, `backend/app/dataset/settings.py`, `backend/app/dataset/errors.py`, `.gitignore`, `datasets/.gitkeep`, maybe `compose.local-ai.yml` comment, `.env.example`.

- [x] Task 2: Author Pydantic contracts for manifest / item / example / stats / pipeline config
  Deliverable: Strict schemas (Pydantic v2) for:
  - `dataset.pipeline.v1` (sources, segmentation, split ratios, seed, normalization knobs, eligibility policy)
  - `dataset.manifest.v1` (name, version_id, pipeline_version, created_at, counts, digests, schema versions)
  - `dataset.item.v1` (ids, provenance enum, license fields, labels, instrument metadata, composition or blob ref, ingest issue summary)
  - `dataset.example.v1` (example_id, parent item, segmentation mode, tick window, payload)
  - `dataset.stats.v1` (distributions listed in decision 10)
  - Provenance status Literal matching the locked enum
  Reject unknown fields where project convention prefers strict models; reuse CompositionV2 embedding validation.
  LOGGING: DEBUG schema validation failures with field names only (no composition dump); INFO when manifest model built with version_id prefix.
  Files: `backend/app/dataset/schemas.py` (or split `*_schemas.py` if large), tests scaffold in Task 12.

- [x] Task 3: Implement filesystem store + source indexer
  Deliverable: Read/write helpers for the layout in decision 4: atomic writes (temp + rename), CAS blob put/get for normalized V2, item/example path conventions, manifest load/save, jsonl split writers. Source catalog loader that reads a sources directory or pipeline `sources:` list with per-file sidecar metadata (JSON/YAML) supplying provenance/license/url/composer/labels — **fail closed** if provenance status missing.
  LOGGING: INFO counts written; DEBUG paths relative to `DATASET_ROOT`; ERROR on atomic write failure with `error_type`.
  Files: `backend/app/dataset/store.py`, `backend/app/dataset/sources.py`.

### Phase 2: Ingest, normalize, segment

- [x] Task 4: Batch ingest MIDI / MusicXML / Composition JSON into items
  Deliverable: Ingest service that:
  - Detects format by content signature (reuse import signature helpers where practical)
  - Calls existing MIDI/MusicXML parsers → `composition_import` canonicalize **without** HTTP
  - Loads Composition V2 JSON via `normalize_composition_json` (V1 allowed only through migrate)
  - Applies `DATASET_*` complexity limits (separate from `IMPORT_*` upload caps)
  - Emits `dataset.item.v1` with ingest report codes/counts
  - Does not retain raw bytes in the item by default (optional operator `retain_sources=false` default)
  Extract shared pure helpers from import routers only if needed for reuse; **do not** change HTTP import behavior.
  LOGGING: INFO per source: source_id, format, note_count, bar_count, provenance_status, elapsed_ms; WARN on skipped files with issue codes; never log payloads.
  Files: `backend/app/dataset/ingest.py`, possibly small refactors in `composition_import.py` / routers limited to shared function extraction.

- [x] Task 5: Deterministic dataset normalization post-pass
  Deliverable: Idempotent normalizer on top of canonicalize:
  - Enforce target PPQ policy
  - Track/instrument mapping stability checks
  - Note validation / malformed handling alignment with import
  - Tempo map retention / defaults
  - Optional duplicate-note collapse (default on)
  - Stable content hash + dataset fingerprint projection (`DATASET_FINGERPRINT_PROFILE`)
  Golden tests: same fixture → same hash; second normalize is no-op.
  LOGGING: INFO collapse counts and ppq rescale; DEBUG fingerprint prefix; WARN non-fatal issue codes.
  Files: `backend/app/dataset/normalize.py`, `backend/app/dataset/fingerprint.py`.

- [x] Task 6: Segmentation into training examples
  Deliverable: Implement `bars`, `sections`, `token_limit`, `phrases` modes per decision 6. Produce `dataset.example.v1` records with deterministic `example_id` (hash of parent item_id + mode + start/end ticks). Phrase mode: use markers/sections/motifs when present; otherwise emit warning and apply configured fallback (e.g. `bars`). Ensure sliced documents remain valid CompositionV2 (recompute duration/bar_count/sections as needed without inventing notes).
  LOGGING: INFO examples_emitted by mode; WARN `phrase_unavailable`; DEBUG window ticks.
  Files: `backend/app/dataset/segment.py`.

### Phase 3: Provenance, dedup, splits, versioning

- [x] Task 7: Provenance enforcement and train eligibility
  Deliverable: Central policy module:
  - Require explicit provenance status on every item at ingest
  - Compute `train_eligible` boolean
  - Hard-fail `build` if config attempts to force `unknown` into train without an explicit unsafe override that defaults off and logs ERROR when used
  - Document SPDX/license string field + source_url/reference requirements for `verified_redistributable` / `public_domain`
  LOGGING: INFO eligibility counts by status; ERROR on attempted train pollution; WARN on restricted items retained for inventory.
  Files: `backend/app/dataset/provenance.py`.

- [x] Task 8: Deduplication, similarity clustering, and leakage-safe splits
  Deliverable:
  - Exact dup via source bytes hash + normalized content hash
  - Near-dup practical check per decision 7
  - Cluster IDs; keep canonical item; mark others `near_duplicate_of` / `exact_duplicate_of`
  - Deterministic split assignment by **cluster** using `split_seed` and ratios; write `splits/*.jsonl`
  - Verify no cluster spans multiple splits
  LOGGING: INFO cluster counts, split sizes, excluded non-eligible; DEBUG sample cluster ids (prefixes).
  Files: `backend/app/dataset/dedup.py`, `backend/app/dataset/split.py`.

- [x] Task 9: Manifest generation and deterministic `dataset_version_id`
  Deliverable: Canonical digest inputs (decision 4); write `manifest.json`, `config.snapshot.yaml`, `BUILD_ID`. `verify` subcommand checks digest recompute matches. Pipeline version constant bump guidelines in docs.
  LOGGING: INFO version_id, pipeline_version, item/example counts; ERROR on verify mismatch.
  Files: `backend/app/dataset/manifest.py`, `backend/app/dataset/versioning.py`.

### Phase 4: Stats, CLI, reproducibility

- [x] Task 10: Dataset statistics aggregator
  Deliverable: Compute `dataset.stats.v1` over eligible and full inventory (separate sections). Include files/items/examples, hours/bars, notes, instrument distribution, key distribution, time signatures, sequence length histograms, provenance tallies, dup stats.
  LOGGING: INFO summary scalars; DEBUG histogram bucket counts.
  Files: `backend/app/dataset/stats.py`.

- [x] Task 11: CLI entrypoint and full reproducible `build`
  Deliverable: `python -m app.dataset.cli` with subcommands listed in decision 9; `build` runs ingest→normalize→segment→dedup→split→stats→manifest in order; exits non-zero on failure; supports `--config` and `--out`. Add `scripts/dataset_build.sh` thin wrapper. Document how to point at a directory of licensed MIDI files end-to-end.
  LOGGING: INFO phase start/end + elapsed_ms; single final INFO build summary with version_id; controllable via `LOG_LEVEL`.
  Files: `backend/app/dataset/cli.py`, `backend/app/dataset/pipeline.py`, `scripts/dataset_build.sh`.

### Phase 5: Tests, fixtures, docs

- [x] Task 12: Fixture mini-corpus and unit/integration tests
  Deliverable: Tiny fixtures under `backend/tests/fixtures/dataset/`:
  - ≥1 public-domain or clearly licensed MIDI
  - ≥1 MusicXML
  - ≥1 Composition V2 JSON
  - Sidecar provenance files (including one `unknown` that must be excluded from train)
  - Near-duplicate pair for leakage test
  Tests covering: schema validation; ingest formats; normalize idempotence; segmentation modes; provenance exclusion; split determinism (same seed → same splits); cluster leakage guard; version_id stability; stats keys present; CLI `build` + `verify` on tmp_path.
  LOGGING: Tests may assert log extras for counts/status via `caplog` where valuable; no payload assertions.
  Files: `backend/tests/fixtures/dataset/**`, `backend/tests/test_dataset_*.py`.

- [x] Task 13: Acceptance path — licensed MIDI directory → versioned dataset
  Deliverable: Pytest (or scripted acceptance under `scripts/` invoked by tests) that runs `build` on the fixture MIDI directory and asserts: manifest exists; version_id matches recompute; train jsonl has zero `unknown`/`restricted`; val/test disjoint clusters; stats.json present; rebuild reproduces version_id.
  LOGGING: INFO acceptance summary fields only.
  Files: `backend/tests/test_dataset_acceptance.py` (and/or `scripts/dataset_acceptance.sh` calling pytest marker).

- [x] Task 14: Documentation and map updates
  Deliverable: Write `docs/datasets.md` (layout, provenance, CLI, reproducibility, limits, secret/logging policy, relationship to import + local-ai training stub). Update README docs table, `AGENTS.md` structure entry, `.ai-factory/ARCHITECTURE.md` logical module row, `.ai-factory/DESCRIPTION.md` brief feature bullet, and add unchecked roadmap milestone **"Symbolic music training dataset pipeline"** (or via `/aif-roadmap` note). Cross-link `docs/import.md` and `docs/local-ai.md` (preprocess ≠ training loop).
  LOGGING: N/A (docs).
  Files: `docs/datasets.md`, `README.md`, `AGENTS.md`, `.ai-factory/ARCHITECTURE.md`, `.ai-factory/DESCRIPTION.md`, `.ai-factory/ROADMAP.md`, `docs/local-ai.md` (short cross-link), `.env.example`.

## Implementer checklist (ultra)

- [x] Confirm no writes to `PROJECT_DB_PATH` in dataset package (grep gate in tests optional).
- [x] Confirm raw import path still yields `harmony: []` when source is MIDI/MusicXML.
- [x] Confirm `unknown` never appears in `splits/train.*`.
- [x] Confirm default Docker up does not mount/require `DATASET_ROOT`.
- [x] Confirm fingerprints use a **dataset-specific** profile name (do not silently overload analysis/edit profiles without documenting).
- [x] Phrase segmentation degrades gracefully without failing the whole build.
- [x] Token-limit mode documents that the proxy is **not** a real tokenizer.
- [x] Large corpora gitignored; fixtures license-clear and small.

## Risks And Edge Cases

| Risk | Mitigation |
|------|------------|
| Import HTTP limits too tight for corpora | Separate `DATASET_*` limits |
| Near-dup false positives | Conservative threshold + exact hash first; document knobs |
| Section-less scores | `unsectioned` single span; prefer bars mode |
| License sidecar missing | Fail closed at ingest |
| Non-determinism from dict order / timestamps | Canonical JSON; freeze `created_at` from config or hash-exclude wall clock from version_id (store timestamp in manifest but omit from version digest **or** inject fixed build time in config — **choose one in Task 9 and test it**; recommended: version_id ignores wall clock, manifest still records UTC `created_at`) |
| Accidental training on restricted data | Eligibility gate + acceptance test |
| Scope creep into training loop | Explicit out-of-scope; training profile remains stub |

## Out Of Scope (reminder)

- Tokenizer / Music Transformer / ACE-Step training
- Frontend dataset UI
- Changing composition.v2 playable semantics
- Web scraping corpora
- Storing datasets inside SQLite project DB
