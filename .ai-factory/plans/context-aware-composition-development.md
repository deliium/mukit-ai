# Implementation Plan: Context-Aware Composition Continuation and Variation

Branch: main (branch creation disabled by `.ai-factory/config.yaml`)
Created: 2026-09-09

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes (mandatory `$aif-docs` checkpoint at completion)

## Goal

Let users develop the current canonical `composition.v2` without replacing unrelated material. The workflow must support appending bars, adding a named section, replacing a selected section with an alternative, producing several independently generated candidates, developing an intro into a verse/chorus/bridge/outro, and creating a contrasting B section that retains recognizable composition identity.

The acceptance baseline is a finished 16-bar A section: requesting an 8-bar continuation returns one or more musically related, canonical 24-bar candidates while the original 16 bars remain exactly unchanged until the user selects and applies one candidate.

## Architecture Decisions

- Add a cohesive Composition Development module rather than extending the new-piece generator or loosening `replace_region` invariants. Use `backend/app/composition_development_schemas.py`, `backend/app/routers/composition_development.py`, and focused services under `backend/app/services/`.
- Expose one stateless `POST /composition/development/preview` endpoint. The backend never persists a candidate; project save remains a separate frontend action after Apply.
- Support three operations: `continue`, `add_section`, and `vary_section`. `continue` and `add_section` append bars at the current duration; `vary_section` replaces an existing inclusive bar/section range at the same duration.
- Model musical direction separately as `development_intent: continue | develop | contrast`. `add_section` also accepts a target section type (`intro`, `verse`, `chorus`, `bridge`, `outro`, or an already-supported canonical section type) and a bounded optional label.
- Require `variation_strength: conservative | balanced | experimental` for every operation. It controls provider instructions and deterministic identity/seam thresholds, not canonical structural permissions.
- Bound `candidate_count` to 1-4. Generate every candidate through a separate provider invocation with its own candidate index/creative direction; do not ask one prompt to return an array. Validate and, at most, repair each draft independently. Return all valid candidates and bounded failure warning codes; fail the request with a structured `502` only when no candidate survives.
- Accept canonical `CompositionV2` input only for this new API. Existing project/import/generation boundaries already normalize V1; rejecting V1 here avoids hidden migration loss in an edit-preview operation.
- Preserve track topology and instrumentation in the first version. Append operations continue every existing track; variation replaces every selected-range track entry while retaining all track IDs, order, roles, instruments, channels, programs, staff metadata, and automation. Adding/removing tracks is out of scope.
- Have providers return bounded relative drafts, never a complete composition. Deterministic services own absolute ticks, IDs, section/timeline extension, harmony placement, motif reconciliation, merge authorization, and final validation.
- Treat `tracks[].events[]` as the only playable source. Harmony, motifs, analysis, and inferred rhythmic character are context/advisory metadata and never synthesize audible events outside the validated provider draft.
- Add a versioned full-document `edit_source_fingerprint` based on canonical complete V2 serialization. Do not reuse the analysis fingerprint, which intentionally excludes expression/marker data. Candidate IDs derive from source fingerprint, normalized operation request, candidate fingerprint, and algorithm version.
- Return complete validated candidate compositions for atomic client apply, plus bounded scope/change/assertion summaries. Keep candidates ephemeral in Zustand and select by stable candidate ID, never by array index.
- Preview, candidate selection, and audition must not mutate `editedMusicJson`, history, revisions, analysis freshness, notation, dirty state, or autosave. Apply must recheck source and candidate fingerprints, use the existing atomic composition edit path, create one undo entry, stop preview playback, mark dirty, and autosave normally.
- Reuse compiled variable-meter boundaries. Never calculate append duration as root meter multiplied by bar count when the source has time-signature changes.
- Default seam policy is immutable prefix: no existing event, tie, marker, harmony span, automation point, motif occurrence, or other source field may be altered. Reject source events/ties that require mutation across the old end boundary with a stable, actionable error in this version.

## API Contract Outline

Request fields:

