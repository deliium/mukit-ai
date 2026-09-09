# Implementation Plan: Interactive Harmony Editing and AI-Assisted Reharmonization

Branch: main
Created: 2026-09-09

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Roadmap Linkage
Milestone: "Interactive harmony and reharmonization"
Rationale: This is a new milestone beyond the completed canonical V2, analysis, and motif milestones because it adds an authored harmony timeline plus a previewed operation that atomically changes canonical note events.

## Goal

Make harmony an explicit, editable `composition.v2` timeline and provide deterministic and AI-assisted reharmonization previews without weakening the rule that audible music comes only from `tracks[].events[]`.

The required acceptance path is:

1. Open a composition with at least 12 bars.
2. Select inclusive bars 9-12.
3. Choose `increase_tension` and `preserve_melody_adapt_harmony`, or enter the equivalent instruction "make the harmony more tense while keeping the melody".
4. Request a preview.
5. Verify the preview has not changed, dirtied, autosaved, or persisted the open composition.
6. Review changed harmony spans, target accompaniment/bass tracks, event counts, compatibility findings, and preservation guarantees.
7. Apply the preview once.
8. Verify every melody event is byte-for-byte unchanged, harmony and at least one explicitly targeted accompaniment/bass track changed coherently inside bars 9-12, unrelated events and structural metadata remain unchanged, and undo restores the exact source composition.

## Scope

### Included
- Normalize canonical harmony metadata to explicit tick ranges.
- Add immutable harmony timeline add, replace, remove, move, and resize operations.
- Add a dedicated Harmony tab with an accessible, variable-meter-aware timeline.
- Add deterministic and optional LLM-backed progression/reharmonization strategies.
- Model melody/accompaniment preservation policy explicitly in the API and preview.
- Validate declared harmony against canonical notes using tonal, chord-tone, bass, and tension evidence without requiring every pitch or chord to be diatonic.
- Keep preview state ephemeral until explicit apply.
- Preserve project, undo/redo, autosave, notation, analysis, export, and import behavior.
- Add backend, frontend, API, store, renderer, persistence, Playwright, fake-provider, logging, and documentation coverage.

### Excluded
- Synthesizing audible notes directly from `harmony` during playback or export.
- Automatically inferring harmony during MIDI/MusicXML import; raw imports continue to set `harmony: []`.
- Silent modulation or implicit mutation of root `key` / `key_changes`.
- Persisting preview drafts or `composition.analysis.v1` inside composition JSON.
- Automatically rewriting drums, percussion, countermelody, or imported `other` tracks without explicit target IDs.
- A database schema migration; projects already persist the whole normalized composition as JSON.

## Architecture Decisions

### Canonical harmony representation

Replace operational V2 `{bar, chord}` points with sorted, non-overlapping half-open tick spans:

```json
{
  "start_tick": 15360,
  "duration_ticks": 1920,
  "chord": "E7(b9)"
}
```

Rules:
- `start_tick >= 0`, `duration_ticks > 0`, and `start_tick + duration_ticks <= composition.duration_ticks`.
- Harmony spans are sorted by `start_tick`, may be adjacent, and may contain gaps, but may not overlap or duplicate a start tick.
- Tick timing is authoritative so mixed meter and later sub-bar harmonic rhythm remain unambiguous. Bar labels are derived from the compiled meter map and are never a second timing authority.
- Chord text remains trimmed, case/spelling preserving, and bounded. Chromatic roots, accidentals, slash basses, extensions, altered dominants, diminished chords, and borrowed chords are allowed.
- Harmony remains metadata. It never becomes a fallback note source for Tone.js, MIDI, WAV, or canonical MusicXML notes.

