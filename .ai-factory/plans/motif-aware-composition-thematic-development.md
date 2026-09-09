# Implementation Plan: Motif-Aware Composition and Thematic Development

Branch: main (branch creation disabled by `.ai-factory/config.yaml`)
Created: 2026-09-09

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes (mandatory completion checkpoint through `$aif-docs`)

## Roadmap Linkage
Milestone: "Motif-aware composition"
Rationale: This is a new product milestone that extends canonical V2 editing, deterministic analysis, and staged LangGraph generation with durable thematic intent.

## Goal

Let a user select a one- or two-bar melody, save it as a named motif such as `Motif A`, inspect its detected and authored usages, and realize a recognizable transformation in another section or track. All audible results must be ordinary canonical `composition.v2` note events; motif metadata must never act as a playback placeholder or synthesize notes in a consumer.

## Architecture Decisions

### Canonical and Derived Split

- Add an optional, default-empty `motifs` collection to `composition.v2`. It stores authored identity and provenance only: motif ID/label plus occurrences that reference existing canonical event IDs.
- Do not copy pitch, onset, duration, velocity, or complete note payloads into motif definitions. `tracks[].events[]` remains the sole playable source.
- Each canonical occurrence references one track and an ordered, duplicate-free list of event IDs. It records a relationship (`original`, `repeat`, `transpose`, `rhythmic_variation`, `melodic_variation`, `inversion`, `augmentation`, `diminution`, `sequence`, `answer`, or `counterphrase`) and bounded transformation provenance, but consumers derive its span and notes from referenced events.
- Require exactly one `original` occurrence per authored motif. Validate unique motif/occurrence IDs, globally resolvable event references, tie-chain completeness, chronological event order, source bounds, and non-percussion source policy.
- Keep automatically detected similarity in `composition.analysis.v1`. Add deterministic motif families that contain fingerprint-bound note references and occurrence relationships. Derived families are cacheable observations, not persisted composition state.
- Derived note references use event IDs where present and fingerprint-bound event indexes as fallback for ID-less compositions. They contain no copied notes and cannot be submitted as canonical authority without resolving against the exact source fingerprint.

### Initial Product Scope

- Authoring initially accepts 3–32 selected notes from one pitched track, with note attacks spanning one or two bars. The notes may cross beats and may include complete tie chains.
- Destination is an existing section and existing pitched track with an explicit start bar/tick. Cross-section and cross-track placement are supported. New-track creation is out of scope for the first motif endpoint.
- Destination placement defaults to replacing canonical events that overlap the realized motif span. Reject boundary-crossing notes/ties unless the request explicitly selects a valid larger replacement region; preserve all unrelated events and metadata byte-for-byte.
- `variation_strength` is a finite float in `0..1`. Mechanical operations use exact parameters and report strength as not applicable; creative operations use strength to set mutation budgets and minimum identity thresholds.
- Deterministic operations: repeat, transpose, inversion, augmentation, diminution, and sequence.
- Bounded AI operations: rhythmic variation, melodic variation, answer, and counterphrase. The model proposes relative changes or draft notes; deterministic services enforce timing, range, destination, and recognizable-identity constraints before applying them.
- Imported compositions already have stable IDs. The frontend continues hydrating missing note IDs before motif authoring. The backend rejects unresolved motif references rather than silently assigning IDs during V1 migration and changing migration fidelity.

### Recognition and Identity

- Extend repetition analysis from flat matches to families containing both the reference and every matched occurrence.
- Detect exact, constant-transposition, rhythm-only, inversion, and rational time-scaled (`augmentation`/`diminution`) relationships where bounded comparison is practical.
- Treat sequence as repeated related occurrences with a consistent temporal and transposition step. Treat answer/counterphrase as authored/AI provenance rather than claiming deterministic semantic detection in v1.
- Include onset rhythm and duration ratios in rhythmic identity. Align melodic projection with the existing melody analyzer's selected-track/skyline policy, or explicitly report the projection method when all pitched tracks are searched.
- Calculate a deterministic weighted identity score from rhythm, contour, normalized intervals, and note-count alignment. Enforce operation-specific minimum scores derived from `variation_strength`; exact arithmetic transforms additionally require exact transform verification.
- Preserve existing search/result caps, use fixed-length SHA-256 signatures instead of unbounded polynomial IDs, report truncation, and keep output ordering deterministic.

