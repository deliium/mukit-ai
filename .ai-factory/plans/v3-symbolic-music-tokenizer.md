# Implementation Plan: Symbolic Music Composition V2 Tokenizer

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-21

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: full, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: trainable tokenizer only (Composition V2 ↔ token ids, versioning, stats, viz, repair); **not** a model training loop or weight export

## Roadmap Linkage
Milestone: "Symbolic music Composition V2 tokenizer"
Rationale: Consumes the completed dataset pipeline’s versioned V2 examples and provides the single train/inference token contract required before any Composer training loop. Add as a new unchecked milestone in `.ai-factory/ROADMAP.md` during docs/implement (roadmap owner: `/aif-roadmap` or docs checkpoint).

## Goal

Design and implement a **versioned, trainable symbolic-music tokenizer** that converts canonical `composition.v2` (and `dataset.example.v1` windows) into a single token vocabulary and reconstructs Composition V2 documents with **documented quantization fidelity**. One scheme serves both training and inference. Include encode/decode, validation/repair, statistics, debug visualization, extensive deterministic round-trip tests, and documentation — without coupling FastAPI, `PROJECT_DB_PATH`, or model weight loading.

## Audit Summary (current state)

### Composition V2 facts that constrain token design

| Area | Contract | Tokenizer implication |
|------|----------|------------------------|
| Playable notes | Only `tracks[].events[]` (`CompositionV2NoteEvent`: `pitch`, `start_tick`, `duration_ticks`, `velocity`, optional id/articulations/tie) | Encode notes from events only; never invent notes from `harmony` |
| Timing | Integer ticks; default `ticks_per_quarter=480`; bar map from meter + `time_signature_changes` | One temporal scheme; quantize onto a fixed grid derived from PPQ |
| Tempo | Root `tempo` + `tempo_changes[{tick,bpm}]` (40–240) | Explicit TEMPO tokens when timeline differs from default / changes |
| Meter | Root `time_signature` + bar-boundary `time_signature_changes` | METER / BAR vocabulary; BAR length = compiled bar ticks / grid |
| Key | Root `key` + `key_changes` | Optional KEY conditioning / context tokens (declared only) |
| Tracks | `id`, `instrument`, `role`, `midi_program`, `channel`, `is_drum`, expression lanes | TRACK header tokens; polyphony = concurrent notes within/across tracks |
| Sections | Contiguous `sections[]` with `type`, tick span | SECTION tokens at boundaries where useful |
| Harmony | Explicit chord spans; non-audible | Optional HARMONY context tokens; never audible source |
| Motifs / articulations / ties / automation | Authored metadata / performance projection | **Out of core round-trip** unless a documented lossy profile includes a subset |
| Analysis sidecar | `composition.analysis.v1` | Never a tokenizer input field |

Primary references: `docs/composition-v2.md`, `backend/app/composition_schemas.py`, `composition_timeline` / `bar_duration_ticks`.

### What already exists (reuse)

| Area | Today | Reuse for tokenizer |
|------|--------|---------------------|
| Dataset examples | `dataset.example.v1` mini V2 windows under `DATASET_ROOT` | Primary batch encode input |
| Dataset CLI pattern | `python -m app.dataset.cli` | Mirror as `python -m app.tokenizer.cli` |
| Dataset fixtures | `backend/tests/fixtures/dataset/` + V2 expressive fixtures | Round-trip + acceptance fixtures |
| Normalize / PPQ | `DATASET_TARGET_PPQ=480`, import canonicalize | Assume pre-normalized V2; optionally assert/rescale once |
| Pitch helpers | `midi_pitch_number` in `composition_schemas.py` | Pitch ↔ MIDI number |
| Timeline compile | `composition_timeline` / bar map | BAR boundaries, position-in-bar |
| Strict Pydantic | `dataset/schemas.py` style | `tokenizer.*.v1` contracts |
| Logging policy | Structured extras; no raw event dumps | Same secrets rules |

### Gaps (must build)

| Gap | Notes |
|-----|--------|
| No tokenizer package | No encode/decode, vocab, version id, or repair |
| No shared train/inference token contract | Dataset `token_limit` is a **note-count proxy**, not a vocabulary |
| No quantization profile | Need explicit grid + velocity bins + pitch policy |
| No model↔tokenizer version binding artifact | Trainers need a manifest field / sidecar |
| No token stats / viz | Required for debugging sequences |
| Docs | Need `docs/tokenizer.md` + handoff update in `docs/datasets.md` |