Compatibility is required because persisted V1 and V2 projects contain `{bar, chord}`:
- V1 migration and V2 pre-normalization interpret each old item as a change point beginning at that bar boundary.
- Each old item ends at the next distinct declaration or `duration_ticks`; no harmony is invented before the first declaration.
- Stable-sort old entries and collapse duplicate bars with explicit last-declaration-wins behavior, matching current MusicXML projection behavior; emit bounded diagnostic/log counts rather than chord payloads.
- Reject out-of-range legacy bars and mixed old/new harmony shapes. Do not clamp corrupt persisted data or rewrite its database row after failed normalization.
- Canonical model dumps, API responses, and successful project rewrites contain only explicit spans.
- Update the V1 fidelity gate to compare a documented legacy projection rather than requiring obsolete item-shape equality.
- Keep `schema_version: "composition.v2"`: this is an input-compatible normalization of an under-specified V2 field, not a second playable document model. No backward-output compatibility shim is added for unknown external clients.

### Harmony edit semantics

Use pure range operations independent of track replacement:
- `add`: insert into a gap; reject overlap unless the user explicitly chooses replace.
- `replace`: replace the selected tick range and preserve unaffected left/right fragments of overlapping spans.
- `remove`: clear only the selected range and preserve unaffected fragments.
- `move`: preserve duration, reject out-of-bounds/overlap, and never move note events.
- `resize`: change one boundary, enforce positive duration/non-overlap, and provide keyboard/numeric controls as the accessible equivalent of pointer dragging.
- Adjacent spans with identical chord spelling normalize into one span after destructive operations; spelling-distinct enharmonic symbols are not merged.
- Every edit is immutable, validates the complete composition, creates one composition-history entry, and leaves all `tracks[].events[]` unchanged.

Do not route metadata-only edits through `apply_region_replacement_patch()`. Its current default target resolution and omitted replacement behavior can erase selected note events. If the generic region patch remains harmony-capable, tighten it so every audible target has an explicit replacement and use a strict V2 harmony-patch DTO; otherwise remove harmony mutation from that path and delegate to the dedicated harmony range service.

### Reharmonization strategies

Expose stable operation values:
- `suggest_progression`
- `reharmonize`
- `increase_tension`
- `decrease_tension`
- `strengthen_cadence`
- `tonicize_target`
- `use_secondary_dominants`
- `use_modal_interchange`
- `simplify_harmony`

Expose exactly three audible-content policies:
- `preserve_melody_adapt_harmony`: melody/lead track events are exact-preserved; harmony spans and explicitly listed harmonic-support/bass tracks may change. Melody pitch classes influence chord ranking and incompatible proposals become findings or failures.
- `preserve_harmony_adapt_melody`: harmony spans are exact-preserved; explicitly listed melody/lead tracks may change to fit them; accompaniment/bass is preserved unless separately and explicitly targeted by a future contract extension.
- `adapt_accompaniment_only`: harmony and melody/lead events are exact-preserved; only explicitly listed bass/harmony/pad/rhythm tracks may change. This realizes existing harmony differently rather than changing the progression.

The preview must list every target track ID and preservation assertion. Role inference may recommend targets but cannot silently authorize them. Drums are always excluded; `countermelody` and `other` require explicit selection.

### Tonal context and chromaticism

- Resolve the active canonical key at the selection start from root `key` plus `key_changes`.
- By default, preserve root `key`, all `key_changes`, and the active tonal center across the result.
- Allow secondary dominants, applied leading-tone chords, mixture/borrowed chords, altered dominants, diminished passing harmony, chromatic bass, and non-chord melody tones when the proposal still supports the active center or reports intentional tonicization.
- `allow_modulation` defaults to `false`. Modulation requires explicit opt-in plus `target_key`; applying it may update key metadata only when the preview shows that change.
- `tonicize_target` requires a parseable `target_chord`; tonicization is temporary unless modulation is explicitly enabled.
- Unknown chord syntax remains valid authored metadata when renderers can preserve it, but deterministic transformations that cannot parse it return a structured unsupported-symbol finding instead of guessing.

### Compatibility validation

Return a bounded compatibility report, not a diatonic allow-list:
- Declared-span versus inferred-note chord agreement weighted by actual tick overlap.
- Melody chord-tone/non-chord-tone classification, strong-beat clashes, sustained avoid-note pressure, and resolution evidence.
- Bass root/third/fifth/seventh or inversion support.
- Accompaniment pitch-class support, duplication, voice-leading movement, and density/tension deltas.
- Tonal-center support versus competing-center evidence, including explicit recognition of secondary dominants and modal mixture.
- Cadence evidence at the selected range end.
- Preservation checks for required tracks, outside-range events, conductor timelines, sections, markers, motifs, and IDs.