### API Shape

- Add `POST /motifs/apply` in a dedicated router. The request contains the current V2 composition, canonical motif ID, source occurrence ID, destination section/track/start, operation, strength, operation parameters, and optional LLM model selection.
- Mechanical operations do not call an LLM. Creative operations use a dedicated motif-edit LangGraph with `draft -> validate -> repair -> apply` stages and the existing retry/provider conventions.
- Return the validated canonical composition, a bounded operation result (resolved source/destination, created event IDs, new occurrence ID, measured relationship/identity score, provider/model when used), warnings, and MusicXML preview metadata consistent with existing edit responses.
- Use stable domain error codes mapped to 422 for invalid/stale source or destination, 503 for unavailable providers on AI-only operations, and 502 for exhausted malformed or identity-failing AI output.

### Generation Shape

- Replace the free-form `ComposerMotifContext` handoff with a bounded structured theme plan created after form and harmony planning.
- The plan selects a seed section/track and explicit recurrence targets/operations. Melody generation produces the seed first, then receives the immutable seed cell while composing ordered section drafts so later sections intentionally recur rather than receiving disconnected prose hints.
- Deterministically realize and verify mechanical recurrences before assembly. Creative recurrences use constrained section outputs and the same identity verifier as motif editing.
- Assemble generated motif definitions only after draft event IDs are finalized. Failed thematic validation routes to the theme planner or affected section, without regenerating unrelated valid sections.
- Pass bounded user `instructions` text into theme/form planning; log only its presence and length, never content.

## Non-Goals

- Do not store symbolic motif instances that playback, notation, or export must expand.
- Do not infer harmony or notes from motif metadata.
- Do not support arbitrary multi-track/polyphonic source motifs in the first authoring workflow.
- Do not guarantee motif metadata round-trip through MIDI or MusicXML. Export must explicitly report metadata omission while preserving note fidelity.
- Do not persist derived analysis reports in projects.
- Do not silently repair dangling canonical motif references after direct JSON edits; surface validation errors. Controlled piano-roll and AI operations may reconcile references explicitly and report what changed.

## Commit Plan

- **Commit 1** (after tasks 1–3): `feat: add canonical and derived motif models`
- **Commit 2** (after tasks 4–6): `feat: implement motif transformation API`
- **Commit 3** (after tasks 7–9): `feat: add motif authoring and usage UI`
- **Commit 4** (after tasks 10–12): `feat: add thematic LangGraph generation`
- **Commit 5** (after tasks 13–14): `test: verify motif workflow and document contracts`

## Tasks

### Phase 1: Contracts and References

- [x] **Task 1: Add canonical motif references to `composition.v2` and define lifecycle invariants.**
  - Add strict Pydantic models and enums in `backend/app/composition_schemas.py` for motif definitions, occurrence references, relationship kinds, variation strength, and bounded transform provenance. Add `motifs: list[...] = []` to `CompositionV2` without changing the schema discriminator.
  - Validate one original occurrence, unique IDs/labels as appropriate, 3–32 ordered event references per occurrence, one track per occurrence, complete referenced tie chains, globally resolvable event IDs, non-drum source motifs, and bounded metadata. Derive spans from canonical events rather than storing duplicated ticks.
  - Update `backend/app/services/composition_validator.py`, `backend/app/services/composition_normalizer.py`, and `backend/app/services/composition_migration.py` so V1 migration receives an empty motif collection, old V2 remains valid, and canonical integrity catches dangling or inconsistent references.
  - Define controlled reconciliation helpers for event deletion/replacement: remove affected occurrences atomically, delete a definition only when its original occurrence is invalidated, and return stable warning codes. Direct malformed JSON remains a validation error.
  - Extend `frontend/src/utils/musicJsonValidation.js` with a matching validation surface and add pure motif-reference helpers under `frontend/src/utils/compositionMotifs.js`.
  - Tests: extend `backend/tests/test_composition_v2_schema.py`, `backend/tests/test_composition_v2_migration.py`, `backend/tests/test_composition_normalizer.py`, `backend/tests/test_composition_validator.py`, and `frontend/src/utils/musicJsonValidation.test.js`. Cover unknown fields, duplicate/dangling references, ties, event ordering, empty defaults, old V2 payloads, and no duplicated note payloads.
  - Logging: DEBUG counts and sanitized motif/occurrence IDs during validation/reconciliation; INFO schema validation totals; WARN stable codes for pruned occurrences; ERROR only sanitized validation context. Never log event arrays, pitches, prompts, or full compositions.
  - Files: `backend/app/composition_schemas.py`, `backend/app/services/composition_validator.py`, `backend/app/services/composition_normalizer.py`, `backend/app/services/composition_migration.py`, `frontend/src/utils/musicJsonValidation.js`, `frontend/src/utils/compositionMotifs.js`, and corresponding tests.

