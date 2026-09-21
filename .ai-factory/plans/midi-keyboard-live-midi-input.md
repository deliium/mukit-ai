# Implementation Plan: MIDI Keyboard / Live MIDI Input

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-21

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: full, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: ship **browser Web MIDI performance capture** into canonical `composition.v2` on a selected track — record / stop / count-in / metronome / optional post-record quantize / active-note visuals / safe disconnect — without requiring a MIDI device at app startup and without inventing notes from harmony or backend MIDI streams

## Roadmap Linkage
Milestone: "MIDI keyboard / live MIDI performance input"
Rationale: Piano-roll editing, Tone.js transport/loop, V2 velocity + `sustain_pedals`, and post-edit quantize already exist, but users still enter notes manually. Add as a new unchecked milestone in `.ai-factory/ROADMAP.md` during docs/implement (roadmap owner: `/aif-roadmap` or docs checkpoint). Prior milestone "Hybrid LLM planner + symbolic note generation pipeline" is already complete.

## Goal

Allow users to create musical ideas by **playing** a MIDI keyboard (or a computer-keyboard test mode) into the workspace: connect a device, arm recording with optional count-in and metronome, capture note-on/off + velocity (+ sustain where practical), commit valid V2 note events to a destination track, optionally quantize after the take, then edit and ask AI to develop the melody — all through the existing composition transaction / playback / piano-roll / save-export path.

```text
MIDI device (or QWERTY test mode)
  → Web MIDI / synthetic note events
  → performance capture (ticks from tempo map + transport)
  → buffered take (raw timing, session-only)
  → Stop → extend timeline if needed → commit V2 events (+ optional sustain_pedals)
  → optional quantize (second transaction)
  → piano roll / Tone.js / Develop / Motifs / save / export
```

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| V2 note events | `pitch`, `start_tick`, `duration_ticks`, `velocity` (1–127), `id`; tracks hold `events[]` | Sole playable write target |
| Sustain | `tracks[].sustain_pedals[]` `{ start_tick, duration_ticks }` validated in `musicJsonValidation.js` | Map CC64 → pedal spans on destination track |
| Note create | `createTrackNote` / `musicStore.createNote` + `commitCompositionTransaction` | Pattern for validation + undo; recording needs **batch** commit + **timeline extend** (create clamps to `duration_ticks`) |
| Quantize | `quantizeNotes` / `quantizeSelection` (`compositionEditorOperations.js`) | Optional post-record quantize of take note refs |
| Destination track | `pianoRollTrackId` + `selectPianoRollTrack` | Default destination; expose explicit selector in MIDI panel |
| Transport | `tonePlaybackEngine.js` + `PlaybackControls.jsx`; ephemeral loop via `playbackLoop.js` | Tempo/`ticks_per_quarter` → tick clock; seek/play-from-cursor |
| Edit cursor | `editCursorTick` | Recording origin / count-in start |
| Pitch helpers | `midiToPitch` / `pitchToMidi` in `pianoRollEvents.js` | MIDI note number → V2 pitch spelling |
| Logging | `appLogger` (`frontend/src/utils/appLogger.js`) | Namespaced MIDI logs; never dump event arrays |
| Tests | Node test runner; `createFakeTone()` in `tonePlaybackEngine.test.js`; store tests mock timers | Same style for fake `MIDIAccess` / message injectors |
| AI develop / motifs | Operate on current `editedMusicJson` tracks | No special bridge — valid V2 notes just work |
| Import MIDI file | Backend `composition_midi_import.py` | **Not** live input; do not conflate file import with Web MIDI |

### Gaps (must build)

| Gap | Notes |
|-----|--------|
| No Web MIDI usage | `navigator.requestMIDIAccess` absent; no device discovery UI |
| No performance capture | No wall-clock/transport → tick mapping for live notes |
| No record/stop/arm UX | No count-in or metronome click track |
| Timeline growth | `clampNoteTiming` / `createTrackNote` refuse notes past `duration_ticks` — recording must extend bars/duration/sections cleanly |
| Raw take buffer | Need session-only take that preserves unquantized timing before optional quantize |
| Active MIDI note UI | No live key/note highlight for external MIDI |
| Disconnect safety | Need state machine for device loss mid-take |
| Computer-keyboard test mode | Useful for CI and machines without hardware — not present |
| Loop overdub | Playback loop exists, but punch/overdub merge policy is ambiguous — **defer** rather than ship a half-baked overdub |

