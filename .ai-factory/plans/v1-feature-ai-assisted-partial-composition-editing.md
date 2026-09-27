# Implementation Plan: AI-Assisted Partial Composition Editing

Branch: main
Created: 2026-09-06

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Current State
- Canonical Composition V1 is defined in `backend/app/schemas.py` with `schema_version: "composition.v1"`, tick-based timing, contiguous sections, track-local `events`, and harmony metadata.
- Existing full-generation AI flow is `POST /llm/generate-music-json` in `backend/app/main.py`, backed by the LangGraph staged composer in `backend/app/services/llm_music_generator.py`.
- Existing provider/model selection is centralized in `backend/app/llm_settings.py` and request schemas in `backend/app/schemas.py`.
- Existing backend validation uses Pydantic model validation plus `backend/app/services/composition_validator.py` integrity checks.
- Existing frontend API calls live in `frontend/src/api/musicApi.js` and validate canonical compositions before accepting generated/export responses.
- Existing editor state and undo/redo live in `frontend/src/store/musicStore.js`; note edit history snapshots already include `editedMusicJson`, `pianoRollTrackId`, and `pianoRollNoteId`.
- Existing piano-roll UI is `frontend/src/components/PianoRollEditor.jsx`; it has single-note/track selection, grid timing metrics, and no dedicated bar-range selection yet.
- Existing section prompt UI is a text input in `frontend/src/components/MusicGenerator.jsx`; there is no visual section timeline component.

## Goals
- Add an AI edit operation that modifies only an explicitly selected canonical Composition V1 region.
- Reuse and extend the existing LangGraph music service instead of adding a second unrelated AI system.
- Prefer a patch/replace-region contract with explicit boundaries that can be validated before application.
- Preserve notes and composition metadata outside the requested scope unless explicitly requested by the edit instruction.
- Make failures non-destructive: the current composition must remain unchanged if provider, validation, or patch application fails.
- Integrate frontend bar/track selection with piano roll and section context.
- Provide instruction input and `Regenerate Selection` / `AI Edit` action.
- Support undo/redo for before/after AI edits using the existing history model.
- Add deterministic mocked tests proving unchanged regions remain byte-for-byte or semantically unchanged where applicable.

## Non-Goals
- Do not replace the existing whole-composition generation endpoint.
- Do not introduce a new provider abstraction outside `llm_settings.py` / existing selection schemas.
- Do not support legacy non-Composition V1 editing.
- Do not persist provider prompts, raw LLM outputs, API keys, or secret-like data in project metadata.
- Do not allow silent tempo/key/time-signature/project-structure changes when the request is scoped to note-level edits.

## Proposed Design
- Add request/response schemas for partial edits in `backend/app/schemas.py`:
  - `CompositionEditSelection` with `start_bar`, `end_bar`, optional `track_ids`, and optional `section_id`/`section_type` context if useful.
  - `CompositionEditInstruction` or request fields containing natural-language `instruction` and existing `selection` provider/model contract.
  - `CompositionRegionReplacementPatch` with explicit `schema_version`, `operation: "replace_region"`, copied boundary fields, optional `target_track_ids`, `replace_tracks`, `added_tracks`, optional `harmony_patch`, and warnings.
  - `LLMCompositionEditRequest` containing `composition: Composition`, `edit: ...`, and `selection: LLMModelSelection`.
  - `LLMCompositionEditResponse` containing `composition: Composition`, `patch: CompositionRegionReplacementPatch`, `provider`, `model`, `musicxml`, and `warnings`.
- Add a backend edit service in `backend/app/services/llm_composition_editor.py` or a tightly scoped extension module imported from `llm_music_generator.py`:
  - Reuse `_select_provider`, `_selected_model`, `_invoke_chat`, JSON extraction, and provider settings from the existing LangGraph service.
  - Build an edit-specific LangGraph with stages such as `analyze_edit_scope`, `draft_region_patch`, `validate_patch`, `apply_patch`, and `repair_patch`.
  - Keep the graph focused on patch generation and validation while leaving the existing full-generation graph untouched.
