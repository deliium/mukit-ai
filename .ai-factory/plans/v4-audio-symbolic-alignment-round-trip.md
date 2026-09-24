# Implementation Plan: V4 Audio-Symbolic Alignment and Round-Trip Editing

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-24
Improved: 2026-09-24 (`/aif-improve` — lock AC to recovery Bind only; require Alembic for `alignment_json`; project-bound discovery + hydrate; stem bindings = role→track only; snapshot fingerprint for stale renders; piano-roll-first (no OSMD cursor hooks); lineage without fake job JSON column; drop meta Task 12; mono durable seek → follow-on)
Planning depth: final, ultra-thorough

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: introduce a first-class **audio↔symbolic alignment** representation and **round-trip editing** loop on top of shipped V4 audio recovery + neural audio egress — map composition ticks/bars ↔ source-audio timestamps (and stem **roles** where available); synchronize waveform audition with piano roll / playback cursor; bar-range selection → audio time window; invalidate dependent neural renders when audio-derived symbolic material changes; **never** silently modify original source audio; preserve provenance across original audio → recovery revision → edited composition → generated render; publish alignment quality/confidence metadata — **without** putting alignment/confidence on playable `composition.v2` note events, **without** inventing a new `playbackSource` / Tone Transport owner for source WAV, **without** inventing `composition.v4`, and **without** writing to `DATASET_ROOT`
- Parent plans / shipped foundations:
  - `.ai-factory/plans/v4-audio-to-symbolic-workflow.md` (**shipped** — mixed recovery jobs, Bind durable source + `audio.recovery.result.v1` overlay, scaffolding/`beat_grid`, HTMLAudio vs Tone ownership lock)
  - `.ai-factory/plans/audio-to-symbolic-musical-input.md` (**shipped** — mono `transcription.preview.v1`; seconds→ticks alignment helpers; ephemeral audio — **no durable source for seek**)
  - `.ai-factory/plans/optional-neural-audio-rendering.md` (**shipped** — `/neural-audio/renders` jobs pin `source_fingerprint`; never mutates V2; never overwrites ingress assets)
  - `.ai-factory/plans/expressive-v2-playback-and-mixing.md` (**shipped** — Tone Transport; `playbackSource`; `tickToSeconds` / `compileTimeline`)
  - `.ai-factory/plans/upgrade-v2-music-editing-workflow.md` (**shipped** — bar/selection ranges, edit cursor, play-from-cursor)
  - `.ai-factory/plans/interactive-harmony-reharmonization.md` (**shipped** — harmony tick spans; Apply fingerprint gate — exemplar for “edit bars 9–12 then continue”)
  - `.ai-factory/plans/safe-ai-experimentation-and-versioning.md` (**shipped** — revisions/CAS fingerprints for provenance anchors)
  - `.ai-factory/plans/daw-interop-v3-e2e-hardening.md` (**shipped** — `generation.provenance.v1` secret-safe fragment pattern)

## Roadmap Linkage
Milestone: "V4 audio-symbolic alignment and round-trip editing" *(proposed — ROADMAP currently has all items checked, including recovery; `/aif-implement` docs checkpoint or `/aif-roadmap` must add this unchecked milestone)*
Rationale: Recovery delivers durable source audio + symbolic overlay; this milestone closes the **correspondence and round-trip** product loop — seek/scrub by bars, honest stale renders after symbolic edits, and provenance that ties original recording through edits to a **new** render without mutating the source WAV.

## Goal

After **recovery Bind** (durable source audio + scaffolding/result), keep a trustworthy mapping between **source-audio time** and **canonical symbolic time** so the user can:

1. Select bar 10 (or bars 9–12) on the piano roll and **seek** the corresponding source-audio location (including after project reopen).
2. Audition a **waveform** playhead synchronized with the piano-roll playhead / edit cursor (without fighting Tone `playbackSource` ownership). OSMD notation follow is **out of v1 AC** (no cursor hooks exist today).
3. Edit symbolic material (e.g. reharmonize bars 9–12), then create a **new** neural render of the changed composition while the **original recording remains unmodified**.
4. See **alignment quality/confidence** and a **provenance chain** linking original audio → recovery result → alignment → edited composition fingerprint/revision → generated render.