- [x] **Task 2: Upgrade deterministic repetition analysis to motif families with actionable references.** (depends on Task 1 only for shared terminology, not canonical authority)
  - Extend `backend/app/analysis_schemas.py` with bounded `DetectedMotifFamily`, derived occurrence, and fingerprint-bound note-reference DTOs. Preserve the existing flat `motifs` field for current `composition.analysis.v1` consumers while adding `motif_families`; document which field is authoritative for grouping.
  - Refactor `backend/app/services/composition_repetition_analysis.py` to retain both sides of a match, group deterministic families, suppress exact/transposed/rhythm duplicates by specificity, and emit stable SHA-256-derived IDs.
  - Include normalized duration/onset rhythm, contour, and interval signatures. Add bounded detection for inversion and rational 2:1/1:2 time scaling; group consistent transposed repetitions into sequences. Do not claim automatic answer/counterphrase detection.
  - Reuse or align with `backend/app/services/composition_melody_analysis.py` projection rules and include the projection method/limitations in inference metadata. Keep drum exclusion explicit for v1.
  - Include all canonical event IDs for each logical note where available; use event indexes only as a fallback tied to the report `source_fingerprint`. Ensure ties map to complete member references without using the evidence truncation cap as an authoritative edit source.
  - Increment the analysis algorithm version, preserve deterministic caps/order/truncation behavior, and extend the bounded LLM analysis projection in `backend/app/services/composition_analysis.py` with family IDs, source/target spans, relationship, note count, and score but no event arrays.
  - Tests: extend `backend/tests/test_analysis_schemas.py`, `test_composition_repetition_analysis.py`, `test_composition_analysis.py`, `test_composition_analysis_golden.py`, `test_composition_analysis_scale.py`, and `test_composition_analysis_context.py` for ties, ID-less fallback, polyphony, cross-track families, classification precedence, inversion/scaling/sequence, ordering, caps, and source immutability.
  - Logging: DEBUG stream/family/comparison counts and projection method; INFO family/occurrence totals and status; WARN truncation codes; ERROR analyzer type only. Never log signatures derived as raw pitch/rhythm cells or full reports.
  - Files: `backend/app/analysis_schemas.py`, `backend/app/services/composition_repetition_analysis.py`, `backend/app/services/composition_analysis.py`, optionally a focused `backend/app/services/composition_motif_similarity.py`, analysis fixtures, and tests.

- [x] **Task 3: Implement pure motif extraction, transformation, and identity verification services.** (depends on Task 1)
  - Create `backend/app/services/composition_motif_transform.py` with a compact internal relative-note representation resolved exclusively from canonical event references.
  - Implement deterministic repeat, semitone transpose, interval inversion around an explicit or first-note axis, rational augmentation/diminution, and bounded sequence operations. Preserve relative articulation/velocity/staff/voice where valid, assign collision-free deterministic destination event IDs, and define tie handling explicitly.
  - Implement deterministic rhythmic-variation and melodic-variation realization from constrained proposal DTOs, plus shared answer/counterphrase result validation. Quantize by PPQ/grid policy, enforce track practical range and destination bounds, and reject invalid overlap/boundary cases.
  - Create `backend/app/services/composition_motif_similarity.py` if not created in Task 2. Calculate component scores for normalized rhythm/durations, contour, intervals, and aligned notes, then an operation-specific combined score and threshold based on `variation_strength`.
  - Return ordinary `CompositionV2NoteEvent` values and a bounded verification result; never return a playable symbolic motif object.
  - Tests: add `backend/tests/test_composition_motif_transform.py` with exact event-vector expectations for every operation, positive/negative transpose, inversion axes, rational rounding, variable meter, sequence steps, strength boundaries, source immutability, tie/articulation behavior, pitch-range errors, overflow, deterministic IDs, and canonical round-trip.
  - Logging: DEBUG operation/parameter/count/score summaries; INFO successful operation and resulting event count; WARN quantization/range/threshold diagnostic codes; ERROR sanitized failure code. Do not log note vectors or source payloads.
  - Files: new motif transform/similarity service files and focused tests.