- `composition`: strict `CompositionV2`.
- `operation`: `continue | add_section | vary_section`.
- `source`: optional section ID or inclusive `start_bar`/`end_bar`; required for `vary_section`, otherwise used as bounded musical context and defaulted to the trailing section/bars.
- `output_bars`: required and bounded for append operations; omitted for variation, whose output span equals the selected span.
- `target_section_type` and optional `target_section_label`: used by `add_section`; section type is optional for plain continuation.
- `development_intent`: `continue | develop | contrast`.
- `variation_strength`: `conservative | balanced | experimental`.
- `candidate_count`: 1-4.
- `allow_modulation`: defaults false; when true, key changes are still restricted to generated bar boundaries.
- `instruction`: optional normalized bounded text.
- `selection_options`: provider/model.
- `options`: bounded repair count and context budget using existing LLM request conventions.

Each candidate response includes:

- `candidate_id`, `candidate_fingerprint`, and shared `edit_source_fingerprint`.
- `algorithm_version` and operation/strength metadata.
- Complete canonical candidate `composition`.
- Resolved source and output bar/tick bounds.
- Section, harmony, motif, and per-track event-count change summaries.
- Required preservation assertions and musical identity/seam diagnostics with stable codes.
- Provider/model and bounded warning codes.

## Canonical and Musical Invariants

- The source composition object remains immutable during request processing.
- For append operations, the entire canonical source document is an exact prefix: all existing arrays retain order and values; existing sections are unchanged; only new section/timeline/harmony/motif material and new events at or after the old `duration_ticks` are appended.
- For variation, all content outside `[start_tick, end_tick)` is exact, unauthorized structural metadata is unchanged, boundary-crossing events/tie chains are rejected, and the selected section geometry remains unchanged.
- `bar_count`, `duration_ticks`, section coverage, timeline changes, markers, harmony spans, event bounds, ties, pedals, automation, motif references, and IDs satisfy strict `CompositionV2` validation after realization.
- Append boundaries inherit the active ending tempo, meter, key, harmony/form context, and rhythmic profile unless an explicitly allowed generated boundary change is supplied.
- Existing track order/metadata are identical and every track has an explicit generated draft entry, including an empty entry when intentional rests are musically valid.
- New IDs are deterministic and collision-safe. Existing IDs never change; new motif occurrences reference real generated event IDs.
- Context is bounded and structured: recent seam bars, selected/source section, adjacent sections, active timeline state, recent harmony, motif exemplars, track roles/instruments/registers, rhythmic density/onset profile, and bounded non-authoritative analysis projection.
- Identity diagnostics compare motif recurrence/similarity, pitch/register movement, rhythmic/onset character, density by role, harmonic continuity, and seam interval/rest behavior. Conservative failures trigger repair/rejection; balanced uses moderate limits; experimental permits greater divergence but still requires at least one identity anchor and canonical seam continuity.
- No prompt, full composition, event array, provider raw output, API key, or full analysis report is logged.

## Commit Plan

- **Commit 1** (after tasks 1-3): `feat: add composition development domain engine`
- **Commit 2** (after tasks 4-6): `feat: expose composition development candidates`
- **Commit 3** (after tasks 7-9): `feat: add composition candidate workflow`
- **Commit 4** (after tasks 10-12): `test: cover composition development workflows`

## Tasks

### Phase 1: Domain Contract and Deterministic Core

