# Implementation Plan: Deterministic Composition V1 MIDI and MusicXML Export

Branch: feature/deterministic-composition-export
Created: 2026-09-06

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Commit Plan
- **Commit 1** (after tasks 1-3): `feat: add canonical export services`
- **Commit 2** (after tasks 4-6): `feat: expose deterministic export downloads`
- **Commit 3** (after tasks 7-9): `test: verify canonical export fidelity`
- **Commit 4** (after task 10): `docs: document canonical export workflow`

## Tasks

### Phase 1: Canonical Export Foundation
- [x] Task 1: Add shared canonical-event export fixture and comparison helpers.
  - Deliverable: create backend test helpers in `backend/tests/test_export_fidelity.py` or local helper functions in updated export tests that build a deterministic `Composition` fixture with multiple tracks, polyphony, non-4/4 meter such as `6/8` or `3/4`, non-zero starts, different durations, velocities, instruments, MIDI programs, channels, volume, pan, and at least one empty/rest gap.
  - Expected behavior: helper extracts canonical note tuples `(track_id/name, pitch, start_tick, duration_ticks, velocity)` from the source composition and from generated MIDI/MusicXML where the target format supports those fields.
  - Files: `backend/tests/test_export_fidelity.py`, optionally `backend/tests/test_music_json_renderer.py`, `backend/tests/test_composition_midi.py`.
  - Dependency notes: this task defines the acceptance fixture used by later implementation tasks.
  - LOGGING REQUIREMENTS: test helpers should keep assertion failure messages explicit about track, pitch, start, duration, velocity, and source format; no production logs required beyond using existing service logs during tests.

- [x] Task 2: Refactor `music_json_renderer` so canonical `Composition` is rendered directly from `tracks[].events[]`.
  - Deliverable: update `backend/app/services/music_json_renderer.py` so `render_musicxml(composition: Composition)` no longer converts canonical data through `LLMMusicJson` and no longer derives notes/accompaniment from harmony for canonical input.
  - Expected behavior: preserve track separation, part names, instrument metadata, tempo, key, time signature, measure layout, pitch, start tick, duration tick, polyphony, staff assignment, and implicit rests. Support overlapping notes and notes with the same start as chords or separate voices where MusicXML/music21 requires it. Split notes that cross measure boundaries into tied MusicXML notes when necessary so total duration and musical position remain correct.
  - Files: `backend/app/services/music_json_renderer.py`.
  - Dependency notes: keep legacy `LLMMusicJson` fallback behavior for existing statistical/legacy paths if any caller still passes legacy objects; canonical paths must never call `_fallback_element_for_track`.
  - LOGGING REQUIREMENTS: DEBUG log canonical render start/end with schema version, ticks per quarter, tempo, key, time signature, track count, event count, and per-track event counts; DEBUG log inserted rests and split/tied cross-measure notes; WARNING log unsupported chord-symbol metadata only; ERROR log render failures with sanitized metadata and no raw prompts.

- [x] Task 3: Implement deterministic MIDI file rendering from canonical `Composition`.
  - Deliverable: extend `backend/app/services/composition_midi.py` with a function such as `render_midi(composition: Composition) -> bytes` while preserving `composition_to_midi_ready` for existing callers/tests.
  - Expected behavior: produce a valid Standard MIDI File with `ticks_per_beat = composition.ticks_per_quarter`, tempo meta event, time signature meta event, key signature meta event where representable, one MIDI track per composition track, track name metadata, program changes, channel mapping, volume/pan controller messages, note-on/note-off events sorted deterministically, velocity preservation, and polyphony support via simultaneous delta-time events. Do not synthesize notes from harmony.
  - Files: `backend/app/services/composition_midi.py`, `backend/requirements.txt` if adding `mido` or another minimal MIDI dependency.
  - Dependency notes: prefer a small pure-Python MIDI dependency such as `mido` for deterministic write/read tests; if not adding a dependency, implement only the small SMF writer needed by this project and document its limits.
  - LOGGING REQUIREMENTS: DEBUG log MIDI render start/end with tempo, meter, tick resolution, track count, event count, and output byte length; DEBUG log per-track MIDI metadata; ERROR log unsupported metadata or write failures with track id/channel/program context.