### Coupling risks to avoid

1. Requiring MIDI permission or `requestMIDIAccess` during app boot / first paint.
2. Writing live notes outside `commitCompositionTransaction` (breaks undo, autosave, analysis invalidation).
3. Inventing audible notes from `harmony` or analysis sidecars.
4. Persisting session MIDI state (`activeNotes`, device ids, raw take) into project JSON / revisions.
5. Silently clamping recorded notes at composition end instead of extending the timeline (or failing with a clear message).
6. Treating file MIDI import and live Web MIDI as the same code path.
7. Growing orchestration inside unrelated panels — keep a dedicated MIDI input module + thin UI shell.
8. Logging full MIDI dumps, event arrays, or composition bodies at INFO.
9. Shipping loop-overdub that merges randomly with existing notes without a clear replace/overdub policy.
10. Blocking startup or Playwright journeys when Web MIDI is missing (Firefox without flag, headless CI).

## Approach Evaluation (locked)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Backend MIDI bridge** (WebSocket server captures MIDI) | OS-level devices | Breaks browser-first architecture; Docker/host MIDI complexity | Reject |
| **B. Live `createNote` on every note-off** | Simple | Undo spam; race with duration clamp; hard to batch quantize | Reject as sole path |
| **C. Session take buffer → one commit on Stop** (+ optional quantize) | Clean undo; preserves raw timing; matches editor transaction model | Slightly more state machine | **Accepted** |
| **D. Loop overdub in v1** | Power-user | Needs merge policy, take lanes, conflict UX | **Deferred** — document; do not block acceptance |

**Decision:** Implement **frontend-only Web MIDI** with a session performance take that commits once into `composition.v2` on Stop. Metronome/count-in are ephemeral Tone.js transport helpers. Loop **playback** may run during armed recording for feel, but **loop recording / overdub is out of scope** until a clean replace-vs-overdub design exists.

## Scope And Decisions

### In scope
- Feature-detect Web MIDI; lazy permission on user gesture (“Enable MIDI”).
- Device discovery, selection, disconnect/reconnect handling.
- Note on / note off / velocity; CC64 sustain → `sustain_pedals` on destination track when spans are valid.
- Record / Stop / optional N-bar count-in / metronome click.
- Destination track selector (default `pianoRollTrackId`).
- Session raw take buffer; optional quantization after commit (reuse `quantizeNotes`).
- Extend composition timeline when the take exceeds current `duration_ticks`.
- Active MIDI note visualization (pitch-lane / key highlight).
- Computer-keyboard test input mode for development and unit tests.
- Frontend unit tests with mocked MIDI events; docs page + editor/playback cross-links.
- Roadmap milestone checkbox addition during docs checkpoint.

### Out of scope
- Backend MIDI servers, USB passthrough in Docker, or OS driver installers.
- MPE, aftertouch, pitch bend as first-class V2 fields (ignore or no-op for v1; log DEBUG counts only).
- Loop overdub / multi-take lanes / punch-in recording.
- Changing FluidSynth export or claiming live MIDI improves AI quality.
- Auto-creating new tracks without user confirmation (may offer “create track” later; v1 requires an existing destination track).
- Persisting preferred MIDI device id across browsers beyond optional `localStorage` preference (allowed if versioned and non-secret).

### Architecture decisions (locked)

**1. Browser-only capture; V2 remains sole score**

```text
Web MIDI / QWERTY  →  session take (ephemeral)
                   →  commit  →  composition.v2 tracks[].events[]
```

No parallel “MIDI clip” document. No backend round-trip for live notes.

**2. Take lifecycle state machine**

```text
idle → enabling → ready → armed → counting_in → recording → stopping → idle
         ↘ unavailable / denied / error
```

- Mid-recording disconnect: stop capture, keep partial take buffered, surface `midi_device_disconnected`; user may Commit partial or Discard.
- Reconnect: refresh device list; do not auto-resume recording.

**3. Timing model**