- [ ] Task 1: Define strict development request, draft, candidate, assertion, summary, and error contracts, plus full edit fingerprints.
  - Files: create `backend/app/composition_development_schemas.py` and `backend/app/services/composition_edit_fingerprint.py`; update schema re-exports only if existing import conventions require it; add `backend/tests/test_composition_development_schemas.py` and `backend/tests/test_composition_edit_fingerprint.py`.
  - Deliverable: implement the operation, intent, strength, source selection, output bars, target section, candidate-count, provider selection, and options DTOs described above. Enforce operation-specific combinations, request/candidate count and text bounds, unique candidate IDs, bounded warnings/assertions, and strict extra-field rejection.
  - Deliverable: define versioned canonical full-document serialization and SHA-256 profiles for source and candidate edit concurrency. Include every persisted Composition V2 field, preserve array order, sort object keys, use ASCII compact JSON, and expose a short prefix helper for logs.
  - Expected behavior: semantically identical complete V2 documents fingerprint identically; any expression, marker, motif, event ID, timeline, section, harmony, or track metadata change invalidates the edit fingerprint. Request validation rejects variation without a source, append without positive output bars, unsupported section types, and excessive candidate/context sizes.
  - Tests: fingerprint determinism/sensitivity, source immutability, request matrix, boundary values, strict OpenAPI-compatible serialization, stable candidate ID derivation, and no collisions across differing operation/candidate fingerprints.
  - Logging requirements: DEBUG only for fingerprint profile/version, short fingerprint prefix, encoded byte count, and bounded composition counts. Log schema validation failures by stable code/field and counts; never log source/candidate JSON, instructions, or events.
  - Dependencies: none.

- [ ] Task 2: Build bounded, deterministic source-context extraction and identity/seam evaluation.
  - Files: create `backend/app/services/composition_development_context.py` and `backend/app/services/composition_development_identity.py`; add `backend/tests/test_composition_development_context.py` and `backend/tests/test_composition_development_identity.py`; reuse `composition_timeline.py`, `composition_analysis.py`, `composition_analysis_context.py`, and motif/role/repetition helpers without persisting analysis.
  - Deliverable: resolve source section or bars, append seam, active tempo/meter/key, recent harmony, prior/next section relationships, motif exemplars with relative notes, track role/instrument/register summaries, rhythmic/onset/density character, cadence/seam notes, and a clamped advisory analysis projection. Use compiled bar boundaries for mixed meter.
  - Deliverable: define versioned strength profiles and stable diagnostics for motif/identity anchors, register/rhythm/density divergence, harmonic continuity, seam gaps/leaps, missing track drafts, and restart-like openings. Classify checks as required errors or advisory warnings by strength.
  - Expected behavior: a trailing A-section context retains enough identity information to prompt an 8-bar continuation without dumping the complete source; contrast mode increases allowed divergence while retaining at least one motif, rhythmic, harmonic, or orchestration anchor. Empty harmony and optional motifs remain valid inputs.
  - Tests: fixed/mixed meter, modulation at the seam, no harmony/motifs, ID-less and duplicate-type sections, sparse/drum tracks, context budget truncation order, each strength profile, contrast identity anchors, source-boundary ties, and deterministic output.
  - Logging requirements: INFO for operation, resolved bar/tick scope, strength, intent, source counts, and context truncation status; DEBUG for metric names/rounded scores and diagnostic codes only. Never log pitches, relative-note arrays, full analysis, or prompt text.
  - Dependencies: Task 1.

- [ ] Task 3: Implement deterministic append and variation draft realization with exact preservation gates.
  - Files: create `backend/app/services/composition_development_patch.py`; update `backend/app/services/composition_region_patch.py` only to extract genuinely reusable public helpers without weakening existing behavior; add `backend/tests/test_composition_development_patch.py` and focused regressions to `backend/tests/test_composition_region_patch.py` if shared code changes.
  - Deliverable: validate provider drafts relative to output scope, translate relative ticks to absolute ticks, allocate deterministic collision-safe IDs, preserve track topology/metadata, append or replace events, extend sections/duration/bar count, append allowed key/meter/tempo/harmony/motif metadata at canonical boundaries, reconcile motif references, and construct strict V2.
  - Deliverable: compare the full source against the result under operation-specific preservation profiles before and after `CompositionV2.model_validate` and canonical integrity validation. Return machine-readable per-track changes, preservation assertions, and resolved ranges.
  - Expected behavior: append leaves every pre-seam source value exactly unchanged and emits only in-range additions; variation leaves all content outside its half-open range exact and preserves section geometry. Existing boundary-crossing notes/ties that cannot be preserved are rejected rather than split or edited. Provider attempts to add/remove tracks, alter instrumentation, overflow ranges, overlap harmony, introduce duplicate IDs, or reference missing motif events fail deterministically.
  - Tests: the exact 16-bar A plus 8-bar acceptance case; all-track continuation; fixed and variable meter; ending key/tempo inheritance; allowed boundary modulation; empty harmony; expressive lanes/markers/pedals preservation; ID collision allocation; malformed ties; event/harmony overflow; missing/extra tracks; motif recurrence/reconciliation; no input mutation; full V2 validator pass; MusicXML/MIDI projection smoke for an accepted candidate.
  - Logging requirements: INFO for operation, old/new bar and tick counts, changed track count, created event count, preservation status, and diagnostic codes; DEBUG for bounded per-track counts and ID collision counts. Never log event IDs in bulk, pitches, harmony arrays, or complete documents.
  - Dependencies: Tasks 1 and 2.

