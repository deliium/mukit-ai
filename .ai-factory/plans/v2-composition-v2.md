# Implementation Plan: Canonical Composition V2

Branch: main
Created: 2026-09-08

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Logging control: backend runtime logs remain controlled by `LOG_LEVEL`; frontend diagnostics remain removable/reducible through the existing build/runtime logging policy. Production verbosity must be reducible without code changes.

## Roadmap Linkage
Milestone: "Canonical Composition V2" (completed 2026-09-08)
Rationale: This plan evolves the completed V1 contract into the next canonical composition and performance model without introducing another note source.

## Goal

Introduce strict `composition.v2` as the single latest operational representation while retaining `composition.v1` as an accepted migration input. V2 must preserve `tracks[].events[]` as the only source of playable notes, add practical expressive and timeline features, and carry those features through project persistence, browser playback, MIDI, MusicXML, WAV, JSON/piano-roll editing, and AI region editing with documented deterministic projections.

## Contract Decisions

### Versioning And Authority
- Add independent strict `CompositionV1` and `CompositionV2` Pydantic document models plus a discriminated `CompositionDocument` input union.
- Use `CompositionV2` as the internal service type, persistence output, API response type, frontend state type, and export source after normalization. V1 remains input-only after rollout; no V2-to-V1 downgrade is added.
- Dispatch by exact `schema_version`: recognized unversioned legacy input follows the existing legacy-to-V1 path and then V1-to-V2; `composition.v1` migrates to V2; `composition.v2` validates directly; unknown explicit versions fail with `unsupported_schema_version` and never enter legacy migration.
- Freeze V1 parsing compatibility, including its current ignored-extra behavior, so previously accepted projects remain readable. Apply `extra="forbid"` to every V2 persisted nested model so no V2 API validation/model dump can silently strip an expressive field; report ignored V1 extras during migration without treating them as supported V1 data.
- Keep every playable pitch exclusively in `tracks[].events[]`. Timeline, harmony, markers, and analysis metadata must never synthesize notes.

### V2 Shape
- Preserve the V1 root fields. `tempo`, `key`, and `time_signature` define state at tick `0`; change arrays contain subsequent transitions only and may not contain tick `0` duplicates.
- Add root `tempo_changes: [{tick, bpm}]`. Changes are instantaneous steps at arbitrary integral ticks in `(0, duration_ticks)`; no tempo ramps in V2.
- Add root `time_signature_changes: [{tick, time_signature}]`. Changes must occur on a derived bar boundary, produce integral bar lengths at the composition PPQ, and leave `duration_ticks` ending on a complete bar.
- Add root `key_changes: [{tick, key}]`. Changes must occur on a derived bar boundary and use the same major/minor spelling rules as the root key.
- Add root `markers: [{id?, tick, kind, label}]`, with `kind` limited to `rehearsal` or `text`. Multiple distinct markers may share a tick; exact duplicates are rejected.
- Extend sections with optional stable `id` and free-text `label`. This is the sole section-marker representation; do not duplicate section positions in `markers`.
- Extend notes with `articulations`, a duplicate-free subset of `staccato`, `staccatissimo`, `tenuto`, `accent`, and `marcato`, and optional `tie: {group_id, type}` where type is `start`, `continue`, or `stop`.
- Validate each tie chain within one track: exactly one start and stop, zero or more continues, same written pitch/staff/voice, contiguous boundaries, deterministic order, and no branch/gap/overlap. Only the chain head velocity starts a sound; consumers collapse the chain to one attack/release before format-specific measure fragmentation.
- Extend tracks with initial `expression` (`0..127`, default `127`), `dynamic_marks: [{tick, level}]` for `ppp` through `fff`, `sustain_pedals: [{start_tick, duration_ticks}]`, and `automation: [{parameter, interpolation, points}]` with at most one lane per `volume`, `pan`, or `expression`.
- Each automation point is `{tick, value}`. Point ticks are strictly increasing and greater than zero; one lane-wide interpolation is `step` or `linear`; volume/expression values are `0..127` and pan values are `-64..63`. Static `volume`, `pan`, and `expression` are the tick-zero state. Do not expose arbitrary MIDI CCs, bank/SysEx, aftertouch, pitch bend, or MIDI-only fields in V2.
- Sustain spans are binary, half-open, positive-duration, non-overlapping, and contained by the composition. Adjacent spans are rejected rather than silently merged.
- Arrays retain authored order where it carries identity (tracks and notes). Timeline, marker, dynamic, pedal, and automation arrays have a documented canonical tick order and reject duplicate/conflicting positions rather than silently sorting malformed input.

