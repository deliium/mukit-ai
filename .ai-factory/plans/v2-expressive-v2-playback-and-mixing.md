# Implementation Plan: Expressive V2 Playback and Mixing

Branch: main (branch creation disabled by project configuration)
Created: 2026-09-10

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Roadmap Linkage
Milestone: "Expressive V2 playback and mixing"
Rationale: Distinct browser performance and evaluation capability beyond versioning. Added to `.ai-factory/ROADMAP.md` via `$aif-roadmap` sync with `plans/`.

## Goal

Upgrade browser playback from a functional oscillator preview to a musically useful Composition V2 performance and mixing engine. A multi-track piano/bass/strings composition must play with clearly differentiated instruments, velocity and V2 expression, controllable balance and pan, mute/solo, basic ambience, smooth transport, tempo-map timing, loop playback, and useful activity indication.

Canonical `composition.v2` notes remain authoritative. Browser sound selection and mixer state remain ephemeral projections. Existing deterministic server-side MIDI, MusicXML, and FluidSynth WAV export must remain unchanged.

## Technology Decision

Use a hybrid browser engine:

- Keep Tone.js 15.1.22 for AudioContext startup, transport scheduling, gain/pan buses, shared reverb, limiting, metering, and deterministic integration with the existing seconds-based V2 schedule.
- Add a small instrument-adapter boundary and use a pinned `smplr` sampler dependency for an audited, locally served core acoustic pack where velocity regions and looped sustains materially improve piano, bass, and strings.
- Keep improved Tone.js synth presets as the guaranteed zero-asset and per-track load-failure fallback. Playback must never depend on a CDN or fail as a whole because one sampled instrument is unavailable.
- Do not use `smplr` built-in/CDN instruments. Package-code licensing and sample-content licensing are separate release gates.
- Do not adopt a SoundFont sequencer, FluidSynth/WASM, WebAudioFont, `soundfont-player`, or a neural audio model. Those options duplicate the existing V2 scheduler/mixer, add large or legacy runtime surfaces, or carry catalog/content licensing ambiguity.
- A direct `Tone.Sampler` implementation is the lower-complexity fallback if the adapter spike shows `smplr` cannot reliably connect to the existing Tone graph. This fallback must be decided before assets are integrated; it still uses the same local manifest and synth fallback contract.

The first shipped pack should be a deliberately small, size-budgeted derivative of explicitly redistributable source material, preferring CC0 VCSL recordings for piano, bass, and strings. No sample may enter the repository or Docker image until its source URL, creator, exact license, source hash, conversion history, output hash, attribution obligations, and redistribution approval are recorded beside the assets. If an instrument does not pass this gate, ship its synth preset instead of substituting an unclear library.

## Scope Boundaries

- Play only canonical `tracks[].events[]`; never derive sound from harmony, motifs, markers, analysis, or arrangement catalog metadata.
- Preserve V2 velocity, articulations, ties, dynamic marks, sustain spans, volume/pan/expression automation, and piecewise tempo timing.
- Keep preset choice, fader trim, pan offset, mute, solo, reverb send, levels, loading state, and transport state outside `editedMusicJson`, persistence, undo/redo, autosave, project history, analysis revisions, notation revisions, and export requests.
- Keep server export implementations and environment settings independent from browser assets. Browser timbre is not expected to match FluidSynth WAV timbre.
- Do not change Composition V2 schemas or add a database migration.
- Preserve legacy/V1 migration input, but make the canonical V2 engine the acceptance target; do not expand the legacy playback path with a second mixer architecture.

## Commit Plan
- **Commit 1** (after tasks 1-3): `fix(playback): establish reliable V2 scheduling and transport`
- **Commit 2** (after tasks 4-6): `feat(playback): add expressive instruments and mixer engine`
- **Commit 3** (after tasks 7-9): `feat(frontend): deliver playback mixer and acceptance coverage`

## Tasks

### Phase 1: Playback Contracts and Correctness

