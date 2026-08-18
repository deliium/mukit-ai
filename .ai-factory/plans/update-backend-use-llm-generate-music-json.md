# Plan: LLM Music JSON Generation With Frontend Editor, Notation, And Playback

Created: 2026-08-17
Branch: main
Plan file: .ai-factory/plans/update-backend-use-llm-generate-music-json.md

## Settings

Testing: yes
Logging: verbose
Docs: yes
Branch creation: no
Roadmap Linkage: not available; no `.ai-factory/ROADMAP.md` found

## Goal

Update the current FastAPI + React music composer so users can generate structured music JSON through an LLM-backed LangChain/LangGraph backend, choose an available model from the frontend when API keys are configured, edit/view the returned JSON, render notes with OpenSheetMusicDisplay, and play the generated notes with Tone.js/Web Audio.

Use `music-example.json` as the initial target JSON shape:

- `tempo`
- `key`
- `time_signature`
- `sections`
- `tracks`
- `harmony`

## Existing Context

Backend:

- `backend/app/main.py` contains all FastAPI routes directly.
- `backend/app/schemas.py` contains simple Pydantic request/response models.
- `backend/app/models/music_composer.py` contains existing statistical MIDI/MusicXML generation.
- `backend/requirements.txt` has no LangChain/LangGraph/provider packages yet.
- No real backend unit test suite exists.

Frontend:

- `frontend/src/App.jsx` owns API/model status with local React state.
- `frontend/src/components/MusicGenerator.jsx` owns all generation parameters, API calls, and output state.
- `frontend/package.json` lacks `zustand`, `opensheetmusicdisplay`, `tone`, and a JSON editor dependency.
- `frontend/vite.config.js` proxies known backend routes only.

## Architecture Decision

Keep the existing statistical `/generate-music` flow working.

Add a separate LLM JSON flow:

- `GET /llm/models` returns configured model/provider options based on env vars.
- `POST /llm/generate-music-json` sends prompt parameters to a LangGraph agent.
- The agent calls an LLM through LangChain.
- The backend validates and returns structured music JSON.
- The backend also returns a derived MusicXML representation or a downloadable/renderable MusicXML payload so OpenSheetMusicDisplay can render notation.
- The frontend uses Zustand for generation state, selected provider/model, JSON result, notation content, playback state, and errors.

## Environment Variables

Add support for:

