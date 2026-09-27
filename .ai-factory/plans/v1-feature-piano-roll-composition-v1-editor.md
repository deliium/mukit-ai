# Implementation Plan: Composition V1 Piano-Roll Editor

Branch: main
Created: 2026-09-06

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Roadmap Linkage
Milestone: "none"
Rationale: No `.ai-factory/ROADMAP.md` was available; this plan is scoped directly from the requested browser piano-roll editor feature.

## Context Summary
- The frontend is React 18 + Vite + styled-components with Zustand state in `frontend/src/store/musicStore.js`.
- The canonical editable composition state is `editedMusicJson`; `generatedMusicJson` is only the reset source after generation.
- Existing playback already rebuilds from `editedMusicJson` and stops active playback on `compositionRevision` changes.
- Existing notation refresh is available through `ExportControls`, which posts the current edited canonical composition to `POST /export/musicxml`, stores the returned MusicXML with `setMusicXml`, and updates `NotationViewer`.
- Existing Composition V1 note data lives in `tracks[].events[]` with `pitch`, `start_tick`, `duration_ticks`, optional `id`, `staff`, `voice`, and MIDI velocity.
- Existing utility tests run with Node's built-in runner through `npm test`; current frontend tests are utility-only, not browser/JSDOM component tests.

## Implementation Decisions
- Do not add a DAW framework. Implement the editor with React, pointer events, styled-components, and small pure utility modules.
- Keep Zustand `editedMusicJson` authoritative. Piano-roll operations call store actions that immutably update `tracks[].events[]` on the current composition.
- Add store-level undo/redo for note-edit operations if it remains a compact bounded history around Composition V1 edits. Do not try to support undo/redo for arbitrary JSON editor edits in this feature.
- Preserve polyphony by representing every note as an independent event and allowing overlapping events on the same track.
- Focus editing on one selected track while optionally showing other tracks as muted/context notes in the background.
- MusicXML regeneration should be an explicit reusable API/store operation triggered after valid note edits with debounce, so notation refresh does not require the user to click Export MusicXML after every drag.

## Commit Plan
- **Commit 1** (after tasks 1-3): `feat: add piano roll composition editing utilities`
- **Commit 2** (after tasks 4-7): `feat: add piano roll editor ui`
- **Commit 3** (after tasks 8-10): `feat: sync piano roll edits with playback and notation`
- **Commit 4** (after task 13): `test: cover piano roll editor behavior`
- **Commit 5** (after task 14): `docs: document piano roll editing workflow`

## Tasks

### Phase 1: Composition Editing Foundations
- [x] Task 1: Add pure Composition V1 piano-roll utilities in `frontend/src/utils/pianoRollEvents.js`.
  Deliverable: Utilities for pitch-to-MIDI conversion, MIDI-to-pitch conversion, note range selection, time-signature bar tick calculation, snap interval derivation for `1/4`, `1/8`, and `1/16`, tick snapping, pixel-to-tick conversion, pixel-to-pitch conversion, clamping to composition bounds, stable note ID generation for events missing `id`, and immutable track-event update helpers.
  Expected behavior: Utilities must support 4/4, 3/4, and 6/8 meters with `ticks_per_quarter`, preserve existing event fields that are not being edited, and allow multiple events with the same or overlapping time ranges.
  Files: `frontend/src/utils/pianoRollEvents.js`, `frontend/src/utils/pianoRollEvents.test.js`.
  Logging requirements: DEBUG log utility-level edit summaries only through caller-provided logger or returned metadata, not from pure helpers by default; WARN metadata should identify invalid pitch/timing inputs without dumping the full composition.

- [x] Task 2: Extend `frontend/src/store/musicStore.js` with canonical note edit actions and bounded undo/redo.
  Deliverable: Store actions such as `selectPianoRollTrack(trackId)`, `selectPianoRollNote(noteId)`, `createNote(trackId, noteDraft)`, `updateNote(trackId, noteId, patch)`, `deleteNote(trackId, noteId)`, `undoNoteEdit()`, and `redoNoteEdit()`, plus UI state for selected track, selected note, snap value, horizontal zoom, and edit status.
  Expected behavior: Actions must update `editedMusicJson` immutably, recompute `compositionRevision`, preserve `trackControls`, validate with `validateMusicJson`, and push undo snapshots only for successful note-edit operations. JSON editor state remains the same object path: it must render the updated `editedMusicJson` after piano-roll edits.
  Files: `frontend/src/store/musicStore.js`.
  Dependencies: Task 1.
  Logging requirements: INFO log create/update/delete/undo/redo with track ID, note ID, sanitized old/new pitch/start/duration; DEBUG log revision/event-count changes; WARN log rejected edits with validation message; ERROR log unexpected immutable update failures.