### Phase 2: LLM Orchestration, Fake Provider, and HTTP Boundary

- [ ] Task 4: Add independent multi-candidate provider orchestration, bounded prompts, repair, and fake generation.
  - Files: create `backend/app/services/llm_composition_development.py`; update `backend/app/services/fake_llm.py`; minimally extract shared provider/model selection from `backend/app/services/llm_music_generator.py` into a focused provider helper only if necessary to avoid importing a private function; add `backend/tests/test_llm_composition_development.py` and extend `backend/tests/test_llm_fake_provider.py`.
  - Deliverable: select configured OpenAI/DeepSeek/fake providers using existing semantics, derive prompts from immutable structural constraints plus Task 2 context, and invoke structured output once per candidate. Give each call a candidate ordinal and bounded creative direction while preserving the same hard constraints.
  - Deliverable: validate and realize each draft independently, issue at most the configured bounded repair for that candidate using only diagnostic codes/bounded summaries, and collect valid candidates. Return partial success with failed-candidate warning codes; raise a sanitized domain/provider error when all fail.
  - Deliverable: implement deterministic fake drafts for continuation, named-section addition, selected-section variation, contrast mode, all strength levels, multiple distinct candidates, and malformed-output injection. Fake drafts must pass through the production realization and validation path rather than returning preassembled compositions.
  - Expected behavior: `candidate_count=3` causes three separate provider calls and produces independently fingerprinted candidates; failure/repair of one candidate cannot mutate or contaminate another. Prompts explicitly preserve immutable source, continue each track role, bridge the seam, use harmony/motifs/instrumentation/rhythm/sections as context, and distinguish strength/intent.
  - Tests: mocked structured-output call counts and prompt constraints, OpenAI/DeepSeek model/base URL selection, deterministic fake output, candidate distinctness, one-candidate repair, partial success, all-failed `502`, timeout/provider exceptions, malformed JSON/schema, oversized context/request rejection, and no network in fake mode.
  - Logging requirements: INFO per request and candidate for provider/model, candidate ordinal, operation, strength, stage, attempt, elapsed time, outcome code, and count summaries; DEBUG for prompt byte/character budgets and diagnostic-code counts. Never log prompts, instructions, provider output, API keys, full candidates, or event arrays.
  - Dependencies: Tasks 1-3.

- [ ] Task 5: Expose the stateless development preview router with structured errors and safe observability.
  - Files: create `backend/app/routers/composition_development.py`; update `backend/app/main.py` only to register the router; add `backend/tests/test_composition_development_routes.py`; extend `backend/tests/test_openapi_v2.py` and `backend/tests/test_secret_hygiene.py`.
  - Deliverable: implement `POST /composition/development/preview`, load LLM settings at the HTTP boundary, call the service, return the strict response model, and map invalid composition/scope/preservation errors to `422`, missing provider to `503`, provider/invalid-output exhaustion to sanitized `502`, and unexpected failures to `500`.
  - Deliverable: preserve bounded lists of stable error/assertion codes in safe error details rather than dropping every non-scalar field. Do not render MusicXML or persist projects in this route.
  - Expected behavior: request validation and all domain/provider errors have stable `{code, message, details?}` shapes; a successful response has 1-4 canonical candidates sharing the correct source fingerprint. OpenAPI exposes strict Composition V2 request/candidate models.
  - Tests: route success for all operations; 16+8 fake acceptance; candidate count; V1/invalid V2; invalid ranges/combinations; no provider; unsupported provider/model; partial and total provider failure; source immutability; no SQLite writes; OpenAPI; CORS compatibility; bounded safe details; secret/log hygiene.
  - Logging requirements: INFO start/completion with operation, intent, strength, requested/returned candidate counts, source/output bars, provider/model, fingerprint prefix, warning-code count, and elapsed milliseconds; WARN/ERROR once at the mapped boundary with stable code and exception type. Never log instructions, source/candidate payloads, events, prompts, or keys.
  - Dependencies: Task 4.

