# Testing

## Backend

Run backend unit tests from the `backend/` directory:

```bash
../.venv/bin/python -m pytest
```

The LLM tests mock provider behavior and do not call OpenAI or DeepSeek APIs.

Focused canonical coverage includes:

- `backend/tests/test_composition_schema.py` for `composition.v1` validation, 4/4, 3/4, 6/8 timing, invalid pitches, velocities, durations, duplicate tracks, and section boundaries.
- `backend/tests/test_composition_normalizer.py` for legacy migration, velocity defaults, canonical pass-through, and harmony-only rejection.
- `backend/tests/test_composition_midi.py` for MIDI-ready timing, channel, program, velocity preservation, and Standard MIDI File rendering.
- `backend/tests/test_export_fidelity.py` for deterministic fixture comparisons across canonical JSON, MIDI bytes, and MusicXML note timing.
- `backend/tests/test_export_routes.py` for `/export/musicxml` and `/export/midi` content types, attachments, and validation errors.
- `backend/tests/test_music_json_renderer.py` for canonical MusicXML rendering from events without harmony fallback.
- `backend/tests/test_composition_validator.py` for integrity diagnostics such as missing roles, empty/sparse tracks, pitch/range issues, and harmony-only rejection.
- `backend/tests/test_llm_staged_composer.py` for mocked multi-stage sequencing, repair/retry, oversized request rejection, provider/model override, and actionable API errors.

### Opt-in Real Provider Smoke Test

Normal `pytest` skips real provider calls. To intentionally spend API credits on a small bounded staged composition:

```bash
RUN_LLM_SMOKE=1 LLM_SMOKE_PROVIDER=openai ../.venv/bin/python -m pytest tests/test_llm_real_provider_smoke.py
```

Requires the matching provider API key. The smoke test logs provider/model, bars, tracks, events, and validation outcome, and is skipped clearly when `RUN_LLM_SMOKE` is unset.

## Frontend Tests

Run from `frontend/`:

```bash
npm test
```

The frontend uses Node's built-in test runner for browser-independent utilities. Tests cover canonical JSON validation, invalid velocity rejection, legacy harmony-only rejection, canonical tick-to-second playback scheduling, polyphony preservation, invalid playback event skipping, and download Blob URL create/revoke cleanup.

## Frontend Smoke Checks

Use these manual checks after `npm run build` and during local development.

1. Start the backend on port `8888` and the frontend dev server on port `3000`.
2. Confirm the system status shows API connected.
3. With no `OPENAI_API_KEY` or `DEEPSEEK_API_KEY`, confirm the LLM composer shows the provider configuration message.
4. With one provider key configured, confirm the provider/model selector shows one option.
5. With both provider keys configured, confirm both provider/model options appear and selection changes are retained.
6. Generate LLM music JSON with a mocked or real configured provider and confirm the editable JSON includes `schema_version: "composition.v1"`.
7. Edit the JSON to an invalid velocity, pitch, duration, section boundary, or track event shape and confirm a validation error appears.
8. Reset the editor and confirm the generated JSON is restored.
9. Confirm notation renders from backend MusicXML.
10. Click Play and Stop to confirm playback starts only after the user gesture, preserves simultaneous notes, and stops cleanly.
11. Click Export MusicXML and Export MIDI; confirm downloads use `.musicxml` / `.mid`, notation preview updates from the exported MusicXML, and playback/export positions match the edited canonical JSON.
12. Resize to a mobile viewport and confirm controls remain usable without horizontal page overflow.

## Frontend Build

Run from `frontend/`:

```bash
npm run build
```

The OSMD/Tone.js bundle can trigger Vite's large chunk warning; that warning is expected until code splitting is added.

## Logging Checks

- Backend: set `LOG_LEVEL=DEBUG` before running the server or tests when diagnosing schema, migration, rendering, export, or MIDI mapping decisions.
- Frontend: use browser devtools console to inspect API response validation, store updates, editor validation, export requests, and playback schedule summaries.
- Logs should include schema version, export format, event counts, timing summaries, byte lengths, and sanitized error messages. API keys, full raw prompts, MusicXML payloads, and MIDI bytes should not appear in logs.