Findings use stable codes and severities (`info`, `warning`, `error`). Chromatic or non-chord content alone is never an error. Structural corruption, unauthorized target changes, stale base revision, invalid timing, unparseable symbols required by a deterministic strategy, and explicit preservation violations are errors.

### Preview transaction

Add `POST /harmony/reharmonize/preview` under a dedicated harmony router. The route is stateless and returns a validated candidate; it never writes a project.

Request outline:

```json
{
  "composition": {},
  "selection": {"start_bar": 9, "end_bar": 12},
  "operation": "increase_tension",
  "content_policy": "preserve_melody_adapt_harmony",
  "target_track_ids": ["bass", "accompaniment"],
  "engine": "deterministic",
  "instruction": "make the harmony more tense while keeping the melody",
  "tonal_context": {
    "allow_modulation": false,
    "target_key": null,
    "target_chord": null
  },
  "selection_options": {"provider": null, "model": null}
}
```

Response outline:

```json
{
  "base_fingerprint": "...",
  "proposal_fingerprint": "...",
  "composition": {},
  "harmony_changes": [],
  "track_changes": [],
  "preservation": [],
  "compatibility": {"status": "compatible", "findings": []},
  "provider": "deterministic",
  "model": null,
  "warnings": []
}
```

The frontend stores the response outside `editedMusicJson`. Apply is a local atomic commit only if the current composition fingerprint still equals `base_fingerprint` and the candidate/summary fingerprints validate. Preview, discard, failure, and stale-response rejection do not touch history, dirty state, autosave, authoritative notation, analysis freshness, or persistence. Apply creates one undo entry, invalidates stale notation/analysis, stops playback, marks the project dirty, and schedules autosave.

## Commit Plan
- **Commit 1** (after tasks 1-3): `feat(harmony): normalize explicit harmony ranges`
- **Commit 2** (after tasks 4-6): `feat(harmony): add reharmonization preview service`
- **Commit 3** (after tasks 7-9): `feat(frontend): add harmony editing and preview state`
- **Commit 4** (after tasks 10-12): `feat(frontend): deliver harmony workflow and acceptance coverage`

## Tasks

### Phase 1: Canonical Contract and Safe Editing

- [ ] **Task 1: Normalize V2 harmony into explicit tick spans and migrate persisted legacy points.**
  - Change `backend/app/composition_schemas.py` so `CompositionV2HarmonyItem` owns `start_tick`, `duration_ticks`, and `chord`, and root validation enforces fit, ordering, unique starts, and non-overlap against `duration_ticks`.
  - Change `backend/app/services/composition_normalizer.py`, `backend/app/services/composition_migration.py`, `backend/app/services/project_composition.py`, and frontend `frontend/src/utils/compositionVersion.js` so old V1/V2 `{bar, chord}` points deterministically become explicit spans before operational validation. Keep import output `harmony: []`.
  - Define duplicate-bar last-wins, leading-gap, final-span, malformed/mixed-shape, variable-meter, idempotence, and failed-project-rewrite behavior in code and tests.
  - Update fixtures only through normalization where practical; add explicit-range fixture vectors where golden canonical output is asserted.
  - Add/modify backend tests in `backend/tests/test_composition_v2_schema.py`, `test_composition_v2_migration.py`, `test_composition_normalizer.py`, `test_project_routes.py`, and `test_project_persistence_acceptance.py`; add frontend migration/validation fixture tests.
  - **Logging:** DEBUG legacy/canonical dispatch and bounded normalization counts; INFO successful legacy harmony normalization with schema/counts; WARNING stable codes for duplicate collapse or rejected malformed ranges. Never log chord arrays, compositions, project JSON, or note events.
  - **Dependencies:** none.