### Phase 2: Backend API and Safe Download Handling
- [x] Task 4: Add export request/response API models and dedicated backend export endpoints.
  - Deliverable: add endpoints in `backend/app/main.py`, for example `POST /export/musicxml` and `POST /export/midi`, accepting a canonical `Composition` body and returning downloadable files.
  - Expected behavior: endpoints validate `composition.v1`, render from the provided canonical JSON, return `application/vnd.recordare.musicxml+xml` or compatible MusicXML content type for MusicXML and `audio/midi` for MIDI, set safe `Content-Disposition` attachment filenames, and do not mutate existing `POST /llm/generate-music-json` behavior. Keep the generation endpoint returning inline `musicxml` for notation preview compatibility.
  - Files: `backend/app/main.py`, `backend/app/schemas.py` if request wrappers or filename fields are needed.
  - Dependency notes: endpoints should reuse `render_musicxml` and `render_midi`; do not add export logic in the route body beyond validation, logging, headers, and error mapping.
  - LOGGING REQUIREMENTS: INFO log export request start/completion by format; DEBUG log schema version, title/filename stem if present, track count, event count, byte/string length; WARNING log validation/render warnings; ERROR log failures with format and sanitized exception type.

- [x] Task 5: Replace unsafe temporary-file patterns with safe scoped handling.
  - Deliverable: update MusicXML rendering file writes in `backend/app/services/music_json_renderer.py` to use `tempfile.TemporaryDirectory()` or another scoped pattern that guarantees cleanup, avoids predictable paths, and reads output only after `music21` closes the file. MIDI should render to bytes in memory unless a library requires a file, in which case use the same scoped cleanup pattern.
  - Expected behavior: no orphaned temp files on success or failure; tests can run repeatedly without filesystem residue; downloads are served from in-memory bytes/strings or safely scoped temp files.
  - Files: `backend/app/services/music_json_renderer.py`, `backend/app/services/composition_midi.py`.
  - Dependency notes: this should be implemented before endpoint tests assert download behavior.
  - LOGGING REQUIREMENTS: DEBUG log only high-level temp handling decisions and output lengths; never log temp file contents or raw MusicXML/MIDI bytes.

- [x] Task 6: Preserve existing generator/statistical endpoint behavior while wiring canonical exports.
  - Deliverable: audit `backend/app/main.py`, `backend/app/services/llm_music_generator.py`, `backend/app/services/composition_normalizer.py`, and any model-backed/statistical generation scripts to ensure existing `POST /llm/generate-music-json` and model files remain untouched except for using the improved canonical `render_musicxml` implementation.
  - Expected behavior: current LLM/statistical generation tests still pass; legacy `LLMMusicJson` rendering remains supported where tests currently require it; harmony-only legacy generation remains rejected by canonical normalization as documented.
  - Files: `backend/app/main.py`, `backend/app/services/llm_music_generator.py`, existing route/tests if needed.
  - Dependency notes: if no statistical FastAPI endpoint currently exists, document that preservation means not deleting/changing model assets or generation interfaces.
  - LOGGING REQUIREMENTS: DEBUG log compatibility path usage when legacy objects are rendered; INFO log unchanged generation completion fields; avoid logging raw prompts or provider secrets.

### Phase 3: Frontend Export Actions
- [x] Task 7: Add frontend API helpers and safe browser download utilities for MusicXML and MIDI.
  - Deliverable: update `frontend/src/api/musicApi.js` with `exportMusicXml(composition)` and `exportMidi(composition)` helpers that post `editedMusicJson` to the backend export endpoints using `responseType: 'blob'` or arraybuffer where appropriate. Add a utility such as `frontend/src/utils/downloadFile.js` for safe Blob URL creation, click-triggered downloads, and `URL.revokeObjectURL` cleanup.
  - Expected behavior: exports use the current edited canonical JSON, validate before sending, download deterministic filenames with `.musicxml` and `.mid`, revoke object URLs in `finally`, and surface backend errors cleanly.
  - Files: `frontend/src/api/musicApi.js`, `frontend/src/utils/downloadFile.js`, optionally `frontend/src/utils/downloadFile.test.js`.
  - Dependency notes: no component test framework exists, so keep download logic in a browser-independent utility that can be unit-tested with Node where practical.
  - LOGGING REQUIREMENTS: console DEBUG log export request start/completion with format, schema version, track count, event count, and blob size; console ERROR log sanitized failure detail; do not log full composition contents.

