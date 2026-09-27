# Implementation Plan: Upgrade V2 Music Editing Workflow

Branch: main (branch creation disabled by project config)
Created: 2026-09-10

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Roadmap Linkage
Milestone: "Upgrade V2 music editing workflow"
Rationale: Added to `.ai-factory/ROADMAP.md` after implementation (was skipped at plan time because no open milestone existed).

## Scope And Decisions
- `editedMusicJson` remains the only editable composition and every committed musical change produces a validated canonical `composition.v2`; no editor-owned composition clone or parallel note model is introduced.
- Selection, clipboard fragments, track visibility/lock flags, viewport position, edit cursor, and loop bounds are ephemeral UI state, not Composition V2 fields and not project persistence data. Clipboard data is an operation payload, not a shadow composition.
- A selected note is identified by `{ trackId, eventId }`, so box/range selection can safely span tracks even if event IDs are not globally unique. Hidden tracks are excluded from hit-testing and box selection; locked tracks remain visible/selectable but are rejected as edit targets.
- Pointer gestures keep only transient geometry/deltas in refs or local interaction state. Drag/move/resize previews update transforms without validating or replacing the composition on every pointer event, then commit one canonical transaction on pointer-up.
- Undo/redo covers every canonical manual editor mutation in this plan: create, delete, move, resize, cut, paste, duplicate, transpose, quantize, velocity, note length, humanize, articulation, and dynamics. Selection, viewport, visibility, locking, cursor movement, and loop controls are navigation/UI state and do not create history entries.
- Copy captures selected note values and relative track/tick placement; cut is copy plus one atomic canonical deletion. Paste generates collision-free event/tie IDs, preserves supported note expression fields, preserves source-track placement for multi-track clips, and uses the active track for a single-track clip when explicitly requested. It rejects unavailable or locked targets atomically.
- Duplicate places a copy after the selected range by default, aligned to the active snap. Transpose is atomic and rejects a command that would move any pitch outside MIDI 0-127; octave transpose is the same operation with `+12` or `-12` semitones.
- Quantization supports start-only and start-plus-end modes against the existing snap grid. Strength is configurable from 0-100%; 0% is a no-op and 100% reaches the nearest grid point. Note lengths remain at least one tick and inside `duration_ticks`.
- Note-length tools include set-to-grid, lengthen/shorten by one snap step, quantize end/length, and legato-to-next-note within the same track/voice without extending beyond the composition.
- Humanize exposes bounded timing and velocity amounts. Timing preserves composition bounds and positive durations; velocity remains 1-127. Pure helpers accept an injectable/seeded random source so tests are deterministic, while each UI invocation is one undoable transaction.
- Basic articulation editing applies the existing five V2 articulation values to all compatible selected notes while preserving tie rules. Basic dynamics editing creates, changes, or removes canonical track `dynamic_marks` at a selected bar/cursor tick using the existing `ppp`-`fff` levels; unsupported automation/pedal authoring remains available through JSON tooling.
- The edit cursor is a tick position independent of live playback position. Bar/section navigation moves the cursor and viewport; Play From Cursor schedules from it. Loop Selected Range derives exact tick bounds from selected notes or the active selected bar range and remains disabled when no valid range exists.
- The JSON tab remains available as advanced/debug tooling. Valid canonical JSON changes enter the shared composition history as one transaction; invalid/incomplete JSON remains repairable in the JSON editor but cannot be played, persisted, or inserted into canonical undo history until valid.

## Commit Plan
- **Commit 1** (after tasks 1-3): `feat(editor): add canonical selection and transform transactions`
- **Commit 2** (after tasks 4-6): `feat(editor): add scalable multi-note editing workflow`
- **Commit 3** (after tasks 7-8): `feat(playback): add cursor navigation and selection loops`
- **Commit 4** (after tasks 9-10): `test(editor): verify large-score editing workflow`

## Tasks

### Phase 1: Canonical Editing Foundation