- [x] Task 3: Add frontend API support for refreshing MusicXML from edited Composition V1 JSON without forcing a download.
  Deliverable: Add `renderMusicXmlPreview(composition)` or equivalent to `frontend/src/api/musicApi.js`, backed by a backend route if needed, and add a store/action integration such as `refreshMusicXmlFromEditedComposition()` or a component-level debounced caller.
  Expected behavior: A valid piano-roll edit triggers or reuses MusicXML regeneration and updates `musicXml` so `NotationViewer` can refresh. If the current backend only offers downloadable `/export/musicxml`, add a lightweight JSON/text preview endpoint such as `POST /export/musicxml/preview` that returns MusicXML text without `Content-Disposition` download side effects.
  Files: `frontend/src/api/musicApi.js`, `frontend/src/store/musicStore.js`, `backend/app/main.py`, backend route tests if a new route is added.
  Dependencies: Task 2.
  Logging requirements: Frontend DEBUG log preview render request/response with schema version, event count, and MusicXML length; frontend WARN log debounce skips/rejected invalid compositions; backend INFO log preview render start/complete and ERROR log render failures with sanitized composition summary.

### Phase 2: Piano-Roll UI
- [x] Task 4: Create `frontend/src/components/PianoRollEditor.jsx` and mount it in `frontend/src/components/MusicGenerator.jsx` near the JSON editor, playback, export, and notation controls.
  Deliverable: A styled editor panel shown for valid canonical `composition.v1` JSON with clear empty/loading/error states for no generation, legacy/non-canonical JSON, no tracks, no events on selected track, invalid composition, and notation-refresh errors.
  Expected behavior: The JSON editor remains available as an advanced representation of the same `editedMusicJson`; the piano roll does not hide or fork JSON state.
  Files: `frontend/src/components/PianoRollEditor.jsx`, `frontend/src/components/MusicGenerator.jsx`.
  Dependencies: Tasks 1-2.
  Logging requirements: DEBUG log mount/render summaries with track count, selected track, selected note, zoom, and snap value; WARN log unsupported non-canonical/invalid compositions; do not log full composition payloads.

- [x] Task 5: Implement track selection and context-track rendering.
  Deliverable: Track selector UI using `editedMusicJson.tracks`, focused editable track styling, and optional background context notes for other tracks with lower opacity and distinct colors.
  Expected behavior: Selected track determines where created notes are inserted and which notes can be dragged/resized/deleted. Context tracks are visible but not editable unless selected. If the selected track disappears after JSON edits, the store picks a sensible first available track and logs the transition.
  Files: `frontend/src/components/PianoRollEditor.jsx`, `frontend/src/store/musicStore.js`.
  Dependencies: Task 4.
  Logging requirements: INFO log user track selection with previous/next track IDs; DEBUG log context note counts by track; WARN log stale selected track recovery.

- [x] Task 6: Implement vertical pitch lanes and horizontal bar/beat grid.
  Deliverable: Render pitch rows vertically with labels, musical time horizontally, bar lines and beat/subdivision grid derived from `time_signature`, `ticks_per_quarter`, `bar_count`, and selected snap value.
  Expected behavior: The grid must handle at least 16-32 bar compositions through horizontal scrolling and zoom, label bars clearly, and keep pitch labels fixed or readable while scrolling when practical.
  Files: `frontend/src/components/PianoRollEditor.jsx`, optional `frontend/src/components/PianoRollGrid.jsx` if the main component becomes too large.
  Dependencies: Tasks 1 and 4.
  Logging requirements: DEBUG log grid metrics including bar ticks, snap ticks, visible pitch range, total width, and zoom; WARN log fallback grid metrics for unsupported or invalid time signatures.

- [x] Task 7: Add snap and zoom controls.
  Deliverable: UI controls for rhythmic snap values `1/4`, `1/8`, `1/16`, and horizontal zoom levels sufficient to edit 16-32 bars without precision loss.
  Expected behavior: Snap affects create, move, and resize operations; zoom changes pixels-per-tick without mutating composition data; controls remain usable on mobile.
  Files: `frontend/src/components/PianoRollEditor.jsx`, `frontend/src/store/musicStore.js`.
  Dependencies: Task 6.
  Logging requirements: INFO log snap/zoom changes; DEBUG log recalculated pixel/tick metrics; WARN log rejected invalid snap/zoom values.