```text
Bound source audio + recovery scaffolding/result
        │
Build / refresh audio.alignment.v1 (Bind + Alembic kind=alignment_json)
  ├─ ticks/bars ↔ source seconds (downbeat offset + V2 timeline)
  ├─ optional stem_bindings: role → track_id (not durable stem WAVs)
  └─ quality/confidence (+ method)
        │
Project reopen: GET …/bound → hydrate source URL + alignment + overlay
        │
FE sync clock (HTMLAudio timeupdate ↔ tick playhead)
  ├─ piano-roll source playhead + edit cursor
  └─ bar-range → audio window seek / waveform highlight
        │
Symbolic edits change live snapshot fingerprint
        │
Dependent neural renders marked stale (source_fingerprint ≠ live/working snapshot fp)
        │
Enqueue NEW render from edited V2  ──►  never overwrite source_audio asset
        │
Provenance: source_audio → recovery_result → alignment → composition_fp/revision → render_id
```

**Acceptance one-liner:** After recovery Bind, selecting bar 10 seeks the corresponding source-audio location (reload-safe); editing the composition and re-rendering produces a new render job without mutating the original recording.

**AC lock:** Waveform / bar-seek / alignment durability require **recovery Bind**. V3 mono Apply (ephemeral audio deleted) does **not** satisfy seek AC in this plan.

## Terminology lock

| Term | Meaning |
|------|---------|
| **Alignment** | Bidirectional map between composition ticks/bars and source-audio timestamps (plus optional stem-role→track refs); non-playable DTO |
| **`audio.alignment.v1`** | Versioned alignment document (session + durable after Bind) with quality metadata |
| **Alignment quality** | Confidence / uncertainty / method describing how trustworthy the map is (never a V2 note field) |
| **Source audio asset** | Immutable project-related WAV under `AUDIO_RECOVERY_ASSET_ROOT` after Bind — **read-only**; never rewritten by render or edit |
| **Stem binding (v1)** | `stem` role → `track_id` (and optional overlay provisional linkage) — **not** a durable separated stem WAV asset (workdir stems remain GC’d with unbound jobs) |
| **Bound discovery** | Project-scoped API returning latest bound `source_audio_asset_id`, `result_asset_id`, `alignment_asset_id` for hydrate |
| **Sync clock** | FE helper converting `HTMLAudio.currentTime` ↔ ticks via alignment + `compileTimeline` / scaffolding |
| **Source audition mode** | HTMLAudio (or peaks scrubber) driven by alignment — **not** a `playbackSource` |
| **Composition audition mode** | Existing PlaybackControls / Tone Transport / `playbackSource` — unchanged ownership |
| **Source playhead** | Piano-roll overlay cursor driven by `sourcePlayheadTick` (distinct from Tone `PlaybackCursor`) |
| **Dependent render** | Neural-audio job whose `source_fingerprint` was computed from a composition that later diverged |
| **Stale render** | Completed render whose pinned **`composition.snapshot.v1`** fingerprint ≠ live/working snapshot fingerprint — UI marks stale; asset remains downloadable |
| **Round-trip** | audio demo → recover → Bind → edit symbolic → **new** render of edited composition (source WAV untouched) |
| **Round-trip provenance** | Durable lineage object linking asset ids + fingerprints across the round-trip without embedding PCM or event arrays |
| **Recovery scaffolding** | Existing tempo / `beat_grid` / structure — **input** to alignment; not replaced by this plan |
| **Confidence overlay** | Existing `audio.recovery.result.v1` event_id→confidence map — remains separate from alignment |

## Non-goals (v1)

- Perfect beat-tracking / SMPTE / DAW clip warping product
- Mutating or “punching in” on the original source WAV
- Auto-deleting prior neural renders when composition changes
- New Tone `playbackSource` owner for source audio
- Putting alignment, confidence, or stem ids on `CompositionV2NoteEvent` (`extra="forbid"`)
- Inventing `composition.v4`
- Writing to `DATASET_ROOT` / auto corpus ingest
- Real-time AI Jam / co-performance transport takeover
- Cloud vendor forced-alignment APIs as the default path
- Full multi-track DAW waveform editor / take lanes
- Replacing FluidSynth export or OSMD with neural audio
- Mandatory GPU sidecars for AC (fake fixtures + bound assets suffice)
- Rebuilding V4 recovery or mono transcription pipelines
- **Durable mono-transcription source audio / hum→Apply bar-seek** (mono remains ephemeral; follow-on if product wants parity)
- **OSMD / notation cursor follow** for source audition (no hooks today; document piano-roll-first)
- **Durable Bind of separated stem WAVs** (v1 stem bindings are role→track only)

