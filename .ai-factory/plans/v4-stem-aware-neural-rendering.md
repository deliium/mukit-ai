# Implementation Plan: Stem-Aware Neural Rendering

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-24
Planning depth: final, ultra-thorough
Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough
- Scope: move neural egress from single final WAV to **independently manageable musical stems** (piano/bass/strings/drums/vocals/other) while reusing `/neural-audio` job architecture, adapters, `NEURAL_AUDIO_RENDER_ROOT`, fake mode, and fidelity labeling — **never** mutating `composition.v2`, **never** claiming sample-accurate unchanged audio for generative engines, **never** silently swapping generative→FluidSynth, **never** writing `DATASET_ROOT` / inventing `composition.v4`
- Parent plans / shipped foundations:
  - `.ai-factory/plans/v3-optional-neural-audio-rendering.md` (**shipped** — mix jobs, adapters, quotas, fake/sidecar)
  - `.ai-factory/plans/v4-audio-symbolic-alignment-round-trip.md` (**shipped** — soft-stale via snapshot fingerprint; recovery `stem_bindings` are role→track only, **not** durable neural stem WAVs)
  - `.ai-factory/plans/v1-feature-server-side-wav-rendering.md` (**shipped** — FluidSynth deterministic mix)
  - `.ai-factory/plans/v3-daw-interop-v3-e2e-hardening.md` (**shipped** — secret-safe provenance patterns)
  - `.ai-factory/plans/v2-safe-ai-preview-version-history-branches.md` (**shipped** — revision fingerprints)

## Roadmap Linkage
Milestone: "Stem-aware neural audio rendering" *(present in `.ai-factory/ROADMAP.md` and marked complete; residual Gaps Close-out is Tasks 11–13 — do not re-insert or reopen the milestone)*
Rationale: Mix-only neural jobs cannot satisfy “render piano/bass/strings and regenerate only strings”; this milestone closes selective stem egress on top of shipped neural + alignment foundations.

## Goal

Allow a **saved Composition** (body and/or pinned revision) to produce a **stem set** of independently manageable WAVs while leaving the symbolic score completely unchanged. The user can:

1. Render the composition into separate stems (e.g. `piano.wav`, `bass.wav`, `strings.wav`, `drums.wav`, `vocals.wav`, `other.wav`).
2. **Rerender only one stem** (e.g. strings) under the same stem set while preserving other stem assets and the symbolic composition.
3. Optionally filter to a **symbolic bar range** when the engine advertises `section_symbolic_filter` (honest: not audio punch-in).
4. Fall back to **explicit** FluidSynth/deterministic stems when requested (never silent swap from generative).
5. See sync/fidelity honesty labels and soft-stale banners when the live composition fingerprint diverges.

```text
Composition V2 (authoritative) + optional source_revision_id
        │
Partition tracks → stem roles (heuristic + user override)
        │
Capability probe on AUDIO_RENDER model
  ├─ direct_stems               (fake CI only in v1)
  ├─ per_track                  (filter 1 track → adapter → engine)
  ├─ grouped_tracks             (filter N tracks → adapter → engine)
  ├─ section_symbolic_filter    (bar-range event filter before render)
  └─ fluidsynth_deterministic   (explicit path only)
        │
Stem set job (queued → running → complete|failed)
  └─ stem members under NEURAL_AUDIO_RENDER_ROOT + provenance + sync
        │
Selective rerender → new stem row (supersedes_stem_id); V2 untouched
        │
UI: stem checklist / status / download / rerender / honesty banners
```

**Acceptance one-liner:** The user can render a composition into separate piano/bass/strings stems and regenerate only the strings render while preserving the symbolic composition.

## Terminology lock