### Phase 2: Motif Application API and AI Variants

- [x] **Task 4: Define typed motif-application API contracts and a thin FastAPI router.** (depends on Tasks 1 and 3)
  - Add `backend/app/motif_schemas.py` for source/destination selectors, operation enum and parameters, variation strength, LLM selection for creative operations, bounded operation diagnostics, and response DTOs.
  - Validate that motif/source occurrence exists, destination section and track agree with composition bounds, destination is pitched, operation parameters are mutually consistent, and LLM selection is required only for creative operations.
  - Add `backend/app/routers/motifs.py` with `POST /motifs/apply`; include it from `backend/app/main.py`. Keep all musical logic in services and map domain errors to sanitized 422/502/503 responses following existing LLM route conventions.
  - Ensure response composition is strict V2 and includes a new canonical occurrence referencing the materialized destination events. Return created IDs and metrics, not copied notes.
  - Add request-size/event-count/operation-count limits before provider invocation and reuse the existing provider settings model.
  - Tests: add `backend/tests/test_motif_schemas.py` and `backend/tests/test_motif_routes.py` for mechanical success, cross-section/cross-track application, malformed references, invalid parameters, destination overflow, unavailable provider, and sanitized errors.
  - Logging: INFO operation start/completion with motif ID, source/destination IDs, operation, provider/model, event counts, and outcome; DEBUG validation/score stages; WARN stable domain codes; ERROR type/code only. Never log instructions, note arrays, or compositions.
  - Files: `backend/app/motif_schemas.py`, `backend/app/routers/motifs.py`, `backend/app/main.py`, and route/schema tests.

- [x] **Task 5: Orchestrate atomic destination replacement and motif-reference reconciliation.** (depends on Tasks 1, 3, and 4)
  - Create `backend/app/services/composition_motif_editor.py` to resolve the canonical source, calculate target ticks through the compiled variable-meter timeline, invoke transformations, and apply an immutable event-level replacement.
  - Reuse `composition_region_patch.py` preservation checks where possible, but avoid replacing an entire bar when only the realized motif span is targeted. Reject target notes/ties that cross replacement boundaries and preserve all events/metadata outside the exact destination span.
  - Append the new occurrence only after destination event IDs are final. Reconcile existing motif occurrences affected by destination replacement and include bounded warning codes in the result.
  - Update generic `composition_region_patch.py` so existing AI region edits cannot leave dangling canonical motif references; preserve unaffected motif definitions byte-for-byte.
  - Verify result with strict V2 validation, canonical integrity, source-region equality, and operation-specific identity checks before returning.
  - Tests: extend `backend/tests/test_composition_region_patch.py` and add service tests for self-overlap, replacing an existing usage, original-source protection, partial tie collisions, destination metadata preservation, failed-operation atomicity, and no symbolic placeholders.
  - Logging: DEBUG resolved bounds, affected occurrence IDs, and preservation checkpoints; INFO applied/pruned occurrence counts; WARN reconciliation codes; ERROR failed invariant/code without note payloads.
  - Files: `backend/app/services/composition_motif_editor.py`, `backend/app/services/composition_region_patch.py`, and tests.