## Approach Evaluation (locked)

### Part A — Where the alignment representation lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Embed map fields on V2 notes / tracks** | Convenient UI | Violates `extra="forbid"`; pollutes export/MIDI | **Reject** |
| **B. Ephemeral FE-only map from scaffolding** | Cheap | Fails reload, multi-client, provenance AC | **Reject** as sole path |
| **C. Versioned `audio.alignment.v1` sibling asset (`kind=alignment_json`)** | Honest; durable after Bind; keeps `audio.recovery.result.v1` unpolluted (`extra="forbid"`) | **Requires Alembic CHECK + allowlist changes** | **Accepted** |

**Locked:** Introduce `audio.alignment.v1` with: origin (`source_audio_asset_id`), composition fingerprint at build time (`composition.snapshot.v1`), parametric map inputs (tempo + `downbeat_offset_seconds` + timeline), optional `stem_bindings[]` (`stem` → `track_id` only — **no** durable stem asset ids unless a later milestone binds stem WAVs), and `quality{…}`. Persist as sibling `kind=alignment_json` under `AUDIO_RECOVERY_ASSET_ROOT`. Pointer `alignment_asset_id` on Bind response + jobs row. **Do not** nest into `AudioRecoveryResultV1` without a versioned field bump (prefer sibling).

### Part A2 — Project reopen / discovery (new lock)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Expect FE to remember asset ids across reload** | No API | `hydrateProject` clears recovery state today | **Reject** |
| **B. Project-scoped bound discovery + hydrate** | Reload-safe AC | Small new route | **Accepted** |

**Locked:** Add `GET /audio-recovery/projects/{project_id}/bound` (or equivalent list of bound jobs → asset ids). On `openProject` / `hydrateProject`, after clear, **reload** bound source blob URL + result overlay + alignment when discovery returns ids. Expose store `list_project_jobs` (already exists, not HTTP-wired).

### Part B — Mapping model (parametric vs dense anchors)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Dense per-bar anchors always** | Flexible for warped audio | Heavy; overfit for demo-length constant-tempo | **Defer** as optional extension |
| **B. Parametric: tempo + meter timeline + downbeat offset** | Matches shipped scaffolding + `compileTimeline` | Weak if user warps tempo mid-piece vs audio | **Accepted** for v1 |
| **C. User-drawn warp markers only** | Accurate | Fails AC out of box | **Insufficient alone** |

**Locked:** v1 parametric map:
`source_seconds = downbeat_offset_seconds + tickToSeconds(compileTimeline(composition), tick − origin_tick)`
(and inverse). When V2 tempo/meter diverge from scaffolding used at Bind, rebuild alignment and **lower** quality confidence + issue `alignment_tempo_diverged`. Optional sparse user overrides out of scope for v1 AC.

### Part C — Waveform + playhead sync ownership

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Drive source WAV from Tone Transport** | One clock | Fights `playbackSource`; sample skew; parent plan forbid | **Reject** |
| **B. Dual clocks with explicit mode + bridge helpers** | Honors HTMLAudio vs Tone lock; seekable | Two playheads possible if miswired | **Accepted** |
| **C. Waveform-only panel, no piano-roll link** | Small | Fails AC (bar 10 seek + sync) | **Reject** |

**Locked:**
- **Source audition:** HTMLAudio (+ peaks canvas) owned by recovery/alignment UI; `timeupdate` → `sourcePlayheadTick` via sync clock; mirror into piano-roll **source playhead** overlay (separate from Tone `PlaybackCursor`).
- **Composition audition:** unchanged PlaybackControls / Tone — optional “follow source while playing score” default **off**.
- **Notation:** document piano-roll-first; do **not** invent OSMD cursor APIs in v1.
- Do **not** register a new `playbackSource`. Note: shipped recovery HTMLAudio is **not** already tick-synced — that work is this plan.

### Part D — Bar range → audio window

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Pure helpers over alignment + `selectedTickBoundaries`** | Testable; reuses editor selection | Needs wired UI | **Accepted** |
| **B. Server round-trip for every seek** | Centralized | Latency; unnecessary | **Reject** |

**Locked:** FE (+ mirrored BE for tests) pure functions:
`tickToSourceSeconds`, `sourceSecondsToTick`, `barRangeToAudioWindow(startBar, endBar, composition, alignment)`.
Wire side effects at `setEditCursorTick` / `gotoBar` / bar selection (gated on alignment present + phase exclusion). Highlight waveform region for multi-bar selection.