### Coupling risks to avoid

1. Writing tokens or corpora into `project_store` / `PROJECT_DB_PATH` / revision history.
2. Changing the playable `composition.v2` contract or inventing notes from `harmony` / analysis.
3. Separate “training tokenizer” vs “inference tokenizer” code paths or vocabs.
4. Logging full token id dumps at INFO for large scores, raw MIDI/MusicXML bytes, full event arrays, or API keys.
5. Implementing PyTorch/training loops, weight loading, or GGUF export in this plan.
6. Growing unrelated logic in `main.py`; default surface is **CLI-only** (optional thin service helpers OK).
7. Making default `docker compose up` depend on tokenizer artifacts or GPU.

## Scope And Decisions

### In scope
- Package `backend/app/tokenizer/` with schemas, vocab, encode, decode, validate/repair, stats, viz, versioning, CLI.
- Single canonical token scheme for train **and** inference.
- Composition V2 → token ids / token strings; token ids → Composition V2.
- Special tokens: BOS, EOS, BAR, TRACK, SECTION, PAD (+ documented structural family).
- Optional conditioning prefix: key, genre, mood, instrument set, desired section type.
- Optional harmony/context tokens (non-audible).
- Tokenizer versioning; trained-model expectation record (`tokenizer_version` in artifact sidecar).
- Statistics: vocab size, avg tokens/bar, max sequence length, unknown/invalid event rate.
- Visualization/debug tooling for token sequences (text + optional lightweight HTML/JSON dump).
- Extensive deterministic round-trip tests + `docs/tokenizer.md`.
- Consume bare V2 JSON **or** `dataset.example.v1` / dataset split paths.

### Out of scope
- Model training, fine-tuning UI, weight export, HuggingFace upload.
- Changing user-facing import HTTP API or Composition V2 schema.
- Persisting analysis sidecars or tokens in projects DB.
- Frontend SPA tokenizer browser (CLI + docs first).
- Perfect lossless round-trip of articulations, ties, automation, motifs, staff/voice, free-text labels (document as **lossy / omitted** under core profile).
- Replacing dataset `token_proxy` segmentation (may later *optionally* call real tokenizer length — not required here).

### Architecture decisions (locked)

**1. Subsystem boundary (mandatory)**  
```text
Composition V2 / dataset.example.v1
        ↓
backend/app/tokenizer/   ← CLI / library API
        ↓
token ids + encode/decode reports + tokenizer.manifest.v1
        ✗ never → project_store / PROJECT_DB_PATH / ai_runtime weight load
```

Place code under `backend/app/tokenizer/` (sibling of `dataset/`). Prefer pure functions + small orchestrators. Do **not** add FastAPI routes in this plan.

**2. One scheme for train and inference**  
Ship a single module path: `encode(composition, config) → EncodedSequence` and `decode(tokens, config) → CompositionV2`. Training scripts and future inference must import the same versioned codec. No dual REMI/MIDI-like forks.

**3. Canonical temporal representation: REMI-style bar + position + duration**  

Chosen over note-off / time-shift MIDI-like schemes because:
- Duration tokens avoid unpaired note-on/note-off repair ambiguity.
- Explicit `BAR` + in-bar `POS_*` matches Composition V2’s bar-compiled timeline.
- Polyphony is natural: multiple Pitch/Duration(/Velocity) events share a position.
- Multi-track via `TRACK_*` headers without exploding a joint pitch×track vocab.

**Canonical time unit:** quantized **grid steps** derived from `ticks_per_quarter` (default 480) and `grid_subdivisions_per_quarter` (default **4** → sixteenth-note grid → `grid_ticks = 120`). All onsets and durations snap to this grid before tokenization. Absolute piece time is recovered as `bar_index * bar_steps + position` using the compiled meter map.

Documented alternatives considered and rejected for v1:
- Raw tick tokens (vocab explosion; sparse).
- Note-off + time-shift only (harder deterministic repair; worse bar structure).
- Seconds-based time (non-deterministic vs tempo edits; V2 is tick-canonical).

**4. Vocabulary families (closed, versioned)**  

