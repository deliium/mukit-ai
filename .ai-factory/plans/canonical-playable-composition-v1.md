# Implementation Plan: Canonical Playable Composition V1 Data Model

Branch: main
Created: 2026-09-05

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Baseline Findings
- Backend schema lives in `backend/app/schemas.py` as `LLMMusicJson`, with tempo, key, time signature, sections, tracks, harmony, and an early `notes` array using `track`, `staff`, `bar`, `beat`, `pitch`, and quarter-note `duration`.
- Backend LLM generation lives in `backend/app/services/llm_music_generator.py`; it validates directly into `LLMMusicJson` and prompts the LLM to output notes, but the schema has no version field and the timing model is still bar-local.
- MusicXML rendering lives in `backend/app/services/music_json_renderer.py`; piano tracks use explicit notes when present, but non-piano tracks can still synthesize notes from harmony via `_fallback_element_for_track`.
- Browser playback lives in `frontend/src/components/PlaybackControls.jsx`; it plays explicit `notes` when present, otherwise falls back to chord playback derived from `harmony`.
- Frontend JSON validation lives in `frontend/src/components/PromptJsonEditor.jsx`; it only validates shallow shape and does not enforce note-event pitch/timing/velocity/track/section rules.
- Zustand state in `frontend/src/store/musicStore.js` stores generated and edited JSON without normalization metadata or migration warnings.
- Existing tests cover current LLM validation, MusicXML rendering, provider errors, and basic note rendering, but not schema versioning, canonical timing, velocity, polyphony invariance, migration, MIDI/export-readiness, or frontend validation.

## Data Model Decisions
- Introduce a new canonical backend model named `Composition` with `schema_version: "composition.v1"`.
- Keep top-level `tempo`, `key`, `time_signature`, `sections`, `harmony`, and `tracks`.
- Use one canonical timing representation everywhere: integer `tick` offsets and integer `duration_ticks`, measured from composition start in ticks per quarter note.
- Add top-level `ticks_per_quarter`, defaulting to `480`, and validate it as a positive integer. This supports arbitrary bars/time signatures without bar/beat floating point drift.
- Define section boundaries explicitly with `start_bar`, `bar_count`, `start_tick`, and `duration_ticks`; validate that sections are contiguous, non-overlapping, and exactly cover the declared composition duration.
- Define top-level `duration_ticks` and `bar_count`; derive/check them from sections and meter so saved JSON has clear boundaries and can be validated without renderer assumptions.
- Track metadata should include stable `id`, `name`, `instrument`, `role`, `midi_program`, `channel`, `is_drum`, `volume`, `pan`, and optional `staff`/notation metadata.
- Note events live inside each track as `events`, not as a separate top-level loose array, so track assignment is intrinsic and cannot reference a missing track.
- Each note event contains `type: "note"`, `pitch`, `start_tick`, `duration_ticks`, and `velocity`. Optional fields may include `id`, `staff`, `voice`, and notation hints, but renderers and playback must not require them to determine the audible result.
- Simultaneous notes/polyphony are represented by multiple note events with the same `start_tick`, overlapping durations, or distinct voices within the same track.
- Rests are implicit empty tick ranges. No rest events or filler notes are required in generated JSON.
- Harmony remains contextual metadata for chord symbols and analysis only. Once canonical note events exist, harmony must never be used to invent audible notes.

## Compatibility Strategy
- Preserve `/llm/generate-music-json` as the existing statistical generation flow, but normalize its output into `Composition` before rendering and returning when possible.
- Add an explicit migration/normalization module that accepts legacy `LLMMusicJson`-shaped objects and produces `Composition` when enough note data exists.
- Legacy objects with top-level `notes` should migrate by converting `bar` and `beat` into `start_tick` using `time_signature` and `ticks_per_quarter`, converting quarter-note `duration` into `duration_ticks`, preserving pitch, staff, track assignment, and defaulting velocity to a documented value such as `80`.
- Legacy objects without explicit notes may be rejected for canonical playback with an actionable error: `Legacy music JSON has harmony/tracks but no note events; regenerate or add notes before canonical rendering/playback.`
- During a short compatibility window, backend generation may continue to ask the LLM for legacy note fields, but the API response must include normalized canonical composition data or a clear validation error. If changing response shape is unavoidable, document the response contract and update frontend consumers in the same task.

## Commit Plan
- **Commit 1** (after tasks 1-3): `feat: add canonical composition schema and migration`
- **Commit 2** (after tasks 4-6): `feat: render and play canonical note events`
- **Commit 3** (after tasks 7-9): `test: cover composition validation and migration`
- **Commit 4** (after tasks 10-11): `docs: document composition v1 contract`

## Tasks