### Part E — Invalidate dependent renders

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Auto-delete stale renders** | Disk tidy | Surprises users; breaks provenance | **Reject** |
| **B. Soft stale flag in UI + optional BE list enrichment** | Safe; downloadable history | User must understand “stale” | **Accepted** |
| **C. Block all edits until re-render** | Forces sync | Hostile UX | **Reject** |

**Locked:** Compare neural job `source_fingerprint` to **live `composition.snapshot.v1` fingerprint** of current `editedMusicJson` (fallback: store `workingFingerprint` after flush). **Never** use FE `fingerprintCompositionOrNull` / `composition.edit.v1` or analysis fingerprints. UI: stale banner + “Render again” enqueues a **new** job. Never rewrite `source_audio` assets.

**Lineage storage lock:** Neural table has **no** generic metadata JSON column (`adapter_warnings_json` is wrong). Prefer: (1) Bind response + `audio.roundtrip.provenance.v1` on revision `summary_json` / FE session, and/or (2) explicit Alembic `lineage_json` or discrete id columns on neural jobs if we must list lineage from BE. **Do not** claim “JSON details without migration.”

### Part F — Round-trip provenance

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only implicit (asset ids scattered)** | No new schema | Hard to audit AC #7 | **Reject** |
| **B. `audio.roundtrip.provenance.v1` fragment** | Explicit chain; secret-safe | Small new DTO | **Accepted** |
| **C. Embed full event arrays / PCM hashes only** | Overkill | Bloat; secret-guard risk | **Reject** |

**Locked:** Compact provenance DTO (ids + sha256_prefixes + fingerprints + timestamps + schema versions). Attach to Bind response, optional neural enqueue (only if columns exist after migration), and/or revision `summary_json` via secret-safe allow-list (mirror `generation.provenance.v1` — never prompts, never PCM, never full overlays).

### Part G — Alignment quality metadata

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Reuse scaffolding confidences only** | No new fields | Weak “alignment quality” product signal | **Insufficient alone** |
| **B. Explicit `quality` object on `audio.alignment.v1`** | Clear UI + tests | Slight schema growth | **Accepted** |

**Locked `quality` fields (v1):**
- `overall_confidence` (0..1)
- `tempo_confidence`, `beat_grid_confidence` (copied/derived)
- `offset_uncertainty_ms` (estimated)
- `method`: `scaffolding` \| `timeline_parametric` \| `defaulted` \| `user_adjusted` (user_adjusted reserved / unused in v1 UI)
- `issues[]` (codes only; e.g. `alignment_low_confidence`, `alignment_tempo_diverged`, `alignment_missing_source`)
- Optional per-stem-role `stem_qualities[]` (roles only)

## Audit Summary (current state)

### What already exists (reuse — do not rebuild)

| Area | Path | Alignment/round-trip reuse |
|------|------|----------------------------|
| Seconds→ticks (mono) | `services/audio_transcription/alignment.py` | Extend with inverse + shared tests; do not fork conflicting formulas |
| V2 timeline | `composition_timeline.py` / `compositionTimeline.js` (`tickToSeconds` / `secondsToTick`) | Parametric conversion spine |
| Scaffolding / beat_grid | `AudioRecoveryScaffolding`, `AudioRecoveryBeatGrid` | Inputs to alignment build |
| Durable source + overlay | Bind + `audio.recovery.result.v1` | Origin for alignment; keep overlay separate |
| HTMLAudio element | `AudioRecoveryPanel.jsx` (`<audio controls>` + blob URL post-Bind) | Add `timeupdate` sync + seek — **not** already tick-synced |
| Asset GET by id | `GET /audio-recovery/assets/{id}` | Reuse once discovery provides ids |
| Store `list_project_jobs` | `audio_recovery_store.py` | Expose via HTTP for reopen |
| Bar selection | `pianoRollSelection.js` `selectedTickBoundaries` | Bar→tick window |
| Edit cursor / gotoBar | `musicStore.js` | Seek side-effect hooks |
| Tone playhead | `PianoRollOverlayLayer` `PlaybackCursor` | Keep for composition audition; add separate source playhead |
| Neural fingerprint pin | `NeuralAudioJobResponse.source_fingerprint` (`composition.snapshot.v1`) | Stale detection (FE currently ignores) |
| Snapshot fingerprint BE | `composition_snapshot_encoding.composition_snapshot_fingerprint` | Mirror or call for live dirty compare |
| Provenance pattern | `generation_provenance.py` + secret guard | Template for round-trip fragment |
| Project-delete GC | `cleanup_project_audio_recovery` FS rmtree | Extends naturally once new kind rows exist |
| Fake modes | `AUDIO_RECOVERY_FAKE_MODE`, `NEURAL_AUDIO_FAKE_MODE` | AC without GPU |