### Phase 3: Note Editing Interactions
- [x] Task 8: Implement note creation, selection, and deletion.
  Deliverable: Pointer/click behavior for creating a snapped note in the selected track, selecting notes, keyboard Delete/Backspace removal when focus is inside the piano-roll editor, and accessible buttons or menu actions for delete on devices without keyboards.
  Expected behavior: Created notes default to a sensible duration based on snap value, a valid velocity such as `90`, selected pitch lane, snapped start tick, and a stable `id`; deletion only removes the selected note from the selected track.
  Files: `frontend/src/components/PianoRollEditor.jsx`, `frontend/src/store/musicStore.js`, `frontend/src/utils/pianoRollEvents.js`.
  Dependencies: Tasks 2, 5, 7.
  Logging requirements: INFO log note create/select/delete with note ID, track ID, pitch, start tick, duration ticks; WARN log ignored deletes with no selection; DEBUG log pointer-derived snapped coordinates.

- [x] Task 9: Implement dragging to move notes in time and transpose notes vertically.
  Deliverable: Pointer drag state that converts horizontal movement into snapped `start_tick` updates and vertical movement into chromatic pitch changes.
  Expected behavior: Dragging must clamp notes to `0..duration_ticks`, keep duration unchanged, support overlapping/polyphonic results, and immediately update `editedMusicJson` so a subsequent Play uses the edited pitch/timing. If active playback is running, existing revision-change behavior should stop playback deterministically.
  Files: `frontend/src/components/PianoRollEditor.jsx`, `frontend/src/store/musicStore.js`, `frontend/src/utils/pianoRollEvents.js`.
  Dependencies: Task 8.
  Logging requirements: DEBUG log drag start/move/end with sanitized old/new pitch/start/duration and snapped deltas; INFO log completed move/transpose; WARN log clamped moves at composition or MIDI pitch bounds.

- [x] Task 10: Implement duration resizing.
  Deliverable: Resize handle on each editable note, with pointer dragging that snaps and clamps `duration_ticks` while preserving note start and pitch.
  Expected behavior: Duration cannot become zero or exceed composition end; resizing a note should immediately affect playback and notation regeneration. Polyphonic overlaps remain allowed.
  Files: `frontend/src/components/PianoRollEditor.jsx`, `frontend/src/store/musicStore.js`, `frontend/src/utils/pianoRollEvents.js`.
  Dependencies: Task 9.
  Logging requirements: DEBUG log resize start/move/end with old/new duration ticks; INFO log completed duration edit; WARN log minimum-duration or composition-end clamping.

### Phase 4: Playback And Notation Synchronization
- [x] Task 11: Wire piano-roll edits to live playback state, cursor display, and MusicXML refresh.
  Deliverable: A playback cursor rendered over the piano roll using `playbackSeconds`, `playbackStatus`, `tempo`, and `ticks_per_quarter`; debounced MusicXML refresh after valid note edits; clear UI status while preview notation regeneration is loading or failed.
  Expected behavior: Pressing Play after moving/transposing/resizing a note schedules exactly the edited `tracks[].events[]`. The piano-roll cursor moves in sync with Tone.js playback position updates already produced by `PlaybackControls`. Notation refresh uses the regenerated MusicXML for the edited composition.
  Files: `frontend/src/components/PianoRollEditor.jsx`, `frontend/src/components/PlaybackControls.jsx` only if more precise cursor timing data is needed, `frontend/src/store/musicStore.js`, `frontend/src/api/musicApi.js`.
  Dependencies: Tasks 3, 8-10.
  Logging requirements: DEBUG log cursor tick/px updates at throttled cadence, notation debounce scheduling/cancel/completion, and composition revision used for preview rendering; INFO log playback cursor activation/deactivation; WARN log skipped MusicXML refresh for invalid composition.

- [x] Task 12: Ensure responsive layout, accessibility, and error handling are complete.
  Deliverable: Keyboard focus handling, ARIA labels for editor grid/notes/buttons, mobile-friendly controls, horizontal scroll behavior, visible selected/focused states, and user-facing empty/loading/error messages.
  Expected behavior: Editor remains usable on desktop and mobile; errors from invalid JSON, failed MusicXML rendering, unavailable canonical data, or empty tracks are visible without breaking JSON editing, export, playback, or reset.
  Files: `frontend/src/components/PianoRollEditor.jsx`, `frontend/src/components/MusicGenerator.jsx`, optional shared styles in the same files.
  Dependencies: Tasks 4-11.
  Logging requirements: WARN log recoverable UI state fallbacks; ERROR log unexpected interaction/render exceptions caught by local handlers if added; DEBUG log viewport/scroll metrics only when useful and throttled.