| Family | Tokens (illustrative) | Notes |
|--------|----------------------|-------|
| Special | `PAD`, `BOS`, `EOS`, `UNK`, `BAR`, `TRACK`, `SECTION` | Fixed ids 0..N; PAD=0 |
| Conditioning (optional prefix) | `COND_KEY_*`, `COND_GENRE_*`, `COND_MOOD_*`, `COND_INSTSET_*`, `COND_SECTION_TYPE_*` | Closed small enums + UNK bucket; never invent notes |
| Structure | `METER_*`, `TEMPO_*`, `KEY_*` (context), `SECTION_TYPE_*` | Declared V2 fields |
| Track | `TRACK_PROG_*` or `TRACK_SLOT_*` + optional `DRUM` flag token | Prefer stable slot order = V2 track order; record program in header |
| Time | `POS_{0..max_bar_steps-1}`, `DUR_{1..max_dur_steps}` | `max_bar_steps` from longest supported meter at grid; clamp/reject beyond |
| Note | `PITCH_{0..127}`, `VEL_{0..velocity_bins-1}` | Pitch = MIDI number via `midi_pitch_number`; spelling not guaranteed on decode |
| Harmony (optional) | `HARM_*` coarse chord classes | Context only; decode may restore `harmony[]` spans if encode emitted them |

Avoid token explosion: do **not** build pitch×velocity×duration compound tokens; use factored events.

**Default note event emission order (per onset group, stable sort):**  
`POS → (for each note sorted by track_slot, pitch): [TRACK_SLOT if track changed] PITCH VEL DUR`  
Bar open: `BAR` then optional `METER`/`TEMPO` if changed at boundary. Piece: `BOS` + conditioning + headers → bars → `EOS`.

**5. Quantization rules (core round-trip contract)**  

| Attribute | Encode policy | Decode policy | Round-trip guarantee |
|-----------|---------------|---------------|----------------------|
| Onset | Snap to nearest grid tick (ties: round half toward even or document floor — **lock: round-to-nearest, ties away from zero / half-up**) | `start_tick = bar_start + pos * grid_ticks` | Equal after snap |
| Duration | Snap to nearest ≥ 1 grid step; clamp to `max_dur_steps` | `duration_ticks = dur * grid_ticks` | Equal after snap |
| Pitch | MIDI 0–127; invalid → drop + `invalid_pitch` count | Spelling via deterministic MIDI→name (prefer natural accidentals helper if present; else sharps) | MIDI number preserved; **spelling may change** |
| Velocity | Bin into `velocity_bins` (default **32**, values map 1..127 → bin centers on decode) | Reconstruct bin center clamped 1..127 | Within bin width |
| Tracks | Preserve order; map to slots `0..N-1`; header stores `midi_program`, `is_drum`, role if representable | Rebuild tracks with deterministic ids `track_{slot}` | Program/slot/is_drum preserved; free-text `name`/`instrument` may normalize |
| Tempo | Quantize BPM to int (already); emit TEMPO when ≠ previous | Restore root + changes on bar boundaries when possible | BPM preserved; tick of change snapped to bar/grid per rules |
| Meter | Exact string enum of supported signatures | Restore root + changes on BAR | Exact if in vocab; else reject or UNK+repair |
| Sections | Emit SECTION_TYPE at section starts when enabled | Rebuild contiguous sections covering duration | Types + quantized boundaries |
| Harmony | Optional coarse tokens at span starts | Optional `harmony[]` restore | Best-effort; **not** required for acceptance core |
| Articulations / ties / automation / motifs / markers labels | Omit in `profile=core` | Empty defaults | Explicitly lossy |

Encode returns `TokenizerEncodeReport` with counts: `notes_in`, `notes_emitted`, `snapped_onset`, `snapped_duration`, `dropped_invalid`, `omitted_expressive`, `unknown_conditioning`.

**6. Invalid generated sequences**  

Pipeline: `validate_tokens` → `repair_tokens` (optional) → `decode`.

| Class | Behavior |
|-------|----------|
| Deterministic repair | Drop tokens before first BOS / after EOS; strip PAD; clamp DUR/POS into range; ignore orphan VEL without PITCH; ignore HARMONY if profile disables; coalesce duplicate BAR at same index |
| Reject (ambiguous / unsafe) | Missing BOS/EOS when required; POS outside active meter bar length; TRACK slot unknown with no header; empty note payload; conflicting METER at non-bar; undecodable garbage rate above threshold |
| Result codes | Closed enum: `ok`, `repaired`, `rejected` + issue codes list |

Never silently invent pitches to “fill” bars.

**7. Versioning**  

