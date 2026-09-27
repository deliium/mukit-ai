# Implementation Plan: One-Click Server-Side WAV Rendering for Composition V1

Branch: feature/server-side-wav-rendering
Created: 2026-09-06

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Commit Plan
- **Commit 1** (after tasks 1-3): `feat: add fluidsynth wav renderer service`
- **Commit 2** (after tasks 4-6): `feat: expose composition wav export`
- **Commit 3** (after tasks 7-9): `test: verify wav rendering and docker smoke`
- **Commit 4** (after tasks 12-13): `docs: document wav export renderer`

## Tasks

### Phase 1: Renderer Choice and Backend Foundation
- [x] Task 1: Add backend renderer configuration and dependency discovery for FluidSynth/SoundFont.
  - Deliverable: create a small configuration surface in `backend/app/services/composition_wav.py` or `backend/app/services/rendering_config.py` for `FLUIDSYNTH_BIN`, `COMPOSITION_WAV_SOUNDFONT`, `COMPOSITION_WAV_SAMPLE_RATE`, `COMPOSITION_WAV_GAIN`, and render timeout defaults.
  - Expected behavior: default Docker values should work without user setup, local runs should return a clear error when `fluidsynth` or the SoundFont path is missing, and configuration should be read at render time so tests can monkeypatch environment values.
  - Files: `backend/app/services/composition_wav.py`, optionally `backend/app/services/rendering_config.py`, `backend/tests/test_composition_wav.py`.
  - Dependency notes: use the FluidSynth CLI instead of a Python synthesizer binding so Docker only needs apt packages and the backend avoids native Python extension coupling. Default SoundFont candidate is Debian package `fluid-soundfont-gm`, expected at `/usr/share/sounds/sf2/FluidR3_GM.sf2`; implementation must verify the exact installed path and document attribution/license from `/usr/share/doc/fluid-soundfont-gm/copyright` or equivalent package metadata.
  - LOGGING REQUIREMENTS: DEBUG log resolved renderer config with paths, sample rate, gain, timeout, and existence checks; INFO log renderer availability during actual renders; ERROR log missing binary/SoundFont with sanitized path metadata and no composition payload bytes.

- [x] Task 2: Implement `render_wav(composition: Composition) -> bytes` by reusing canonical MIDI bytes.
  - Deliverable: add `CompositionWavError` and `render_wav` in `backend/app/services/composition_wav.py` that calls `render_midi(composition)` from `backend/app/services/composition_midi.py`, writes the MIDI to a scoped temporary directory, invokes FluidSynth with `subprocess.run(..., shell=False)`, reads the WAV bytes, and always cleans up temporary files.
  - Expected behavior: WAV note content must come from the existing Composition V1 MIDI renderer, preserving track events, tempo, tick resolution, MIDI programs, channels, volume, pan, velocity, drums channel behavior, polyphony, and section/form duration. Use a command shape equivalent to `fluidsynth -ni -F <output.wav> -r <sample_rate> -g <gain> <soundfont.sf2> <input.mid>` unless local testing proves different flags are required.
  - Files: `backend/app/services/composition_wav.py`, `backend/app/services/composition_midi.py` only if a minimal helper is needed, `backend/tests/test_composition_wav.py`.
  - Dependency notes: depends on task 1 config. Do not re-map Composition notes independently in the WAV renderer. Do not use predictable temp file names outside `tempfile.TemporaryDirectory()`.
  - LOGGING REQUIREMENTS: DEBUG log MIDI byte length before synthesis, command argv with temp paths redacted to basenames, sample rate, gain, timeout, track count, event count, and duration ticks; INFO log render start/completion with output byte length; ERROR log subprocess failures with exit code and bounded stderr/stdout snippets.