- [x] Task 1: Define reusable editor references, selection/range geometry, and canonical V2 validation parity.
  - Add a small pure editor-domain utility module, expected at `frontend/src/utils/compositionEditorSelection.js`, for `{ trackId, eventId }` references, stable keys, note/range bounds, box intersection, visible/locked-track filtering, and selection reconciliation after composition changes.
  - Extend `frontend/src/utils/pianoRollSelection.js` only where existing variable-meter bar selection primitives can be shared; do not merge AI-region state with note selection or transport state.
  - Complete frontend validation of the already-supported `dynamic_marks` shape, level enum, sorted/unique ticks, and composition bounds in `frontend/src/utils/musicJsonValidation.js` so direct dynamics editing cannot create data accepted by the browser but rejected by backend Pydantic validation.
  - Add focused cases to `frontend/src/utils/pianoRollSelection.test.js`, a new `frontend/src/utils/compositionEditorSelection.test.js`, and `frontend/src/utils/musicJsonValidation.test.js`, including duplicate event IDs across different tracks, variable meter, hidden/locked tracks, edge intersections, and invalid dynamics.
  - Logging: keep pure utilities log-free; callers must log only selected counts, track counts, normalized tick bounds, and validation codes/messages through `frontend/src/utils/appLogger.js` at DEBUG/WARN, never event arrays or full compositions.

- [x] Task 2: Replace note-only store mutations with one canonical composition transaction and history path. (depends on Task 1)
  - Refactor `frontend/src/store/musicStore.js` so a single internal transaction helper validates and commits `editedMusicJson`, updates revision/notation/autosave/analysis/preview invalidation, reconciles selection, and records one bounded undo entry per logical operation.
  - Rename or generalize `noteEditUndoStack`, `noteEditRedoStack`, `undoNoteEdit`, and `redoNoteEdit` to composition-editor history while updating all current callers/tests; avoid compatibility aliases unless an external consumer is found.
  - Preserve structural sharing for untouched tracks/events in transaction results. Store history entries as bounded before/after canonical references plus the minimal UI restoration data needed for selection and current track, rather than serializing JSON per command.
  - Route current note, tie, articulation, harmony/motif apply, arrangement/development apply, and valid `PromptJsonEditor` canonical changes through the common commit semantics where they are already undoable editor actions. Define invalid JSON handling explicitly so it stays repairable without replacing valid history snapshots.
  - Do not reset the edit cursor to tick zero after ordinary edits; stop stale playback and clamp cursor/selection to the resulting composition instead.
  - Expand `frontend/src/store/musicStore.test.js` and affected `musicStore.*.test.js` suites for transaction atomicity, redo invalidation, 50-entry bounds, JSON edit history, selection restoration, preview invalidation, dirty/autosave revisions, and failed validation leaving composition/history untouched.
  - Logging: replace direct `console.*` in touched transaction/history paths with `appLogger`; INFO records operation name, affected note/track counts, history depth, and revision prefix, DEBUG records timing/validation duration, WARN records bounded rejection reasons, and ERROR records sanitized unexpected failures.

- [x] Task 3: Implement immutable bulk note operations and clipboard semantics. (depends on Tasks 1-2)
  - Add `frontend/src/utils/compositionEditorOperations.js` with single-pass/indexed operations for delete/cut, paste, duplicate, semitone/octave transpose, velocity set/delta/scale, start/end quantize with strength, fixed/incremental/legato note lengths, articulation changes, and bounded humanize.
  - Define operation inputs as canonical composition plus note references/options and outputs as `{ composition, selection, summary }`; preserve all untouched Composition V2 event/track fields and reject the whole operation on locked/missing targets or invalid pitch/timing results.
  - Rebuild pasted tie groups with new IDs, remove partial/incompatible ties when only part of a chain is copied, generate collision-free note IDs, and run existing motif reconciliation when delete/cut invalidates canonical motif references. Do not copy motif metadata as if it were note data.
  - Use timeline/snap utilities from `frontend/src/utils/compositionTimeline.js` and `frontend/src/utils/pianoRollEvents.js`; support variable meter and non-default `ticks_per_quarter` without assuming constant bar length.
  - Add comprehensive deterministic tests in `frontend/src/utils/compositionEditorOperations.test.js` for boundaries, 0/50/100% quantization, seeded humanize, velocity 1-127, MIDI pitch limits, locked tracks, ties, motifs, multi-track paste, overlap policy, positive lengths, and immutability/structural sharing.
  - Logging: pure operations remain log-free and return compact summaries/rejection codes; store callers emit INFO per committed bulk operation and DEBUG duration/count summaries, with no pitches, note payloads, clipboard contents, event arrays, or full composition data.