### Metadata Categories
| Primary category | Canonical fields | Rule |
|---|---|---|
| Musical content/structure | note pitch/start/notated duration, ties, articulations, meter timeline, key timeline | Defines authored notes and score structure; notes remain only in track events. |
| Semantic analysis/navigation | section type/boundaries/labels, harmony, rehearsal/text markers | May inform UI, notation, and LLM context but never creates audible notes. |
| Playback/performance | note velocity, tempo timeline, track volume/pan/expression, dynamic marks, sustain spans, automation | Directs deterministic projections but never replaces note content. |

Each field has exactly one primary owner above. Notation, playback, UI, and LLM uses are projections, not additional canonical ownership.

### Timing And Projection Rules
- Build one tested timeline compiler per runtime (Python and JavaScript) from the same V2 rules. It must provide bar boundaries, bar/tick lookup, active tempo/meter/key, piecewise `tick_to_seconds`, inverse `seconds_to_tick`, and total duration.
- Dynamic levels map to expression values `ppp=32`, `pp=48`, `p=64`, `mp=80`, `mf=96`, `f=112`, `ff=120`, `fff=127`; before the first mark, the dynamic factor is `127` (no attenuation). The current expression lane value is multiplied by the active dynamic value and divided by `127`, rounded to the nearest integer and clamped to `0..127`; velocity remains separate attack intensity. User mute/solo gain remains UI-only and separate.
- Performed articulation transforms are normative: `staccato` uses 50% gate, `staccatissimo` 25% gate, `tenuto` 100% gate, `accent` adds 12 velocity, and `marcato` uses 75% gate and adds 20 velocity. Gate rounds to the nearest tick with minimum one tick; velocity clamps to `1..127`. Reject `staccato`+`staccatissimo`, either short articulation+`tenuto`, and `accent`+`marcato`. Notated duration remains canonical. Tie chains permit attack articulations only on the head and reject gate-shortening articulations anywhere in the chain.
- Linear automation is evaluated in tick space. MIDI samples it at endpoints and intervals no larger than `max(1, ticks_per_quarter // 16)`, rounds to the nearest parameter integer, and removes consecutive duplicate values; Tone evaluates the same segments as parameter ramps. Shared golden vectors define rounding at exact half values so Python and JavaScript cannot diverge.
- At equal ticks, projections use a stable order: conductor changes; markers/directions; track automation/pedal; note-offs; note-ons; end cleanup. Tie boundaries produce no off/on pair.
- Track-local MIDI automation/pedal on shared channels is not silently approximated. Static and automated CC7/10/11 plus CC64 streams are compiled per track; byte-identical streams on one channel may be deduplicated, while any differing stream returns `midi_channel_control_conflict` mapped consistently by MIDI/WAV export routes.
- Define a shared backend `ProjectionReport` and stable issue-code registry before individual exporters. Every renderer returns its artifact plus this report; shared golden projection vectors verify that Tone and backend projections apply identical expression/articulation precedence.

## Export Fidelity Matrix

| V2 field | Tone.js | MIDI / WAV | MusicXML / OSMD |
|---|---|---|---|
| Canonical note pitch/start/notated duration/velocity | Scheduled from the compiled timeline; velocity preserved | Note events at canonical ticks; WAV inherits the same MIDI projection | Notes/rests/voices from canonical events |
| Articulations | Deterministic gate/velocity approximation | Same deterministic gate/velocity approximation | Native declared articulation symbols |
| Semantic ties | One attack and release for the validated chain | One logical sustained note; MIDI cannot retain tie IDs | Native semantic ties, independently fragmented at barlines |
| Dynamic marks | Documented expression-gain mapping | Combined into CC11 values; WAV inherits | Native dynamic marks |
| Sustain spans | Deferred releases with explicit pedal state | CC64 on/off; WAV inherits | Pedal start/stop directions |
| Volume/pan/expression automation | Gain/panner/expression parameter scheduling | CC7/CC10/CC11; linear curves sampled deterministically and reported as approximated | Not emitted as score notation except dynamic marks; report intentional omission |
| Tempo changes | Piecewise timing for notes, cursor, seek, and completion | Conductor tempo events; BPM quantization reported | Metronome changes at the corresponding offsets |
| Meter changes | Shared bar/cursor/grid map; no direct sound | Conductor time-signature events | Time-signature attributes in every affected part |
| Key changes | Navigation/display only; no notes inferred | Key-signature events where MIDI supports spelling, otherwise warning | Key-signature attributes in every affected part |
| Section labels and markers | Navigation/display only | Marker/text meta events | Section labels and rehearsal/text expressions |
| Harmony/analysis metadata | Ignored by design; never made audible | Ignored by design | Existing chord-symbol projection only |

- Renderer results must carry structured projection issues with stable codes and exact/approximated/omitted counts. Download endpoints expose compact issue codes in response headers, frontend export actions display them, and backend logs contain counts/codes only, never raw composition or binary/XML payloads.
- MIDI tempo integer quantization, sampled linear automation, articulation transforms, unsupported key spelling, and non-notational automation are declared approximations/omissions, not described as lossless.
- WAV remains the FluidSynth rendering of the exact MIDI bytes produced for that request; acoustic sample identity across SoundFonts/FluidSynth versions is not promised.