- [x] **Task 1: Define source identity, mixer semantics, and browser asset contracts.**
  - Add stable `sourceKind`, `sourceId`, `sourceKey`, and mixer-scope output to `frontend/src/utils/playbackSource.js`; wire every existing audition source, including AI edit, motif, reharmonization, generation, development, arrangement, and version playback.
  - Define session controls as neutral browser overrides, for example `trimDb: 0`, `panOffset: 0`, `muted: false`, `solo: false`, and bounded `reverbSend`, while canonical `track.volume`, `track.pan`, `track.expression`, and automation retain their current authority. Remove the current default behavior that effectively squares canonical volume.
  - Add `frontend/src/utils/browserPlaybackAssets.js` with a versioned, allowlisted manifest schema, deterministic instrument/GM-program/role mapping, local same-origin URL resolution, digest/size/format limits, cache keys, fallback preset IDs, and provenance/license metadata validation. Do not permit arbitrary composition-provided URLs.
  - Add the instrument-adapter interface used by sampled and synthesized voices: asynchronous prepare, note-ID-aware attack/release, release-all, output connection, readiness/fallback status, and disposal.
  - Files: `frontend/src/utils/playbackSource.js`, `frontend/src/utils/playbackTracks.js`, new `frontend/src/utils/browserPlaybackAssets.js`, new `frontend/src/utils/playbackInstrumentPresets.js`, and corresponding unit tests.
  - Logging: use namespaced `VITE_LOG_LEVEL` logging; DEBUG only bounded source/preset IDs and mapping reasons, WARN unsupported mappings/invalid manifests, ERROR validation failures. Never log compositions, event arrays, sample bytes, full remote URLs with query strings, or license-file contents.
  - Dependencies: none.

- [x] **Task 2: Correct V2 performance scheduling before changing timbre.**
  - Give every logical note and attack/release item a stable identity so overlapping same-pitch notes release independently.
  - Fix sustain projection so a nominal note-off occurring during a pedal span is deferred to pedal-up, including notes attacked before pedal-down and exact half-open boundary cases.
  - Retain articulation gate/velocity transforms, tie collapse, dynamic/expression combination, release-before-attack ordering, harmony exclusion, and piecewise tempo conversion.
  - Represent linear controller automation as bounded segments suitable for native parameter ramps, or explicitly retain bounded sampling if Tone parameter behavior cannot preserve deterministic seek reconstruction; align tests and docs with the selected behavior.
  - Expose logical-note intervals and controller state-at-time so play-from-cursor, seek, resume, and loop wrap can reconstruct sounding notes and active controls.
  - Files: `frontend/src/utils/playbackEvents.js`, `frontend/src/utils/playbackEvents.test.js`, and only if shared timing helpers need correction, `frontend/src/utils/compositionTimeline.js` and its tests.
  - Logging: keep compilation pure where practical and return a bounded summary; DEBUG schedule counts/duration/tempo-segment count at the orchestration boundary, WARN malformed optional expression data, never log pitches or events in bulk.
  - Dependencies: task 1 for note/instrument adapter contracts.

- [x] **Task 3: Make transport ownership and relocation smooth and race-safe.**
  - Replace global `Tone.Transport.cancel()` cleanup with clearing only IDs owned by this engine; ensure legacy cleanup cannot cancel canonical events or future metronome/UI events.
  - Implement one relocation path for initial start tick, seek, pause/resume, loop wrap, and live loop changes: short click-free fade, release active voices, clear owned callbacks, restore controller state, reconstruct notes crossing the target, schedule remaining events, and fade in when playing.
  - Preserve the existing absolute-seconds tempo-map model rather than maintaining a second Tone tempo map. Store the authoritative playback tick/bar/tempo calculated against the actual auditioned composition so preview/version cursors are not reconverted through the working composition.
  - Add operation/session tokens so stale `Tone.start()`, asset preparation, completion callbacks, and source changes cannot start or stop the wrong session. Physically stop the engine whenever source identity or audible revision changes, regardless of the Zustand status string.
  - Normalize loop bounds against the active playback source and preserve half-open loop semantics. Define seek outside an enabled loop to relocate to loop start.
  - Files: `frontend/src/utils/tonePlaybackEngine.js`, `frontend/src/utils/playbackPosition.js`, `frontend/src/utils/playbackLoop.js`, `frontend/src/components/PlaybackControls.jsx`, `frontend/src/components/PianoRollEditor.jsx`, `frontend/src/components/piano-roll/PianoRollOverlayLayer.jsx`, and their tests.
  - Logging: INFO start/pause/resume/stop/source-change and fallback outcomes; DEBUG bounded relocation reason, target tick, owned event counts, and operation ID; WARN invalid loops/stale operations; ERROR startup, scheduling, or cleanup failures. Do not log position polling or individual notes.
  - Dependencies: tasks 1 and 2.