### Phase 2: Productive And Scalable Editor UI

- [x] Task 4: Split the piano roll into stable viewport/note layers and eliminate high-frequency canonical updates. (depends on Tasks 1-3)
  - Refactor `frontend/src/components/PianoRollEditor.jsx` into focused components/hooks under `frontend/src/components/piano-roll/` only where this prevents the current monolithic component from rerendering all notes for cursor, selection-box, or drag-preview updates.
  - Add a viewport model in `frontend/src/utils/pianoRollViewport.js` that derives visible tick/pitch bounds from `scrollLeft`, client size, zoom, and a render buffer; render only intersecting notes/grid labels for 50-100 bar scores while retaining native horizontal scrolling and accessible offscreen navigation.
  - Isolate the playback cursor and transient selection/drag overlays from the memoized note layer. Use refs/CSS transforms or narrow local state for pointer movement and commit one store transaction on pointer-up; cancel/Escape restores the canonical view without a mutation.
  - Build per-render `Map`/`Set` indexes once for track/note lookup and avoid repeated whole-composition scans. Preserve zoom anchoring around the pointer or viewport center and keep mobile horizontal scrolling usable.
  - Add `frontend/src/utils/pianoRollViewport.test.js` for culling, buffers, zoom anchors, and scroll bounds. Keep React component behavior for direct browser E2E in Task 9 rather than adding an unconfigured JSDOM stack.
  - Logging: use throttled DEBUG diagnostics for viewport ranges/rendered counts and gesture completion duration; do not log each pointer move. WARN only for clamping/cancelled invalid commits and ERROR for sanitized render/interaction failures.

- [x] Task 5: Add reliable multi-select, box/range selection, clipboard commands, duplicate, and keyboard shortcut routing. (depends on Task 4)
  - Implement click, Ctrl/Cmd-click toggle, Shift-click range extension, and drag-box selection across visible tracks in the piano-roll note layer; preserve a primary note for inspector defaults and fix the current pointerdown/click ordering that collapses Shift selection before toggling.
  - Reserve a clear gesture/modifier for bar-range selection so note box selection and the existing AI region range do not conflict. Add Select All Visible and Clear Selection actions and show selected note/track/range counts.
  - Wire Ctrl/Cmd+C, X, V, D, A, Delete/Backspace, Ctrl/Cmd+Z, Ctrl/Cmd+Shift+Z, Ctrl/Cmd+Y, arrow/nudge, octave transpose, Space, Escape, and documented navigation shortcuts through one `frontend/src/utils/editorShortcuts.js` dispatcher.
  - Ignore shortcuts originating in input, textarea, select, button, contenteditable, or the JSON editor, except deliberately scoped transport shortcuts. Make clipboard commands use the store clipboard rather than requiring browser clipboard permissions.
  - Connect copy/cut/paste/duplicate/delete to the Task 2 transaction and Task 3 operations. Paste at the edit cursor, retain the newly created selection, and provide disabled states/rejection feedback for locked tracks or empty clipboard.
  - Add `frontend/src/utils/editorShortcuts.test.js` and store tests for platform modifiers, editable-target guards, command dispatch, selection transitions, clipboard lifecycle, and one-history-entry behavior.
  - Logging: DEBUG logs normalized command IDs and selection counts; INFO logs committed cut/paste/duplicate/delete summaries; WARN logs guarded/locked/empty commands. Never log key-by-key typing, clipboard note bodies, or JSON editor content.