- [ ] Task 6: Complete backend regression, scale, and export-fidelity coverage for development candidates.
  - Files: extend the new backend test files from Tasks 1-5; update `backend/tests/test_composition_timing.py`, `backend/tests/test_export_fidelity.py`, or shared fixtures only where cross-module behavior requires coverage; add a compact deterministic fixture under `backend/tests/fixtures/` only if builders cannot express the 16-bar A case clearly.
  - Deliverable: assemble a focused backend suite that proves canonical timing/sections after append and exact source/outside-range preservation using canonical serialization, not only analysis fingerprints. Cover candidate bounds and ensure output remains consumable by MusicXML/MIDI/playback projections.
  - Expected behavior: existing generation, region editing, motif, harmony, analysis, migration, and export suites remain unchanged in semantics. Large-but-allowed input with four candidates remains within explicit request/context/event caps; over-limit requests fail before provider invocation.
  - Tests: parameterize 4/4, 3/4, 6/8, mixed-meter, modulation, imported-style empty harmony, motifs, expressive V2, sparse tracks, and drums. Add captured-log assertions for allowed keys and prohibited prompt/event/API-key material.
  - Logging requirements: tests must assert stage/provider/count/code logs at DEBUG while proving raw instructions, distinctive pitches/event IDs, serialized compositions, and secrets are absent.
  - Dependencies: Tasks 1-5.

### Phase 3: Frontend Candidate Lifecycle and UX

- [ ] Task 7: Add frontend API contracts and pure candidate verification/selection helpers.
  - Files: update `frontend/src/api/musicApi.js` and `frontend/src/api/musicApi.test.js`; create `frontend/src/utils/compositionCandidates.js` and `frontend/src/utils/compositionCandidates.test.js`; extract generic section-key helpers from `frontend/src/utils/compositionAnalysis.js` into `frontend/src/utils/compositionSections.js` only if this avoids duplicating stable ID-less section handling.
  - Deliverable: add `previewCompositionDevelopment`, a typed API error, strict request normalization, candidate-array bounds/uniqueness checks, canonical validation of each composition, metadata/assertion validation, and non-mutating request behavior.
  - Deliverable: implement operation-specific defaults, source/section resolution, candidate lookup by ID, local full edit fingerprinting compatible with the backend profile, and independent apply verification for source fingerprint, candidate fingerprint, exact append prefix or variation outside-range preservation, track topology, and required assertions.
  - Expected behavior: malformed or duplicate candidates never enter store state. Apply verification recomputes the selected candidate fingerprint locally instead of trusting the response value. ID-less sections remain uniquely selectable by stable section key.
  - Tests: exact request payloads for all operations, multiple candidate normalization, duplicate/missing IDs, malformed candidate, invalid fingerprints/assertions, typed 4xx/5xx errors, request immutability, section recovery, mixed-meter ranges, exact preservation checks, and backend/frontend fingerprint vectors.
  - Logging requirements: API/util diagnostics may log operation, status, candidate count, stable code, and fingerprint prefixes only. Never `console` full request/response data, instructions, compositions, or events.
  - Dependencies: Tasks 1 and 5.