### Gaps (this plan closes)

| Gap | Impact |
|-----|--------|
| No `audio.alignment.v1` + CHECK forbids new asset kind | Must Alembic + allowlists |
| No project-bound discovery; hydrate clears recovery | Reload seek/waveform impossible |
| No inverse ticks↔source-seconds helper with offset | Seek math inconsistent |
| HTMLAudio not tick-synced; no waveform | Fails sync AC |
| No `sourcePlayheadTick` overlay path | Cannot mirror source audition on piano roll |
| Neural panel ignores `source_fingerprint` | No stale UX |
| No snapshot fingerprint helper on FE | Dirty edit.v1 fingerprint would mis-stale |
| No explicit round-trip provenance chain | AC #7 weak |
| Mono Apply has no durable audio | AC must not claim mono seek |

## Commit Plan

- **Commit 1** (tasks 1–4): `feat(audio-align): schemas, map math, Bind+Alembic, bound discovery`
- **Commit 2** (tasks 5–7): `feat(audio-align): sync clock, waveform, bar-range seek`
- **Commit 3** (tasks 8–9): `feat(audio-align): stale renders, snapshot fp, provenance`
- **Commit 4** (tasks 10–11): `test(audio-align): round-trip e2e + docs + roadmap`

## Tasks

### Phase 1: Contracts, persistence, reopen

- [x] Task 1: Define `audio.alignment.v1` + quality + bind/list DTOs
  Deliverable: Pydantic schemas (`extra="forbid"`) for alignment document, quality block, `stem_bindings[]` as **`stem` role → `track_id` only** (document: no durable stem WAV asset ids in v1), issue codes, `audio.roundtrip.provenance.v1` fragment. Error codes for missing source / rebuild failure. Extend Bind response with `alignment_asset_id`. FE mirror constants/JSDoc as needed for unit tests.
  LOGGING: DEBUG schema accept/reject field counts; INFO alignment build method + overall_confidence; never log full maps with large arrays at INFO (cap DEBUG list lengths).
  Files: `backend/app/audio_alignment_schemas.py` (new), thin wiring in `audio_recovery_schemas.py` (`AudioRecoveryBindResponseV1`, `AudioRecoveryAssetMeta.kind`), tests `test_audio_alignment_schemas.py`.

- [x] Task 2: Pure bidirectional map builders (BE + FE)
  Deliverable: Shared algorithm: build parametric alignment from scaffolding + composition timeline; `tick_to_source_seconds` / `source_seconds_to_tick`; `bar_range_to_audio_window`; quality derivation + `alignment_tempo_diverged` when V2 tempo ≠ scaffolding tempo beyond epsilon. Mirror helpers in `frontend/src/utils/audioAlignment.js` (+ tests) and `backend/app/services/audio_alignment.py`. Golden vectors for fixed tempo 120 BPM / PPQ 480 / offset 0.5s / bar 10.
  LOGGING: INFO build summary `{method, overall_confidence, bar_count, source_asset_prefix}`; WARN diverged tempo; DEBUG sample (tick, seconds) pairs (≤5).
  Files: `services/audio_alignment.py`, `frontend/src/utils/audioAlignment.js`, `*.test.*`.

- [x] Task 3: Persist alignment on Bind + required Alembic (depends on 1, 2)
  Deliverable: **Required** Alembic migration: widen `audio_recovery_assets.kind` CHECK to include `alignment_json`; add `audio_recovery_jobs.alignment_asset_id`; update `write_durable_asset_bytes` allowlist + Pydantic Literal. On successful Bind, build alignment from scaffolding + applied composition fingerprint, write sibling asset, return `alignment_asset_id`; idempotent re-bind returns same ids; **never** mutate source WAV bytes; GC already FS-cascades project dir — add tests that new kind rows are cleaned. Do **not** nest into `AudioRecoveryResultV1` (`extra="forbid"`).
  LOGGING: INFO bind alignment write `{asset_id_prefix, byte_size, quality}`; ERROR path confinement failures; WARN rebuild-on-edit.
  Files: new Alembic under `backend/app/db/alembic/versions/`, `audio_recovery_store.py`, `audio_recovery/pipeline.py` bind path, store/pipeline tests.