### Phase 2: Instruments and Mixer Engine

- [x] **Task 4: Integrate audited local sample instruments with deterministic synth fallback.**
  - Spike the pinned `smplr` adapter against Tone's AudioContext and track output bus. If it cannot satisfy scheduling, polyphony, connection, cleanup, and testability without parallel transport logic, record the code-level decision and implement the same adapter with `Tone.Sampler` instead.
  - Curate and encode a compact local piano/bass/strings core pack under `frontend/public/audio/<pack-id>/`; use sparse pitch coverage, practical browser codecs, velocity regions where supported, and validated loop points for sustained strings. Enforce the size/memory budgets defined by task 1.
  - Lazy-load only profiles needed by the active composition, deduplicate concurrent loads and decoded buffers, wait for readiness before transport starts, and atomically choose a synth fallback for each failed track before scheduling. Never swap timbre during active playback.
  - Expand deterministic Tone presets for piano/electric keys, bass, strings/pad, guitar/pluck, brass, woodwind/lead, mallet, and basic GM drum groups so offline/no-pack operation remains differentiated and polyphonic where canonical tracks require it.
  - Add/pin dependencies only after code-license and bundle checks; update `frontend/package.json` and `frontend/package-lock.json`. Keep audio assets outside the JavaScript bundle and same-origin by default.
  - Files: `frontend/package.json`, `frontend/package-lock.json`, `frontend/src/utils/browserPlaybackAssets.js`, `frontend/src/utils/playbackInstrumentPresets.js`, `frontend/src/utils/tonePlaybackEngine.js`, `frontend/public/audio/<pack-id>/**`, and focused tests.
  - Logging: INFO one bounded load/fallback result per profile, DEBUG cache/load timing and byte counts, WARN per-track fallback with stable reason code, ERROR unrecoverable adapter initialization. Never log sample data, signed URLs, manifests wholesale, or repeated per-note messages.
  - Dependencies: tasks 1-3; create the required provenance/license records with each asset, and treat task 9's final audit as release-blocking.

- [x] **Task 5: Build the track mixer, shared ambience, protection, and metering graph.**
  - Refactor each route to separate canonical expression/volume/pan from session trim/pan/mute/solo and remove double application of canonical volume.
  - Use post-fader sends into one shared `Tone.Reverb` ambience bus and route dry/wet output through a conservative master gain/limiter before destination. Await reverb readiness and clear wet tails on full stop/dispose.
  - Apply live volume trim, pan offset, mute, solo, and send changes without rescheduling notes. Smooth parameter changes over short ramps to avoid clicks and ensure mute/solo also silence new wet input.
  - Add one lightweight track meter after audible gain/pan and expose a bounded activity snapshot. Poll at no more than 10-20 Hz, avoid Zustand updates when values have not materially changed, and disable/limit meters when the engine's track/CPU budget is exceeded.
  - Calibrate velocity response and per-profile output gain so piano, bass, and strings are distinguishable and balanced without clipping; canonical note velocity and V2 dynamics remain the input, not rewritten values.
  - Files: `frontend/src/utils/tonePlaybackEngine.js`, `frontend/src/utils/playbackTracks.js`, `frontend/src/utils/playbackInstrumentPresets.js`, and unit tests.
  - Logging: DEBUG graph/profile/route counts and bounded peak/clipping diagnostics, INFO engine readiness, WARN meter/effect degradation or limiter intervention summaries, ERROR route construction/disposal failures. Never log periodic meter frames.
  - Dependencies: task 4.

- [x] **Task 6: Centralize ephemeral playback/mixer state and safe logging.**
  - Update Zustand playback state/actions for authoritative source key/tick, operation epoch, per-source mixer isolation, neutral control defaults, pan/send/preset status, and activity reset.
  - Preserve working, candidate, and version mixer isolation without duplicating action implementations. Reconcile controls by active source/track IDs without pruning working controls when auditioning a candidate.
  - Ensure mixer and browser profile changes do not mutate composition JSON, composition/notation/analysis revisions, save status, persistence fingerprints, autosave, history snapshots, or composition undo/redo.
  - Consolidate playback logging through `frontend/src/utils/appLogger.js`, add bounded metadata sanitization/redaction, remove direct playback `console.*`, throttle slider diagnostics, and use stable reason codes for asset and engine fallbacks.
  - Files: `frontend/src/store/musicStore.js`, `frontend/src/utils/appLogger.js`, `frontend/src/components/PlaybackControls.jsx`, plus store/logger/source tests.
  - Logging: INFO only major state transitions, DEBUG committed mixer changes and bounded source IDs, WARN invalid controls/reconciliation fallback, ERROR failed transitions; never INFO-log every slider input or log full store/composition state.
  - Dependencies: tasks 1, 3, and 5.