- [x] Task 3: Handle silence, duration, instruments, and failure modes explicitly in the WAV service.
  - Deliverable: add helper logic/tests in `backend/app/services/composition_wav.py` for expected duration calculation, empty-composition silence handling, optional WAV duration validation/padding when FluidSynth output is shorter than the canonical composition duration, and safe error messages for timeout, missing renderer, missing SoundFont, invalid output, or non-zero exit. Audit `INSTRUMENT_PROGRAMS` / `_midi_program_for_instrument` in `backend/app/services/composition_normalizer.py` and add any missing common General MIDI mappings or tests needed for requested track instruments.
  - Expected behavior: compositions with zero note events return a valid silent WAV matching `composition.duration_ticks` and `composition.tempo`; trailing silence after the last note is preserved or padded without adding audible notes; multiple tracks are rendered via FluidSynth General MIDI programs with existing `CompositionTrack.midi_program` values and sensible fallback to program `0` for unknown non-drum instruments; drum tracks keep existing channel/program behavior; invalid FluidSynth output never returns a partial download.
  - Files: `backend/app/services/composition_wav.py`, `backend/app/services/composition_normalizer.py`, `backend/tests/test_composition_wav.py`, `backend/tests/test_composition_normalizer.py`, optionally `backend/app/services/composition_midi.py` if MIDI end-of-track handling needs adjustment.
  - Dependency notes: maintain the browser Tone.js path as interactive preview only; no frontend playback code should call this renderer. If padding is required, use Python's standard `wave` module or a minimal safe byte-level approach after validating `RIFF/WAVE` metadata.
  - LOGGING REQUIREMENTS: DEBUG log expected duration seconds, measured WAV duration seconds, silence-path decisions, padding decisions, and per-render event counts; WARNING log tolerated duration padding; ERROR log malformed WAV headers or irrecoverable duration mismatch.

### Phase 2: Docker and Backend API Integration
- [x] Task 4: Install FluidSynth and a legally installable default SoundFont in the standard Docker backend image.
  - Deliverable: update `backend/Dockerfile` to install `fluidsynth` and `fluid-soundfont-gm` (or a better legally redistributable/installable Debian package discovered during implementation), set or document the default SoundFont path, and keep apt cache cleanup. Update `docker-compose.yml` backend environment with `COMPOSITION_WAV_SOUNDFONT=/usr/share/sounds/sf2/FluidR3_GM.sf2` if that path is verified.
  - Expected behavior: `docker compose up --build` starts a backend container that can render WAV by default with no host DAW, no host SoundFont, and no user-installed audio tools. The package must be legally redistributable/installable for this project; implementation must record package name, upstream SoundFont name, license, and attribution in docs.
  - Files: `backend/Dockerfile`, `docker-compose.yml`, optionally `backend/requirements.txt` only if a Python helper dependency is truly needed.
  - Dependency notes: avoid bundling an `.sf2` binary into the repository unless legal review and repo-size tradeoffs are explicitly accepted later. Prefer OS packages because Docker build provenance and license metadata are clearer.
  - LOGGING REQUIREMENTS: no runtime logging in Docker files, but document the env vars used by backend logs and include clear comments only where the package choice/license rationale would otherwise be non-obvious.

- [x] Task 5: Add `POST /export/wav` backend endpoint with safe attachment response and useful HTTP errors.
  - Deliverable: update `backend/app/main.py` to import `CompositionWavError`/`render_wav`, add `POST /export/wav`, return `audio/wav` bytes with safe `Content-Disposition` filename from `_safe_export_filename(composition, "wav")`, and map renderer failures to actionable HTTP errors.
  - Expected behavior: endpoint accepts canonical `Composition` body like existing `/export/midi`, renders from current edited/generated JSON, returns a downloadable `.wav`, and does not create persistent files. Missing renderer/SoundFont should return a useful `503` detail; invalid rendering or unexpected synthesis failures should return `500` with a concise user-safe message; validation failures remain FastAPI `422`.
  - Files: `backend/app/main.py`, `backend/app/services/composition_wav.py`, `backend/tests/test_export_routes.py`.
  - Dependency notes: mirror the existing MIDI endpoint shape and logging. Do not add separate download-token storage unless response streaming later becomes necessary.
  - LOGGING REQUIREMENTS: INFO log WAV export request start/completion with format, track count, event count, duration ticks, tempo, and byte length; DEBUG log filename and renderer config summary; WARNING log client-actionable missing renderer/config failures; ERROR log synthesis failures with exception type and sanitized metadata.

- [x] Task 6: Add temporary-file retention safeguards and operational limits.
  - Deliverable: ensure all render intermediates live in scoped temp directories, add render timeout defaults, bound captured subprocess output, and verify no persistent audio cache/directory is introduced. If implementation creates any optional cache later, add size/age cleanup; otherwise explicitly keep the renderer stateless.
  - Expected behavior: repeated WAV exports cannot retain unlimited MIDI/WAV files, cannot hang worker processes indefinitely, and cannot leak full raw compositions or audio bytes into logs. Concurrent exports should use isolated temporary paths.
  - Files: `backend/app/services/composition_wav.py`, `backend/tests/test_composition_wav.py`, optionally `docs/testing.md` for operational troubleshooting.
  - Dependency notes: depends on tasks 2 and 5. Use `subprocess.run(timeout=...)` and catch `TimeoutExpired`.
  - LOGGING REQUIREMENTS: DEBUG log temp lifecycle decisions and timeout value; WARNING log timeout events; ERROR log cleanup failures only if cleanup actually fails and include path basenames rather than full temp contents.