- [x] **Task 6: Add bounded LangGraph drafting/repair for creative motif operations and fake-provider parity.** (depends on Tasks 3–5)
  - Create `backend/app/services/llm_motif_editor.py` with `draft_variation -> validate_variation -> repair_variation -> apply_variation` flow for rhythmic variation, melodic variation, answer, and counterphrase.
  - Prompt with compact relative motif cells, target harmony/section/track range, operation, strength-derived mutation budgets, and output schema. Do not include unrelated composition events or full analysis reports.
  - Validate model proposals through the deterministic transform/similarity services. Route parse failures and identity/range failures through separate bounded retry accounting consistent with existing generation/edit services.
  - Extend `backend/app/services/fake_llm.py` so fake motif editing uses deterministic constrained proposals and the production realization path instead of role-based pitch cycles.
  - Add mocked provider tests for valid proposals, malformed JSON, repair success, exhausted identity failure, provider exceptions, prompt bounds, and source/destination preservation. Ensure tests make no external API calls.
  - Extend `backend/tests/test_secret_hygiene.py` to assert motif requests, logs, and errors never expose instructions, event arrays, API keys, or full prompts.
  - Logging: DEBUG graph stage/attempt/prompt length/score only; INFO provider/model/operation transitions and final status; WARN parse/identity repair codes; ERROR provider type and exhausted code. Remove or replace any generation logging that includes the first characters of user instructions.
  - Files: `backend/app/services/llm_motif_editor.py`, `backend/app/services/fake_llm.py`, `backend/tests/test_llm_motif_editing.py`, `backend/tests/test_secret_hygiene.py`, and related settings/helpers.

### Phase 3: Frontend Authoring, State, and Usage UI

- [x] **Task 7: Add frontend motif extraction, validation, API client, and derived-usage helpers.** (depends on Tasks 1, 2, and 4)
  - Add pure helpers in `frontend/src/utils/compositionMotifs.js` for validating a 3–32-note, one-track, one/two-bar selection; collecting selected event IDs in canonical order; resolving section/bar/tick labels; naming `Motif A`, `Motif B`, etc.; reconciling controlled note edits; and projecting canonical plus detected usages for display.
  - Extend `frontend/src/utils/pianoRollSelection.js` with neutral motif source/destination range helpers that use the compiled variable-meter timeline and reject ranges over two bars rather than silently truncating.
  - Add `applyMotif` to `frontend/src/api/musicApi.js`, validate the typed response and canonical V2 composition, and normalize backend errors using existing API conventions.
  - Extend frontend analysis parsing to understand motif families and treat event-index fallbacks as valid only while the analysis fingerprint matches the current composition revision.
  - Tests: add/extend `compositionMotifs.test.js`, `pianoRollSelection.test.js`, `compositionAnalysis.test.js`, and `musicApi.test.js` for source eligibility, naming, staleness, section recovery, exact payloads, malformed responses, and derived/canonical usage merging.
  - Logging: frontend DEBUG logs may include motif/section/track IDs, operation, strength, counts, and status; WARN validation/staleness codes; ERROR sanitized API code/status. Never log selected note objects, event arrays, or full compositions.
  - Files: `frontend/src/utils/compositionMotifs.js`, `frontend/src/utils/pianoRollSelection.js`, `frontend/src/utils/compositionAnalysis.js`, `frontend/src/api/musicApi.js`, and tests.

- [x] **Task 8: Add Zustand motif state, edit history, analysis invalidation, and project lifecycle behavior.** (depends on Task 7)
  - Extend `frontend/src/store/musicStore.js` with source selection, selected motif, destination, operation parameters, variation strength, request status/error/warnings, and highlighted usage state. Keep motif definitions inside `editedMusicJson.motifs`, not in a second authoritative store collection.
  - Add actions to mark/rename/delete/select a motif; select/navigate usages; configure destination/transformation; and start/complete/fail motif application atomically.
  - Route mark/delete/apply and controlled source-note deletion through the existing immutable note-edit history so each user action has one undo entry, clears redo correctly, stops active playback, updates notation, invalidates analysis, and marks the project dirty.
  - Define explicit reset/hydration behavior for generation, import, project open/new/duplicate/delete, JSON edits, AI region replacement, undo, and redo so motif UI state cannot leak between projects.
  - Update `frontend/src/utils/projectPersistRevision.js` tests to confirm motif metadata changes affect autosave naturally through the canonical composition and survive save/reopen without a separate DB field.
  - Tests: extend `frontend/src/store/musicStore.test.js`, `musicStore.analysis.test.js`, `musicStore.project.test.js`, and `projectPersistRevision.test.js` for atomic failure, undo/redo, source deletion reconciliation, stale usages, cross-project reset, and persistence.
  - Logging: DEBUG action/status transitions with IDs and counts; INFO mark/delete/apply completion; WARN stale/pruned usage codes; ERROR sanitized API failure. Never log note arrays or full store snapshots.
  - Files: `frontend/src/store/musicStore.js`, `frontend/src/utils/projectPersistRevision.js`, and store/persistence tests.