- [ ] **Task 2: Implement pure harmony range operations and separate them from audible region replacement.**
  - Create `backend/app/services/composition_harmony_timeline.py` for normalization helpers, overlap queries, inferred bar labels, split/merge behavior, and immutable add/replace/remove/move/resize operations.
  - Add strict operation DTOs in a dedicated `backend/app/harmony_schemas.py`; use explicit operation discriminators so an empty replacement cannot accidentally mean clear.
  - Audit `backend/app/services/composition_region_patch.py` and `backend/app/schemas.py`: eliminate the permissive V1-first harmony union, prevent omitted target replacements from deleting notes, and ensure metadata-only harmony changes never run note boundary-crossing checks or resolve all tracks by default.
  - Preserve all note events, IDs, motifs, conductor fields, and source object identity during harmony-only operations; require explicit behavior at selection-crossing harmony spans.
  - Add focused tests in new `backend/tests/test_composition_harmony_timeline.py` and extend `test_composition_region_patch.py` for successful harmony-only edits, explicit clear, empty/no-op, omitted targets, crossing notes, strict extras, source immutability, and exact event preservation.
  - **Logging:** DEBUG operation/range and before/after span counts; INFO accepted operation and changed span count; WARNING stable rejection code for overlap, out-of-bounds, ambiguous clear, or unsafe target replacement. Do not log chord payload collections or event arrays.
  - **Dependencies:** Task 1.

- [ ] **Task 3: Upgrade chord parsing, tonal policy, and note-compatibility analysis for explicit spans.**
  - Refactor/extend parsing in `backend/app/services/composition_tonality.py` to handle the documented roots, accidentals, qualities, extensions, alterations, and slash basses without silently discarding unknown suffixes. Preserve authored spelling separately from normalized pitch-class semantics.
  - Create `backend/app/services/composition_harmony_compatibility.py` for duration-weighted declared-versus-realized evidence, melody clash/resolution findings, bass/inversion support, accompaniment support, voice-leading/tension deltas, tonal-center support, and cadence evidence.
  - Reuse bounded deterministic outputs from `composition_harmony_analysis.py`, `composition_tension_analysis.py`, `composition_melody_analysis.py`, `composition_role_analysis.py`, and compiled timeline helpers; do not persist analysis results.
  - Define stable finding codes and thresholds. Recognized chromatic function and ordinary non-chord tones remain informational/warnings, not hard failures; only unauthorized mutations and invalid structure fail a proposal.
  - Add tests covering major/minor keys, key changes, secondary dominants, borrowed chords, altered dominants, slash chords, enharmonic spelling, sustained melody tensions/resolutions, imported `other` roles, unparseable text, and competing tonal centers.
  - **Logging:** DEBUG bounded evidence/threshold counts and active-key identity; INFO compatibility status and finding-code counts; WARNING unsupported syntax or competing-center risk by code only. Never log full reports, chord timelines, prompts, or event arrays.
  - **Dependencies:** Tasks 1-2.

<!-- Commit checkpoint: tasks 1-3 -->

### Phase 2: Reharmonization Domain and API

- [ ] **Task 4: Define the preview API contract and deterministic reharmonization engine.**
  - Complete request/response DTOs in `backend/app/harmony_schemas.py`, including inclusive bar selection, operation, engine, content policy, explicit target IDs, bounded optional instruction, tonal options, compatibility report, preservation assertions, change summaries, and fingerprints.
  - Create `backend/app/services/composition_reharmonization.py` to resolve half-open tick bounds and active key, authorize targets, generate a candidate, realize canonical note replacements where policy allows, validate the whole candidate, run compatibility checks, and compute deterministic summaries/fingerprints.
  - Implement deterministic behavior for all listed operations. Use melody pitch evidence when preserving melody, preserve harmony exactly under the other two policies, favor parsimonious voice-leading, and ensure `increase_tension` changes both progression and at least one selected accompaniment/bass track when `preserve_melody_adapt_harmony` is used.
  - Require explicit target selection after role-based recommendations. Reject drums; require opt-in for `countermelody` and `other`; define behavior when no realizable harmonic-support track exists rather than returning an inaudible success.
  - Preserve all events outside `[start_tick, end_tick)`, reject unresolved crossing notes/ties rather than truncating them, reconcile motif references using existing policy, and preserve root metadata unless modulation is authorized.
  - Add `backend/tests/test_composition_reharmonization.py` with a strategy/policy/key/role matrix and exact bars 9-12 acceptance assertions.
  - **Logging:** INFO start/completion with operation, engine, policy, bar range, target count, changed-span/event counts, compatibility status, and fingerprint prefixes; DEBUG stage/finding-code counts; WARNING/ERROR sanitized domain failures. Never log instructions verbatim, harmony arrays, candidate compositions, or event arrays.
  - **Dependencies:** Tasks 1-3.