- [ ] Task 8: Implement ephemeral Zustand candidate state, request races, selection, audition source, and atomic Apply.
  - Files: update `frontend/src/store/musicStore.js`; update `frontend/src/store/musicStore.test.js` or create `frontend/src/store/musicStore.development.test.js`; update playback-source helpers under `frontend/src/utils/` only as needed.
  - Deliverable: add controls for operation, source section/range, output bars, target section, intent, strength, candidate count, and instruction; add request status/error/warnings, request sequence, base revision/fingerprint, candidates, selected candidate ID, and preview playback source.
  - Deliverable: request candidates without modifying authoritative state; ignore out-of-order responses; reject responses if `compositionRevision` changed while loading; clear stale candidates on generation/import/project hydration/JSON edits/AI edits/motif/harmony edits/undo/redo/project changes; preserve controls when appropriate.
  - Deliverable: select/discard candidates without dirtying or autosaving; audition the selected candidate from its canonical `tracks[].events[]` without assigning it to `editedMusicJson`; stop working playback before audition and stop candidate playback on selection/discard/base edits. Apply only after local verification, through the existing `applyCompositionEdit` path, with one undo snapshot and normal dirty/autosave/analysis/notation behavior. Leave `generatedMusicJson` as the original generation/import reset baseline.
  - Expected behavior: preview is observational and ephemeral; apply is one atomic authored edit. A stale/tampered candidate is blocked with a stable UI error and cannot modify history or project state.
  - Tests: A/B/A races, base edit during request, base edit after response, selection non-mutation, discard, preview failure, candidate tampering, independent fingerprint recomputation, apply/undo/redo, one history entry, autosave only after apply, analysis/notation revision behavior, playback switching/stopping, reset baseline, project/import/generation invalidation, and memory-safe bounded candidate state.
  - Logging requirements: INFO for request/apply/discard/audition transitions with operation, candidate count/ID suffix or fingerprint prefix, source/output ranges, revision, and status code; DEBUG for race/stale reasons and bounded assertion codes. Never log candidates, events, prompts, or instructions.
  - Dependencies: Task 7.

- [ ] Task 9: Build a responsive Composition Development tab for controls, candidate comparison, audition, and explicit commit.
  - Files: create `frontend/src/components/CompositionDevelopmentPanel.jsx`; update `frontend/src/components/ComposerWorkspace.jsx`, `frontend/src/components/PianoRollEditor.jsx`, `frontend/src/components/AiRegionEditPanel.jsx`, and `frontend/src/components/PlaybackControls.jsx` only where needed for tab handoff/selection/audition; add reusable styles/components locally unless already shared.
  - Deliverable: add a full-width `Develop` tab. Present Continue, Add section, and Vary section modes; stable section/range controls; append length; target section type; Continue/Develop/Contrast intent; Conservative/Balanced/Experimental strength; candidate count; provider/model context; and optional instruction.
  - Deliverable: default continuation to the trailing section/bars and variation to the current piano-roll AI selection. Allow the existing region panel to open the Develop tab with that selection instead of duplicating candidate logic.
  - Deliverable: render independently selectable candidate cards with IDs/labels, added/replaced range, section/track/harmony/motif summaries, identity/seam diagnostics, warnings, and required assertion status. Provide Generate/Regenerate, Audition/Stop, Apply selected, and Discard actions. Require explicit confirmation before replacing an existing section.
  - Expected behavior: controls communicate that previews do not alter/save the composition; Apply is disabled for loading, missing selection, stale source, invalid fingerprint, or failed required assertions. Candidate selection and keyboard navigation are accessible; status uses `aria-live`; 390px and desktop layouts do not overflow.
  - Tests: expose stable `data-testid` hooks for Playwright; avoid adding a component-test dependency unless needed. Verify tab keyboard semantics, labels/help for strength and contrast, disabled reasons, loading/error/partial-success states, and candidate-audition indicator through E2E in Task 11.
  - Logging requirements: UI logs only bounded user-action/status metadata already defined by store/API conventions; no render-time logging and no raw musical content.
  - Dependencies: Task 8.

### Phase 4: Acceptance, Documentation, and Quality Gates