- Code constant `TOKENIZER_VERSION = "tokenizer.v1"` (bump on vocab or quantization rule changes).
- Artifact `tokenizer.manifest.v1`: `{tokenizer_version, vocab_hash, config_digest, special_token_ids, grid_subdivisions_per_quarter, velocity_bins, profile, created_at?}`.
- `vocab_hash` = SHA-256 of canonical JSON token→id map (key-sorted).
- Future trained models **must** record `expected_tokenizer_version` + `vocab_hash` in their card/sidecar; encode/decode CLI verifies match when `--require-version` is set.
- Config digest excludes wall-clock so rebuilds are bit-identical.

**8. Library + CLI surface**  

```text
python -m app.tokenizer.cli encode   --input <v2.json|example.json> [--config tokenizer.yaml] --out tokens.json
python -m app.tokenizer.cli decode   --input tokens.json [--config tokenizer.yaml] --out composition.json
python -m app.tokenizer.cli roundtrip --input <v2.json> [--config tokenizer.yaml]
python -m app.tokenizer.cli stats    --dataset-dir <versioned dataset> | --inputs glob
python -m app.tokenizer.cli viz      --input tokens.json --out sequence.txt|sequence.html
python -m app.tokenizer.cli vocab    --config tokenizer.yaml --out vocab.json
python -m app.tokenizer.cli verify   --manifest tokenizer.manifest.json
```

Optional: `encode-dataset` reading `splits/*.jsonl` + examples (read-only).

**9. Statistics (`tokenizer.stats.v1`)**  
At least: `vocab_size`, `avg_tokens_per_bar`, `max_sequence_length`, `unknown_or_invalid_event_rate`, histograms (tokens/bar, notes/bar), drop/snap counts, profile name, tokenizer_version.

**10. Visualization**  
Human-readable dump: token string stream with bar breaks; optional compact HTML table (bar | pos | track | pitch | vel | dur). Never embed full raw V2 event arrays in viz logs.

**11. Logging / secrets**  
Structured `extra={...}` via `LOG_LEVEL`. Log: tokenizer_version, vocab_hash prefix, profile, counts, issue codes, elapsed_ms, path basenames. **Never** log: API keys, full prompts, raw MIDI/MusicXML/WAV, full event arrays, full token id lists at INFO for large sequences (DEBUG may log bounded prefixes, e.g. first 64 tokens).

## Acceptance criteria mapping

| Criterion | Tasks |
|-----------|-------|
| Research V2 before token semantics | 1 (audit locked above) |
| Represent bar/time/pitch/dur/vel/track/section/tempo/meter/optional harmony | 2–5 |
| One scheme train+inference | 2, 3 |
| One canonical temporal representation | 2 |
| Polyphony + multi-track | 4, 5, 12 |
| Avoid token explosion | 2 (factored vocab) |
| Special tokens BOS/EOS/BAR/TRACK/SECTION/PAD | 2, 3 |
| Optional conditioning | 3, 6 |
| Encode + decode | 4, 5 |
| Round-trip under quantization rules | 5, 12, 13 |
| Validate / repair / reject | 7 |
| Tokenizer versioning + model expectation record | 8 |
| Statistics | 9 |
| Visualization/debug | 10 |
| Tests + docs | 11–14 |

## Commit Plan
- **Commit 1** (tasks 1–3): `feat(tokenizer): add schemas, vocab, and versioned config`
- **Commit 2** (tasks 4–7): `feat(tokenizer): encode, decode, and invalid-sequence repair`
- **Commit 3** (tasks 8–10): `feat(tokenizer): versioning, stats, and sequence visualization`
- **Commit 4** (tasks 11–14): `test(tokenizer): round-trip fixtures and documentation`

## Tasks

### Phase 1: Contracts, vocab, config

- [x] Task 1: Confirm V2 audit notes in package docs stub and isolation rules
  Deliverable: Create `backend/app/tokenizer/` package skeleton (`__init__.py`, `settings.py`, `errors.py`) documenting hard rules: filesystem/CLI library only; never `PROJECT_DB_PATH`; playable notes only from `tracks[].events[]`; no FastAPI routes in this milestone. Add env knobs pattern (`TOKENIZER_*`) in settings aligned with dataset/import style. Confirm default Compose unchanged.
  LOGGING: INFO on settings load with version string; DEBUG resolved grid_ticks; WARN on missing optional config (defaults applied).
  Files: `backend/app/tokenizer/__init__.py`, `settings.py`, `errors.py`, `.env.example` (optional keys only).