### Phase 1: Canonical Backend Model
- [x] Task 1: Add canonical composition schemas in `backend/app/schemas.py` or a new focused module such as `backend/app/composition_schema.py`.
  Deliverable: `Composition`, `CompositionSection`, `CompositionTrack`, and `NoteEvent` Pydantic models with `schema_version`, `ticks_per_quarter`, `duration_ticks`, `bar_count`, section boundaries, track metadata, and track-local note events.
  Validation: reject invalid pitches, velocities outside MIDI range 1-127, non-positive durations, negative start ticks, events outside composition duration, malformed meter, malformed sections, duplicate track IDs, invalid MIDI channels/programs, and malformed track metadata.
  Logging: DEBUG log model validation failures with model, field, sanitized value preview, and reason; INFO log successful composition validation summaries with schema version, bar count, track count, event count, and duration ticks.

- [x] Task 2: Implement timing utilities in a new backend module such as `backend/app/services/composition_timing.py`.
  Deliverable: deterministic conversion helpers for meter parsing, bar duration in ticks, bar-to-start-tick conversion, legacy bar/beat quarter-unit conversion, and section boundary derivation.
  Validation: reject fractional/invalid tick results unless they are exactly representable; avoid floats in canonical persisted timing after migration.
  Logging: DEBUG log timing conversion inputs/outputs and WARN log rejected legacy timing values with actionable reasons.
  Dependency: Task 1.

- [x] Task 3: Add explicit migration/normalization in a new backend module such as `backend/app/services/composition_normalizer.py`.
  Deliverable: `normalize_composition_json(raw)` that accepts canonical `Composition` unchanged, migrates legacy `LLMMusicJson` with explicit notes, and rejects legacy harmony-only JSON with a clear actionable error.
  Behavior: preserve pitches, timing, durations, staff, and track assignment across load/save/reload; default missing legacy velocity with a documented constant; place migrated events inside the correct track; compute section boundaries and duration.
  Logging: INFO log normalization path (`canonical`, `legacy_migrated`, `legacy_rejected`), DEBUG log event counts and timing summaries, WARN log compatibility defaults such as missing velocity, and ERROR log unrecoverable malformed input.
  Dependency: Tasks 1 and 2.

### Phase 2: Generation And API Integration
- [x] Task 4: Update `backend/app/services/llm_music_generator.py` to generate or normalize into canonical `Composition` while preserving the existing `/llm/generate-music-json` statistical flow as much as practical.
  Deliverable: prompt/schema contract updated to require actual note events, velocity, deterministic timing, and track metadata, or a controlled legacy-output normalization step if keeping the LLM prompt close to current shape is safer.
  Behavior: retries should include validation feedback when note events are missing, invalid, or outside composition bounds; generation must not succeed with harmony-only playable content.
  Logging: INFO log provider/model/schema version and normalization path; DEBUG log sanitized prompt parameters and validation retry details; WARN log compatibility migration warnings.
  Dependency: Task 3.

- [x] Task 5: Update `backend/app/main.py` and response schemas to return canonical composition JSON and derived MusicXML without breaking provider/model/warnings behavior.
  Deliverable: response model uses `Composition` or a compatibility wrapper with a clearly named canonical field; endpoint errors distinguish invalid LLM output, migration rejection, and MusicXML rendering failure.
  Behavior: keep `/llm/models` unchanged and avoid changing unrelated statistical generation behavior.
  Logging: INFO log request completion with schema version and warning count; DEBUG log canonical response shape including track IDs, event count, duration ticks, and MusicXML length.
  Dependency: Task 4.

- [x] Task 6: Add MIDI-export-ready service boundaries even if a public MIDI endpoint is not added in this iteration.
  Deliverable: a backend helper module such as `backend/app/services/composition_midi.py` that consumes `Composition` and maps track metadata plus canonical note events into a MIDI-ready intermediate structure, or a minimal actual MIDI export if existing dependencies support it.
  Behavior: consume the same track-local note events as MusicXML/playback, preserve velocity, program, channel, start tick, and duration ticks.
  Logging: DEBUG log MIDI mapping summaries per track and ERROR log unsupported instrument/channel metadata.
  Dependency: Task 1.

### Phase 3: Rendering And Playback From Canonical Events
- [x] Task 7: Refactor `backend/app/services/music_json_renderer.py` to consume `Composition` canonical events only.
  Deliverable: MusicXML rendering reads `track.events` and inserts rests only for empty tick ranges; it does not call harmony-to-note fallback once canonical events exist.
  Behavior: harmony may render chord symbols; tracks with no events render rests/empty measures, not invented notes; polyphonic simultaneous notes render as chords when compatible and as voices when overlaps require independent durations.
  Logging: INFO log render start/end with schema version; DEBUG log per-track event grouping, rests inserted, polyphony grouping, and measure boundaries; WARN log notation-only limitations without changing audible data.
  Dependency: Tasks 1 and 2.