## Out Of Scope
- Alternative note/event stores, harmony-derived notes, score-import round trips, arbitrary MIDI controllers, pitch bend, aftertouch, half-pedal, ornaments/slurs/lyrics, tempo ramps, pickup/partial measures, and mid-measure meter/key changes.
- A full graphical automation-lane editor. V2 automation remains editable in the canonical JSON editor; the piano roll adds note-expression editing and timeline visualization while preserving every V2 field.
- Database normalization of tracks/events or a separate composition-version SQL column. The existing JSON document contains its version and remains atomically stored.
- Compatibility with an old deployed frontend receiving V2 responses. Backend and frontend V2 support ship together; backward compatibility is for stored V1 documents and V1 request payloads.

## Commit Plan
- **Commit 1** (after tasks 1-3): `feat(composition): add v2 schema migration and timing`
- **Commit 2** (after tasks 4-6): `feat(composition): integrate v2 generation editing and state`
- **Commit 3** (after tasks 7-9): `feat(frontend): edit and play composition v2 expression`
- **Commit 4** (after tasks 10-12): `test(docs): preserve and verify composition v2 expression`

## Tasks

### Phase 1: Contract, Migration, And Timing

- [x] **Task 1: Define strict versioned Composition V2 models and invariants.**
  - First inventory every backend/frontend occurrence of `composition.v1`, `Composition`, `schema_version`, direct constant tempo/meter math, event fingerprints, and selective revision serialization. Record each hit in this plan's implementation notes as migrated, intentionally retained as V1 input compatibility, or unaffected; explicitly include `legacyPlaybackEvents.js`, `pianoRollSelection.js`, `ComposerWorkspace.jsx`, `TrackPlaybackControls.jsx`, `MusicGenerator.jsx`, `test_llm_real_provider_smoke.py`, and `test_project_store.py`.
  - Create `backend/app/composition_schemas.py` for V1/V2 document contracts, shared pitch/key/meter helpers, V2 timeline/direction models, note expression/tie models, and discriminated input aliases; update `backend/app/schemas.py` to consume/re-export the canonical types needed by existing LLM request/response models without circular imports.
  - Encode every decision in **V2 Shape** and **Timing And Projection Rules**, including complete meter-map duration/section checks, event/tie bounds, unique track/event/tie IDs where present, canonical timeline ordering, exact parameter ranges/rounding/transforms/precedence, pedal overlap checks, and `extra="forbid"` on V2 persisted models.
  - Keep V1 validation and ignored-extra behavior sufficient to read all existing fixtures/projects; collect sanitized ignored-field paths in the migration report and add explicit unsupported-version domain errors rather than shape-based fallback.
  - Add `backend/app/services/composition_projection.py` with the shared `ProjectionReport`, severity/status model, bounded issue details, and stable issue-code registry consumed by all export tasks.
  - Add focused model tests in `backend/tests/test_composition_schema.py` and new `backend/tests/test_composition_v2_schema.py`, including mixed meter, malformed ties, invalid automation/pedal spans, unknown fields/versions, and a minimal V2 document with empty expressive defaults.
  - **Logging:** At schema/migration/projection boundaries, use `LOG_LEVEL`-controlled DEBUG schema-dispatch/version and count summaries, INFO successful V2 validation with bars/tracks/notes/change counts, and ERROR sanitized field paths/error counts. Never log full values, event lists, or raw documents.

- [x] **Task 2: Implement pure, explicit legacy -> V1 -> V2 migration and project round trips.**
  - Add `backend/app/services/composition_migration.py` with a non-mutating `migrate_v1_to_v2()` and migration result metadata; refactor `backend/app/services/composition_normalizer.py` into exact version dispatch that always returns `CompositionV2` operationally.
  - V1-to-V2 must preserve root metadata, section positions/types, harmony, track order/metadata, event order, and every note `id`, pitch spelling, timing, velocity, staff, and voice exactly; it changes only `schema_version`, adds deterministic section IDs where absent, and materializes empty/default V2 fields.
  - Define the rewrite equality gate as ordered equality of every V1 root/section/harmony/track field and every ordered V1 note field after projecting migrated V2 back onto the V1 field set; only schema version, deterministic section IDs, and documented empty/default V2 additions may differ. On mismatch, return `v1_v2_migration_fidelity_failed` and leave stored JSON untouched.
  - Continue accepting unversioned legacy top-level notes by running the current legacy conversion first, then V1-to-V2. Explicit unknown versions must fail without touching the legacy path.
  - Update `backend/app/services/project_composition.py`, `backend/app/project_schemas.py`, `backend/app/services/project_store.py`, and `backend/app/routers/projects.py` so create/save/open/duplicate return and store validated V2, rewrite V1 on open using the existing canonical rewrite flow, and never rewrite the row after a failed migration/equality gate.
  - Add golden fixtures under `backend/tests/fixtures/` and tests in `backend/tests/test_composition_v2_migration.py`, `test_composition_normalizer.py`, `test_project_routes.py`, and `test_project_persistence_acceptance.py` for source immutability, note sequence equality, repeated-open idempotence, failed migration non-destruction, save/reopen expression equality, and duplicate/restart behavior.
  - **Logging:** At normalizer/project route/store boundaries, use `LOG_LEVEL`-controlled INFO source/target version, migration path, project ID, rewritten flag, and aggregate counts; DEBUG source/target deterministic hashes and default counts; WARNING compatibility defaults; ERROR sanitized code/path only. Never log source or migrated JSON.

