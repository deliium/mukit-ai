[← Project Persistence](project-persistence.md) · [Back to README](../README.md) · [Codebase Map →](CODEBASE_MAP.md)

# Testing

## Backend

Run backend unit tests from the `backend/` directory:

```bash
../.venv/bin/python -m pytest
```

The LLM tests mock provider behavior and do not call OpenAI or DeepSeek APIs.
Set `LLM_FAKE_MODE=1` for a deterministic in-process fake provider (fixtures under
`backend/app/fixtures/` / `backend/tests/fixtures/`) used by unit tests, Docker
acceptance, and Playwright. Fake generation prefers native V2 `composition_v2_expressive.json`
when duration matches; otherwise V1 fixtures migrate to V2.

Focused canonical coverage includes:

- Set `LOG_LEVEL=DEBUG` to inspect stage/provider/model/attempt, constraint summaries, and diagnostic codes. Full prompts, API keys, and raw composition/export payloads must never appear in logs.
- `backend/tests/test_llm_fake_provider.py` for fake generate/edit, fixture constraint gating (aliases, missing requirements, instrumentation report), `/llm/models`, malformed `502` without clobbering projects, and unsupported-instrument fixture export.
- `backend/tests/test_instrument_identity.py` for sound-source normalization, requirement satisfaction, and duplicate-content classification.
- `backend/tests/test_secret_hygiene.py` for `/ready`, `/llm/models`, project CRUD, and committed config secret leakage checks.
- `backend/tests/test_docker_persistence_acceptance.py` opt-in Compose restart persistence (`RUN_DOCKER_ACCEPTANCE=1`).
- `backend/tests/test_composition_schema.py` for `composition.v1` validation, 4/4, 3/4, 6/8 timing, invalid pitches, velocities, durations, duplicate tracks, and section boundaries.
- `backend/tests/test_composition_v2_schema.py` for strict V2 invariants: mixed meter, malformed ties, conflicting articulations, automation/pedal overlap, unknown fields/versions.
- `backend/tests/test_composition_v2_migration.py` for V1→V2 note-sequence equality, source immutability, idempotence, and `v1_v2_migration_fidelity_failed`.
- `backend/tests/test_composition_timing.py` and `frontend/src/utils/compositionTimeline.test.js` for variable tempo/meter bar boundaries and tick/seconds parity.
- `backend/tests/test_composition_normalizer.py` for legacy migration, velocity defaults, canonical pass-through, and harmony-only rejection.
- `backend/tests/test_composition_midi.py` for MIDI-ready timing, channel, program, velocity preservation, and Standard MIDI File rendering.
- `backend/tests/test_composition_wav.py` for mocked FluidSynth argv construction (`shell=False`), config/env discovery, silence path, timeout/non-zero/invalid output errors, duration padding, and temp cleanup.
- `backend/tests/test_export_fidelity.py` for deterministic fixture comparisons across canonical JSON, MIDI bytes, MusicXML note timing, WAV duration/silence, and V2 projection issue codes.
- `backend/tests/test_export_routes.py` for `/export/musicxml`, `/export/musicxml/preview`, `/export/midi`, and `/export/wav` content types, attachments vs preview (no download header), `503`/`500` WAV mapping, validation errors, and `X-Mukit-Projection-*` headers.
- `backend/tests/test_wav_renderer_smoke.py` opt-in real FluidSynth smoke (`RUN_WAV_RENDERER_SMOKE=1`).
- `backend/tests/test_music_json_renderer.py` for canonical MusicXML rendering from events without harmony fallback, plus V2 tempo/meter/key changes, ties, articulations, dynamics, pedal, markers, and automation omission reports.
- `backend/tests/test_composition_validator.py` for integrity diagnostics such as missing roles, empty/sparse tracks, pitch/range issues, harmony-only rejection, and generation-constraint instrumentation/duplicate diagnostics (requested-instrument ownership lives here, not in integrity matching).
- `backend/tests/test_composition_tonality.py` for tonal-center scoring, F#-minor chromatic pass cases, and A-minor contradiction detection.
- `backend/tests/test_llm_staged_composer.py` for mocked multi-stage sequencing, repair/retry (including duplicate bass/bass accompaniment regression), oversized request rejection, provider/model override, F#-minor constraint repair/exhaustion, and actionable API errors.
- `backend/tests/test_composition_region_patch.py` for deterministic bar-to-tick selection, melody/all-track replacement, added tracks, harmony/metadata preservation, and rejected out-of-scope patches.
- `backend/tests/test_llm_composition_editing.py` for mocked region-edit graph success paths (dramatic melody bars 9–12 with unchanged outside notes/harmony), accompaniment/bass/counter-melody scenarios, malformed JSON, out-of-scope repair exhaustion, and provider failures.
- `backend/tests/test_llm_routes.py` for `/llm/edit-composition-region` success, `503` without providers, and `502` invalid patch mapping.
- `backend/tests/test_health_and_cors.py` for `/health`, `/ready` (non-secret readiness flags), CORS allow/deny origins, and empty-provider readiness.
- `backend/tests/test_project_store.py` for SQLite migrations, project CRUD/duplicate/delete, and generation metadata without keys.
- `backend/tests/test_project_routes.py` for `/projects` routes, 404/422 behavior, secret-field rejection, and legacy composition migration on open.
- `backend/tests/test_project_persistence_acceptance.py` for create → edit → reopen composition equality (Docker-restart acceptance at the data layer).