- [ ] Task 10: Finish frontend API, utility, store, and playback regression coverage.
  - Files: complete `frontend/src/api/musicApi.test.js`, `frontend/src/utils/compositionCandidates.test.js`, `frontend/src/store/musicStore.development.test.js` (or existing store tests), and relevant `frontend/src/utils/playback*.test.js` suites.
  - Deliverable: prove non-mutating preview and candidate selection, stale/tamper rejection, canonical extension validation, source/outside-range preservation, candidate audition isolation, and one-step apply/undo across all three operations.
  - Expected behavior: existing generation, import, analysis, motif, harmony, piano-roll, playback, project, save, and reset tests continue to pass. No candidate state appears in persisted project payloads.
  - Tests: include the 16-bar A/8-bar continuation vector, several separately returned candidates with selection of a non-first candidate, named verse/chorus/bridge/outro addition, contrast B, all strengths, variable meter, empty harmony, and section variation outside-range equality.
  - Logging requirements: captured console tests permit operation/count/status/code/fingerprint-prefix metadata and reject full payloads, event arrays, instructions, and candidate documents.
  - Dependencies: Tasks 7-9.

- [ ] Task 11: Add deterministic Playwright acceptance for continuation, variation, stale protection, persistence, and mobile layout.
  - Files: create `frontend/e2e/composition-development-workflow.spec.js`; update `frontend/e2e/helpers.js` and Playwright fixture/routing helpers as needed.
  - Deliverable: with `LLM_FAKE_MODE=1`, open a finished 16-bar A composition, request three independent 8-bar continuation candidates, prove the working composition/revision/save state/history remain unchanged, audition and select a non-default candidate, apply it, and verify a canonical 24-bar result whose original 16 bars are deep-equal to the source.
  - Deliverable: cover add-section transitions from intro to verse/chorus/bridge/outro, contrasting B identity diagnostics, selected-section alternative replacement, exact outside-range preservation, undo/redo, save/reopen of only the applied candidate, discard, and edit-after-preview stale blocking.
  - Deliverable: test keyboard tab/candidate selection, status/error accessibility, partial candidate warnings, and no horizontal document overflow at 390px.
  - Expected behavior: unselected candidates are never persisted; preview playback does not dirty the project; playback/export after Apply use the chosen candidate's canonical events.
  - Logging requirements: E2E may inspect sanitized operation/candidate-count/status/code logs only. Do not emit or attach full composition/event/prompt data except normal Playwright failure artifacts already governed by repository policy.
  - Dependencies: Tasks 5, 9, and 10.

- [ ] Task 12: Document the Composition Development contract, lifecycle, controls, safety guarantees, and test commands.
  - Files: create `docs/composition-development.md`; update `README.md`, `docs/composition-v2.md`, `docs/testing.md`, `docs/CODEBASE_MAP.md`, `.env.example` only if new limits/settings are introduced, and `AGENTS.md` if the project structure/key entry points change. Route documentation work through the mandatory `$aif-docs` checkpoint.
  - Deliverable: document operations/intents/strength semantics, request/response examples, independent candidate generation, stateless preview/apply lifecycle, full edit fingerprints, source preservation, track/instrument rules, mixed-meter extension, motifs/harmony/analysis context, seam policy, partial candidate success, fake mode, error codes, limits, and privacy-safe logging.
  - Deliverable: add focused backend/frontend/E2E commands and the 16+8 acceptance scenario. State clearly that analysis is derived/advisory, harmony is non-playable metadata, candidates are not persisted until Apply, and source ties crossing an immutable append boundary are rejected in this version.
  - Expected behavior: README links the detailed page without duplicating it; codebase maps point to the new schema/router/services/component/store flow. Reconcile the documented `analysis.native.v1` versus code `analysis.native.v2` version drift discovered during planning if still present, without changing analysis behavior.
  - Logging requirements: documentation must list allowed fields (operation, stage, provider/model, counts, timings, codes, fingerprint prefixes) and prohibited data (keys, prompts/instructions, full compositions/analysis, event/harmony arrays, provider raw output).
  - Dependencies: Tasks 5, 9, 10, and 11.