| Term | Meaning |
|------|---------|
| **Mix job** | Existing `neural_audio_render.job.v1` single WAV (unchanged default path) |
| **Stem set** | Parent job grouping related stem members that share timeline sync + source fingerprint |
| **`neural_audio_stem_set.v1`** | Versioned stem-set DTO (metadata only; no PCM) |
| **Stem** | One role-labeled WAV + metadata (source tracks, model, params, sync) |
| **`neural_audio_stem.v1`** | Versioned stem member DTO |
| **Stem role** | Egress label: `piano` \| `bass` \| `strings` \| `drums` \| `vocals` \| `other` |
| **Capability** | `direct_stems` / `per_track` / `grouped_tracks` / `section_symbolic_filter` / `fluidsynth_deterministic` |
| **Sync class** | `deterministic_midi` \| `timeline_aligned` \| `generative_independent` — **never** `sample_accurate` for generative |
| **Section scope** | Optional bar filter on **symbolic** material before render (`symbolic_filter`) — **not** audio punch-in |
| **Alignment stem_binding** | Recovery role→`track_id` only — **not** these durable neural stem WAVs |
| **Supersede** | Selective rerender creates a new stem row with `supersedes_stem_id`; prior stem remains downloadable |

## Non-goals (v1)

- Sample-accurate generative stem sync / DAW clip warping / SMPTE
- Silent generative→FluidSynth fallback when neural fails
- Durable Bind of recovery/Demucs separated stem WAVs (different product surface)
- Putting stem ids / sync on `CompositionV2NoteEvent` (`extra="forbid"`)
- Inventing `composition.v4`
- Writing to `DATASET_ROOT` / auto corpus ingest
- Replacing Tone.js preview or Export WAV mix button
- Baking MusicGen/MIDI-DDSP weights into default Compose image
- Mandatory GPU sidecars for AC (fake fixtures suffice)
- Rebuilding V4 recovery or mix-only neural pipelines

## Approach Evaluation (locked)

### Part A — Persistence

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Overload `neural_audio_renders.audio_relpath` with ZIP** | No new tables | Breaks mix download; weak per-stem provenance | **Reject** |
| **B. One mix-style job per stem (no parent)** | Simple | Hard shared sync; selective rerender UX weak | **Reject** |
| **C. New `neural_audio_stem_sets` + `neural_audio_stems` tables** | Clear parent/member; history via supersede | Alembic required | **Accepted** |

**Locked:** Alembic after `20260924_0006`. Files: `{project_id}/{stem_set_id}/{stem_id}.wav` under `NEURAL_AUDIO_RENDER_ROOT`. Mix jobs unchanged.

### Part B — Capability adapters

| Capability | Engines (v1) | Behavior |
|------------|--------------|----------|
| `direct_stems` | `fake:neural-audio` only | One call returns role→WAV map |
| `per_track` | fake, midi-ddsp, musicgen | Slice to one `track_id` → existing adapter → `render` |
| `grouped_tracks` | fake, midi-ddsp, musicgen | Slice to N track ids → same path |
| `section_symbolic_filter` | fake + fluidsynth + per/group neural | Filter events outside bar range before render |
| `fluidsynth_deterministic` | when FluidSynth+SF2 available | Explicit engine only; filtered V2 → MIDI → FluidSynth |

**Locked:** Missing capability → 422 `stem_capability_unsupported`. No silent downgrade. MusicGen/MIDI-DDSP do **not** advertise `direct_stems`. Do not invent a new `AiOperation` — extend descriptor / `/ai/models` with `stem_capabilities`.

### Part C — Partitioning

**Locked:** Default heuristic from V2 `tracks[].role` / `instrument` / `is_drum`:
- `drums` / `percussion` / `is_drum` → `drums`
- `bass` → `bass`
- piano/keys instrument names → `piano`
- string family names → `strings`
- voice/vocal → `vocals`
- else → `other` (or group by role when multiple tracks share a stem)

Request may override with explicit `stems[{stem_role, track_ids[]}]`. Empty groups omitted (`stem_partition_empty` if nothing left).

### Part D — Sync & provenance

**Locked every stem records:**
- `source_revision_id` (optional), `source_fingerprint` (`composition.snapshot.v1`)
- `source_track_ids[]`, `stem_role`
- `model_id`, `model_version`, `adapter_kind`, `fidelity_class`
- render params: seed, tempo_bpm, instruction_chars (never full prompt at INFO)
- `capability_used`, `sync_class`, `sample_rate`, `tempo_bpm`, `origin_tick`, `duration_ticks`, optional `bar_range`
- `mutates_composition: false`

Stem set pins shared `timeline_sync` (origin_tick, duration_ticks, tempo_bpm, sample_rate policy, source_fingerprint). Generative members: `sync_class=generative_independent` or `timeline_aligned` — UI copy must refuse “sample-accurate unchanged audio” claims.