### Phase 3: Frontend Export Action
- [x] Task 7: Add frontend API support for WAV blob export.
  - Deliverable: update `frontend/src/api/musicApi.js` with `exportWav(composition)` that reuses `exportComposition` with endpoint `/export/wav`, format `wav`, fallback filename `composition.wav`, and expected content type `audio/wav` or compatible `audio/wave` handling.
  - Expected behavior: WAV export uses the current `editedMusicJson`, keeps the existing canonical Composition V1 validation gate, downloads the filename supplied by the backend, and surfaces backend blob error details from missing renderer or render failures.
  - Files: `frontend/src/api/musicApi.js`, optionally `frontend/src/api/musicApi.test.js` if export helpers are already testable under the current Node test setup.
  - Dependency notes: depends on task 5 endpoint. Do not change Tone.js playback scheduling; WAV remains an explicit export/render operation.
  - LOGGING REQUIREMENTS: console DEBUG log WAV export start/completion with format, schema version, track count, event count, blob size, content type, and filename; console ERROR log sanitized failure detail.

- [x] Task 8: Add an `Export WAV` action to the existing export controls.
  - Deliverable: update `frontend/src/components/ExportControls.jsx` to import `exportWav`, add an `Export WAV` button next to MusicXML/MIDI, and route `runExport('wav')` to the WAV API helper with clear loading/success/error states.
  - Expected behavior: the button is disabled under the same conditions as MIDI/MusicXML export, exports the latest edited Composition V1 after piano-roll or AI edits, shows `Downloaded <filename>` on success, and does not trigger browser playback or notation refresh. Existing MusicXML refresh behavior remains unchanged.
  - Files: `frontend/src/components/ExportControls.jsx`.
  - Dependency notes: depends on task 7. Keep UI copy explicit that WAV is export, not preview playback, if extra helper text is added.
  - LOGGING REQUIREMENTS: console DEBUG log WAV button clicks and export state transitions; console WARN log disabled export attempts; console ERROR log failed WAV export with format and message.

### Phase 4: Backend Tests and Docker Smoke Verification
- [x] Task 9: Add unit tests for WAV renderer command construction, temp cleanup, and error mapping.
  - Deliverable: create `backend/tests/test_composition_wav.py` with monkeypatched `render_midi`, `subprocess.run`, environment variables, and temporary paths to assert command argv construction, no shell usage, SoundFont/bin config validation, timeout handling, non-zero exit handling, invalid output handling, and scoped cleanup behavior.
  - Expected behavior: tests prove the service invokes FluidSynth with the generated MIDI file and configured SoundFont, returns bytes only after a valid `RIFF/WAVE` output exists, raises `CompositionWavError` with useful messages, and never relies on installed FluidSynth for normal unit tests.
  - Files: `backend/tests/test_composition_wav.py`, `backend/app/services/composition_wav.py`.
  - Dependency notes: use existing fixture patterns from `backend/tests/test_composition_midi.py` and `backend/tests/test_export_fidelity.py`. Normal pytest must remain fast and deterministic on hosts without FluidSynth.
  - LOGGING REQUIREMENTS: use `caplog` where useful to assert DEBUG/INFO/ERROR messages include format, duration, byte length, and failure type without raw audio or full composition JSON.

- [x] Task 10: Extend route and fidelity tests for WAV duration, silence, and multiple tracks.
  - Deliverable: update `backend/tests/test_export_routes.py` for `/export/wav` response status, media type, safe filename, `422` validation, and renderer error status mapping. Extend or add fidelity tests using `backend/tests/test_export_fidelity.py` fixtures to cover multiple tracks/instruments, trailing silence, all-silence rendering, and duration tolerance.
  - Expected behavior: route tests can monkeypatch `render_wav` for deterministic endpoint coverage; service-level tests assert duration math and WAV header metadata; no test compares exact audio samples unless the renderer smoke flag is enabled.
  - Files: `backend/tests/test_export_routes.py`, `backend/tests/test_export_fidelity.py`, `backend/tests/test_composition_wav.py`.
  - Dependency notes: depends on tasks 3 and 5. If `mido` parsing remains the canonical note-fidelity proof, state in assertions that WAV fidelity is anchored by reusing `render_midi` and smoke-tested for audio output/duration.
  - LOGGING REQUIREMENTS: route tests should assert start/completion/failure logs include `format="wav"`, event count, and duration ticks; assertion messages should identify duration/silence/multiple-track failures.