- [ ] **Task 5: Add bounded AI orchestration and deterministic fake-provider proposals.**
  - Create `backend/app/services/llm_reharmonizer.py` or a cohesive reharmonization graph that asks the provider for a strict harmony/track proposal, validates it through Task 4, repairs bounded failures, and never lets provider output bypass authorization or preservation checks.
  - Keep deterministic planning/realization available without an LLM. For `engine: "ai"`, support existing provider/model selection and unavailable-provider errors; feed only selected-range notes, explicit harmony spans, active tonal context, strategy, preservation policy, and bounded analysis context.
  - Encode every operation and chromatic/modulation rule in prompts and structured output. Require complete explicit replacements for every authorized audible target and reject unauthorized track or metadata changes.
  - Extend `backend/app/services/fake_llm.py` and fixtures so fake mode deterministically exercises bars 9-12 increased tension while melody is exact-preserved and accompaniment/harmony change.
  - Add `backend/tests/test_llm_reharmonization.py`, fake-provider tests, malformed-output/repair-exhaustion tests, and secret/log hygiene assertions.
  - **Logging:** INFO provider/model/stage/attempt and bounded result counts; DEBUG sanitized instruction metadata (length/hash/recognized intent only) and diagnostic codes; WARNING repair and provider failures. Never log API keys, complete prompts/responses, harmony timelines, or note arrays.
  - **Dependencies:** Task 4.

- [ ] **Task 6: Expose the preview route and update every harmony consumer to span semantics.**
  - Add `backend/app/routers/harmony.py`, register it in `backend/app/main.py`, and expose `POST /harmony/reharmonize/preview` with thin HTTP-to-domain error mapping (`400/422` invalid request/domain conflict, `503` missing provider, `502` invalid provider output).
  - Return the candidate and bounded summaries without project mutation. Render optional candidate MusicXML only if it can fail independently with a structured warning; do not make rendering a prerequisite for a valid musical preview.
  - Update generation assembly/plans in `composition_planner.py` and `llm_music_generator.py` to emit explicit spans and enforce valid ordered coverage where generated harmony exists.
  - Update `composition_harmony_analysis.py`, `composition_tonality.py`, `composition_density_analysis.py`, analysis fingerprints/context, motif harmony context, `music_json_renderer.py`, and projection issue codes so duration/overlap semantics are consistent. MusicXML places symbols at span starts; MIDI/WAV/Tone remain event-only.
  - Verify import stays empty-harmony and update region-edit compatibility or remove its obsolete harmony mutation surface cleanly.
  - Add route, generation, analysis, renderer, fingerprint, export-fidelity, import regression, and HTTP error tests.
  - **Logging:** Router INFO request/result metadata and service error codes; DEBUG response shape/counts and renderer issue codes; WARNING mapped domain/provider/render issues. Never log request compositions, prompts, MusicXML, full reports, or events.
  - **Dependencies:** Tasks 1-5.

<!-- Commit checkpoint: tasks 4-6 -->

### Phase 3: Frontend Model, State, and API

- [ ] **Task 7: Add frontend harmony validation, timeline operations, diffs, and preservation checks.**
  - Create `frontend/src/utils/compositionHarmony.js` using `compositionTimeline.js` boundaries for span-to-bar projection, selection geometry, immutable add/replace/remove/move/resize, normalization, split/merge rules, and before/after diffs.
  - Extend `frontend/src/utils/musicJsonValidation.js`, `compositionVersion.js`, `compositionAnalysis.js`, and persistence/notation revision projections to mirror backend range invariants and legacy normalization exactly.
  - Add pure candidate verification that compares authorized inside-range changes, exact-preserved melody/harmony as required, exact outside-range events, conductor/section metadata, and proposal/base fingerprints.
  - Keep synthetic UI keys outside canonical JSON; never add frontend-only IDs to harmony spans.
  - Add `compositionHarmony.test.js` and extend timeline, validation, fingerprint, mixed-meter, and migration tests, including bars 9-12 and chromatic symbols.
  - **Logging:** Pure utilities do not log normal flow. Callers log DEBUG operation names/counts and WARNING validation/preservation codes; tests assert logs never serialize compositions, chord lists, or events.
  - **Dependencies:** Tasks 1-3 and finalized DTO semantics from Task 4.