### Part E — Selective rerender

**Locked:** `POST /neural-audio/stem-sets/{set_id}/stems/{stem_id}/rerender` creates a **new** stem row with `supersedes_stem_id`, same role/tracks/sync anchors from set, new fingerprint from request composition/revision. Old stem stays downloadable. Never mutates V2.

### Part F — FluidSynth

**Locked:** Explicit only (`engine: fluidsynth` or user selects deterministic stems). Labels `fidelity_class=deterministic`, `sync_class=deterministic_midi`. Does not replace Export WAV mix.

### Part G — UI

**Locked:** Extend `NeuralAudioRenderPanel` with a **Stems** section (checklist, render set, per-stem rerender/download, sync + fidelity banners, soft-stale vs live snapshot fingerprint). Keep mix enqueue path.

## Audit Summary (current state)

### What already exists (reuse)

| Area | Path | Stem reuse |
|------|------|------------|
| Mix job schemas | `neural_audio_schemas.py` | Extend; do not break mix DTOs |
| Orchestration | `services/neural_audio_render.py` | Pattern for enqueue/run/fingerprint gate |
| Adapters | `services/neural_audio_adapters.py` | Run on sliced composition |
| Store | `services/neural_audio_render_store.py` | Quotas, write bytes, path confinement |
| Router | `routers/neural_audio.py` | Add stem-set routes beside mix |
| Fake engine | `ai_runtime/runtimes/fake_neural_audio.py` | Extend for `direct_stems` multi-WAV |
| MusicGen sidecar | `ai_runtime/runtimes/sidecar_musicgen.py` | Per/group only |
| MIDI-DDSP | `ai_runtime/runtimes/local_midi_ddsp.py` | Per/group midi_projection |
| FluidSynth | `services/composition_wav.py` | Deterministic stem path |
| MIDI projection | `services/composition_midi.py` | Feed sliced V2 |
| Snapshot FP | `composition_snapshot_encoding.py` | Pin + stale |
| FE panel | `NeuralAudioRenderPanel.jsx` / `neuralAudioRenderUi.js` | Stem section + disclaimers |
| Soft-stale | `compositionSnapshotFingerprint.js` | Same compare for stem sets |

### Gaps (this plan closes)

| Gap | Impact |
|-----|--------|
| Single mix WAV only | Cannot manage piano/bass/strings independently |
| No stem capability advertisement | Cannot choose adapter path honestly |
| No track/bar composition slice helper | Per-track / section renders ad hoc |
| No stem-set tables / FS layout | No durable per-stem provenance |
| No selective rerender API | AC “rerender only strings” fails |
| No explicit FluidSynth stem path | Req 7 unmet without silent mix export |
| UI is mix-only | No stem management |
| Docs lack stem sync honesty | Risk of overclaiming sample accuracy |

## Commit Plan

- **Commit 1** (tasks 1–3): `feat(neural-stems): schemas, Alembic, slice + capability adapters`
- **Commit 2** (tasks 4–6): `feat(neural-stems): stem-set jobs, FluidSynth path, selective rerender API`
- **Commit 3** (tasks 7–8): `feat(neural-stems): stem management UI + soft-stale`
- **Commit 4** (tasks 9–10): `test(neural-stems): coverage + docs + roadmap milestone`
- **Commit 5** (tasks 11–13): `fix(neural-stems): honest direct_stems + test gaps + stem-set poll`

## Tasks

### Phase 1: Contracts & capabilities

- [x] Task 1: Define stem-set / stem DTOs + error codes
  Deliverable: Pydantic schemas (`extra="forbid"`) for `neural_audio_stem_set.v1`, `neural_audio_stem.v1`, timeline_sync, stem roles, sync_class, enqueue/rerender requests, list responses. Extend error code registry. FE JSDoc/constants mirrors for unit tests.
  LOGGING: DEBUG schema accept/reject field keys only (never prompts/PCM/events).
  Files: `backend/app/neural_audio_schemas.py`, optional `frontend/src/utils/neuralAudioStemUi.js`