- [x] **Task 3: Centralize variable-tempo and variable-meter timeline calculations.**
  - Extend `backend/app/services/composition_timing.py` or add `backend/app/services/composition_timeline.py` with bar-boundary compilation, bar-range-to-tick range, active state lookup, piecewise tick/seconds conversion, inverse conversion, and total-duration integration.
  - Add the matching pure frontend utility `frontend/src/utils/compositionTimeline.js`; derive it only from canonical V2 fields and enforce parity through shared golden fixture expectations rather than introducing stored compiled timelines.
  - Replace constant-meter assumptions in schema validation, `backend/app/services/composition_region_patch.py`, section/harmony checks, `frontend/src/utils/pianoRollEvents.js`, `frontend/src/utils/pianoRollSelection.js`, `frontend/src/utils/playbackPosition.js`, `frontend/src/components/PianoRollEditor.jsx`, and validation geometry with timeline helpers.
  - Render variable-width bars and correct selection/cursor bar labels in the piano roll while leaving note start/duration ticks unchanged.
  - Add `backend/tests/test_composition_timing.py`, `frontend/src/utils/compositionTimeline.test.js`, and mixed `4/4 -> 3/4 -> 6/8` boundary/inverse tests, including notes and sections spanning changes and invalid incomplete final bars.
  - **Logging:** At timeline compile callers, use `LOG_LEVEL`-controlled DEBUG segment counts/boundaries and conversion failures, INFO one timeline summary per validation/render operation, and WARNING only for explicit projection approximation. Pure conversion functions add no runtime logging; do not log each cursor poll or note conversion.

### Phase 2: Generation, Editing, And Frontend Authority

- [x] **Task 4: Move generation and validation to V2 while keeping expressive output optional.**
  - Update `backend/app/services/llm_music_generator.py`, `composition_planner.py`, `composition_validator.py`, `generation_constraints.py`, `composition_tonality.py`, `instrument_identity.py`, `fixture_compositions.py`, and `fake_llm.py` to accept the latest model and preserve V2 metadata in fingerprints and validation.
  - Keep existing form/harmony/note stage prompts focused on canonical note events. Assembly emits `composition.v2` and materializes empty change/marker/expression collections when the provider supplies no expressive data, so existing generation behavior and note output remain valid.
  - Load existing V1 fake fixtures through the explicit migration path and add one native V2 expressive fixture under `backend/app/fixtures/` with its test mirror so fake mode exercises tempo changes and note expression without API credits.
  - Extend integrity checks for tie-chain musical validity and contradictory articulation, but do not make expressive metadata mandatory for generated music.
  - Update generation response/API typing in `backend/app/main.py` and `backend/app/schemas.py`; add tests in `test_llm_music_generation.py`, `test_llm_staged_composer.py`, `test_llm_fake_provider.py`, `test_composition_validator.py`, and `test_instrument_identity.py` proving old staged outputs assemble unchanged notes into valid V2.
  - **Logging:** At generation stages and validation gates, retain `LOG_LEVEL`-controlled stage/provider/model/attempt diagnostics; add output schema version and expressive feature counts at DEBUG/INFO; log validation codes without full prompts, draft JSON, composition payloads, or provider secrets.

- [x] **Task 5: Make AI region patches V2-aware and preservation-safe.**
  - Version the replacement patch in `backend/app/composition_schemas.py` / `backend/app/schemas.py` and update `backend/app/services/composition_region_patch.py`, `llm_composition_editor.py`, and `fake_llm.py` so replacement notes may carry V2 articulations/ties while root timelines, section labels, markers, dynamic marks, pedals, automation, and track metadata are preserved by default.
  - Compare complete canonical preserved subtrees instead of V1 tuples. Continue allowing harmony and added tracks only through current explicit permissions; do not add global tempo/meter/key or track-automation editing to the region LLM in this milestone.
  - Reject a selected edit with an actionable diagnostic when a targeted boundary-crossing note, tie chain, or semantic span cannot be changed without mutating content outside the region; do not truncate/split it silently.
  - Update prompt contracts to describe V2 note metadata and patch scope while keeping provider output patch-only. Remove instruction previews/raw validation values from logs.
  - Extend `backend/tests/test_composition_region_patch.py`, `test_llm_composition_editing.py`, `test_llm_routes.py`, and `test_llm_fake_provider.py` for expressive replacement, outside-region byte-equivalent canonical serialization, crossing-chain rejection, and V1-input-to-V2-output edits.
  - **Logging:** At edit orchestration/patch validation/application boundaries, use `LOG_LEVEL`-controlled DEBUG schema/patch version, selected tick/bar range, target IDs, feature counts, and preservation hashes; INFO apply/reject outcomes and repair count; ERROR stable diagnostics only. Never log instructions, full patches, or compositions.