### Opt-in Real Provider Smoke Test

Normal `pytest` skips real provider calls. To intentionally spend API credits on a small bounded staged composition:

```bash
RUN_LLM_SMOKE=1 LLM_SMOKE_PROVIDER=openai ../.venv/bin/python -m pytest tests/test_llm_real_provider_smoke.py
```

Requires the matching provider API key. The smoke test logs provider/model, bars, tracks, events, and validation outcome, and is skipped clearly when `RUN_LLM_SMOKE` is unset.

### Opt-in FluidSynth WAV Smoke Test

Normal `pytest` does not require FluidSynth. Inside the standard Docker backend image (packages `fluidsynth` + `fluid-soundfont-gm`):

```bash
docker compose run --rm -e RUN_WAV_RENDERER_SMOKE=1 backend \
  python -m pytest tests/test_wav_renderer_smoke.py
```

Or locally with FluidSynth and `COMPOSITION_WAV_SOUNDFONT` pointing at an installed `.sf2`:

```bash
RUN_WAV_RENDERER_SMOKE=1 ../.venv/bin/python -m pytest tests/test_wav_renderer_smoke.py
```

Useful logs: renderer path basename, SoundFont basename, byte length, measured duration, missing-bin/SoundFont errors, timeout, invalid WAV, and duration padding warnings. Raw composition/audio bytes are never logged.

### Opt-in Docker Persistence Acceptance

Proves healthchecks + named-volume reopen after Compose restart with **fake LLM only** (no API credits):

```bash
RUN_DOCKER_ACCEPTANCE=1 ./scripts/v1_docker_acceptance.sh
RUN_DOCKER_ACCEPTANCE=1 ./scripts/v2_docker_acceptance.sh
# or
RUN_DOCKER_ACCEPTANCE=1 ../.venv/bin/python -m pytest tests/test_docker_persistence_acceptance.py
```

Uses Compose project names `mukit-v1-accept` / `mukit-v2-accept` by default and removes the volume on exit unless `KEEP_VOLUME=1`. V2 script covers V1→V2 migration on reopen plus expressive fake generate.

## Frontend Tests

Run from `frontend/`:

```bash
npm test
```

### Playwright E2E (fake LLM)

Requires a running stack with `LLM_FAKE_MODE=1` (Compose preferred):

```bash
# once
npx playwright install chromium

# against http://localhost:3000
LLM_FAKE_MODE=1 docker compose up --build -d
npm run test:e2e
# UI mode
npm run test:e2e:ui
```

Specs live in `frontend/e2e/` (`v1-user-journey`, `v1-upgrade-to-v2`, `v2-user-journey`, persistence suites). Persistence reopen after Compose restart is opt-in:

```bash
RUN_PLAYWRIGHT_DOCKER_RESTART=1 npm run test:e2e -- e2e/v1-persistence.spec.js
RUN_PLAYWRIGHT_DOCKER_RESTART=1 npm run test:e2e -- e2e/v2-persistence.spec.js
```

Artifacts (trace/video on failure) are gitignored under `frontend/test-results/` and `frontend/playwright-report/`.

## Frontend Smoke Checks