- `OPENAI_API_KEY`
- `OPENAI_MODEL`, default `gpt-4o-mini` or similar
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_MODEL`, default `deepseek-chat`
- `DEFAULT_LLM_PROVIDER`, optional, values `openai` or `deepseek`
- `LLM_REQUEST_TIMEOUT_SECONDS`, optional
- `LLM_TEMPERATURE`, optional default for model creativity

Model selection rule:

- If only one key is present, expose only that provider/model.
- If both keys are present, expose both and let the frontend choose.
- If no keys are present, `/llm/models` returns an empty list and generation returns a clear `503` error.

## Tasks

### Phase 1: Backend Schemas And Configuration

- [x] 1. Add LLM music Pydantic schemas in `backend/app/schemas.py`.

Deliverable:

- Create request models for prompt parameters, model selection, and generation options.
- Create response models for validated music JSON, provider/model metadata, MusicXML text, and warnings.
- Mirror `music-example.json` with nested models for `sections`, `tracks`, and `harmony`.
- Add validation constraints for tempo, bars, key/time signature strings, non-empty tracks, and supported section roles.

Files:

- `backend/app/schemas.py`

Logging requirements:

- Log schema validation failures at `DEBUG` with sanitized request context.
- Do not log API keys or raw secrets.

Dependencies:

- Needed before endpoint and agent implementation.

- [x] 2. Add backend LLM settings loader.

Deliverable:

- Add a small settings module that reads provider keys and model names from environment variables.
- Expose helper functions for available providers and default provider selection.
- Keep settings deterministic and testable.

Files:

- `backend/app/llm_settings.py` or `backend/app/services/llm_settings.py`

Logging requirements:

- Log available provider names at `INFO`.
- Log missing provider keys at `DEBUG`.
- Never log key values.

Dependencies:

- Needed by `/llm/models` and the LangGraph service.

- [x] 3. Update Python dependencies.

Deliverable:

- Add LangChain/LangGraph/OpenAI-compatible dependencies.
- Prefer OpenAI-compatible client support for both OpenAI and DeepSeek.

Files:

- `backend/requirements.txt`
- `backend/requirements-modern.txt` if it is still maintained in parallel

Suggested dependencies:

- `langchain`
- `langgraph`
- `langchain-openai`
- `openai`

Logging requirements:

- No runtime logging in dependency files.

Dependencies:

- Needed before backend service implementation.

### Phase 2: LangGraph Music JSON Agent

- [x] 4. Add LLM music generator service.

Deliverable:

- Create a service module that builds a LangGraph agent/graph for music JSON generation.
- Use a strict prompt instructing the model to return only valid JSON matching the schema.
- Include user-editable prompt parameters such as mood, genre, tempo range, key, time signature, instruments/tracks, section structure, complexity, and duration/bars.
- Validate LLM output with Pydantic before returning it.
- Include retry or correction path for invalid JSON if practical.

Files:

- `backend/app/services/llm_music_generator.py`
- Optional: `backend/app/services/__init__.py`

Logging requirements:

- `INFO`: provider/model selected, generation started/completed, validation retry count.
- `DEBUG`: sanitized prompt parameters, parsed JSON keys, validation errors.
- `WARN`: fallback/correction path used.
- `ERROR`: provider/API failures with sanitized error detail.

Dependencies:

- Depends on tasks 1-3.

- [x] 5. Add JSON-to-MusicXML conversion helper.

Deliverable:

- Convert the validated music JSON into a simple `music21.stream.Stream`.
- Use `tempo`, `key`, `time_signature`, and `harmony` to create a playable/renderable score.
- Return MusicXML text or a generated temporary MusicXML filename.
- Keep the first implementation musically simple but deterministic.

Files:

- `backend/app/services/music_json_renderer.py`

Logging requirements:

- `INFO`: conversion started/completed.
- `DEBUG`: bar count, chord count, tempo/key/time signature.
- `WARN`: unsupported chord/instrument skipped or simplified.
- `ERROR`: conversion failure with JSON metadata, not full sensitive prompt content.

Dependencies:

- Depends on task 1.

### Phase 3: Backend API Routes

- [x] 6. Add LLM model discovery endpoint.

Deliverable:

- Add `GET /llm/models`.
- Return available providers/models based on env keys.
- Include default selected model.

Files:

- `backend/app/main.py`
- `backend/app/schemas.py`

Logging requirements:

- `INFO`: model discovery requested.
- `DEBUG`: provider availability summary without secrets.

Dependencies:

- Depends on tasks 1-2.

- [x] 7. Add LLM JSON generation endpoint.

Deliverable:

- Add `POST /llm/generate-music-json`.
- Accept model/provider selection and prompt parameters.
- Call the LangGraph service.
- Return validated JSON and derived MusicXML content/filename.
- Return clear errors for no configured providers, unsupported model selection, invalid model output, and provider failures.

Files:

- `backend/app/main.py`
- `backend/app/services/llm_music_generator.py`
- `backend/app/services/music_json_renderer.py`

Logging requirements:

- `INFO`: request start/end, provider/model used, success/failure.
- `DEBUG`: sanitized request parameters and response shape.
- `WARN`: invalid LLM output corrected or provider unavailable.
- `ERROR`: final failures with exception type and sanitized message.

Dependencies:

- Depends on tasks 1-6.

- [x] 8. Update Vite proxy for new routes.

Deliverable:

- Proxy `/llm` to `http://localhost:8888`.

Files:

- `frontend/vite.config.js`

Logging requirements:

- No runtime logging required.

Dependencies:

- Needed before local frontend integration testing.

### Phase 4: Frontend State And API Client

- [x] 9. Add frontend dependencies.

Deliverable:

- Add `zustand`.
- Add `opensheetmusicdisplay`.
- Add `tone`.
- Add a JSON editor dependency, preferably lightweight for this app.