- [x] **Task 6: Upgrade frontend version boundaries, validation, state, and revisions to one V2 document.**
  - Update `frontend/src/utils/musicJsonValidation.js` to recognize V1, V2, legacy, and unsupported versions explicitly and mirror all user-actionable V2 invariants; backend validation remains authoritative.
  - Add a small `frontend/src/utils/compositionVersion.js` migration/guard boundary if needed so API V1 payloads are normalized immediately and Zustand `editedMusicJson` remains one V2 document. Do not add a sidecar performance model.
  - Replace selective project and playback/notation fingerprints in `frontend/src/utils/projectPersistRevision.js`, `playbackPosition.js`, and `frontend/src/store/musicStore.js` with deterministic full-document canonical serialization for persistence plus explicit complete audible/notation revisions. Preserve array order, recursively sort object keys, and surface serialization failure as dirty/error rather than a time-based clean key.
  - Update `frontend/src/api/musicApi.js`, `projectApi.js`, `frontend/src/store/musicStore.js`, `MusicGenerator.jsx`, and `ComposerWorkspace.jsx` so generation/open/edit/save/autosave/export request boundaries accept compatibility input but install only validated V2 state.
  - Bind every derived MusicXML string and projection warning set to the exact notation revision that produced it; clear stale notation on relevant edits and discard out-of-order preview responses whose requested revision no longer matches current V2 state.
  - Extend validation, revision, Zustand project/generation, and API tests for V1 migration, unsupported versions, every persisted V2 field affecting dirty state, every audible/notational field affecting refresh state, and stale preview rejection.
  - **Logging:** At API/store/version/revision boundaries, use browser DEBUG diagnostics for version normalization, validation category, revision transitions, and feature counts; INFO migration/save/export state transitions; WARNING unsupported versions or preservation failures. Production verbosity must be reducible without source edits; never log full documents or expression arrays.

- [x] **Task 7: Preserve and edit V2 expression through the piano roll and JSON workflows.**
  - Ensure `ensureCompositionNoteIds`, `frontend/src/utils/pianoRollEvents.js`, create/move/resize/delete, undo/redo, AI edit hydration, project hydration, and JSON edits preserve all V2 root, section, track, and note fields. Hydration-only UI IDs must not create a false persisted change.
  - Add selected-note articulation controls in `frontend/src/components/PianoRollEditor.jsx` and an atomic tie-chain action that selects two or more contiguous compatible notes and writes/removes the complete validated chain in one state update; never create an invalid one-ended tie as an intermediate save/autosave state.
  - Add read-only tempo/meter/key/section-label/marker cues to the variable-width piano-roll timeline. Dynamic/pedal/automation arrays remain editable through `frontend/src/components/PromptJsonEditor.jsx` in this milestone.
  - Update `TrackPlaybackControls.jsx`, `PlaybackControls.jsx`, `AiRegionEditPanel.jsx`, selection helpers, and editor guards for V2 without maintaining expression sidecars.
  - Extend piano-roll event/selection/component and Zustand edit/history tests for atomic ties, articulation conflicts, V2 field preservation across every mutation and undo/redo, timeline cues, and backend-invalid JSON remaining visibly unsaved.
  - **Logging:** At editor/store mutation boundaries, use browser DEBUG diagnostics for action type, selected IDs, tick ranges, and feature counts; INFO atomic tie/articulation outcomes; WARNING rejected incompatible selections. Production verbosity must be reducible without source edits; never log full notes, tracks, or documents.

### Phase 3: Authoritative Playback And Exports