- [x] Task 4: Project-bound discovery + hydrate on reopen (depends on 3)
  Deliverable: HTTP `GET /audio-recovery/projects/{project_id}/bound` (or list bound jobs) returning `source_audio_asset_id`, `result_asset_id`, `alignment_asset_id`, job id, sha prefixes — wire existing `list_project_jobs`. FE: after `hydrateProject` clears recovery state, call discovery when `project_id` present; refetch blob URL + result JSON + alignment; restore overlay + alignmentDocument. Tests: reopen restores seekable source without re-Bind; missing bound → idle.
  LOGGING: INFO discovery hit/miss `{project_id_prefix, bound}`; WARN hydrate asset fetch failures with codes only.
  Files: `routers/audio_recovery.py`, schemas, `musicApi.js`, `musicStore.js` hydrate path (`~12435` / `clearedAudioRecoveryState`), backend+FE tests.

### Phase 2: Sync clock, waveform, selection seek

- [x] Task 5: Alignment sync clock + store slice (depends on 2, 3, 4)
  Deliverable: FE state: `alignmentDocument`, `alignmentAssetId`, `sourceAuditionMode` (`idle`\|`playing`\|`scrubbing`), `sourcePlayheadTick`, `audioWindowHighlight`. Actions: load alignment after Bind **and** after Task 4 hydrate; HTMLAudio `timeupdate` → tick via Task 2 helpers; seek from tick/bar; clear on project switch before re-hydrate. Phase exclusion: reuse recovery guards vs MIDI/live/mono record; document mutual clarity vs Tone play (still no new `playbackSource`). Note baseline: current panel has no tick sync.
  LOGGING: INFO mode transitions; DEBUG seek `{tick, seconds}` (rate-limit); WARN seek without alignment.
  Files: `musicStore.js` slice, `musicStore.audioAlignment.test.js`, `AudioRecoveryPanel.jsx` (`timeupdate` / seek), `musicApi.js`.

- [x] Task 6: Lightweight waveform peaks + scrubber UI (depends on 5)
  Deliverable: Decode bound source blob (AudioBuffer) → downsampled peaks (cap points); canvas/SVG waveform with playhead + selection highlight; scrub updates HTMLAudio + tick playhead via alignment. Prefer client decode of already-fetched blob URL — no new unbounded upload. Do not treat `audioRecorder.js` decode as waveform UI. Document memory caps.
  LOGGING: INFO peaks build `{duration_s, peak_count}`; WARN decode failure codes only (no PCM dump).
  Files: `frontend/src/utils/audioWaveformPeaks.js`, `AudioAlignmentWaveform.jsx` (or panel sibling), unit tests for peak downsample.

- [x] Task 7: Wire piano-roll / bar selection seek (depends on 2, 5, 6)
  Deliverable: Selecting bar 10 (edit cursor / `gotoBar` / selection) seeks source audio to mapped seconds when alignment present; multi-bar selection sets `audioWindowHighlight`; piano-roll overlay adds **source playhead** from `sourcePlayheadTick` in source audition mode (Tone `PlaybackCursor` remains composition-audition-only). **OSMD / NotationViewer:** document piano-roll-first — **no** notation cursor work in this task. Do **not** add `playbackSource`.
  LOGGING: UI namespace `audioAlignment`; INFO seek-from-bar `{bar, seconds}`; no event array dumps.
  Files: `AudioRecoveryPanel.jsx`, `PianoRollOverlayLayer.jsx`, selection bridge util, `setEditCursorTick`/`gotoBar` side effects (gated), component tests.

### Phase 3: Round-trip invalidation and provenance

- [x] Task 8: Dependent neural-render staleness + snapshot fingerprint (depends on 3)
  Deliverable: Add FE (or shared) **`composition.snapshot.v1` fingerprint** helper for live `editedMusicJson` — do **not** use `fingerprintCompositionOrNull` / edit.v1. `NeuralAudioRenderPanel` compares job `source_fingerprint` to live snapshot fp (fallback `workingFingerprint` after autosave flush); mark stale; CTA “Render again” enqueues **new** job from current composition; assert store/API never PATCH source audio asset; prove source `sha256_prefix` unchanged. Lineage: attach via provenance fragment / Bind ids in UI — **or** Alembic neural `lineage_json` if product requires BE list fields; **no** stuffing into `adapter_warnings_json`.
  LOGGING: INFO stale detect `{job_id_prefix, fp_prefix}`; INFO re-enqueue; never log composition bodies.
  Files: `frontend/src/utils/compositionSnapshotFingerprint.js` (or BE-backed), `NeuralAudioRenderPanel.jsx`, `neuralAudioRenderUi.js`, optional Alembic for neural lineage, FE/BE tests.