- canonical JSON validation, invalid velocity rejection, and legacy harmony-only rejection
- piano-roll pitch conversion, snap intervals (480 TPQ), 4/4 and 6/8 bar metrics, create/move/resize/delete immutability, clamping, and polyphony
- Zustand note-edit actions including undo/redo boundaries and skip-history drag updates
- AI region selection helpers, API response validation for `editCompositionRegion`, and store non-destructive failure / single-undo success paths
- generation duplicate guards (`startGeneration` / `startAiEdit` while loading) and empty LLM model load behavior
- project API client HTTP paths plus Zustand project open/hydrate, dirty→saving→saved, autosave debounce cancel/fire, generation metadata without keys, and delete clearing the active project
- canonical tick-to-second playback scheduling with multi-track ordering, polyphony, and velocity
- export-fidelity-style fixture parity fields (track ID, pitch, ticks, velocity, metadata)
- no harmony-derived events for canonical compositions
- track volume/mute/solo effective audible state and unsupported-instrument fallback selection
- Tone playback engine lifecycle with a fake Transport (schedule, cancel, stop, seek-to-start, dispose)
- download Blob URL create/revoke cleanup

## Frontend Smoke Checks

Use these manual checks after `npm run build` and during local development.

1. Start the backend on port `8888` and the frontend dev server on port `3000`.
2. Confirm the system status shows API connected.
3. With no `OPENAI_API_KEY` or `DEEPSEEK_API_KEY`, confirm the LLM composer shows the provider configuration message.
4. With one provider key configured, confirm the provider/model selector shows one option.
5. With both provider keys configured, confirm both provider/model options appear and selection changes are retained.
6. Generate LLM music JSON with a mocked or real configured provider and confirm the editable JSON includes `schema_version: "composition.v2"` (V1 fixtures migrate on load).
7. Edit the JSON to an invalid velocity, pitch, duration, section boundary, or track event shape and confirm a validation error appears.
8. Reset the editor and confirm the generated JSON is restored.
9. Confirm notation renders from backend MusicXML.
10. Click Play and confirm AudioContext starts only after the user gesture; simultaneous multi-track notes are audible; Pause/Resume, Stop, and Seek Start work; current seconds/bar update while playing.
11. Use per-track Mute, Solo, and Volume while playing; confirm routing changes without editing composition JSON.
12. Edit a note event while idle, then Play again; confirm playback uses the edited events. Edit during playback and confirm active playback stops.
13. Open the piano-roll editor: select the melody track, set snap to `1/8` or `1/16`, drag a note to another pitch/time, resize duration, confirm the JSON editor shows the same `tracks[].events[]` change, confirm notation refreshes after the debounce, then Play and confirm the edited pitch/duration are heard with the red playback cursor moving.
14. Use piano-roll Undo/Redo and Play again; confirm audible result follows the current edited state. Undo/redo applies only to note edits (not arbitrary JSON editor typing).
15. Click Export MusicXML, Export MIDI, and Export WAV; confirm downloads use `.musicxml` / `.mid` / `.wav`, notation preview updates from the exported MusicXML, projection warnings appear when headers report approximations/omissions, WAV does not start browser playback, and a known fixture's playback positions match MIDI export note tuples.
16. Open browser devtools and confirm sanitized playback/piano-roll diagnostics (path, event counts, note edit summaries, MusicXML preview length, instrument strategy/fallback, mute/solo gains) without raw composition dumps.
17. Resize to a mobile viewport and confirm piano-roll controls, playback, and track controls remain usable.

## Frontend Build

Run from `frontend/`:

```bash
npm run build
```

The OSMD/Tone.js bundle can trigger Vite's large chunk warning; that warning is expected until code splitting is added.

## Logging Checks

- Backend: set `LOG_LEVEL=DEBUG` before running the server or tests when diagnosing schema, migration, rendering, export, or MIDI mapping decisions.
- Frontend: use browser devtools console to inspect API response validation, store updates, editor validation, export requests, and playback schedule summaries.
- Logs should include schema version, export format, event counts, timing summaries, byte lengths, projection status/issue codes, and sanitized error messages. API keys, full raw prompts, MusicXML payloads, and MIDI bytes should not appear in logs.

## See Also

- [Composition V2](composition-v2.md) — V2 contract, fixtures, projection headers
- [Composition V1](composition-v1.md) — V1 parser regressions
- [Project persistence](project-persistence.md) — migration-on-open acceptance