- Implement deterministic patch utilities in `backend/app/services/composition_region_patch.py`:
  - Convert selected bars to tick boundaries using `bar_duration_ticks` / `bar_to_start_tick`.
  - Remove and replace only events within the target region and target tracks.
  - Preserve outside-region events exactly by copying original event dictionaries without normalization/reordering beyond what Pydantic already does.
  - Allow `added_tracks` for counter-melody only when the instruction requires it and validate unique track IDs/channels.
  - Preserve `tempo`, `key`, `time_signature`, `ticks_per_quarter`, `duration_ticks`, `bar_count`, `sections`, and outside-scope `harmony` unless the request explicitly asks to alter them.
- Add preservation validation:
  - Validate inbound composition is canonical V1.
  - Validate selection boundaries are in range and bar-based ticks are exact.
  - Validate patch boundaries match the requested selection.
  - Validate all replacement events start/end within region boundaries.
  - Validate no non-target track has in-region changes unless the instruction explicitly requests cross-track edits.
  - Validate outside-region notes are unchanged semantically and, where model dump ordering allows, byte-for-byte by canonical JSON comparison.
  - Validate the fully applied composition with `Composition.model_validate()` and `validate_composition_integrity()` before returning.
- Add `POST /llm/edit-composition-region` in `backend/app/main.py`:
  - Match existing endpoint error handling: provider unavailable, unsupported provider, invalid LLM output, render failure.
  - Return `502` on invalid patch/repair exhaustion without mutating anything server-side.
  - Render preview MusicXML only after successful patch application.
- Frontend integration:
  - Add API function `editCompositionRegion(payload)` in `frontend/src/api/musicApi.js` with canonical response validation.
  - Add selection state in `frontend/src/store/musicStore.js`: `aiEditSelection`, selected bar range, optional selected track IDs, instruction, status, error, and warnings.
  - Add store actions to set/clear selection, start AI edit, apply successful AI edit with one undo snapshot, and fail without mutating `editedMusicJson`.
  - Extend `PianoRollEditor.jsx` with bar-range selection affordances and visual overlay for selected bars.
  - Add an AI edit panel near the piano roll or generated composition area with instruction input and `Regenerate Selection` / `AI Edit` button.
  - Default optional track scope to the currently selected piano-roll track, with a visible choice for all tracks in the selected bars.

## Acceptance Criteria
- Selecting bars 9-12 of a melody track and requesting `make this phrase more dramatic but keep the harmony` sends a canonical V1 edit request with explicit `start_bar: 9`, `end_bar: 12`, and the melody track ID.
- Backend returns a validated `replace_region` patch and applied canonical V1 composition.
- All notes outside bars 9-12 remain unchanged.
- Harmony, tempo, key, time signature, sections, bar count, and duration remain unchanged for this request.
- If the provider returns malformed JSON, out-of-bound notes, changed outside-region notes, or invalid final composition, the API returns an error and the frontend keeps the original composition intact.
- Undo restores the exact pre-edit composition and redo reapplies the AI-edited composition.

## Verification Commands
- Backend full suite: `../.venv/bin/python -m pytest` from `backend/`
- Backend targeted tests: `../.venv/bin/python -m pytest tests/test_llm_composition_editing.py tests/test_llm_routes.py tests/test_composition_region_patch.py` from `backend/`
- Frontend full suite: `npm test` from `frontend/`
- Frontend build: `npm run build` from `frontend/`

## Commit Plan
- **Commit 1** (after tasks 1-3): `feat: add composition region patch contract`
- **Commit 2** (after tasks 4-6): `feat: add LangGraph partial composition editing endpoint`
- **Commit 3** (after tasks 7-9): `feat: add frontend AI region editing workflow`
- **Commit 4** (after tasks 10-12): `test: verify non-destructive partial composition editing`
- **Commit 5** (after task 13): `docs: document AI partial composition editing`