- [x] Task 6: Add track controls and multi-note transformation/expression inspectors. (depends on Tasks 3-5)
  - Add compact, responsive editor toolbar/inspector components under `frontend/src/components/piano-roll/` for semitone/octave transpose, quantize mode/grid/strength, velocity set/delta, note-length operations, timing/velocity humanize amounts, and duplicate.
  - Upgrade articulation controls to apply compatible changes across the current selection and clearly report notes skipped by tie constraints without partially violating V2 rules.
  - Add basic dynamics controls for the active track: choose `ppp` through `fff`, create/update at the edit cursor or selected bar start, and remove a mark. Keep `dynamic_marks` sorted and unique and commit through canonical history.
  - Add per-track visibility and lock controls alongside track selection. Visibility/lock remain editor UI state, are reconciled when tracks change, and do not alter persisted V2, playback mute/solo, arrangement protection, or JSON.
  - Ensure every action is operable by keyboard, has an accessible label/focus state, works in the narrow workspace layout, and exposes stable `data-testid` values for acceptance tests.
  - Extend `frontend/src/store/musicStore.test.js` for editor preferences, lock enforcement, bulk expression changes, dynamics history, and reconciliation after import/generation/project load.
  - Logging: INFO logs user-invoked operation IDs and compact affected counts; DEBUG logs sanitized options such as strength/amount/grid and elapsed time; WARN logs no-op, incompatible tie, lock, and bound failures. UI-only visibility changes log at DEBUG, not INFO.

### Phase 3: Navigation, Cursor, Loop, And Playback

- [x] Task 7: Add zoom, scrolling, current-bar navigation, and section navigation around an explicit edit cursor. (depends on Task 4)
  - Add canonical tick-based edit cursor and viewport request state/actions to `frontend/src/store/musicStore.js`, with pure conversions in `frontend/src/utils/compositionTimeline.js` or a focused `frontend/src/utils/editorNavigation.js`.
  - Support ruler/grid cursor placement, current-bar input plus previous/next bar, previous/next section, section dropdown, horizontal scrollbar, wheel/trackpad scrolling, Ctrl/Cmd+wheel zoom around the pointer, zoom in/out, zoom-to-selection, and zoom-to-fit.
  - Section navigation uses canonical V2 section tick/bar bounds, including unlabeled section types with generated display labels, and works with variable meter. Every navigation action clamps to `duration_ticks` and requests viewport scrolling without mutating the composition.
  - Keep scroll/zoom updates local/narrow enough that note content is not rebuilt until the visible range or zoom materially changes. Use passive listeners where no `preventDefault` is needed and requestAnimationFrame for coalesced scroll measurements.
  - Add `frontend/src/utils/editorNavigation.test.js` plus store tests for cursor clamping, variable-meter bar jumps, section boundaries, zoom anchors, selection fit, and project/composition replacement.
  - Logging: DEBUG logs debounced navigation command, destination bar/section/tick, zoom, and viewport range; WARN logs malformed timeline/section targets. Do not log every scroll event.

- [x] Task 8: Support playback from cursor and looping the selected range. (depends on Tasks 5 and 7)
  - Extend `frontend/src/utils/tonePlaybackEngine.js` with explicit bounded start and loop-range semantics, including rescheduling completion behavior, controller state before the start tick, loop enable/disable while paused/playing, and correct cleanup on stop/source revision changes.
  - Ensure schedule rebuilding includes all tracks and relies on live effective gain for mute/solo so an initially muted track can be unmuted during playback without missing scheduled notes; preserve candidate audition behavior and do not persist transport state in Composition V2.
  - Add transport actions/state in `frontend/src/store/musicStore.js` for cursor start and loop `{ startTick, endTick, enabled }`, deriving loop bounds from selected notes or active bar range and clearing/clamping stale ranges after composition edits/replacement.
  - Update `frontend/src/components/PlaybackControls.jsx` and piano-roll controls with Play From Cursor, Set/Loop Selection, loop status, and seek/bar synchronization. Ordinary Play retains the documented start behavior; Space uses play/pause/resume without stealing focus from editable controls.
  - Move the playback cursor subscription into the isolated overlay from Task 4 so 100 ms position updates do not rerender the note layer; auto-follow is optional/toggleable and must not fight manual scrolling.
  - Extend `frontend/src/utils/tonePlaybackEngine.test.js`, `playbackPosition.test.js`, playback/store tests, and schedule tests for nonzero starts, exact loop boundaries, variable tempo/meter, controller/dynamic state, mute/solo changes, completion, edits during loops, and candidate audition.
  - Logging: use `appLogger` for INFO transport start/stop/loop changes with tick/second bounds and source revision prefix, DEBUG schedule counts/rebuild durations, WARN invalid/stale ranges, and ERROR sanitized Tone failures. Never log schedules, event arrays, or full compositions.

### Phase 4: Acceptance, Performance, Logging, And Documentation