- [x] Task 2: Alembic + stem store helpers
  Deliverable: Migration `20260924_0007_neural_audio_stems` creating `neural_audio_stem_sets` + `neural_audio_stems` with CHECKs/indexes; store allocate/insert/update/list/delete/write under `NEURAL_AUDIO_RENDER_ROOT` path `{project}/{set_id}/{stem_id}.wav`; project-delete GC extension.
  LOGGING: INFO migration; INFO write with stem_id, byte_size, sha256_prefix; never PCM.
  Files: `backend/app/db/alembic/versions/20260924_0007_neural_audio_stems.py`, `backend/app/services/neural_audio_stem_store.py` (or extend render_store)

- [x] Task 3: Capability matrix + composition slice + adapter routing
  Deliverable: Pure helpers `filter_composition_tracks` / `filter_composition_bar_range`; partition heuristic; per-model `stem_capabilities` on descriptors; fake `direct_stems` multi-WAV; route capability→adapter without silent FluidSynth.
  LOGGING: INFO capability_used, track_count, bar_range, stem_role; never event arrays.
  Files: `backend/app/services/neural_audio_stem_partition.py`, `backend/app/services/neural_audio_adapters.py`, `backend/app/ai_runtime/runtimes/fake_neural_audio.py`, `sidecar_musicgen.py`, `local_midi_ddsp.py`, `ai_runtime/types.py` / models DTO enrichment

### Phase 2: Jobs & API

- [x] Task 4: Stem-set orchestration
  Deliverable: `enqueue_stem_set` / `run_stem_set` / fingerprint before+after gate; never write composition; status aggregation (set complete when all members complete, or failed if any fail — document: fail-fast per stem with set status `failed` if any stem failed, members keep individual status).
  LOGGING: INFO set_id, stem_id, role, status, duration_ms, sha256_prefix, fingerprint_prefix.
  Files: `backend/app/services/neural_audio_stems.py` (or extend `neural_audio_render.py`)

- [x] Task 5: Explicit FluidSynth stem path
  Deliverable: When `engine=fluidsynth` (or deterministic stem request), render sliced V2 via MIDI→FluidSynth; 503 if unavailable; never use as silent neural fallback.
  LOGGING: WARN unavailable; INFO deterministic stem complete.
  Files: `backend/app/services/neural_audio_stems.py`, reuse `composition_wav.py`

- [x] Task 6: Router + selective rerender + model discovery
  Deliverable: Routes per API surface below; rerender creates superseding stem; enrich `GET /ai/models?operation=audio_render` with `stem_capabilities`.
  LOGGING: INFO method/path, set_id/stem_id, HTTP status; sanitize error details.
  Files: `backend/app/routers/neural_audio.py`, `backend/app/routers/ai_models.py` (or models response builder), `backend/app/main.py` (router already mounted)

### Phase 3: UI

- [x] Task 7: Stem management UI + API client
  Deliverable: Stems section — role/track checklist, Render stems, per-stem Download/Rerender, sync_class + fidelity banners, disable punch-in claims; wire `musicApi.js`.
  LOGGING: `appLogger('neuralAudioStems')` phase transitions only.
  Files: `frontend/src/components/NeuralAudioRenderPanel.jsx`, `frontend/src/utils/neuralAudioStemUi.js`, `frontend/src/api/musicApi.js`

- [x] Task 8: Soft-stale stem sets
  Deliverable: Compare set/member `source_fingerprint` to live snapshot fingerprint; stale banner; rerender enqueues new stem member only (does not delete siblings).
  LOGGING: DEBUG stale compare prefixes only.
  Files: `frontend/src/utils/neuralAudioStemUi.js`, `compositionSnapshotFingerprint.js` reuse

### Phase 4: Tests & docs

- [x] Task 9: Tests
  Deliverable: Backend `tests/test_neural_audio_stems.py` — schema accept/reject, partition/filter helpers, fake multi-stem set + selective strings rerender (siblings + V2 unchanged), `/ai/models` `stem_capabilities`, no-PCM responses. Frontend unit tests for sync/fidelity copy. Capability 422 / section symbolic_filter / FluidSynth skip-if-missing / distinct `direct_stems` bytes deferred to Task 12. Playwright e2e smoke is optional and **not** a v1 deliverable.
  LOGGING: N/A (assert log hygiene in services via existing patterns).
  Files: `backend/tests/test_neural_audio_stems.py`, `frontend/src/utils/neuralAudioStemUi.test.js`