### Phase 3: Mixer UI, Acceptance, and Documentation

- [x] **Task 7: Deliver an accessible, responsive mixer UI.**
  - Expand `TrackPlaybackControls` into compact rows showing track name, canonical instrument, resolved sampled/synth profile and load/fallback status, volume trim, pan, mute, solo, reverb send, and a basic level/activity bar.
  - Keep automatic instrument-aware preset selection; show the resolved profile rather than rewriting canonical instrument/program fields. A manual browser preset selector is optional only if it can remain clearly session-only and source-isolated.
  - Add proper labels, values/units, `aria-pressed`, keyboard-operable 44px targets, non-color status text, stable test IDs, and reduced-motion-safe level animation.
  - Make the sticky transport mixer collapsible or internally bounded so it works at 390x844 without horizontal overflow or intercepting lower workspace controls.
  - Keep transport loading/error/fallback feedback actionable while allowing per-track sample failure to continue with synth fallback.
  - Files: `frontend/src/components/TrackPlaybackControls.jsx`, `frontend/src/components/PlaybackControls.jsx`, and if needed `frontend/src/components/ComposerWorkspace.jsx`.
  - Logging: DEBUG only committed UI control changes with track/source IDs; WARN rejected values; no render, animation-frame, raw pointer, or meter logging.
  - Dependencies: tasks 5 and 6.

- [x] **Task 8: Add deterministic unit, integration, browser, and export-regression coverage.**
  - Extend pure tests for source identity/priority, asset manifests and allowlisting, instrument/GM mappings, control normalization, synth fallback, note identity, sustain boundaries, dynamics/articulations, velocity extremes, tempo changes, linear automation, and loop normalization.
  - Extend the fake Tone environment and engine tests for asynchronous sample/reverb readiness, one-track failure fallback, shared effect count, graph gains, velocity delivery, overlapping same-pitch voices, owned Transport IDs, click-free relocation, held-note reconstruction, pause/resume, seek, loop wrap/change, live mixer controls, meters, stale async cancellation, disposal, and cache reuse.
  - Add store regressions proving source mixer isolation and zero composition/persistence/history/analysis mutation.
  - Add `frontend/e2e/playback-mixer.spec.js` using a normalized three-track expressive V2 fixture and deterministic local/test assets. Cover play/loading/fallback, volume/pan/mute/solo/send, activity/reset, tempo-map position, pause/resume, play-from-cursor, looping, source switch while playing, save/reopen non-persistence, export request purity, keyboard/ARIA behavior, and mobile layout.
  - Do not use cross-browser waveform snapshots as correctness tests. Add a bounded manual listening gate for piano/bass/strings differentiation, dynamics, ambience, balance, clicks, stuck notes, and repeated transport use; automate onset/RMS/activity checks only where stable.
  - Run frontend `npm test`, `npm run lint`, `npm run build`, focused Playwright and relevant existing journeys; run backend schema/MIDI/MusicXML/WAV/export-fidelity suites and the opt-in FluidSynth smoke where available.
  - Files: new and existing tests under `frontend/src/utils/`, `frontend/src/store/`, `frontend/e2e/`, and existing backend tests only if a no-regression assertion is missing; do not alter export behavior to satisfy tests.
  - Logging: tests assert bounded lifecycle/fallback reason codes, absence of event/composition dumps, and no noisy per-note/meter logs. Capture browser console failures without retaining asset payloads.
  - Dependencies: tasks 1-7.

