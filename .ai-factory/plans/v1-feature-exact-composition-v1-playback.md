# Implementation Plan: Exact Composition V1 Multi-Track Playback

Branch: feature/exact-composition-v1-playback
Created: 2026-09-06

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Scope
Replace the current simplified browser playback path with a Tone.js playback engine that schedules canonical `composition.v1` `tracks[].events[]` note events exactly, so audible playback matches MIDI/MusicXML export pitch, timing, duration, velocity, and track structure.

Composition V1 playback must not generate substitute notes or chords from `harmony`. Unsupported instruments should play through an explicit fallback synth configuration with visible/logged diagnostics rather than silently changing musical content.

## Acceptance Criteria
- A known Composition V1 fixture plays the same note tuples represented by MIDI/MusicXML exports: track identity, pitch, start tick/time, duration, velocity, and polyphonic simultaneity.
- Multiple simultaneous tracks and overlapping/polyphonic notes are scheduled without flattening into substitute chords.
- Play, pause/resume, stop, and seek-to-start are available and preserve browser user-gesture AudioContext startup requirements.
- Current playback position/bar updates while playing and resets correctly on stop/seek-to-start.
- Per-track mute, solo, and volume affect playback routing without mutating the canonical composition.
- Repeated play/stop/edit/play cycles do not leak Tone.js nodes or duplicate scheduled events.
- Playback resynchronizes from the current edited composition after edits.
- Frontend deterministic scheduling tests pass, and docs describe the playback/export parity contract.

## Commit Plan
- **Commit 1** (after tasks 1-3): "feat: model composition playback schedule"
- **Commit 2** (after tasks 4-6): "feat: add multi-track tone playback engine"
- **Commit 3** (after tasks 7-9): "feat: add playback controls tests and docs"

## Tasks

### Phase 1: Canonical Playback Model
- [x] Task 1: Expand pure playback event modeling in `frontend/src/utils/playbackEvents.js` so canonical Composition V1 playback events include stable `trackId`, `trackName`, `instrument`, `role`, `midiProgram`, `channel`, `isDrum`, `trackVolume`, `pan`, `pitch`, `startTick`, `durationTicks`, `velocityMidi`, normalized Tone velocity, second-based `position`, `duration`, and `stopPosition`. Preserve current tick-to-second math from `tempo` and `ticks_per_quarter`, preserve simultaneous events as separate events, and add deterministic sorting by `position`, `trackId`, and original event order. Logging requirements: `DEBUG` timing summary with tempo, TPQ, track/event counts; `WARN` skipped invalid note events with track ID and sanitized timing/velocity fields; `DEBUG` empty tracks.

- [x] Task 2: Add pure utilities in `frontend/src/utils/playbackTracks.js` for per-track runtime state derived from canonical track metadata: default volume from Composition V1 `volume` (`0..127` to gain scalar), mute/solo flags, effective audible state, and unsupported-instrument fallback selection. Map common instruments/roles/programs to Tone-compatible synth options for piano/keyboard, bass, strings/pad, guitar-like plucked/synth fallback, lead/synth, and drums/percussion fallback. Do not load external samples unless assets are explicitly added. Logging requirements: `DEBUG` selected instrument strategy per track; `WARN` unsupported instrument/program fallback with track ID, instrument, role, program, and fallback strategy.

- [x] Task 3: Remove or isolate non-canonical substitute-note generation from `frontend/src/components/PlaybackControls.jsx`. For `schema_version: "composition.v1"`, only use `buildCanonicalPlaybackEvents()` output from track events and never inspect `harmony` or synthesize chord tones. If legacy playback remains for non-canonical payloads, gate it behind clearly named legacy-only helpers and ensure canonical validation errors prevent playback. Logging requirements: `INFO` canonical vs legacy playback path; `WARN` rejected harmony-only or unsupported playback JSON; no raw full composition payloads in logs.

### Phase 2: Tone.js Engine And Lifecycle
- [x] Task 4: Create a thin playback engine module, for example `frontend/src/utils/tonePlaybackEngine.js`, that owns Tone.js scheduling and exposes `createPlaybackEngine({ Tone, logger })` or equivalent dependency injection for deterministic tests. It should create one instrument/output chain per track, schedule every canonical event on `Tone.Transport`, support polyphony per track, apply per-track volume/mute/solo at routing time, and route unsupported instruments through the explicit fallback from Task 2. Logging requirements: `DEBUG` node creation/disposal counts, scheduled event IDs/counts, track route summaries, and transport positions; `INFO` play/pause/resume/stop transitions; `ERROR` scheduling/start failures with sanitized context.