- [x] Task 2: Lock token semantics + quantization profile in schemas
  Deliverable: Strict Pydantic models for:
  - `tokenizer.config.v1` (grid_subdivisions_per_quarter, velocity_bins, max_dur_steps, max_tracks, profile=`core`|`core_harmony`, conditioning enabled flags, require_bos_eos)
  - `tokenizer.vocab.v1` / in-memory vocab builder output
  - `tokenizer.sequence.v1` (token_ids, token_strs optional, tokenizer_version, vocab_hash, encode_report)
  - `tokenizer.encode_report.v1` / `tokenizer.decode_report.v1`
  - Closed issue/result code Literals
  Document the locked REMI-style scheme and quantization table in module docstring (full prose later in Task 14).
  LOGGING: DEBUG validation failures with field names only; INFO when config digest computed (prefix).
  Files: `backend/app/tokenizer/schemas.py`, maybe `profiles.py`.

- [x] Task 3: Implement vocabulary builder + special/conditioning tokens
  Deliverable: Deterministic token↔id map including PAD=0, BOS, EOS, BAR, TRACK, SECTION, UNK, POS/DUR/PITCH/VEL/METER/TEMPO ranges, optional COND_* and HARM_*. Stable sort for hashing. `build_vocab(config) -> Vocab`. Reject configs that exceed practical size guards (document max vocab).
  LOGGING: INFO vocab_size + tokenizer_version; DEBUG family counts; ERROR on non-deterministic hash mismatch in self-check.
  Files: `backend/app/tokenizer/vocab.py`, `special_tokens.py`.

### Phase 2: Encode / decode / repair

- [x] Task 4: Implement Composition V2 → tokens encoder
  Deliverable: `encode_composition(doc: CompositionV2, config, *, conditioning=None) -> TokenizerSequence`.
  - Compile bar map via existing timeline helpers
  - Quantize onsets/durations; group by bar/position; emit factored tokens
  - Multi-track: track headers then slot switches; polyphony at same POS
  - Optional section/tempo/meter/harmony tokens per config
  - Accept `dataset.example.v1` by extracting `.composition`
  - Do not mutate input document
  LOGGING: INFO note/token/bar counts + version; DEBUG snap/drop counters; never full event dump.
  Files: `backend/app/tokenizer/encode.py`, helpers `quantize.py`, `timeline_adapter.py`.

- [x] Task 5: Implement tokens → Composition V2 decoder
  Deliverable: `decode_tokens(ids|strs, config, vocab) -> (CompositionV2, DecodeReport)`.
  - Rebuild duration_ticks/bar_count/sections/tracks/events under quantization inverse
  - Deterministic track ids, default names from program/role
  - Pitch MIDI→name deterministic; velocity from bin centers
  - Validate output with `CompositionV2` model; attach report codes for clamped/repaired fields
  LOGGING: INFO decode status + note counts; WARN on clamps; ERROR on reject path.
  Files: `backend/app/tokenizer/decode.py`.

- [x] Task 6: Optional conditioning token encode/decode
  Deliverable: Prefix conditioning for key/genre/mood/instrument set/desired section type from explicit args or dataset labels when present. Unknown labels → COND_*_UNK. Decode exposes conditioning sidecar in report (not forced into V2 unless key/meter already represented).
  LOGGING: DEBUG conditioning tokens applied (names only); INFO count of UNK conditioning.
  Files: `backend/app/tokenizer/conditioning.py` (or section in encode/decode).

- [x] Task 7: Validation + deterministic repair + rejection
  Deliverable: `validate_token_sequence`, `repair_token_sequence`, integrated decode path with `on_invalid=repair|reject`. Unit tests for orphan velocity, missing EOS, POS overflow, empty bars.
  LOGGING: INFO result code; DEBUG issue codes list (bounded); WARN when repair mutates sequence length.
  Files: `backend/app/tokenizer/validate.py`, `repair.py`.

### Phase 3: Versioning, stats, viz, CLI

- [x] Task 8: Tokenizer versioning + model expectation artifact
  Deliverable: `tokenizer.manifest.v1` writer/reader; `expected_tokenizer_version` helper for future trainers; `verify_manifest` checks vocab_hash. Constant bump policy documented.
  LOGGING: INFO version + hash prefix on write; ERROR on mismatch when required.
  Files: `backend/app/tokenizer/versioning.py`, `manifest.py`.