- [x] Task 10: Docs + licensing + ROADMAP
  Deliverable: Update `docs/neural-audio-rendering.md` (stems API, capabilities, sync honesty, licensing note that stem paths inherit engine licenses); `AGENTS.md` pointer; ROADMAP milestone present (already checked — do not re-add).
  LOGGING: N/A.
  Files: `docs/neural-audio-rendering.md`, `AGENTS.md`, `.ai-factory/ROADMAP.md`

### Phase 5: Gaps close-out (post-improve)

- [x] Task 11: Honest `direct_stems` fake + orchestration path
  Depends on: Task 3 (descriptor/capability matrix), Task 4 (run_stem_set / `_run_one_stem`)
  Deliverable: Fake engine exposes a true multi-stem call (one invocation → role→WAV map; bytes vary by `stem_role` + seed/fingerprint — not identical siblings). When `capability_used=direct_stems`, `run_stem_set` consumes that map once and writes members; when the engine only supports per/group, label `per_track` / `grouped_tracks` honestly (never stamp `direct_stems` on a per-stem single `render`). No silent FluidSynth.
  LOGGING: INFO capability_used, stem_role count, sha256_prefix per role; never PCM/prompts/events.
  Files: `backend/app/ai_runtime/runtimes/fake_neural_audio.py`, `backend/app/services/neural_audio_stems.py`

- [x] Task 12: Close stem test gaps (capability / section / FluidSynth / honesty)
  Depends on: Task 11
  Deliverable: Extend `backend/tests/test_neural_audio_stems.py` — `stem_capability_unsupported` (and/or `section_render_unsupported`) → 422; `bar_range` uses `section_symbolic_filter` and filters events outside the window; FluidSynth stem path with `pytest.mark.skipif` when bin/SF2 missing (503 when forced unavailable); assert distinct fake stem bytes across roles and honest `capability_used` for `direct_stems` vs `per_track`.
  LOGGING: N/A.
  Files: `backend/tests/test_neural_audio_stems.py`

- [x] Task 13: Poll incomplete stem sets in UI
  Depends on: Task 7
  Deliverable: When any stem set (or member) is `queued`/`running`, poll `getNeuralAudioStemSet` on an interval (parity with mix `getNeuralAudioRender`); update list in place; stop when idle. Phase-transition logs only via `appLogger('neuralAudioStems')`.
  LOGGING: DEBUG poll status / set_id prefixes only.
  Files: `frontend/src/components/NeuralAudioRenderPanel.jsx`, `frontend/src/api/musicApi.js` (reuse existing get client)

## API surface (v1)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/neural-audio/stem-sets` | Enqueue stem set |
| `GET` | `/neural-audio/stem-sets/{id}` | Set + members (no PCM) |
| `GET` | `/neural-audio/stem-sets?project_id=` | List |
| `POST` | `/neural-audio/stem-sets/{id}/stems/{stem_id}/rerender` | Selective stem rerender |
| `GET` | `/neural-audio/stems/{id}/audio` | Download when complete |
| `DELETE` | `/neural-audio/stem-sets/{id}` | Delete set + member files |
| `GET` | `/ai/models?operation=audio_render` | Include `stem_capabilities` |

Error codes (add): `stem_capability_unsupported`, `stem_set_not_found`, `stem_not_found`, `section_render_unsupported`, `stem_partition_empty`.

## Coupling risks

1. Mutating V2 / snapshots / autosave on stem complete
2. Silent FluidSynth fallback when neural fails
3. Claiming sample-accurate sibling sync for MusicGen stems
4. Confusing recovery `stem_bindings` with durable neural stem assets
5. Overloading mix `audio_relpath` without migration
6. Logging full prompts, PCM, or event arrays
7. Blocking CI without fake multi-stem path
8. Putting stem ids on playable note events

## Verification

```bash
cd backend && NEURAL_AUDIO_FAKE_MODE=1 pytest tests/test_neural_audio_*.py -q
node --test frontend/src/utils/neuralAudioRenderUi.test.js frontend/src/utils/neuralAudioStemUi.test.js
```

AC manual: generate piano/bass/strings stem set → confirm distinct stem downloads → rerender strings only → confirm V2 JSON unchanged and prior piano/bass stems still downloadable. After Task 13: leave a stem set in `running`/`queued` (or force slow path) and confirm the Stems list refreshes without remount.