- [x] **Task 8: Compile V2 expression into deterministic Tone.js playback.**
  - Refactor `frontend/src/utils/playbackEvents.js`, `playbackTracks.js`, and `tonePlaybackEngine.js` to compile canonical notes, tie chains, articulation transforms, dynamic/expression values, sustain state, automation, and piecewise tempo into an ephemeral ordered schedule.
  - Use piecewise tick-to-seconds scheduling (or another equally testable single projection) rather than the current global seconds-per-tick formula. Schedule separate attacks/releases so tied and pedaled notes do not retrigger/release early; track pressed/deferred notes and clear them on stop/dispose.
  - Keep mute/solo gain independent from persisted volume/expression automation. Base completion/cursor/seek on compiled composition duration, including trailing silence; define seek as rebuilding active controller/note state rather than replaying stale callbacks.
  - Update `frontend/src/components/PlaybackControls.jsx` to use timeline inverse conversion and stop/rebuild playback whenever any audible V2 field changes.
  - Extend `playbackEvents.test.js`, `playbackTracks.test.js`, `tonePlaybackEngine.test.js`, and `playbackPosition.test.js` with fake-clock cases at tempo boundaries, ties, pedal deferral/release, automation interpolation, same-tick ordering, trailing silence, seek, pause/resume, and cleanup.
  - **Logging:** At playback compile/engine lifecycle boundaries, use browser DEBUG diagnostics for one compile summary, timeline/controller counts, fallback strategy, and lifecycle transitions; INFO prepare/play/pause/stop outcomes; WARNING skipped invalid schedule items. Production verbosity must be reducible without source edits; avoid per-frame/per-note logs and never log the source document.

- [x] **Task 9: Render deterministic V2 MIDI and propagate it to WAV.**
  - Refactor `backend/app/services/composition_midi.py` around one absolute-tick projection stream containing conductor changes, markers, controller state, logical tie-collapsed/articulated notes, sustain, and sampled automation with stable same-tick ordering.
  - Emit initial plus changed tempo/meter/key metadata; map volume/pan/expression to CC7/10/11 and sustain to CC64; sample linear automation using a documented bounded tick/value-error policy with endpoint retention/deduplication.
  - Detect channel-scoped conflicts for track-local automation/pedal and return an explicit export error rather than leaking controls across tracks. Report tempo quantization, automation sampling, articulation transformation, unsupported key spelling, marker normalization, and MIDI-inherent loss such as pitch spelling/tie IDs.
  - Update `backend/app/services/composition_wav.py` to consume the same rendered MIDI bytes/report and calculate expected duration by integrating the tempo map; retain safe FluidSynth invocation and explicit tail/padding policy.
  - Expand `backend/tests/test_composition_midi.py`, `test_composition_wav.py`, `test_export_fidelity.py`, and `test_wav_renderer_smoke.py` with parsed absolute-tick MIDI assertions, ordering, controller data, tempo quantization bounds, shared-channel rejection, V1-versus-migrated-V2 note equality, and tempo-map WAV duration.
  - **Logging:** At MIDI projection/WAV synthesis boundaries, use `LOG_LEVEL`-controlled DEBUG feature counts, sampling bounds, MIDI message counts, and duration math; INFO byte length/status/issue counts; WARNING stable approximation codes; ERROR sanitized renderer codes. Never log MIDI/WAV bytes, SoundFont contents, or full event streams.

- [x] **Task 10: Render V2 notation semantics in MusicXML and expose projection issues.**
  - Update `backend/app/services/music_json_renderer.py` to build measures from the meter map; insert changed key/meter attributes in every affected part and tempo/direction/marker output once in a designated visible part.
  - Collapse semantic tie chains to logical sounds, then generate native sounding/notated MusicXML ties while independently fragmenting at measure boundaries. Emit declared articulations, dynamic marks, sustain pedal directions, section labels, rehearsal/text markers, and existing harmony chord symbols.
  - Preserve canonical notes as the source; no marker, harmony, or direction may create notes. Track automation that has no score notation is intentionally omitted with a structured issue instead of being silently ignored.
  - Consume the shared projection-report model from Task 1. Update export routes in `backend/app/main.py` to accept V1/V2 input, normalize to V2, and return bounded `X-Mukit-Projection-Status`, `X-Mukit-Projection-Issues`, and issue-count headers. Expose those names through CORS, parse missing/malformed headers safely in `musicApi.js`, and display warnings without embedding raw reports in binary payloads.
  - Expand `backend/tests/test_music_json_renderer.py`, `test_export_routes.py`, and `test_export_fidelity.py` with serialized XML assertions for tempo/meter/key changes, ties (`tie` and `tied`), articulation, dynamics, pedal, labels/markers, and explicit automation omission reports; add frontend API/store warning tests.
  - **Logging:** At MusicXML projection/export route boundaries, use `LOG_LEVEL`-controlled DEBUG measure/timeline/direction counts and projection decisions; INFO format/status/exact-approximate-omitted counts; WARNING stable issue codes; ERROR sanitized failure context. Never log MusicXML text or canonical JSON.

### Phase 4: Acceptance, Documentation, And Release Gate