- [x] Task 9: Add direct interaction acceptance tests and enforce a large-score performance budget. (depends on Tasks 4-8)
  - Add a focused Playwright journey, expected at `frontend/e2e/v2-editor-workflow.spec.js`, that uses real pointer and keyboard interaction rather than direct store mutation to select several bars, copy/paste or duplicate, transpose, change velocities, quantize, set the edited loop, play from cursor, verify loop bounds, then undo/redo the complete operation sequence.
  - Cover box selection across visible tracks, locked/hidden track behavior, keyboard shortcut focus guards, articulation/dynamics edits, note length/humanize, horizontal scroll, zoom anchor, current-bar/section navigation, and mobile-width usability.
  - Add a deterministic 100-bar/high-note-count fixture/helper under `frontend/e2e/fixtures/` or `frontend/e2e/helpers.js`. Instrument rendered note-node count, canonical commit count during a drag, and interaction latency using stable browser marks/counters available only in development/test mode.
  - Set explicit, hardware-tolerant gates: offscreen culling keeps rendered note nodes proportional to the buffered viewport rather than total score; a drag produces zero canonical commits before pointer-up and exactly one on completion; playback cursor ticks do not rerender the note layer; representative box-select/transform/navigation interactions complete within a documented CI budget established from a baseline run.
  - Run `npm test`, `npm run lint`, `npm run build`, and the focused Playwright editor journey from `frontend/`; then run the existing V1/V2/import journeys most likely to regress. Record test commands and performance budgets in `docs/testing.md`, not in a standalone report artifact.
  - Logging: tests assert `VITE_LOG_LEVEL=silent` suppression and verify representative verbose operation summaries contain no full composition/event/clipboard payloads. Performance instrumentation logs aggregated DEBUG measurements only and is disabled or inert in normal production builds.

- [x] Task 10: Complete logging migration and user/developer documentation. (depends on Tasks 1-9)
  - Replace remaining direct `console.*` calls in touched editor/playback/store paths with `frontend/src/utils/appLogger.js`; centralize editor logger namespaces and preserve environment-controlled `VITE_LOG_LEVEL` behavior.
  - Update `README.md` with the productive V2 editor workflow and keyboard/transport highlights.
  - Add or expand a dedicated editor section/page under `docs/` and update `docs/composition-v2.md` with canonical mutation, clipboard, selection, history, articulation, dynamics, track visibility/lock, and JSON-tooling boundaries.
  - Update `docs/testing.md` with unit/E2E/performance commands, the 100-bar fixture and budgets, direct interaction coverage, logging-level checks, and browser/mobile limitations.
  - Update `docs/project-persistence.md` if history/autosave wording currently implies note-only undo, and update `docs/CODEBASE_MAP.md` plus `AGENTS.md` if the new `components/piano-roll/` and editor utility modules materially change navigation.
  - Clarify in `.env.example` that `VITE_LOG_LEVEL` is build-time in Vite production builds; only change Docker/Compose wiring if implementation chooses to make it an explicit build argument.
  - Logging: document INFO/DEBUG/WARN/ERROR event policy and prohibited payloads; add logger tests where needed to prove production reduction requires no code edits and sensitive/full musical payloads are never emitted.

## Verification Checklist
- The composition shown in JSON, saved to projects, exported, analyzed, rendered, and played is the same validated `editedMusicJson` updated by each editor transaction.
- Selecting notes across several bars and visible tracks supports copy/cut/paste, duplicate, transpose/octave transpose, velocity, quantize strength, note length, humanize, articulation, dynamics, and atomic undo/redo.
- Hidden tracks are absent from editor rendering/selection; locked tracks cannot be changed by pointer, shortcut, paste, or bulk operations; neither setting modifies canonical V2 or playback mute/solo.
- Loop Selected Range and Play From Cursor use variable-tempo/meter tick conversion and play only the intended edited range from canonical `tracks[].events[]`.
- A 100-bar fixture demonstrates viewport-bounded rendering, one canonical commit per pointer gesture, isolated playback-cursor rendering, responsive zoom/scroll/navigation, and bounded history.
- JSON editing remains available, valid canonical JSON participates in history, and invalid JSON cannot leak into playback/persistence/export.
- All unit tests, lint, production build, focused Playwright workflow, and relevant regression journeys pass with sanitized environment-controlled logs and updated documentation.