- [x] Task 9: Round-trip provenance fragment + UI summary (depends on 1, 3, 8)
  Deliverable: Build/attach `audio.roundtrip.provenance.v1` across Bind and (where storage allows) neural enqueue/list; secret-guard; small UI “Provenance” readout (id prefixes only). Chain: original audio → recovery result → alignment → composition fingerprint/revision → render id.
  LOGGING: INFO provenance attach stage keys only; `assert_no_secret_fields`.
  Files: `services/audio_roundtrip_provenance.py`, bind + optional neural wiring, tests, panel snippet.

- [x] Task 10: Round-trip e2e / integration AC proof (depends on 4, 7, 8, 9)
  Deliverable: Playwright under `AUDIO_RECOVERY_FAKE_MODE` + `NEURAL_AUDIO_FAKE_MODE` **or** equivalent API+FE integration suite: Bind fixture → (optional reopen hydrate) → select bar 10 seeks audio → edit bars 9–12 (harmony or notes) → fingerprint changes → prior render stale → new render job succeeds → source asset sha unchanged. Manual checklist lives in docs (Task 11), not a separate task.
  LOGGING: Quiet e2e; document observation points.
  Files: `frontend/e2e/audio-alignment-roundtrip.spec.js` and/or backend+FE integration tests, fixture hooks.

### Phase 4: Docs and roadmap

- [x] Task 11: Documentation + AGENTS/DESCRIPTION/ROADMAP (depends on 1–10 for accuracy)
  Deliverable: New `docs/audio-symbolic-alignment.md` — map model, quality fields, HTMLAudio vs Tone, dual playheads, bar→seek, **AC = recovery Bind only**, mono seek OOS, reopen discovery, stale renders (snapshot fingerprint), provenance chain, non-mutation of source, stem bindings = roles only, piano-roll-first / OSMD deferred, acceptance checklist, logging forbidden payloads. Cross-link `docs/audio-recovery.md`, `docs/neural-audio-rendering.md`, `docs/browser-playback.md`, `docs/audio-transcription.md`, testing. Update ROADMAP with unchecked milestone; AGENTS.md + DESCRIPTION one bullet each; `.env.example` only if new settings appear (prefer reuse `AUDIO_RECOVERY_*`).
  LOGGING: Document namespaces `audio.alignment` / `audioAlignment`.
  Files: docs + ROADMAP + AGENTS + DESCRIPTION.

## Implementation Notes For Agents

- Prefer new `audio_alignment_schemas.py` + `services/audio_alignment.py` over bloating recovery scaffolding estimators.
- Reuse `compileTimeline` / `tickToSeconds` — do not invent a second timeline compiler.
- Sibling `alignment_json` **requires** Alembic CHECK + Python/Pydantic allowlists — do not skip migration.
- Source WAV is **immutable** after Bind; renders are always new egress objects.
- Confidence overlay (`audio.recovery.result.v1`) ≠ alignment document — keep concerns split.
- Stale detection uses **`composition.snapshot.v1` only** — never edit.v1 / analysis fingerprints.
- Do not import jam/`ensureJamRoleTracks` modules.
- Keep UI copy humble: estimated alignment, confidence-gated, stale means “composition moved on.”
- Fake modes must satisfy AC without Demucs/MusicGen sidecars.
- Related plans: parent recovery workflow; neural egress; expressive playback ownership.

## Out-of-Scope Reminders (do not implement in this plan)

- Perfect forced alignment / tempo maps with user warp UI
- Overwriting source recordings or stem WAVs
- Auto-deleting historical neural renders
- New Tone `playbackSource` for HTMLAudio
- Confidence/alignment fields on V2 note events
- `composition.v4` / DATASET_ROOT ingest
- AI Jam transport integration
- Durable mono-transcription source / hum→Apply seek parity
- OSMD notation cursor follow
- Durable Bind of separated stem audio assets