- [ ] **Task 8: Generalize Zustand composition transactions and add isolated preview lifecycle state.**
  - Refactor `frontend/src/store/musicStore.js` so the existing note-only commit/history mechanism becomes a composition-edit transaction reused by note, motif, harmony, and accepted reharmonization edits without changing existing behavior.
  - Add structured harmony selection/edit actions and include relevant selection in undo snapshots. Each pointer resize/move produces one history entry; subsequent drag updates use the existing `skipHistory` pattern.
  - Add ephemeral preview state: request sequence, status/error/warnings, base revision/fingerprint, candidate, patch/change summaries, compatibility, optional candidate MusicXML, and engine/provider metadata.
  - Add start/succeed/fail/discard/apply actions with stale-response and stale-base rejection. Preview must not mutate `editedMusicJson`, `generatedMusicJson`, revisions, history, dirty/autosave state, authoritative notation, playback, or analysis.
  - On apply, verify candidate and fingerprints again, commit exactly once, stop playback, clear redo/preview, mark dirty, invalidate notation/analysis, schedule autosave, and keep `generatedMusicJson` reset semantics consistent with ordinary authored edits rather than silently redefining the baseline.
  - Invalidate pending previews on JSON edits, note/motif/harmony edits, undo/redo, project switch/open/delete, import, generation, and reset.
  - Extend `musicStore.test.js`, analysis/project/motif store tests for no-op preview, atomic apply, one-step undo/redo, stale candidates/responses, failure/discard, autosave boundaries, and exact preservation.
  - **Logging:** INFO preview lifecycle and apply/discard summaries; DEBUG sequence/revision prefixes, operation, policy, counts, and undo depth; WARNING stale/invalid/duplicate actions; ERROR sanitized API/apply failures. Never log instructions, compositions, harmony arrays, or events.
  - **Dependencies:** Task 7.

- [ ] **Task 9: Add the frontend reharmonization API client and strict response validation.**
  - Extend `frontend/src/api/musicApi.js` with `previewReharmonization(payload)` and a typed error class following analysis/motif/import conventions.
  - Normalize and validate the outbound composition, validate all enums/selection/targets, post to `/harmony/reharmonize/preview`, normalize/validate the candidate, and verify response fingerprints/summaries before returning it to the store.
  - Keep the existing generic region editor behavior separate; do not enable its dormant `harmony_patch` as a shortcut.
  - Add `frontend/src/api/musicApi.test.js` coverage for bars 9-12 payloads, all policies/engines, chromatic targets, provider errors, malformed candidates/summaries, stale fingerprints, and no mutation of request data.
  - **Logging:** INFO bounded request/result metadata; DEBUG validated response counts/fingerprint prefixes; WARNING/ERROR sanitized HTTP and contract failures. Do not log full Axios payloads, instructions, compositions, harmony lists, or event arrays.
  - **Dependencies:** Tasks 4 and 6-8.

<!-- Commit checkpoint: tasks 7-9 -->

### Phase 4: Interactive UI, Acceptance, and Documentation