- Origin: `editCursorTick` at Arm/Record start (after count-in completes).
- Clock: map elapsed musical time using composition tempo map + `ticks_per_quarter` (reuse `playbackPosition` / tempo helpers where possible; do not invent a second PPQ).
- Note-on opens a held note keyed by MIDI number (+ channel if needed); note-off / second note-on closes duration (`max(1, end - start)` ticks).
- Velocity from note-on (clamp 1–127; velocity 0 note-on = note-off per MIDI spec).
- **Raw timing:** take stores unquantized ticks until optional post-commit quantize.

**4. Commit policy**

- On Stop (or explicit Commit after disconnect): extend timeline to cover take end (complete bars under active meter), append events to destination track, merge non-overlapping `sustain_pedals`, validate, **one** `commitCompositionTransaction` (`action: 'midi-record'`).
- Optional “Quantize after recording”: second transaction via existing `quantizeNotes` on the new note refs only.
- Undo: one step removes the whole take commit (quantize is separate undo if applied).

**5. Timeline extension**

- New pure helper (e.g. `extendCompositionToTick(composition, endTick)`) updates `duration_ticks`, `bar_count`, and trailing section bounds without inventing notes.
- Must keep V2 meter/bar invariants; unit-test mixed meter if helpers already support it, otherwise document single-meter limitation and refuse with clear code when extension cannot be computed.

**6. Metronome / count-in**

- Ephemeral Tone.js scheduled clicks; never written into V2.
- Count-in: 1–2 bars (UI control) before first recorded tick at origin.
- Metronome may continue during recording (toggle).
- Must not require a MIDI device (works with QWERTY mode).

**7. Loop recording**

- **Not in v1.** Existing selection/bar loop remains audition/transport only.
- Docs explicitly state overdub deferred.

**8. Active notes UI**

- Session set of currently held MIDI pitches (from device or QWERTY).
- Highlight corresponding piano-roll pitch rows (and optional mini keyboard strip in MIDI panel).
- Clear on note-off, panic/all-notes-off, Stop, or disconnect.

**9. Computer-keyboard test mode**

- Map a fixed two-octave layout (document keys) → synthetic note on/off with default velocity.
- Disabled while typing in inputs/textarea/contenteditable (same focus guards as piano-roll shortcuts).
- Primary purpose: tests + demos without hardware; label as “Test input”.

**10. Logging**

- Verbose structured logs via `appLogger` namespace `midiInput` / `midiCapture`: support probe, permission result, device id/name (not SysEx), state transitions, take note/pedal counts, tick origin/end, quantize strength, disconnect codes.
- Never INFO-log full MIDI byte streams, full takes, or composition JSON.

**11. Startup**

- No `requestMIDIAccess` on module import or root mount.
- UI shows “MIDI unsupported” / “Enable MIDI” without blocking generator, editor, or playback.

## Acceptance criteria mapping

| Criterion | Tasks |
|----------|-------|
| Evaluate Web MIDI + frontend architecture | 1 |
| Device discovery/selection | 2–3 |
| Note on/off, velocity, sustain | 2, 4–5 |
| Record into canonical V2 | 5–6 |
| Record / stop / count-in / metronome / destination / optional quantize | 3, 6–8 |
| Preserve raw timing before optional quantize | 4–5, 7 |
| Loop recording only if clean | Decision D — deferred; documented in 12 |
| Active MIDI notes visually | 8–9 |
| Disconnect/reconnect safe | 2–3, 6 |
| No MIDI required at startup | 2, 9, 11 |
| Computer-keyboard test mode | 4, 10 |
| Works with playback, piano roll, AI develop, motifs, save/export | 5–6, 11–12 |
| Mocked MIDI frontend tests + docs | 10–12 |

## Commit Plan
- **Commit 1** (tasks 1–3): `feat(midi): Web MIDI access layer, device selection, and session state machine`
- **Commit 2** (tasks 4–6): `feat(midi): performance capture into composition.v2 with timeline extend`
- **Commit 3** (tasks 7–9): `feat(midi): metronome, count-in, quantize-after-record, and active-note UI`
- **Commit 4** (tasks 10–12): `feat(midi): keyboard test mode, mocked MIDI tests, docs, and roadmap milestone`

## Tasks

### Phase 1: Access layer + session state