- [x] Task 5: Implement lifecycle safety in the engine and `PlaybackControls.jsx`: keep `Tone.start()` only inside user-triggered play/resume handlers; call `Transport.cancel()` and dispose prior track nodes before rebuilding schedules; store and clear scheduled event IDs if Tone returns them; reset `Transport.position` on stop/seek-to-start; dispose all synth/gain/pan nodes on unmount and composition changes. Logging requirements: `DEBUG` cleanup before/after with event ID count and disposed node count; `WARN` cleanup anomalies such as missing node handles; `ERROR` dispose failures without throwing away UI recovery.

- [x] Task 6: Add edit synchronization so playback is rebuilt from the latest `editedMusicJson` whenever play starts after an edit, and any active playback is stopped or rescheduled deterministically when the composition changes. Use a stable composition revision key or JSON reference tracking in `PlaybackControls.jsx`/store as needed, without mutating Composition V1. Logging requirements: `DEBUG` detected composition revision changes with previous/current event counts; `INFO` active playback stopped because edited composition changed; `WARN` invalid edited JSON prevents playback.

### Phase 3: UI Controls And Playback State
- [x] Task 7: Extend `frontend/src/store/musicStore.js` and `frontend/src/components/PlaybackControls.jsx` for playback UI state: `idle`, `loading`, `playing`, `paused`, `error`; controls for play, pause/resume, stop, and seek-to-start; current playback seconds and current bar display calculated from `time_signature`, `ticks_per_quarter`, `tempo`, and transport seconds/ticks. Keep controls responsive on mobile. Logging requirements: `DEBUG` UI state transitions and position updates throttled to avoid console spam; `INFO` user playback actions; `WARN` impossible actions ignored, such as pause while idle.

- [x] Task 8: Add per-track controls in `PlaybackControls.jsx` or a small child component under `frontend/src/components/` for mute, solo, and volume. Controls should be generated from canonical `tracks` and apply to engine track routing without editing `editedMusicJson`. Solo behavior should mute all non-solo tracks when at least one track is soloed; mute should silence the muted track unless solo semantics intentionally override it. Logging requirements: `DEBUG` effective track state after mute/solo/volume changes; `INFO` user track control changes; `WARN` controls hidden/disabled for non-canonical or invalid compositions.

### Phase 4: Tests And Documentation
- [x] Task 9: Add deterministic frontend tests using Node's built-in test runner. Extend `frontend/src/utils/playbackEvents.test.js` and add focused tests for new pure utilities such as `playbackTracks.test.js` and, if dependency-injected, `tonePlaybackEngine.test.js`. Cover fixture parity fields, multi-track ordering, polyphony/overlap, velocity normalization, track volume/mute/solo effective state, unsupported-instrument fallback, no harmony-derived events for canonical compositions, repeated schedule cleanup with fake Tone transport, and seek-to-start reset behavior. Logging requirements: test fakes should assert important lifecycle calls without requiring browser Web Audio; avoid noisy console output unless testing warning paths.

- [x] Task 10: Update documentation in `docs/composition-v1.md`, `docs/testing.md`, and `README.md` if command or manual QA instructions change. Document that Composition V1 playback, MIDI, and MusicXML share `tracks[].events[]`; `harmony` is metadata only; playback supports track controls; unsupported instruments use audible fallback synths; and manual smoke checks should compare a known fixture across playback/export. Logging requirements: docs must mention browser devtools playback diagnostics and the expected sanitized log fields.

## Implementation Notes
- Current `frontend/src/components/PlaybackControls.jsx` creates a single `Tone.PolySynth(Tone.Synth)` and schedules all events through it. The implementation should replace this with per-track routing while keeping Tone.js startup behind a user gesture.
- Current `frontend/src/utils/playbackEvents.js` already performs canonical tick-to-second conversion. Preserve that behavior and enrich the event model rather than replacing it with bar/beat notation.
- Current frontend tests are plain `node --test src/utils/*.test.js`; avoid adding JSX/Tone browser component tests unless the project intentionally adopts a browser test runner.
- The best backend parity fixture is currently inline in `backend/tests/test_export_fidelity.py`. Mirror the relevant Composition V1 object in frontend tests or extract a shared JSON fixture only if that reduces duplication without complicating test setup.
- Tone.js scheduling should use absolute seconds from canonical ticks. Do not schedule canonical Composition V1 via generated transport bar notation or harmony symbols.

## Verification Commands
- `cd frontend && npm test`
- `cd frontend && npm run build`
- `cd backend && ../.venv/bin/python -m pytest tests/test_export_fidelity.py tests/test_composition_midi.py tests/test_music_json_renderer.py`