- [x] **Task 9: Build the Motifs tab and piano-roll selection/usage visualization.** (depends on Task 8)
  - Add `frontend/src/components/MotifPanel.jsx` and a `Motifs` tab in `frontend/src/components/ComposerWorkspace.jsx`, preserving existing accessible tab keyboard behavior.
  - In `frontend/src/components/PianoRollEditor.jsx`, expose “Mark as motif” for the current multi-note selection, show the one/two-bar eligibility and selected-note count, and render distinct source, selected usage, and destination overlays without overloading AI region selection.
  - The Motifs tab lists authored definitions and canonical/derived occurrences with relationship, section, track, bars, confidence/identity, and stale/truncated state. “Go to usage” selects the piano-roll track/range and returns to the piano tab.
  - Add destination section, track, start position, operation selector, operation-specific parameters, and a labeled `0..1` variation-strength control. Disable invalid combinations with explicit reasons and require confirmation when destination replacement removes existing notes.
  - Surface loading, repair, provider-required, warning, and failure states without mutating the composition on failure. Keep layout usable at 390px and desktop widths.
  - Keep selection/transformation math in utils/store; components only render and dispatch actions. Follow the existing styled-components visual language rather than introducing a new design system.
  - Tests: cover component-independent behavior through utility/store tests and add Playwright selectors/ARIA assertions for tab navigation, controls, disabled reasons, overlays, mobile overflow, and keyboard access.
  - Logging: DEBUG view/selection/action changes by IDs/counts; INFO submitted operation metadata; WARN confirmation/stale states; ERROR sanitized UI failure. Never log selected events or instructions.
  - Files: `frontend/src/components/MotifPanel.jsx`, `PianoRollEditor.jsx`, `ComposerWorkspace.jsx`, supporting styles/utils, and E2E helpers.

### Phase 4: Thematic LangGraph Generation

- [x] **Task 10: Replace ephemeral motif prose with a bounded structured theme plan.** (depends on Tasks 1 and 3)
  - Replace or evolve `ComposerMotifContext` in `backend/app/services/composition_planner.py` into explicit seed, relative-cell, section deployment, target role/track, operation, parameters, and strength models with strict list/string/event bounds.
  - Add a `plan_themes` node after harmony planning in `backend/app/services/llm_music_generator.py`. The node must choose a valid seed section and at least one later recurrence when the form has multiple suitable sections, while allowing an explicit no-theme result for unsuitable requests.
  - Pass bounded user `prompt.instructions` content into form/theme planning so requests such as “invert the opening motif in the bridge” actually reach the provider. Preserve hard constraints as immutable authority.
  - Refactor melody drafting into seed-first and ordered section-aware outputs. Every later section receives the immutable seed/theme plan plus a bounded prior-section handoff; avoid independent prompts with no thematic context.
  - Feed the structured plan to bass/accompaniment only as relevant compact context. Do not ask harmony metadata to synthesize notes.
  - Tests: extend `backend/tests/test_llm_staged_composer.py` for graph ordering, prompt projection, strict plan bounds, missing/single-section behavior, section handoff, and no raw instruction logging.
  - Logging: DEBUG stage, section index/ID, deployment counts, prompt lengths, and constraint presence; INFO planned theme/target totals; WARN truncation/repair codes; ERROR schema/provider type only. Never log theme pitch/rhythm cells or instruction text.
  - Files: `backend/app/services/composition_planner.py`, `backend/app/services/llm_music_generator.py`, `backend/app/services/generation_constraints.py`, and staged-composer tests.