## Tasks

### Phase 1: Backend Contract And Patch Utilities
- [x] Task 1: Add canonical partial-edit schemas in `backend/app/schemas.py`.
  - Deliverable: Define `CompositionEditSelection`, `CompositionRegionReplacementPatch`, `LLMCompositionEditRequest`, and `LLMCompositionEditResponse` using Composition V1 types and existing `LLMModelSelection`.
  - Expected behavior: Requests require an existing `Composition`, a valid inclusive bar range, optional track IDs, a non-empty natural-language instruction, and provider/model selection. Responses include both the explicit patch and the fully applied validated composition.
  - Logging requirements: Add DEBUG-level schema validation failure logs consistent with `_log_validation_failure`; log value types/previews only, never full compositions or instructions.
  - Dependencies: None.

- [x] Task 2: Implement deterministic selection and patch application helpers in `backend/app/services/composition_region_patch.py`.
  - Deliverable: Add helpers to derive tick boundaries from bars, summarize selected tracks/events, apply `replace_region` patches immutably, and compare preserved regions.
  - Expected behavior: Replace only selected-region events for target tracks, preserve all outside-scope events exactly, support explicit `added_tracks`, and return actionable validation errors for boundary/preservation failures.
  - Logging requirements: INFO log patch application start/completion with schema version, bar range, target track count, added track count, replaced event counts, and preserved event counts; DEBUG log sanitized preservation comparison summaries; WARN log rejected patch reasons without dumping full composition JSON.
  - Dependencies: Task 1.

- [x] Task 3: Add unit tests for patch helpers in `backend/tests/test_composition_region_patch.py`.
  - Deliverable: Deterministic tests for bar-to-tick selection, melody-only replacement, all-track replacement, added counter-melody track, harmony-preserving edit, metadata preservation, invalid boundaries, out-of-region replacement events, and attempted outside-region mutation rejection.
  - Expected behavior: Tests assert unchanged regions using canonical JSON dumps for byte-for-byte checks where ordering is preserved, and semantic event tuple comparison where Pydantic normalization may alter representation.
  - Logging requirements: Use `caplog` for at least one rejected patch and one successful patch to verify sanitized INFO/WARN log fields.
  - Dependencies: Task 2.

### Phase 2: LangGraph Editing Service And API
- [x] Task 4: Extend the existing LangGraph music service with an edit-specific graph in `backend/app/services/llm_music_generator.py` or `backend/app/services/llm_composition_editor.py`.
  - Deliverable: Add `edit_composition_region(request, settings)` that reuses provider selection, model selection, chat invocation, JSON extraction, existing staged validation style, and LangGraph state management.
  - Expected behavior: The graph prompts the provider to return only a `replace_region` patch for the selected bars/tracks, with explicit instructions to keep harmony and metadata unless requested. Supported scenarios include regenerating melody, making melody more active, simplifying accompaniment, changing bass line, adding counter-melody, and increasing tension in the selected section.
  - Logging requirements: INFO log edit start/completion with provider, model, bar range, target track count, operation, patch warning count, and final event counts; DEBUG log sanitized scope summary, graph stage transitions, and patch shape; ERROR log provider failures with stage and error type only.
  - Dependencies: Tasks 1-2.

- [x] Task 5: Add patch validation, repair, and non-destructive failure handling to the edit service.
  - Deliverable: Implement validation stages that reject invalid patch JSON, boundary mismatch, out-of-scope mutations, invalid added tracks, invalid final Composition V1, and integrity diagnostics. Add one bounded repair attempt using the same provider and sanitized diagnostics.
  - Expected behavior: The original composition object is never mutated. Failed validation raises `InvalidLLMOutputError` or a dedicated edit error mapped to `502`; successful validation returns the applied composition and patch only after all checks pass.
  - Logging requirements: WARN log repair attempts with diagnostic codes/counts and target stage; ERROR log repair exhaustion with diagnostic summary, retry count, and no raw LLM payload.
  - Dependencies: Task 4.