- [x] Task 1: Web MIDI capability audit module + constants
  Deliverable: Pure helpers `isWebMidiSupported()`, support reason codes (`unsupported`, `insecure_context`, `permission_denied`, `available`), and typed MIDI message parsers (note on/off, CC64). No `requestMIDIAccess` side effects on import. Document browser caveats (Safari/Firefox flags) in module header only — full docs in Task 12.
  LOGGING: DEBUG support probe result; never throw on missing API.
  Files: `frontend/src/utils/midiInputSupport.js` (new), `frontend/src/utils/midiInputMessages.js` (new), tests colocated.

- [x] Task 2: MIDI access / device registry with disconnect safety
  Deliverable: Async `enableMidiAccess()` (user-gesture only) wrapping `navigator.requestMIDIAccess({ sysex: false })`; list inputs; select by id; subscribe `statechange`; emit normalized device list; dispose listeners cleanly. Handle device removal mid-session without crashing. Optional versioned `localStorage` last-device preference (`midiInput:v1`), non-secret.
  LOGGING: INFO enable success/failure code + input count; WARN disconnect/reconnect with device id prefix; ERROR unexpected access failures (sanitized).
  Files: `frontend/src/utils/midiInputAccess.js` (new), tests with mocked `MIDIAccess` / `MIDIInput`.

- [x] Task 3: Zustand MIDI session slice + state machine
  Deliverable: Ephemeral store fields: `midiSupport`, `midiAccessStatus`, `midiInputs`, `midiSelectedInputId`, `midiArmed`, `midiPhase`, `midiDestinationTrackId`, `midiMetronomeEnabled`, `midiCountInBars`, `midiQuantizeAfterRecord`, `midiActiveNotes`, `midiTakeSummary`, error codes. Actions: enable, select device, set destination (defaults to `pianoRollTrackId`), arm/disarm, panic (all notes off). No persistence into project autosave payload.
  LOGGING: INFO phase transitions; DEBUG destination track id; WARN invalid transitions.
  Files: `frontend/src/store/musicStore.js` (slice), `frontend/src/store/musicStore.midiInput.test.js` (new).

### Phase 2: Capture → V2 commit

- [x] Task 4: Performance take buffer + computer-keyboard synthesizer
  Deliverable: `midiPerformanceCapture.js` — open/close notes, velocity, CC64 pedal spans, tick conversion from transport origin; expose `injectMessage` for tests. `computerKeyboardMidi.js` — QWERTY → note on/off with focus guards. Both feed the same capture API.
  LOGGING: DEBUG note open/close counts; INFO take finalize summary (note count, pedal count, tick span); never log pitches list at INFO beyond counts.
  Files: `frontend/src/utils/midiPerformanceCapture.js`, `frontend/src/utils/computerKeyboardMidi.js`, tests.

- [x] Task 5: Timeline extend + batch apply take to V2
  Deliverable: Pure `extendCompositionToTick` (and section/`bar_count` reconcile) + `applyMidiTakeToComposition(composition, { trackId, notes, sustainPedals })` producing validated V2. Reuse `midiToPitch`, id allocation (`ensureNoteId`), sustain non-overlap rules. Reject locked destination tracks with closed error code.
  LOGGING: INFO extend bars delta + note/pedal counts; WARN clamp/skip reasons; ERROR validation failures (message only).
  Files: `frontend/src/utils/midiTakeApply.js` (new), possibly small exports from `pianoRollEvents.js` / timeline utils; tests including past-end recording.

- [x] Task 6: Record / Stop orchestration in store
  Deliverable: Wire access + capture + apply: Start/Stop recording; on Stop commit via `commitCompositionTransaction` (`action: 'midi-record'`); select new notes when practical; invalidate analysis/previews per existing commit hooks; autosave path unchanged. Partial commit after disconnect. Discard clears buffer without composition mutation.
  LOGGING: INFO record start (origin tick, destination, count-in bars); INFO commit result; WARN disconnect-during-record; ERROR commit validation failure.
  Files: `musicStore.js` actions; tests with fake clock + injected MIDI messages.

### Phase 3: Transport UX + visuals