- [x] **Task 11: Add end-to-end V2 compatibility and persistence acceptance coverage.**
  - Add `frontend/e2e/v1-upgrade-to-v2.spec.js`, `frontend/e2e/v2-user-journey.spec.js`, and `frontend/e2e/v2-persistence.spec.js`; add a `scripts/v2_docker_acceptance.sh` path or extend the existing acceptance script without weakening the retained V1 journey.
  - Cover: open a seeded V1 project; prove exact per-track note sequence equality after migration; edit expressive metadata; save; restart; reopen; play across a tempo/meter change; perform a safe AI region edit; export MusicXML/MIDI/WAV; and verify projection warnings are visible where approximation is expected.
  - Keep existing V1 fixtures and V1-input request cases as regression gates, but update response assertions that intentionally become V2. Retain separate immutable V1 parser/migration regressions rather than requiring obsolete V1-output assertions to remain unchanged. Add OpenAPI assertions that V1 and V2 request branches are represented and responses are V2.
  - Run backend pytest, frontend unit tests/lint/build, Playwright fake-LLM journeys, and opt-in Docker/FluidSynth acceptance where dependencies are available. Treat any silent field disappearance across create/open/save/edit/export as a release blocker.
  - **Logging:** Run acceptance with `LOG_LEVEL=DEBUG` and browser diagnostics enabled; assert sanitized migration/projection codes and counts at API/export boundaries while API keys, prompts, raw composition JSON, MusicXML, MIDI, and WAV bytes are absent. Production remains reducible through existing controls.

- [x] **Task 12: Publish the V2 contract, migration, fidelity, and operator guidance through the mandatory `$aif-docs` checkpoint.**
  - Create `docs/composition-v2.md` with complete JSON examples, field ownership/categories, invariants, timeline math, tie/articulation semantics, automation precedence, and the export fidelity matrix.
  - Update `docs/composition-v1.md` to mark V1 as accepted migration input, `docs/project-persistence.md` with migrate-on-open/idempotence/failure behavior, `docs/testing.md` with V2 commands/fixtures, `docs/CODEBASE_MAP.md`, `README.md`, `.ai-factory/DESCRIPTION.md`, `.ai-factory/ARCHITECTURE.md`, and `AGENTS.md` so they identify V2 as latest canonical while retaining V1 compatibility facts.
  - Document explicit limitations and projection issue codes, including MIDI channel conflicts, tempo quantization, automation sampling/notation omission, key spelling support, Tone approximation, and WAV/SoundFont variability.
  - Add a release checklist requiring green migration equality, persistence round-trip, frontend preservation, playback timing, export projection, and secret/log hygiene gates before V2 responses are enabled in production.
  - **Logging:** This documentation-only task adds no runtime logging. Through `$aif-docs`, document the `LOG_LEVEL`/browser control points, safe DEBUG/INFO fields, production reduction policy, and forbidden payloads; no example may include secrets or imply that approximation warnings contain raw music data.

## Dependencies
- Task 2 depends on Task 1.
- Task 3 depends on Task 1 and blocks every variable-timing consumer.
- Task 4 depends on Tasks 1-3.
- Task 5 depends on Tasks 1-4.
- Task 6 depends on Tasks 1-3 and the API/persistence behavior from Task 2.
- Task 7 depends on Tasks 3, 5, and 6.
- Task 8 depends on Tasks 3, 6, and 7.
- Task 9 depends on Tasks 1-4 and may proceed in parallel with Tasks 7-8 after shared timing/projection rules stabilize.
- Task 10 depends on Tasks 1-4, Task 6, and the projection-report contract; it may proceed in parallel with Task 9 after those dependencies complete.
- Task 11 depends on Tasks 2-10.
- Task 12 starts with Task 1's contract and completes after Tasks 2-11 confirm actual behavior.

## Acceptance Gates
- A V1 fixture migrated to V2 has identical ordered track IDs and identical ordered note fields (`id`, pitch spelling, start tick, duration ticks, velocity, staff, voice); migration is source-immutable and idempotent.
- Existing unexpressive staged/fake generation succeeds and returns V2 with the same notes plus empty/default expressive fields.
- Native V2 supports at least one mid-composition tempo change and note-level articulation/tie metadata through validation, save/open, playback, MIDI, and MusicXML.
- Supported V2 fields survive create/open/save/duplicate/AI-edit round trips. Export projections either encode them according to the matrix or return visible structured approximation/omission/error codes.
- Tone, MIDI, MusicXML, and WAV consume projections compiled only from the same V2 Composition; no persisted sidecar or harmony-derived note path exists.
- Existing V1 fixtures, parser behavior, and V1-input regression cases remain green alongside new V2 suites; response assertions are updated where the intentional latest canonical output is V2.
- Verbose logs are useful and count-based, with no API keys, full prompts/instructions, raw composition documents, MusicXML, MIDI, or WAV payloads.

## Implementation Notes

### Task 1 inventory (2026-09-08)