- [x] Task 6: Add `POST /llm/edit-composition-region` to `backend/app/main.py`.
  - Deliverable: Wire request/response models, load settings, call edit service, render MusicXML preview after success, and map errors consistently with `generate_llm_music_json`.
  - Expected behavior: `400` for unsupported provider, `503` for no provider, `422` for invalid request schema/selection, `502` for invalid provider output or failed patch validation, and `500` only for post-success MusicXML render failure. The endpoint returns canonical V1 `composition`, explicit `patch`, `musicxml`, `provider`, `model`, and warnings.
  - Logging requirements: INFO log request start/completion, DEBUG log response shape, WARN log client/provider validation failures, ERROR log render/unexpected failures. Include counts and IDs only; do not log full instruction or full composition.
  - Dependencies: Tasks 4-5.

### Phase 3: Frontend API, Store, And Selection State
- [x] Task 7: Add frontend API support in `frontend/src/api/musicApi.js`.
  - Deliverable: Implement `editCompositionRegion(payload)` that posts to `/llm/edit-composition-region`, validates returned canonical composition with `validateMusicJson()`, checks that a patch exists, and throws on invalid responses.
  - Expected behavior: Successful responses return validated data. Failed responses propagate a user-safe error. Invalid client-side composition or response is rejected before mutating store state.
  - Logging requirements: DEBUG log request start/completion with provider, model, bar range, track scope count, valid response, schema version, and warning count; ERROR log failed request/validation with status and message only.
  - Dependencies: Task 6.

- [x] Task 8: Add AI edit selection and action state to `frontend/src/store/musicStore.js`.
  - Deliverable: Add state/actions for selected bar range, selected edit track mode/IDs, instruction text, edit status/error/warnings, start/fail/complete AI edit, and undo/redo integration.
  - Expected behavior: `completeAiEdit` validates the returned composition and applies it through a single undoable snapshot; `failAiEdit` leaves `editedMusicJson`, `compositionRevision`, `musicXml`, and history stacks unchanged except for status/error fields. Undo restores the exact pre-edit composition and redo restores the AI edit.
  - Logging requirements: INFO log selection changes and AI edit apply/undo/redo; DEBUG log sanitized revision/event-count summaries; WARN log invalid selection/instruction; ERROR log failed edit with message only.
  - Dependencies: Task 7.

- [x] Task 9: Implement bar-range selection helpers in `frontend/src/utils/pianoRollSelection.js` and integrate with `frontend/src/components/PianoRollEditor.jsx`.
  - Deliverable: Pure helpers for normalizing bar ranges, mapping pointer positions to bars, deriving selected tick boundaries, and determining default target track IDs. Add visual selected-region overlay in the piano roll.
  - Expected behavior: Users can select bars by dragging or setting start/end controls; selected range clamps to composition bars; current track is the default target; all-track mode is available for section-level edits.
  - Logging requirements: Keep pure helpers log-free. In `PianoRollEditor.jsx`, DEBUG log pointer-derived bar changes at throttled cadence and INFO log committed selection with start/end bars and track scope.
  - Dependencies: Task 8.

### Phase 4: Frontend UI Workflow
- [x] Task 10: Add AI edit controls to `frontend/src/components/MusicGenerator.jsx` or a new colocated `frontend/src/components/AiRegionEditPanel.jsx`.
  - Deliverable: Show selected range/track scope, natural-language instruction input, provider/model context, `Regenerate Selection` / `AI Edit` button, status, warnings, and errors.
  - Expected behavior: Button is disabled without canonical composition, valid selected bars, configured provider/model, and non-empty instruction. Submitting calls `editCompositionRegion`, then store completion only after response validation.
  - Logging requirements: INFO log user-initiated edit with range/scope and provider/model; DEBUG log UI disabled reasons; ERROR log submission failure with safe message.
  - Dependencies: Tasks 7-9.