- [x] **Task 11: Realize, assemble, and validate intentional thematic recurrences during generation.** (depends on Tasks 3 and 10)
  - Add a `realize_themes` graph stage after all needed track drafts exist and before canonical assembly. Use deterministic transforms for mechanical deployments and constrained LLM section drafts for creative deployments.
  - Ensure every generated seed/deployment note has a stable unique event ID before assembly. Build canonical motif definitions/occurrences from those final IDs only after all clamping/normalization decisions are complete.
  - Extend `backend/app/services/generation_constraints.py` and `composition_validator.py` with thematic diagnostics such as `theme_source_empty`, `theme_target_missing`, `theme_transform_mismatch`, `theme_identity_below_threshold`, `theme_target_out_of_bounds`, and `theme_plan_truncated`.
  - Route theme-plan failures back to `plan_themes`, creative deployment failures to the affected section/stage, and assembly/reference failures to `realize_themes`. Preserve unrelated valid section drafts and separate parse from integrity retry budgets.
  - Extend the bounded generation validation report with thematic relationship outcomes and measured scores, without embedding event data.
  - Tests: assert clear derived recurrence across sections, targeted repair, unaffected-section preservation, hard-constraint precedence, motif reference validity, operation verification, deterministic ordering, and canonical playable output.
  - Logging: DEBUG deployment/validation summaries and repair routing; INFO realized/verified recurrence counts and scores; WARN diagnostic codes; ERROR exhausted stage/code. Never log event arrays, cells, or raw provider output.
  - Files: `backend/app/services/llm_music_generator.py`, `generation_constraints.py`, `composition_validator.py`, schema/report models, and `backend/tests/test_llm_staged_composer.py`.

- [x] **Task 12: Make fake generation and mocked AI coverage prove thematic recurrence.** (depends on Tasks 6, 10, and 11)
  - Update `backend/app/services/fake_llm.py` and/or add a motif-aware V2 fixture so fake generation creates a seed and deterministic varied recurrence through production transform/assembly code, not a hand-authored symbolic claim.
  - Add exact fixture vectors asserting the target occurrence is related to `Motif A`, references real destination event IDs, has the expected identity score/operation, and remains audible through the normal event path.
  - Extend route tests for generation/edit responses and fake provider behavior. Add mocked real-provider tests that return structured theme plans and section drafts, including repair and failure paths.
  - Verify user-requested section recurrence, cross-track motif application after generation, and no regression to ordinary generation requests that do not request explicit themes.
  - Logging: DEBUG fake scenario/operation/counts; INFO deterministic recurrence outcome; WARN unsupported fixture constraints; ERROR stable failure code. Never log fixture event arrays or full generated compositions.
  - Files: `backend/app/services/fake_llm.py`, `backend/app/fixtures/`, `backend/tests/fixtures/`, `backend/tests/test_llm_fake_provider.py`, `test_llm_routes.py`, and staged tests.

### Phase 5: Fidelity, Acceptance, and Documentation

- [ ] **Task 13: Integrate motif metadata with persistence, export projection, and full regression gates.** (depends on Tasks 1–12)
  - Confirm project create/update/duplicate/reopen persists canonical motif definitions automatically inside `composition_json`; no SQL migration or separate analysis persistence is expected.
  - Add a stable `motif_metadata_omitted` projection issue in `backend/app/services/composition_projection.py` for MIDI/MusicXML/WAV exports when canonical motif metadata is not representable. Notes must export identically with and without motif metadata.
  - Confirm playback, notation, imports, analysis, JSON editing, and V1 migration ignore motif metadata for sound generation while preserving/validating it as canonical semantic data.
  - Run and fix full backend tests, frontend unit tests/lint/build, focused scale/golden tests, and export fidelity tests. Do not weaken unrelated assertions to accommodate the feature.
  - Tests: extend `backend/tests/test_project_persistence_acceptance.py`, `test_project_routes.py`, `test_export_fidelity.py`, `test_export_routes.py`, `test_composition_midi.py`, and `test_music_json_renderer.py`; add frontend save/reopen assertions.
  - Logging: DEBUG persistence/projection counts and codes; INFO save/reopen/export status; WARN omission code; ERROR sanitized persistence/export type. Never log stored composition JSON, event arrays, or binary/text export payloads.
  - Files: projection/export services, persistence tests, frontend project tests, and any fixture expectations affected by the optional default field.