- [x] Task 7: Metronome + count-in (Tone.js ephemeral)
  Deliverable: Click scheduler integrated with playback engine or a small sibling helper using composition tempo; count-in bars before capture origin; metronome toggle during recording. Must work without MIDI hardware. Ensure clicks are cleared on Stop/unmount and never persist into V2 / export.
  LOGGING: INFO metronome schedule (bpm, count-in bars); DEBUG click count; WARN audio context blocked.
  Files: `frontend/src/utils/midiMetronome.js` (new) and/or hooks in `tonePlaybackEngine.js` / `PlaybackControls.jsx`; tests with fake Tone.

- [x] Task 8: Optional post-record quantize
  Deliverable: When `midiQuantizeAfterRecord` is on, after successful take commit run `quantizeNotes` on take refs only (snap default matching piano-roll snap or dedicated control). Preserve ability to leave raw (toggle off). Strength/snap documented.
  LOGGING: INFO quantize applied (ref count, snap, strength) or skipped; WARN quantize rejection codes.
  Files: store action + reuse `compositionEditorOperations.js`; tests.

- [x] Task 9: Active-note visuals + MIDI panel UI
  Deliverable: `MidiInputPanel.jsx` in sticky transport area (`ComposerWorkspace` / beside `PlaybackControls`): Enable MIDI, device select, destination track, Arm/Record/Stop, count-in, metronome, quantize-after, test-keyboard toggle, status/errors. Piano-roll pitch-lane highlight for `midiActiveNotes` (lightweight CSS/class on existing lane chrome — avoid remounting note layer).
  LOGGING: UI actions at DEBUG/INFO via store; no console dumps of takes.
  Files: `frontend/src/components/MidiInputPanel.jsx`, `ComposerWorkspace.jsx` / `PlaybackControls.jsx`, `PianoRollEditor.jsx` or pitch gutter child; styled-components consistent with workspace.

### Phase 4: Tests, docs, acceptance

- [x] Task 10: Frontend test suite for mocked MIDI
  Deliverable: Unit tests covering: support probe; message parse; device disconnect; capture note/pedal; timeline extend; commit + undo; quantize-after; keyboard mode; no access call on store init. Prefer dependency-injected access for headless CI.
  LOGGING: N/A (tests assert logger not required); ensure production paths still log.
  Files: `*.test.js` colocated under `frontend/src/utils/` and `frontend/src/store/`.

- [x] Task 11: Acceptance path smoke (manual/Playwright optional)
  Deliverable: Documented manual checklist: enable → select/test-keyboard → record melody → stop → edit on piano roll → Develop/continue or motif apply → save/export. Optional Playwright journey using computer-keyboard mode only (no real Web MIDI in CI). Do not fail CI if Web MIDI API absent.
  LOGGING: E2E uses existing quiet logging.
  Files: optional `frontend/e2e/midi-live-input.spec.js`; checklist in docs.

- [x] Task 12: Documentation + roadmap + AGENTS touchpoints
  Deliverable: New `docs/midi-live-input.md` (support matrix, permissions, record flow, raw vs quantize, sustain, deferred loop overdub, test keyboard, privacy: no SysEx). Link from README, `docs/composition-editor.md`, `docs/browser-playback.md`, `docs/testing.md`. Add ROADMAP unchecked milestone. Brief AGENTS.md entry for MIDI live input utils/panel. Update DESCRIPTION core features one bullet.
  LOGGING: N/A for docs; note log namespaces in testing/logging section.
  Files: `docs/midi-live-input.md`, README, editor/playback/testing docs, `.ai-factory/ROADMAP.md`, `AGENTS.md`, `.ai-factory/DESCRIPTION.md`.

## Manual verification (implementer)

1. Cold start with no MIDI device — app loads; generator/editor/playback work.
2. Chrome + MIDI keyboard: Enable → select device → set destination track → count-in 1 → metronome on → record → stop → notes appear with velocities → optional quantize → undo removes take.
3. Sustain pedal down/up produces `sustain_pedals` span; playback reflects sustain projection.
4. Unplug device mid-note: UI warns; partial take can commit or discard; no stuck active highlights.
5. Computer-keyboard mode records without hardware.
6. Develop tab can continue from recorded melody; Motifs can select recorded notes; Save/Export MIDI/MusicXML succeed.
7. Confirm loop overdub is not offered (or clearly disabled with “coming later”).