- [x] Task 11: Refresh notation/playback/project autosave after successful AI edits.
  - Deliverable: Reuse `compositionRevisionKey`, `refreshMusicXmlFromEditedComposition`, existing playback invalidation, and project dirty/autosave paths after applying an AI edit.
  - Expected behavior: Successful AI edits update notation and mark projects dirty; playback stops or invalidates consistently with manual piano-roll edits; failures do not trigger autosave of any partial result.
  - Logging requirements: DEBUG log notation refresh request with revision and event counts; WARN log stale preview results; INFO log autosave-dirty transition after AI edit.
  - Dependencies: Task 8 and Task 10.

### Phase 5: Deterministic Tests And Acceptance Coverage
- [x] Task 12: Add backend mocked edit-service and route tests.
  - Deliverable: Add `backend/tests/test_llm_composition_editing.py` and route coverage in `backend/tests/test_llm_routes.py`.
  - Expected behavior: Mock LangGraph/provider responses for successful melody bars 9-12 edit, more-active melody, simplified accompaniment, changed bass line, added counter-melody, tense-section edit, malformed JSON failure, out-of-scope mutation failure, and provider failure. Acceptance test verifies bars outside 9-12 remain unchanged and harmony remains unchanged for `make this phrase more dramatic but keep the harmony`.
  - Logging requirements: Use `caplog` to assert edit start/completion, validation rejection, repair attempt, and non-destructive failure logs are sanitized and count-based.
  - Dependencies: Tasks 4-6.

- [x] Task 13: Add frontend API/store/selection tests.
  - Deliverable: Add tests to `frontend/src/api/musicApi.test.js`, `frontend/src/store/musicStore.test.js`, and new `frontend/src/utils/pianoRollSelection.test.js`.
  - Expected behavior: API validates edit responses, store does not mutate on failure, successful AI edit pushes exactly one undo snapshot, undo/redo restores before/after compositions, selection helpers clamp/normalize bars, and all bars outside the selected range remain unchanged in test fixtures.
  - Logging requirements: Assert important status transitions where existing frontend test style supports console spies; avoid brittle checks for every debug message.
  - Dependencies: Tasks 7-10.

### Phase 6: Documentation
- [x] Task 14: Document partial composition editing in `docs/composition-v1.md`, `docs/testing.md`, and `README.md` if README’s feature list/API section needs updating.
  - Deliverable: Add API contract, patch/replace-region semantics, preservation guarantees, non-destructive failure behavior, supported edit examples, logging/privacy notes, and test commands.
  - Expected behavior: Docs clearly state that playable notes remain in `tracks[].events[]`, harmony is metadata unless explicitly patched, and normal tests use mocked providers only.
  - Logging requirements: Documentation should mention sanitized verbose logs and no raw prompts/full composition dumps in backend or frontend logs.
  - Dependencies: Tasks 1-13.

## Risks And Mitigations
- Risk: The LLM returns a full composition and changes unrelated areas.
  - Mitigation: Require a patch-only contract, reject out-of-scope mutations, and apply patches deterministically to the original composition.
- Risk: Byte-for-byte preservation is hard after Pydantic normalization.
  - Mitigation: Compare original model-dumped canonical dicts for unchanged tracks/events where possible and fall back to semantic tuples for unavoidable normalized fields.
- Risk: Section-level edits such as `make more tense` may imply harmony changes.
  - Mitigation: Require explicit scope expansion for harmony or non-target tracks; otherwise reject changes outside notes in selected tracks and surface a warning/request for broader scope.
- Risk: Added counter-melody tracks can collide with track IDs/channels.
  - Mitigation: Validate uniqueness and derive safe defaults in patch helpers before final Composition validation.
- Risk: Frontend autosave could persist a failed/partial edit.
  - Mitigation: Store only applies returned composition after client validation and backend validation; failure path does not alter `editedMusicJson` or revision.