- [ ] **Task 14: Add end-to-end acceptance coverage and complete mandatory documentation.** (depends on Task 13)
  - Add `frontend/e2e/motif-workflow.spec.js` using fake/mocked AI: select a one- or two-bar melody, mark `Motif A`, inspect source usage, choose another section/track, request a varied version, and assert a clearly derived canonical destination occurrence.
  - Assert source and unrelated events remain unchanged, destination event IDs are new/unique, no symbolic placeholders exist, notation/playback revisions update, usage analysis refreshes, undo/redo works, and save/reopen preserves `Motif A`.
  - Cover invalid >2-bar/empty/percussion selections, stale/deleted sources, destination overlap confirmation, backend 422/502/503 behavior, failed-operation atomicity, analysis truncation, accessible keyboard navigation, and 390px responsive layout.
  - Update E2E helpers and mocked responses so tests assert exact typed request payloads and deterministic transformed note vectors without external provider calls.
  - Route documentation changes through `$aif-docs`. Update `README.md`, `docs/composition-v2.md`, `docs/composition-analysis.md`, `docs/testing.md`, and `docs/CODEBASE_MAP.md` with canonical/derived ownership, operation semantics, strength/identity policy, API contracts/errors, LangGraph stages, export omission, UI workflow, logging hygiene, and test commands. Update `AGENTS.md` only if the project structure/key entry points changed.
  - Final verification commands: `cd backend && ../.venv/bin/python -m pytest`; `cd frontend && npm test`; `cd frontend && npm run lint`; `cd frontend && npm run build`; and `cd frontend && npm run test:e2e -- e2e/motif-workflow.spec.js` against `LLM_FAKE_MODE=1`.
  - Logging: E2E/debug output may report operation IDs, statuses, counts, scores, and error codes only. Add hygiene assertions proving no prompts, selected notes, event arrays, full reports, or compositions are emitted.
  - Files: `frontend/e2e/motif-workflow.spec.js`, `frontend/e2e/helpers.js`, docs listed above, optional `AGENTS.md`, and test configuration only if required.

## Dependency Summary

- Canonical references (Task 1) and deterministic transform/similarity services (Task 3) are the foundation for every mutation path.
- Derived detection (Task 2) can proceed in parallel with Task 3, then feeds frontend usage display and bounded LLM context.
- API contracts/router (Task 4) precede orchestration (Task 5) and creative LangGraph behavior (Task 6).
- Frontend utilities/API (Task 7) precede store lifecycle (Task 8), which precedes UI work (Task 9).
- Structured generation planning (Task 10) and realization/validation (Task 11) reuse the same contracts and deterministic services as editing; fake/mocked coverage follows in Task 12.
- Persistence/export regression work (Task 13) and E2E/docs (Task 14) are final integration gates.

## Acceptance Traceability

- **Select 1–2 bars and mark Motif A:** Tasks 1, 7, 8, 9, and 14.
- **No duplicate notes in motif representation:** Tasks 1–2 schema tests and Task 14 placeholder assertion.
- **Detect repeated/similar patterns:** Task 2, surfaced by Tasks 7 and 9.
- **Repeat/transpose/invert/augment/diminish/sequence:** Tasks 3–5 with exact deterministic vectors.
- **Rhythmic/melodic variation and answer/counterphrase:** Tasks 3, 6, and 12 with mocked/fake AI and deterministic identity gates.
- **Apply to another section or track:** Tasks 4–5 and UI Tasks 7–9.
- **Configurable recognizable variation:** Task 3 scoring/thresholds, Task 6 constrained proposals, and Task 9 strength control.
- **Themes recur intentionally during generation:** Tasks 10–12.
- **Expose usage in UI:** Tasks 2, 7, and 9.
- **Canonical playback events only:** Tasks 3, 5, 11, 13, and E2E Task 14.
- **Tests, mocked AI, logging, documentation:** embedded in every task, with final gates in Tasks 12–14.

## Risks and Mitigations

- **Dangling references after edits:** centralize reconciliation for controlled edits, validate direct JSON strictly, and test every mutation path.
- **Unstable IDs in legacy/generated material:** require IDs only for authored motif membership, retain fingerprint-bound derived fallback references, and finalize generated IDs before motif assembly.
- **False-positive similarity:** expose relation/confidence/limitations, prefer longer and more specific families, and never promote detections to canonical definitions without user action.
- **LLM produces unrecognizable material:** use constrained relative proposals, deterministic score thresholds, bounded repair, and atomic failure.
- **Prompt/payload growth:** cap motifs, notes, targets, proposal size, and analysis projection; pass compact relative cells only.
- **Destructive destination replacement:** preview affected-note counts, require UI confirmation, reject crossing notes/ties, preserve outside content, and provide one-step undo.
- **Schema compatibility:** make `motifs` default-empty, retain the V2 discriminator, preserve the existing analysis flat field while adding families, and add old-document regression tests.
- **Export expectations:** preserve exact notes and report motif metadata omission explicitly rather than inventing lossy interchange encoding.