| Path | Pattern | Classification | Note |
|------|---------|----------------|------|
| `backend/app/schemas.py` | Composition / schema_version | migrated | Re-exports V1/V2 from `composition_schemas.py` |
| `backend/app/composition_schemas.py` | CompositionV1/V2 | migrated | New strict V2 + frozen V1 contracts |
| `backend/app/services/composition_projection.py` | ProjectionReport | migrated | Shared issue registry for exporters |
| `backend/app/services/composition_normalizer.py` | composition.v1 | migrated | Task 2: dispatch → always CompositionV2 |
| `backend/app/services/project_composition.py` | COMPOSITION_SCHEMA_VERSION | migrated | Task 2 |
| `backend/app/services/project_store.py` | composition JSON | migrated | Task 2 persistence round trips |
| `backend/app/routers/projects.py` | schema_version logs | migrated | Task 2 |
| `backend/app/project_schemas.py` | Composition | migrated | Task 2 typing → V2 |
| `backend/app/services/llm_*.py` / `fake_llm.py` / `fixture_compositions.py` | composition.v1 | migrated | Tasks 4–5 |
| `backend/app/services/composition_{midi,wav,region_patch}.py` / `music_json_renderer.py` | Composition | migrated | Tasks 5/9/10 |
| `backend/app/services/composition_timing.py` | constant meter math | migrated | Task 3 timeline compiler |
| `backend/tests/test_composition_schema.py` | Composition V1 | intentionally retained | V1 parser regression |
| `backend/tests/test_composition_v2_schema.py` | CompositionV2 | migrated | New V2 invariants |
| `backend/tests/test_project_store.py` | schema_version v1 | migrated | Task 2 |
| `backend/tests/test_llm_real_provider_smoke.py` | schema_version assert | migrated | Task 4 response → v2 |
| `frontend/src/utils/legacyPlaybackEvents.js` | refuses composition.v1 | intentionally retained | Legacy refusal; extend for v2 in Task 8 |
| `frontend/src/utils/pianoRollSelection.js` | bar/tick math | migrated | Task 3/7 |
| `frontend/src/components/ComposerWorkspace.jsx` | composition.v1 copy | migrated | Task 6/7 |
| `frontend/src/components/TrackPlaybackControls.jsx` | composition.v1 hint | migrated | Task 7 |
| `frontend/src/components/MusicGenerator.jsx` | generation wiring | migrated | Task 6 |
| `frontend/src/utils/{musicJsonValidation,projectPersistRevision,playback*}.js` | v1 fingerprints | migrated | Tasks 3/6/8 |
| `docs/*`, `AGENTS.md`, `.ai-factory/DESCRIPTION.md` | composition.v1 canonical | migrated | Task 12 docs |
| Fixture JSON under `backend/app/fixtures` / `backend/tests/fixtures` | composition.v1 | intentionally retained | Migration input; Task 2/4 add native V2 fixture |

### Task 4–5 notes (2026-09-08)

- Assemble emits native `CompositionV2` (empty expressive collections); fixtures load via normalizer → V2; `composition_v2_expressive.json` for fake 4-bar generates.
- Region patches accept V2 notes (articulations/ties); preserve root/track expression metadata; reject boundary-crossing notes and tie chains.
- Edit request input accepts V1|V2; responses are always V2.

### Task 8–12 notes (2026-09-08)

**Task 8 — Tone.js playback:** `playbackEvents.js`, `playbackTracks.js`, and `tonePlaybackEngine.js` compile V2 tie chains, articulation gates/velocity deltas, dynamic×expression, sustain deferral, automation ramps, and piecewise tempo. Mute/solo stays UI-only. Seek rebuilds controller/note state; audible-field edits stop active playback.

**Task 9 — MIDI/WAV:** `composition_midi.py` emits one absolute-tick stream (conductor, markers, CC7/10/11, CC64, sampled linear automation, logical tie-collapsed notes). `composition_wav.py` renders those exact bytes via FluidSynth. Shared-channel CC conflicts return `midi_channel_control_conflict`. Tempo integer quantization and automation sampling are documented approximations.

**Task 10 — MusicXML + headers:** `music_json_renderer.py` builds measures from the meter map; emits tempo/meter/key attributes, semantic ties (barline-fragmented), articulations, dynamics, pedal, section labels, markers, harmony. Track automation omitted with `automation_omitted_from_notation`. Export routes attach `X-Mukit-Projection-*` headers; CORS exposes them; `musicApi.js` parses and surfaces warnings.

**Task 11 — E2E acceptance:** Playwright `v1-upgrade-to-v2`, `v2-user-journey`, `v2-persistence` specs plus `scripts/v2_docker_acceptance.sh`; V1 journey asserts post-ingest `composition.v2`. V1 parser/migration regressions retained separately from V2 response assertions.

**Task 12 — Documentation:** Published `docs/composition-v2.md`; updated V1/persistence/testing/README/AGENTS/DESCRIPTION/ARCHITECTURE/CODEBASE_MAP with V2-as-canonical guidance, migration fidelity, projection headers, fake LLM expressive fixture operator notes, and release checklist.