- [x] Task 9: Statistics generator
  Deliverable: Compute `tokenizer.stats.v1` over a list of compositions or a dataset version dir (read examples via dataset store helpers without importing training loops). Include vocab_size, avg tokens/bar, max sequence length, unknown/invalid event rate, snap/drop aggregates.
  LOGGING: INFO summary metrics; DEBUG per-file failures with basenames only.
  Files: `backend/app/tokenizer/stats.py`.

- [x] Task 10: Visualization / debug tooling
  Deliverable: Render token sequences to plain text (and optional simple HTML) with bar grouping and human token strings. CLI `viz` subcommand. Bound output size for huge sequences (truncate with notice).
  LOGGING: INFO output path basename + token count; DEBUG truncate notice.
  Files: `backend/app/tokenizer/viz.py`.

- [x] Task 11: CLI entrypoint
  Deliverable: `python -m app.tokenizer.cli` with subcommands encode/decode/roundtrip/stats/viz/vocab/verify (+ optional encode-dataset). Mirror dataset CLI logging/error exit codes. Optional `scripts/tokenizer_roundtrip.sh` thin wrapper.
  LOGGING: INFO command + elapsed_ms; ERROR domain codes to stderr.
  Files: `backend/app/tokenizer/cli.py`, `__main__.py`, maybe `scripts/tokenizer_roundtrip.sh`.

### Phase 4: Tests and documentation

- [x] Task 12: Deterministic round-trip and polyphony/multi-track tests
  Deliverable: Pytest coverage for:
  - Fixture V2 expressive + dataset `v2_sample.json` encode→decode pitch/timing/duration/velocity-bin/track/polyphony preservation under quantization
  - Empty tracks, single-note, dense chord (polyphony), multi-track alignment
  - Tempo/meter changes at bar boundaries
  - Invalid sequence repair/reject cases
  - Vocab hash stability (byte-identical across runs)
  - Version mismatch verify failure
  LOGGING: Tests may assert log extras via caplog where useful; no secrets.
  Files: `backend/tests/test_tokenizer_*.py`, fixtures under `backend/tests/fixtures/tokenizer/` if needed.

- [x] Task 13: Acceptance test — fixture Composition V2 round-trip gate
  Deliverable: Explicit acceptance test matching user criterion: encode/decode preserves pitches, quantized timing/durations, tracks, and polyphony per documented rules; fails if core fields drift beyond quantization.
  LOGGING: N/A (assert-based); implementation logs already covered.
  Files: `backend/tests/test_tokenizer_acceptance.py`.

- [x] Task 14: Documentation + handoff updates
  Deliverable: Author `docs/tokenizer.md` (semantics, quantization table, special tokens, conditioning, versioning, CLI, stats, viz, limits). Update `docs/datasets.md` handoff table to point at tokenizer. Touch README / AGENTS.md / ARCHITECTURE.md module lists. Add ROADMAP unchecked milestone “Symbolic music Composition V2 tokenizer”. Cross-link local-ai training stub as “next: training loop consumes tokenizer.v1”.
  LOGGING: N/A for docs; mention logging/secrets policy in docs.
  Files: `docs/tokenizer.md`, `docs/datasets.md`, `README.md`, `AGENTS.md`, `.ai-factory/ARCHITECTURE.md`, `.ai-factory/ROADMAP.md`, `.ai-factory/DESCRIPTION.md` (tokenizer bullet).

## Open questions (resolved in this plan)

| Question | Decision |
|----------|----------|
| REMI vs MIDI-like note-off? | **REMI-style** bar/pos/dur for v1 |
| Pitch spelling fidelity? | MIDI number canonical; spelling may change |
| Velocity resolution? | Default **32 bins** (configurable) |
| Include articulations/ties in core? | **No** — `profile=core` omits; future profile may extend |
| HTTP API? | **No** in this plan — CLI/library only |
| Where does code live? | `backend/app/tokenizer/` sibling of `dataset/` |

## Handoff to future trainers

| Artifact | Consumer |
|----------|----------|
| `tokenizer.manifest.v1` + vocab.json | Model card must store `expected_tokenizer_version` + `vocab_hash` |
| `encode` / `decode` library API | Collate scripts + inference detokenizer |
| `tokenizer.stats.v1` | Experiment tracking |
| Dataset `splits/*.jsonl` + examples | Batch encode inputs (already provenance-filtered) |

This plan stops at **versioned Composition↔tokens codec ready for model training**.
)