- [x] **Task 9: Complete asset licensing, operator, fidelity, and architecture documentation.**
  - Add `docs/browser-playback.md` describing the browser projection boundary, selected technology, instrument mapping/fallback, mixer semantics, V2 expression handling, tempo/transport/loop behavior, performance budgets, troubleshooting, and browser-versus-WAV timbre expectations.
  - Place exact upstream license text and attribution/conversion records beside each pack and add `THIRD_PARTY_NOTICES.md` entries. Document package-code licenses separately from audio-content licenses. Require hashes and release approval for every shipped binary; omit any unverified sample.
  - Update `README.md`, `.env.example` only if an explicit build-time asset-base option is implemented, `docs/composition-v2.md`, `docs/composition-editor.md`, `docs/testing.md`, `docs/CODEBASE_MAP.md`, and `AGENTS.md`. Do not conflate browser assets with the server FluidR3/FluidSynth SoundFont.
  - Document that Vite `VITE_*` values are build-time in production and that local same-origin assets are the default. If configurable remote assets are retained, document HTTPS/CORS, origin allowlisting, immutable manifests, integrity, and fallback behavior.
  - Flag the pre-existing root `LICENSE` (CC0) versus README (MIT) inconsistency for owner resolution; do not silently change the project license in this feature.
  - Record verification commands and the manual musical acceptance checklist. Use `$aif-docs` for the mandatory docs checkpoint.
  - Logging: document `VITE_LOG_LEVEL`, lifecycle/reason-code fields, redaction and bounded metadata policy, and prohibited payloads. No asset license/catalog contents should be emitted to runtime logs.
  - Dependencies: tasks 4 and 8; documentation and asset notices are release-blocking.

## Verification Gates

Run from `frontend/`:

```bash
npm ci
npm test
npm run lint
npm run build
npm run test:e2e -- e2e/playback-mixer.spec.js
```

Run relevant existing Playwright playback, editor, arrangement, development, import, and version-history journeys against `LLM_FAKE_MODE=1` before the full `npm run test:e2e` gate.

Run from `backend/`:

```bash
../.venv/bin/python -m pytest \
  tests/test_composition_v2_schema.py \
  tests/test_composition_midi.py \
  tests/test_music_json_renderer.py \
  tests/test_composition_wav.py \
  tests/test_export_fidelity.py \
  tests/test_export_routes.py
```

Run the full backend suite and opt-in real FluidSynth smoke when the environment provides its dependencies. Identical canonical input must continue producing the same deterministic MIDI/MusicXML behavior and the same WAV rendering path regardless of browser mixer state.

## Acceptance Checklist

- A normalized V2 piano/bass/strings fixture plays all tracks with clearly differentiated, balanced sounds; local samples are used when licensed assets load and deterministic synth profiles are used otherwise.
- Velocity, accent/marcato gates, staccato/tenuto behavior, dynamic marks, expression automation, sustain, ties, polyphony, and tempo changes are audible and covered by deterministic schedule/engine tests.
- Volume, pan, mute, solo, and ambience changes apply live, do not click or leave wet/stuck notes, and never mutate or persist canonical composition data.
- Activity indicators respond while sounding and reset on mute, stop, source change, and disposal without causing excessive rendering or logging.
- Pause/resume, seek, play-from-cursor, repeated play/stop, source changes, and half-open loops remain synchronized and reconstruct held notes without stale callbacks.
- Mixer UI remains usable and non-obstructive on desktop and 390x844 mobile, with keyboard and screen-reader semantics.
- Sample assets have explicit redistribution records and notices; no CDN or legally unclear library is required for normal playback.
- Server MIDI, MusicXML, and WAV export paths and fidelity tests continue to pass unchanged.

## Risks and Mitigations

- **Audio asset size and memory:** enforce pack/file/decoded-memory/concurrency budgets, lazy loading, sparse pitch maps, cache reuse, and synth fallback.
- **Sampler/Tone interoperability:** complete the adapter spike before asset conversion; fall back to `Tone.Sampler` without changing schedule, manifest, UI, or canonical contracts.
- **Browser codec differences:** select broadly supported formats, feature-detect decoding, and test supported Chromium plus targeted Safari/Firefox smoke where available.
- **Perceived-quality subjectivity:** combine deterministic routing/expression tests with a short, fixed listening rubric on the piano/bass/strings fixture.
- **Changed loudness after fixing double gain:** document neutral trim semantics, calibrate profile output, use conservative master limiting, and add total-route gain tests.
- **Transport reconstruction limitations:** retrigger held notes with a short fade rather than pretending to restore an exact sample/envelope phase; test for timing, no clicks, and no stuck voices.
- **Global Tone transport coupling:** clear only engine-owned IDs and preserve a single active playback owner with operation tokens.
- **Licensing ambiguity:** treat asset notices and provenance as release-blocking; ship the corresponding synth profile when approval is absent.