- [x] Task 8: Add Export MIDI and Export MusicXML UI actions and keep notation preview aligned with exported MusicXML.
  - Deliverable: update `frontend/src/components/MusicGenerator.jsx` or create a small `ExportControls.jsx` component rendered near `PromptJsonEditor`, `NotationViewer`, and `PlaybackControls`.
  - Expected behavior: buttons are disabled when no valid canonical `editedMusicJson` exists or an export is in progress; `Export MusicXML` downloads a fresh render for the current edited JSON and updates `musicXml` in the store so OpenSheetMusicDisplay previews the same MusicXML being downloaded; `Export MIDI` downloads the MIDI bytes for the same edited JSON. Show concise success/error state without breaking existing generation flow.
  - Files: `frontend/src/components/MusicGenerator.jsx`, optional `frontend/src/components/ExportControls.jsx`, `frontend/src/store/musicStore.js`.
  - Dependency notes: depends on task 7 API helpers; do not remove legacy playback fallbacks unless intentionally covered by existing tests, but canonical export actions should only accept `composition.v1`.
  - LOGGING REQUIREMENTS: console DEBUG log button clicks, validation results, export state transitions, and MusicXML store updates; console WARN log disabled/invalid export attempts; console ERROR log failures.

### Phase 4: Fidelity Tests and Verification
- [x] Task 9: Add backend tests proving MIDI, MusicXML, and canonical JSON describe the same note events.
  - Deliverable: add/extend tests in `backend/tests/test_export_fidelity.py`, `backend/tests/test_music_json_renderer.py`, `backend/tests/test_composition_midi.py`, and route tests in `backend/tests/test_llm_routes.py` or a new endpoint test file.
  - Expected behavior: deterministic fixture assertions compare source canonical events to parsed MIDI events, including track separation, pitch, start tick, duration tick, velocity, tempo, time signature, MIDI programs/channels, and polyphony. MusicXML assertions parse the generated XML with `music21.converter.parseData` and compare pitch/start/duration musical positions as exactly as MusicXML/music21 quantization allows; assert OSMD-compatible MusicXML structure includes valid part/measure metadata. Endpoint tests assert content types, attachment filenames, error mapping, and that generation endpoints remain intact.
  - Files: `backend/tests/test_export_fidelity.py`, `backend/tests/test_music_json_renderer.py`, `backend/tests/test_composition_midi.py`, `backend/tests/test_llm_routes.py` or `backend/tests/test_export_routes.py`.
  - Dependency notes: depends on tasks 2-5; use the same deterministic fixture from task 1.
  - LOGGING REQUIREMENTS: use `caplog` where valuable to assert export start/completion logs include format and event counts; assertion messages should identify mismatched note tuples and format.

- [x] Task 10: Add frontend utility tests/build checks and update documentation.
  - Deliverable: add tests for API/download utilities where feasible under Node's built-in runner, update `docs/composition-v1.md` and `docs/testing.md` with canonical export behavior, routes, download safety, and verification commands.
  - Expected behavior: docs state that MIDI, MusicXML, notation preview, browser playback, and canonical JSON all consume the same `tracks[].events[]`; harmony is metadata only and never used to invent export notes. Testing docs include backend `../.venv/bin/python -m pytest`, frontend `npm test`, and frontend `npm run build` checks.
  - Files: `frontend/src/utils/*.test.js`, `docs/composition-v1.md`, `docs/testing.md`.
  - Dependency notes: final documentation should reflect actual endpoint paths, content types, and any MIDI dependency selected in task 3.
  - LOGGING REQUIREMENTS: document DEBUG/INFO logs available for export diagnosis; frontend tests can assert cleanup behavior but should not rely on noisy console output unless intentionally mocked.

## Verification Checklist
- Run backend tests from `backend/`: `../.venv/bin/python -m pytest`.
- Run frontend utility tests from `frontend/`: `npm test`.
- Run frontend build from `frontend/`: `npm run build`.
- Manually generate or load the deterministic fixture, export MIDI and MusicXML, render MusicXML through OpenSheetMusicDisplay, and confirm playback/export positions match the canonical JSON.
- Confirm no raw prompts, API keys, MusicXML payloads, or MIDI bytes are written to logs.