Files:

- `frontend/package.json`
- `frontend/package-lock.json`

Logging requirements:

- No runtime logging required.

Dependencies:

- Needed before component implementation.

- [x] 10. Add Zustand music store.

Deliverable:

- Store API status, model-loaded status, available LLM models, selected provider/model, prompt parameters, generated JSON, MusicXML content, generation status, playback status, and UI errors.
- Move generation-related local state out of `MusicGenerator.jsx`.

Files:

- `frontend/src/store/musicStore.js`

Logging requirements:

- Use `console.debug` for development-only state transitions if needed.
- Use `console.error` for failed API calls.
- Avoid logging full prompt/output repeatedly unless behind targeted debug statements.

Dependencies:

- Depends on task 9.

- [x] 11. Add frontend API helper module.

Deliverable:

- Centralize calls for `/health`, `/llm/models`, `/llm/generate-music-json`, existing training endpoints, and downloads.
- Keep components focused on UI and store actions.

Files:

- `frontend/src/api/musicApi.js`

Logging requirements:

- `console.debug`: request intent and endpoint.
- `console.error`: request failures with sanitized response details.

Dependencies:

- Supports task 10 and later UI tasks.

### Phase 5: Frontend LLM UI, JSON Editor, Notation, Playback

- [x] 12. Refactor app status into Zustand.

Deliverable:

- Update `App.jsx` to use the Zustand store for health/model status.
- Load available LLM models on startup.
- Preserve existing layout and training flow.

Files:

- `frontend/src/App.jsx`
- `frontend/src/components/TrainingDataUploader.jsx`

Logging requirements:

- `console.debug`: startup health/model discovery events.
- `console.error`: health/model discovery failures.

Dependencies:

- Depends on tasks 10-11.

- [x] 13. Split and extend music generation UI.

Deliverable:

- Add controls for LLM prompt parameters:
- provider/model
- genre
- mood
- key
- time signature
- tempo
- sections/bars
- instruments/tracks
- complexity
- freeform instructions
- Keep existing statistical generation available or clearly separate it from LLM JSON generation.
- Submit requests to `/llm/generate-music-json`.

Files:

- `frontend/src/components/MusicGenerator.jsx`
- Optional: `frontend/src/components/GenerationControls.jsx`

Logging requirements:

- `console.debug`: selected provider/model and sanitized generation parameters.
- `console.error`: generation failures.

Dependencies:

- Depends on tasks 6-11.

- [x] 14. Add JSON result editor/viewer.

Deliverable:

- Display returned music JSON in an editable JSON editor.
- Validate edited JSON client-side before using it for notation/playback.
- Allow reset to latest generated JSON.

Files:

- `frontend/src/components/PromptJsonEditor.jsx`
- `frontend/src/components/MusicGenerator.jsx`
- `frontend/src/store/musicStore.js`

Logging requirements:

- `console.debug`: JSON editor parse success/failure.
- `console.warn`: invalid edited JSON state.

Dependencies:

- Depends on task 13.

- [x] 15. Add OpenSheetMusicDisplay notation viewer.

Deliverable:

- Render backend-provided MusicXML content.
- Re-render when generated or edited JSON changes if conversion is available.
- Show actionable empty/error states.

Files:

- `frontend/src/components/NotationViewer.jsx`
- `frontend/src/components/MusicGenerator.jsx`

Logging requirements:

- `console.debug`: OSMD render start/completion.
- `console.error`: OSMD render failures.

Dependencies:

- Depends on tasks 5, 7, 13, 14.

- [x] 16. Add Tone.js playback controls.

Deliverable:

- Play generated/edited JSON using Tone.js by mapping tempo, harmony, and simple generated notes/chords to scheduled synth events.
- Include play, stop, loading, and error states.
- Ensure playback starts only after a user gesture.
- Clean up Tone transport/synth resources on stop/unmount.

Files:

- `frontend/src/components/PlaybackControls.jsx`
- `frontend/src/store/musicStore.js`
- `frontend/src/components/MusicGenerator.jsx`

Logging requirements:

- `console.debug`: playback schedule summary and transport state.
- `console.warn`: unsupported JSON/chord data skipped.
- `console.error`: audio initialization/playback failures.

Dependencies:

- Depends on tasks 9, 10, 14.

### Phase 6: Tests

- [x] 17. Add backend unit tests.

Deliverable:

- Add pytest-based tests for:
- provider/model discovery with different env var combinations
- schema validation
- no-provider error behavior
- mocked LangGraph/LLM generation success
- invalid LLM JSON handling
- JSON-to-MusicXML conversion
- Mock provider calls; do not call real OpenAI or DeepSeek APIs.

Files:

- `backend/tests/test_llm_settings.py`
- `backend/tests/test_llm_music_generation.py`
- `backend/tests/test_music_json_renderer.py`
- `backend/tests/test_llm_routes.py`
- Update requirements if pytest/httpx are missing

Logging requirements:

- Tests should assert no secrets appear in logs where practical.

Dependencies:

- Depends on tasks 1-7.

- [x] 18. Add frontend tests or smoke checks.

Deliverable:

- Add tests for Zustand store actions and key UI states if the repo has/accepts a frontend test runner.
- If no test runner is configured, add documented manual smoke checks and avoid introducing a large test stack unless agreed.

Files:

- Preferred if adding Vitest:
- `frontend/package.json`
- `frontend/src/store/musicStore.test.js`
- `frontend/src/components/*.test.jsx`
- Otherwise:
- `docs/testing.md`

Logging requirements:

- No runtime logging beyond existing component/store behavior.

Dependencies:

- Depends on tasks 10-16.

### Phase 7: Documentation

- [x] 19. Update project documentation.

Deliverable:

- Document new environment variables.
- Document LLM provider/model selection.
- Document `/llm/models` and `/llm/generate-music-json`.
- Document frontend JSON editor, notation viewer, and playback behavior.
- Document how to run backend/frontend tests.
- Include an example request/response based on `music-example.json`.

Files:

- `README.md`
- Optional:
- `docs/llm-music-generation.md`
- `docs/frontend-preview-playback.md`

Logging requirements:

- Documentation should explain verbose backend/frontend logging and secret redaction expectations.

Dependencies:

- Depends on implementation tasks, can be finalized after tests.

## Commit Plan

1. `feat(backend): add llm music generation schemas and settings`

Tasks 1-3

2. `feat(backend): add langgraph music json generation api`

Tasks 4-8

3. `feat(frontend): add zustand llm generation workflow`

Tasks 9-13

4. `feat(frontend): add json editor notation preview and playback`

Tasks 14-16

5. `test: cover llm music generation workflow`

Tasks 17-18

6. `docs: document llm music generation setup`

Task 19

## Verification

Backend:

- `cd backend && python -m pytest`
- `cd backend && python test_gpu.py`
- `cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8888`
- Verify `GET /health`
- Verify `GET /llm/models` with no keys, OpenAI key only, DeepSeek key only, and both keys
- Verify `POST /llm/generate-music-json` with mocked or real configured provider

Frontend:

- `cd frontend && npm install`
- `cd frontend && npm run build`
- `cd frontend && npm run lint`
- `cd frontend && npm run dev`
- Verify provider/model selector appears only for configured providers
- Verify JSON generation result appears in editor
- Verify invalid JSON shows a clear editor error
- Verify OpenSheetMusicDisplay renders notation
- Verify playback starts/stops from a user click
- Verify mobile layout remains usable

## Risks And Edge Cases

- LLMs may return invalid JSON; backend must validate and either correct or fail clearly.
- DeepSeek uses an OpenAI-compatible API, but base URL/model names must be configurable.
- OpenSheetMusicDisplay renders MusicXML, so backend should return MusicXML derived from JSON or expose a conversion endpoint.
- Tone.js does not parse arbitrary MusicXML by itself; playback should initially schedule notes/chords from the validated JSON structure.
- No real frontend test runner appears configured; adding Vitest may be worthwhile but increases scope.
- Existing backend uses `print`; this feature should introduce structured `logging` without refactoring the entire app unless needed.