- [ ] **Task 10: Build the Harmony tab, timeline editor, strategy controls, and preview review UI.**
  - Create `frontend/src/components/HarmonyTimelinePanel.jsx` and add a `harmony` tab in `ComposerWorkspace.jsx` using existing accessible tab behavior.
  - Render a horizontally scrollable bar/tick ruler from compiled variable-meter boundaries, explicit chord blocks, range selection overlay, and handles for move/resize. Reuse/extract piano-roll bar-selection math rather than duplicating fixed-meter arithmetic.
  - Support pointer and keyboard/numeric add, replace, remove, move, and resize actions with clear overlap/clear confirmation and 44px touch targets. Display gaps explicitly and state that harmony metadata itself is silent.
  - Add inclusive bar start/end controls, operation presets, free-text instruction, engine/provider selection, tonal context, explicit modulation opt-in/target, tonicization target chord, content-policy radio controls, and explicit recommended/selected track IDs.
  - Present preview before/after harmony spans, changed tracks/event counts, tonal/compatibility findings, melody/outside-range preservation checks, and Apply/Discard. Disable apply for stale/error candidates or failed preservation assertions and require confirmation when note events will be replaced.
  - Allow candidate playback from candidate `tracks[].events[]` and candidate notation in isolated UI state without swapping `editedMusicJson`; never synthesize preview audio from harmony metadata.
  - Follow existing styled-components language while making the feature visually distinct and usable at desktop and `390x844`; prevent document-level horizontal overflow and maintain accessible labels/focus states/status announcements.
  - Update `PromptJsonEditor.jsx`, `PlaybackControls.jsx`, and `NotationViewer.jsx` only where required for preview isolation/interoperability.
  - **Logging:** INFO explicit user operations, preview requests, applies, and discards using bounded metadata; DEBUG disabled reasons/selection/target counts; WARNING blocked destructive/stale actions; ERROR sanitized UI failures. Never log text instructions, chord arrays, compositions, or events.
  - **Dependencies:** Tasks 7-9.

- [ ] **Task 11: Add end-to-end acceptance and complete regression verification.**
  - Add `frontend/e2e/harmony-workflow.spec.js` and helper route/store utilities in `frontend/e2e/helpers.js` using a deterministic 16-bar multi-track fixture.
  - Cover local timeline CRUD/resize, variable meter, keyboard access, chromatic symbols, destructive confirmations, preview loading/error/retry/discard, stale-base rejection, undo/redo, save/reopen, and mobile no-overflow behavior.
  - Implement the exact acceptance scenario: select bars 9-12, request increased tension with `preserve_melody_adapt_harmony`, assert captured request scope/policy/targets, return or generate a deterministic candidate, prove preview non-mutation, apply, prove melody deep equality, prove coherent harmony plus accompaniment/bass changes inside range, prove outside-range/structural preservation, then undo/redo and reopen.
  - Add backend acceptance assertions against the same musical vectors so the UI mock cannot mask a service regression. Validate harmony-to-note compatibility and tension increase with bounded deterministic metrics rather than only checking that JSON differs.
  - Run focused tests during implementation, then full gates:
    - `cd backend && ../.venv/bin/python -m pytest`
    - `cd frontend && npm test`
    - `cd frontend && npm run lint`
    - `cd frontend && npm run build`
    - `cd frontend && npm run test:e2e -- harmony-workflow.spec.js`
    - Run existing V2/import/analysis/motif Playwright journeys affected by normalization and store refactoring.
  - **Logging:** Test fixtures capture/assert stable operation, policy, range, count, finding, and failure codes; secret-hygiene tests reject prompts, raw compositions, harmony arrays, event arrays, and provider secrets in logs. Playwright diagnostics may include bounded IDs/counts but not full musical payloads.
  - **Dependencies:** Tasks 1-10.

- [ ] **Task 12: Document the canonical range contract, strategy semantics, API, UX, testing, and migration behavior.**
  - Route the documentation checkpoint through `$aif-docs` and update `docs/composition-v2.md`, `docs/composition-analysis.md`, `docs/project-persistence.md`, `docs/testing.md`, `README.md`, `.ai-factory/DESCRIPTION.md`, `.ai-factory/ARCHITECTURE.md`, and `AGENTS.md` only where the implemented structure/entry points change.
  - Document explicit half-open harmony ranges, legacy point normalization, gap/overlap/resize semantics, chromatic support, active-key behavior, explicit modulation, every operation/content policy, target authorization, compatibility findings, preview/apply transaction, undo/autosave behavior, and export/playback fidelity.
  - Include request/response examples for deterministic and AI preview without real prompts or event-array dumps, plus focused backend/frontend/E2E commands and the bars 9-12 acceptance journey.
  - State prominently that `tracks[].events[]` remains the only audible source and `composition.analysis.v1` remains derived/non-persisted.
  - **Logging:** Document `LOG_LEVEL` and frontend console expectations, stable operation/finding/error codes, and prohibited sensitive/large fields. Verify examples and tests demonstrate sanitized count/code logging only.
  - **Dependencies:** Tasks 1-11; documentation must describe final implemented behavior, not planned behavior.