### Phase 5: Tests And Documentation
- [x] Task 13: Add deterministic tests for editor utilities, store actions, and backend MusicXML preview route if added.
  Deliverable: `frontend/src/utils/pianoRollEvents.test.js` plus any focused store/action tests that can run under Node without a browser test runner; backend tests for `POST /export/musicxml/preview` if introduced.
  Expected behavior: Tests cover pitch conversion, snap values for 480 TPQ, bar/beat grid metrics for 4/4 and 6/8, create/move/transpose/resize/delete immutability, clamping, polyphonic overlapping notes, undo/redo boundaries, composition validation after edits, MusicXML preview refresh contract, and acceptance-level scheduling inputs matching edited notes.
  Files: `frontend/src/utils/pianoRollEvents.test.js`, optional `frontend/src/store/musicStore.test.js` if practical with Zustand under Node, `backend/tests/test_export_routes.py` if a preview endpoint is added.
  Dependencies: Tasks 1-12.
  Logging requirements: Tests should assert key warning/error paths via fakes where practical and avoid noisy console output in passing runs.

- [x] Task 14: Update documentation and manual QA instructions.
  Deliverable: Update `README.md`, `docs/composition-v1.md`, and `docs/testing.md` with piano-roll usage, snapping/zoom behavior, undo/redo scope, MusicXML preview refresh behavior, logging diagnostics, and the acceptance smoke test.
  Expected behavior: Docs explain that piano roll, JSON editor, playback, export, and notation all operate on the same canonical `editedMusicJson`; manual QA includes generating a piece, dragging a melody note to another pitch, resizing it, pressing Play, and confirming the edited result is heard.
  Files: `README.md`, `docs/composition-v1.md`, `docs/testing.md`.
  Dependencies: Tasks 1-13.
  Logging requirements: Docs must mention browser devtools log fields for piano-roll edit actions, playback cursor, MusicXML preview refresh, and sanitized backend preview-render logs.

## Acceptance Mapping
- Generate a piece: existing `MusicGenerator` continues to populate `editedMusicJson` with canonical `composition.v1` and MusicXML.
- Select a track: Task 5 adds track selection and focused editing.
- Drag a melody note to another pitch: Task 9 transposes by vertical drag and updates `tracks[].events[]`.
- Change duration: Task 10 resizes `duration_ticks` with snapping/clamping.
- Press Play: Task 11 relies on existing canonical playback rebuilding from `editedMusicJson`, so Tone.js schedules the edited events.
- Hear exactly the edited version: Tasks 2, 9, 10, and 11 keep Zustand canonical state authoritative and avoid any harmony-derived audible fallback.
- Refresh notation: Tasks 3 and 11 regenerate MusicXML from the edited Composition V1 data.

## Risks And Mitigations
- Risk: Component-level pointer interaction tests are hard without a browser/JSDOM test runner. Mitigation: keep geometry/edit logic in pure utilities and cover interaction-critical calculations under `node --test`; rely on documented manual smoke checks for actual pointer behavior.
- Risk: Calling `/export/musicxml` for every drag could trigger downloads or too many requests. Mitigation: add a preview route or non-download API helper and debounce refresh until edit commit/end.
- Risk: Generated notes may lack stable IDs. Mitigation: normalize IDs in editor utilities/store on first edit while preserving all other event fields.
- Risk: Large 32-bar pieces can become visually heavy. Mitigation: render focused track primarily, context tracks lightly, and avoid virtualization unless profiling shows it is needed.
- Risk: Undo history can grow large. Mitigation: bounded history of note-edit snapshots only, with clear scope documented.

## Verification Commands
- `npm test` from `frontend/`
- `npm run lint` from `frontend/`
- `npm run build` from `frontend/`
- `../.venv/bin/python -m pytest` from `backend/` if a backend MusicXML preview route or tests are changed

## Manual Smoke Test
1. Start backend and frontend locally.
2. Generate a 16-32 bar canonical `composition.v1` piece with at least melody and accompaniment tracks.
3. Open the piano-roll editor, select the melody track, and choose `1/8` or `1/16` snap.
4. Drag one melody note vertically to a different pitch and horizontally to a nearby snapped time.
5. Resize the same note duration.
6. Confirm the JSON editor shows the changed `tracks[].events[]` note event.
7. Confirm notation preview refreshes after the edit.
8. Press Play and confirm the edited pitch and duration are heard exactly, with the playback cursor moving over the edited timeline.
9. Use undo and redo, then play again to confirm the audible result follows the current state.