- [ ] Task 13: Run focused and full quality gates and fix regressions without weakening invariants.
  - Files: no planned artifact beyond corrections required by failing checks; do not create a test report.
  - Deliverable: run backend focused development tests, full backend pytest, frontend unit tests, frontend production build, focused Playwright fake-provider workflow, and relevant existing harmony/motif/editing E2E regressions when the stack is available.
  - Commands: from `backend/`, `../.venv/bin/python -m pytest tests/test_composition_development_schemas.py tests/test_composition_edit_fingerprint.py tests/test_composition_development_context.py tests/test_composition_development_identity.py tests/test_composition_development_patch.py tests/test_llm_composition_development.py tests/test_composition_development_routes.py`; then `../.venv/bin/python -m pytest`. From `frontend/`, run `npm test`, `npm run build`, and `npm run test:e2e -- e2e/composition-development-workflow.spec.js` against `LLM_FAKE_MODE=1`.
  - Expected behavior: all gates pass; the acceptance test proves original 16-bar deep equality, canonical 24-bar timing/sections, separate candidate generation, explicit selection, and apply-only mutation. If the live E2E stack is unavailable, record that limitation in the final implementation response rather than claiming the test passed.
  - Logging requirements: run focused captured-log/secret-hygiene assertions at DEBUG and inspect failures for prohibited payload leakage; do not increase logs to include raw provider or composition data while diagnosing.
  - Dependencies: Tasks 1-12.

## Risks and Mitigations

- Full candidate documents can consume memory. Enforce 1-4 candidates, request/event/context caps, avoid cloning all candidates on selection, and clear previews aggressively when the source changes.
- Multiple provider calls increase latency and cost. Surface requested/returned counts and per-candidate status, permit partial success, bound retries independently, and do not silently multiply retries beyond the request cap.
- Musical relatedness is not completely provable. Combine strong prompt context with deterministic bounded identity/seam metrics and transparent warnings; keep canonical/preservation checks hard and musical scoring versioned.
- Mixed meter makes append duration nontrivial. Extend from compiled active meter and validate newly compiled `bar_count + 1` boundaries before accepting a candidate.
- A source may end with a note/tie that conceptually continues. Preserve source immutability by rejecting unsupported seam crossings now; add an explicit future boundary-rewrite policy rather than silently altering the prefix.
- Analysis fingerprints are intentionally incomplete. Use the new full edit fingerprint for candidate concurrency and keep analysis fingerprints only for analysis caching/advisory context.
- Preview playback can conflict with working playback. Use one explicit playback source, stop before switching, key scheduling by candidate fingerprint, and reset on candidate/source changes.
- Replacing event IDs can break motifs. Reconcile only affected occurrences, preserve unaffected motif definitions exactly, and validate every resulting occurrence against real event IDs.
- Existing section IDs are optional. Select sections with stable composite keys in the UI and resolve to exact bars server-side; never use section type alone when repeated sections exist.
- Frontend revisions protect one browser session, not concurrent clients. Full source fingerprints prevent local stale apply; project-level compare-and-swap persistence remains separate future work.

## Definition of Done

- A 16-bar A composition can request three separately generated 8-bar continuations in fake and mocked-provider tests.
- Every returned continuation is strict canonical `CompositionV2`, has 24 bars with exact section/timeline coverage, continues all existing tracks, and preserves the complete original 16-bar prefix.
- Users can generate, inspect, audition, select, discard, and apply candidates; nothing persists or mutates before Apply.
- Add-section, intro-to-target-section, selected-section variation, and contrasting B workflows are covered at all three strength levels.
- Source/candidate fingerprints and revision checks block stale or tampered applies.
- Apply is atomic, undoable, autosaved normally, and used by playback/notation/export after commit.
- Backend unit/route/mock/fake tests, frontend API/util/store tests, Playwright acceptance, full pytest, `npm test`, and `npm run build` pass.
- Logging is verbose and useful while satisfying secret/payload hygiene.
- Documentation and structural maps are updated through the mandatory docs checkpoint.