- [x] Task 8: Refactor `frontend/src/components/PlaybackControls.jsx` to schedule browser playback from canonical `track.events` only.
  Deliverable: Tone.js playback converts canonical ticks to seconds using `tempo` and `ticks_per_quarter`, supports overlapping notes/polyphony, applies velocity, and uses track metadata where practical.
  Behavior: remove harmony-derived audible fallback for canonical compositions; if a legacy object is loaded, trigger frontend normalization or show a clear error instead of inventing notes.
  Logging: DEBUG log schedule summaries by track and event counts; WARN log rejected legacy/harmony-only data; ERROR log scheduling/audio failures with sanitized context.
  Dependency: Tasks 1 and 3.

- [x] Task 9: Update frontend JSON handling in `frontend/src/store/musicStore.js`, `frontend/src/components/PromptJsonEditor.jsx`, and `frontend/src/api/musicApi.js`.
  Deliverable: frontend validation enforces `schema_version`, timing fields, sections, tracks, events, pitch, velocity, duration, composition bounds, and track-local event shape; store records migration warnings and avoids mutating canonical timing on edit/reset.
  Behavior: loading, saving, and reloading editor JSON must preserve pitches, start ticks, durations, velocities, and track assignments exactly.
  Logging: DEBUG log validation outcomes and canonical shape summaries; WARN log migration-required or rejected JSON; ERROR log invalid edits with actionable messages.
  Dependency: Tasks 3, 5, and 8.

### Phase 4: Automated Tests
- [x] Task 10: Expand backend tests in `backend/tests/test_llm_music_generation.py`, `backend/tests/test_music_json_renderer.py`, and new tests such as `backend/tests/test_composition_schema.py` and `backend/tests/test_composition_normalizer.py`.
  Deliverable: tests cover canonical timing in 4/4, 3/4, and 6/8; polyphony/simultaneous notes; rests as empty space; invalid pitch; invalid velocity; zero/negative duration; timing outside composition; duplicate/invalid tracks; malformed/non-contiguous sections; legacy migration; legacy harmony-only rejection; MusicXML using canonical events without harmony fallback; MIDI-ready mapping preserving velocity/channel/program.
  Logging: use pytest `caplog` where useful to assert key validation/migration logs are emitted without secrets or excessive raw JSON.
  Dependency: Tasks 1-7.

- [x] Task 11: Add frontend validation/playback tests if a test runner exists, or introduce the smallest practical frontend test setup; otherwise document manual smoke checks in `docs/testing.md`.
  Deliverable: coverage for valid canonical JSON, invalid timing/velocity/pitch, legacy migration/rejection UI messages, and playback event scheduling from ticks preserving polyphony.
  Logging: tests assert validation failures produce actionable errors and playback does not fall back to harmony-derived notes for canonical compositions.
  Dependency: Tasks 8 and 9.

### Phase 5: Documentation
- [x] Task 12: Update `README.md` with the Composition V1 JSON contract and API behavior.
  Deliverable: example canonical JSON showing `schema_version`, `ticks_per_quarter`, section boundaries, track metadata, note events, polyphony, implicit rests, and harmony as non-audible metadata.
  Logging: documentation notes where backend and frontend structured logs surface validation, migration, rendering, and playback decisions.
  Dependency: Tasks 1-9.

- [x] Task 13: Update `docs/testing.md` and add a focused schema reference document such as `docs/composition-v1.md`.
  Deliverable: test commands, manual smoke checks, migration examples, rejection examples, and acceptance criteria mapping for saved JSON determinism, reload invariance, MusicXML/MIDI/playback consumption, and validation coverage.
  Logging: document recommended `LOG_LEVEL=DEBUG` or browser console checks for diagnosing schema/migration/playback issues.
  Dependency: Tasks 10 and 11.

## Acceptance Mapping
- Acceptance 1 is covered by Tasks 1, 3, 4, 7, 8, and 12.
- Acceptance 2 is covered by Tasks 1, 3, 9, 10, and 11.
- Acceptance 3 is covered by Tasks 6, 7, 8, and 10.
- Acceptance 4 is covered by Tasks 3, 4, 5, 9, 10, and 13.
- Acceptance 5 is covered by Tasks 10 and 11.

## Implementation Risks
- API response shape may need a compatibility wrapper if the frontend or tests assume `music` is exactly `LLMMusicJson`; resolve this inside Task 5 with the smallest contract change.
- LLMs may produce invalid canonical tick math more often than legacy bar/beat data; Task 4 should prefer clear validation feedback and may keep legacy LLM prompt fields plus normalization if that preserves generation reliability.
- MusicXML representation of overlapping durations may require voices rather than simple chords; Task 7 should preserve audible timing even if notation needs warnings for unsupported layouts.
- Frontend has no visible test runner in the inspected files; Task 11 may need either minimal test setup or documented manual checks if adding tooling is out of scope.