- [x] Task 11: Add an opt-in Docker renderer smoke test for installed FluidSynth and SoundFont.
  - Deliverable: add a pytest smoke test such as `backend/tests/test_wav_renderer_smoke.py` skipped unless `RUN_WAV_RENDERER_SMOKE=1`, rendering a small multi-track Composition V1 with the actual installed FluidSynth/SoundFont and asserting non-empty `RIFF/WAVE` bytes, sane duration, and no raised errors. Document a Docker command to run it.
  - Expected behavior: inside the standard backend Docker image, `RUN_WAV_RENDERER_SMOKE=1 ../.venv/bin/python -m pytest tests/test_wav_renderer_smoke.py` or the container-equivalent command passes using the default installed renderer. On local hosts without FluidSynth, the test is skipped by default with a clear reason.
  - Files: `backend/tests/test_wav_renderer_smoke.py`, `backend/tests/test_export_fidelity.py`, `docs/testing.md`.
  - Dependency notes: depends on task 4 Docker installation and task 2 renderer. Use a tiny fixture to keep the smoke test quick.
  - LOGGING REQUIREMENTS: INFO log smoke render start/completion with renderer path, SoundFont basename, byte length, and measured duration; skip reason should mention required env var and SoundFont config.

### Phase 5: Documentation and Final Verification
- [x] Task 12: Document WAV export behavior, renderer setup, SoundFont license, and troubleshooting.
  - Deliverable: update `README.md`, `docs/composition-v1.md`, and `docs/testing.md` to describe `POST /export/wav`, the frontend `Export WAV` action, Docker default renderer setup, local prerequisites, env vars, smoke-test command, and SoundFont license/attribution.
  - Expected behavior: docs clearly state that browser Tone.js playback remains the fast interactive preview, while WAV is a server-side export operation; MIDI, MusicXML, WAV, notation preview, and Tone playback all consume canonical `tracks[].events[]`; WAV specifically reuses `render_midi()` so it does not represent different notes. Include the verified license name and attribution for the chosen SoundFont package.
  - Files: `README.md`, `docs/composition-v1.md`, `docs/testing.md`.
  - Dependency notes: final docs must reflect the actual package/path discovered in task 4 and should not claim repo-bundled SoundFonts if Docker installs an OS package.
  - LOGGING REQUIREMENTS: document which backend DEBUG/INFO/WARNING/ERROR logs help diagnose missing renderer, missing SoundFont, timeout, invalid WAV, and duration padding.

- [x] Task 13: Run verification commands and perform an end-to-end manual export check.
  - Deliverable: run backend tests, frontend tests/build where available, Docker build/smoke verification, and a manual browser/API flow that generates or loads a Composition V1, edits it, clicks `Export WAV`, and confirms the downloaded audio contains the same form/notes as browser playback and MIDI export.
  - Expected behavior: all normal tests pass; opt-in Docker smoke passes in the standard Docker setup; exported WAV has a valid header, useful duration, audible multi-track notes, preserved trailing silence, and safe filename; failures return clear UI/backend errors.
  - Files: no code files unless verification finds defects; update implementation files/tests as needed if failures are found.
  - Dependency notes: depends on all prior tasks. Manual comparison can use a deterministic Composition V1 fixture and MIDI export as the note reference; do not attempt to make WAV sample output bit-for-bit deterministic across FluidSynth versions.
  - LOGGING REQUIREMENTS: capture relevant command outputs and inspect logs for `format="wav"`, renderer path/SoundFont basename, duration, byte length, and error handling without raw composition/audio payloads.

## Verification Checklist
- Run backend unit/route/fidelity tests from `backend/`: `../.venv/bin/python -m pytest tests/test_composition_wav.py tests/test_export_routes.py tests/test_export_fidelity.py`.
- Run full backend tests from `backend/`: `../.venv/bin/python -m pytest`.
- Run frontend tests from `frontend/`: `npm test`.
- Run frontend build from `frontend/`: `npm run build`.
- Rebuild standard Docker setup: `docker compose build backend` and `docker compose up`.
- Run renderer smoke in Docker with `RUN_WAV_RENDERER_SMOKE=1` using the backend service/container command documented during implementation.
- Manually generate and edit a canonical Composition V1, click `Export WAV`, and confirm the downloaded WAV follows the same notes/form as Tone.js preview and MIDI export.
- Verify all-silence, trailing-silence, and multi-track fixtures produce valid WAV files with useful duration and no leaked temp files.
- Confirm documentation includes the selected SoundFont package, exact license/attribution, installed path, and troubleshooting for missing renderer/SoundFont.