<!-- Commit checkpoint: tasks 10-12 -->

## Verification Matrix

| Area | Required evidence |
|------|-------------------|
| Canonical schema | Explicit positive tick spans; ordered, unique, in bounds, non-overlapping; old V1/V2 points normalize deterministically |
| Persistence | Old projects normalize/rewrite only after success; reopen is idempotent; failed normalization leaves stored JSON unchanged |
| Local editing | Add/replace/remove/move/resize are immutable, mixed-meter aware, one undo step, and preserve every note event |
| Strategy coverage | Every named operation has deterministic tests; AI output passes the same validator; fake mode is stable |
| Tonality | Active key/key changes preserved by default; modulation requires opt-in; chromatic functions are supported rather than rejected as non-diatonic |
| Content policy | Authorized tracks match policy; melody/harmony/outside-range preservation is deep-equal, not inferred from labels |
| Compatibility | Duration-weighted findings cover melody, bass, accompaniment, cadence, tension, and tonal center with stable codes |
| Preview safety | Preview/discard/error/stale response cannot dirty, autosave, persist, invalidate authoritative analysis/notation, alter playback, or add history |
| Apply safety | Fingerprint rechecked; candidate validates; one atomic commit and undo entry; no partial metadata/event apply |
| Canonical audio | Tone/MIDI/WAV continue to consume only candidate/applied `tracks[].events[]`; metadata-only edits are silent |
| Notation/export | MusicXML emits chord symbols at exact span starts; unsupported symbols produce harmony-specific bounded issues |
| Import | MIDI/MusicXML import remains `harmony: []`; no accidental analysis or note synthesis |
| Responsive/accessibility | Keyboard editing and tab navigation work; controls have labels/status; `390x844` has no page overflow |
| Logging/security | Structured counts/codes only; no secrets, full prompts, compositions, harmony timelines, reports, MusicXML, or event arrays |

## Primary Risks and Mitigations

- **Schema ripple across strict V2 consumers:** land normalization and parity tests first, then update every backend/frontend projection before enabling the UI.
- **Legacy point semantics were inconsistent:** make change-point-to-span behavior explicit and golden-tested; never silently clamp corrupt projects.
- **Generic region patch can delete omitted tracks:** separate metadata operations and require explicit audible replacements before reuse.
- **Harmony metadata is inaudible:** preview/apply summaries must distinguish symbol changes from changed canonical accompaniment/bass events; playback always uses candidate events.
- **Role labels are imperfect, especially imports:** recommendations are advisory and every destructive target is explicit in request/preview.
- **Naive chord parsing can misclassify chromatic harmony:** parse full supported syntax, preserve spelling, and return unsupported-symbol findings rather than simplifying unknown tails.
- **Preview can become stale during concurrent edits/autosave:** bind it to the complete composition fingerprint and request sequence, then revalidate immediately before apply.
- **History/reset semantics are currently inconsistent for AI edits:** generalize composition transactions and keep preview acceptance aligned with ordinary authored edits.
- **Motif references can dangle after note replacement:** use existing reconciliation policy and show resulting warnings before apply.
- **Large provider payloads/logs can leak musical content:** bound selected context, summaries, diagnostics, and log only codes/counts/hashes.

## Completion Gate

The feature is complete only when all full test gates pass and the bars 9-12 acceptance journey proves all of the following in one run: preview is non-destructive, melody events remain exactly unchanged, harmony spans and explicitly selected accompaniment/bass events change inside the requested range, compatibility/tension evidence supports the result, outside-range and structural data remain exact, apply is atomic, undo restores the source, and save/reopen preserves the accepted candidate.
