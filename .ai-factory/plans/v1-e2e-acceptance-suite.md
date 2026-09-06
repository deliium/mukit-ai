# Implementation Plan: V1 End-to-End Acceptance Suite

Branch: none (`git.create_branches=false`; work on current branch `main`)
Created: 2026-09-06

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Context Snapshot

Production-local Docker, projects, staged LLM generate/edit, piano roll, notation, Tone.js playback, and exports already exist. Gaps for a reliable V1 release are **verification and a credit-free deterministic LLM path**, not missing core product surfaces:

- No Playwright (or other browser E2E); `docs/testing.md` has a **manual** 17-step smoke checklist only.
- LLM tests monkeypatch `_invoke_chat` / route handlers; there is **no runtime fake provider** for Docker/browser E2E without spending OpenAI/DeepSeek credits.
- Composition fixtures are inline Python/JS builders, not shared canonical JSON files.
- Persistence “acceptance” is store-layer only (`test_project_persistence_acceptance.py`); Docker restart → reopen is manual in README.
- Frontend unit tests cover utils/store/api; no real-browser journey for play / notation / piano-roll / export downloads.

Acceptance target: from a clean app state, the documented Docker Compose workflow + Playwright (fake LLM) proves create → generate → play → notation → edit → hear edit → AI region edit → undo → save → export MIDI/MusicXML/WAV → restart containers → reopen persisted composition. Unit/lint/build gates pass; real-provider smoke stays opt-in.

## Commit Plan
- **Commit 1** (after tasks 1–3): `test: add composition fixtures and fake LLM provider`
- **Commit 2** (after tasks 4–5): `test: harden backend acceptance for exports, safety, and persistence`
- **Commit 3** (after tasks 6–8): `test: add Playwright V1 journey with Docker persistence`
- **Commit 4** (after tasks 9–11): `fix: resolve V1 acceptance defects and document usage`

## Tasks

### Phase 1: Deterministic fixtures and fake LLM

- [x] Task 1: Add shared valid Composition V1 fixtures (16–32 bar multi-track)
  - Create versioned JSON fixtures under `backend/tests/fixtures/` (and optionally mirror or load the same files from frontend/E2E):
    - `composition_v1_16bar_multitrack.json` — ≥16 bars, ≥3 tracks (melody/bass/accompaniment), real `tracks[].events[]` notes (not harmony-only)
    - `composition_v1_export_fidelity.json` — align with existing `build_export_fidelity_composition()` / frontend `FIDELITY_FIXTURE` fields where practical
    - `composition_v1_unsupported_instrument.json` — unknown instrument token that must degrade to piano/program 0 without dropping notes
    - Optional small `composition_v1_minimal.json` for fast unit paths
  - Add a tiny loader helper (e.g. `backend/tests/fixtures/load_fixture.py` or `backend/app/services/fixture_compositions.py` used only by fake LLM + tests) that validates via Pydantic `Composition`
  - Prefer loading fixtures from tests over duplicating large inline builders; keep one builder only if a test needs programmatic variation
  - LOGGING: DEBUG fixture path + bar/track/event counts on load; never log full composition JSON at INFO
  - Files: `backend/tests/fixtures/*.json`, loader helper, update `docs/composition-v1.md` only if fixture examples clarify the contract (prefer Task 11 for user docs)

- [x] Task 2: Implement deterministic fake/mock LLM mode (no API credits)
  - Add an env-gated **fake provider** (recommended: `LLM_FAKE_MODE=1` and/or provider id `fake`) that:
    - Appears in `GET /llm/models` when enabled (display name clear, e.g. “Fake (deterministic)”)
    - Satisfies generate + region-edit contracts with fixture-backed responses (16–32 bar multi-track with notes; region edit returns a valid patch that changes only the selected bar range)
    - Never opens network sockets to OpenAI/DeepSeek
  - Wire through `llm_settings.py` + `llm_music_generator.py` / `llm_composition_editor.py` (or a thin `services/fake_llm.py`) without breaking real providers when keys are set
  - Default automated E2E/Docker test profile: fake mode ON; production docs: fake OFF unless explicitly enabled for local demos
  - Document env vars in `.env.example` (no secrets)
  - LOGGING: INFO when fake mode is active; DEBUG stage/fixture used for generate vs edit; WARNING if both fake mode and real keys are set (fake wins for tests, or document precedence); never log API keys
  - Files: `backend/app/llm_settings.py`, `backend/app/schemas.py` (if provider Literal expands), `backend/app/services/llm_music_generator.py`, `backend/app/services/llm_composition_editor.py`, new `fake_llm` helper, `.env.example`, `backend/tests/test_llm_fake_provider.py`

- [x] Task 3: Backend unit/route coverage for fake LLM + malformed output safety
  - Tests: fake generate returns canonical notes; fake region edit patches only selected bars; `/llm/models` lists fake when enabled and omits secrets
  - Malformed LLM path: with a controllable bad response (monkeypatch or fake-mode inject), assert `502`/`InvalidLLMOutputError` and that an existing project’s stored composition is **unchanged** after failed generate/edit
  - Unsupported instrument fixture: normalize/playback/export still yield notes; program falls back gracefully
  - LOGGING: assert log hygiene in tests where practical (no key substrings in captured logs for generate/edit failure paths)
  - Files: `backend/tests/test_llm_fake_provider.py`, extend `test_llm_routes.py` / `test_llm_composition_editing.py` / `test_project_routes.py` as needed
  <!-- Commit checkpoint: tasks 1-3 -->

### Phase 2: Backend acceptance gates (exports, secrets, Docker data)

- [x] Task 4: Strengthen export and safety acceptance tests
  - Assert MusicXML / MIDI / WAV exports are **non-empty** and structurally valid (MIDI SMF parse / MusicXML parse / WAV header+duration) using fixtures — keep FluidSynth real path opt-in (`RUN_WAV_RENDERER_SMOKE=1`); default suite may use existing WAV mocks **plus** a Docker-targeted test that runs real WAV when the backend image is available
  - Secret exposure checks: `/llm/models`, `/ready`, `/health`, project CRUD responses, and committed config (`.env.example`, compose) never contain API key values; project body rejects `api_key`; grep/CI-friendly assertion helper OK
  - Keep `RUN_LLM_SMOKE=1` real-provider test opt-in and clearly skipped by default
  - LOGGING: INFO export byte lengths and content-types in tests’ subject under test; never log raw payloads
  - Files: `backend/tests/test_export_routes.py`, `test_export_fidelity.py`, `test_health_and_cors.py`, `test_project_routes.py`, `test_llm_real_provider_smoke.py` (verify still opt-in)

- [x] Task 5: Docker healthcheck + volume persistence verification (automated)
  - Add a scripted acceptance path (pytest + docker CLI, or `scripts/v1_docker_acceptance.sh`) that:
    1. `docker compose up --build -d` with `LLM_FAKE_MODE=1` (and without requiring real keys)
    2. Waits for backend + frontend healthchecks
    3. Creates a project via HTTP, writes a composition (fake generate or PATCH), restarts containers, reopens project, asserts composition equality
    4. Tears down appropriately (document whether named volume is kept or removed for clean runs)
  - Mark as opt-in if too slow for default pytest (`RUN_DOCKER_ACCEPTANCE=1`) but document it as part of V1 release gate
  - LOGGING: INFO compose project name, health wait timing, project id, restart outcome; ERROR with container status on failure
  - Files: `scripts/v1_docker_acceptance.sh` and/or `backend/tests/test_docker_persistence_acceptance.py`, `docker-compose.yml` (env for fake mode if needed), `docs/testing.md`
  <!-- Commit checkpoint: tasks 4-5 -->

### Phase 3: Playwright browser E2E

- [x] Task 6: Add Playwright to the frontend (or repo root) for Vite/React
  - Prefer Playwright over introducing Cypress; configure for Chromium
  - Layout: `frontend/e2e/` or root `e2e/` with `playwright.config.*`, baseURL `http://localhost:3000` (Compose) or Vite preview
  - npm scripts: `test:e2e`, `test:e2e:ui`; document browser install (`npx playwright install`)
  - CI/local default: point at stack with **fake LLM**; do not call real providers
  - LOGGING: Playwright trace/video on failure; console error capture; no secrets in reports
  - Files: `frontend/package.json`, `playwright.config.js` (or `.ts`), `frontend/e2e/` (or `e2e/`), `.gitignore` for Playwright artifacts

- [x] Task 7: Implement V1 user-journey Playwright suite (fake LLM)
  - Automate the required journey against a running app (Compose preferred for release gate):
    1. Open site
    2. Create project
    3. Select available configured model (fake provider)
    4. Request 16–32 bar multi-track generation
    5. Assert canonical composition with actual notes in UI/store or visible editor
    6. Play (handle AudioContext / user-gesture; assert transport/playing UI or Tone schedule hooks — not silent failure)
    7. View notation (OSMD container renders / non-empty SVG or canvas)
    8. Edit a note in the piano roll
    9. Hear/verify edit (playback state or event list change + play)
    10. AI regenerate selected region
    11. Undo if desired
    12. Save project
    13–16. Export MIDI, MusicXML, WAV — assert download/response non-empty
  - Use resilient selectors (`data-testid` where needed); add minimal testids to components rather than brittle CSS
  - LOGGING: step-level INFO in test annotations; capture network failures for `/llm/*` and `/export/*`
  - Files: `frontend/e2e/v1-user-journey.spec.js` (or `.ts`), component testid hooks in `ProjectBrowser`, `MusicGenerator`, `PlaybackControls`, `PianoRollEditor`, `NotationViewer`, `AiRegionEditPanel`, `ExportControls`, `ProjectComposerBar`

- [x] Task 8: Playwright persistence reopen after Docker restart
  - Extend journey or separate spec: after save + exports, restart Compose stack, reopen same project, assert edited composition persisted (notes from piano-roll edit still present)
  - Coordinate with Task 5 script or call Compose from Playwright global setup/teardown carefully (document single recommended command)
  - LOGGING: INFO project id before/after restart; fail with clear message if volume wiped unexpectedly
  - Files: `frontend/e2e/v1-persistence.spec.js`, shared helpers, `docs/testing.md`
  <!-- Commit checkpoint: tasks 6-8 -->

### Phase 4: Defect fixes, release gates, documentation

- [x] Task 9: Fix defects discovered while implementing the acceptance suite
  - Treat failing acceptance/E2E/unit checks as product bugs: fix root causes (fake provider wiring, autosave races, notation debounce, export headers, healthchecks, unsupported instrument paths, project-state clobber on LLM 502, etc.) rather than weakening assertions without justification
  - Prefer extending `routers/` + `services/` over growing `main.py`
  - LOGGING: add targeted DEBUG/INFO around each fixed failure mode; never log keys, full prompts, or raw MusicXML/MIDI/WAV
  - Files: as discovered under `backend/app/**`, `frontend/src/**`, Compose/nginx

- [x] Task 10: Run and green the V1 verification gate
  - Backend: `pytest` (default, no credit spend)
  - Frontend: `npm test`, `npm run lint`, `npm run build`
  - E2E: Playwright against Compose with fake LLM
  - Docker: healthchecks healthy; persistence restart path green (`RUN_DOCKER_ACCEPTANCE=1` and/or Playwright persistence spec)
  - Opt-in only: `RUN_LLM_SMOKE=1`, `RUN_WAV_RENDERER_SMOKE=1` (document; do not require for default green)
  - Record commands in `docs/testing.md`; fix any failures found
  - LOGGING: keep suite output actionable; sanitize any accidental secret leakage found during the gate
  - Files: `docs/testing.md`, CI script if added (optional; no requirement to add GitHub Actions unless already desired)

- [x] Task 11: Concise V1 usage documentation (not an implementation report)
  - Update `README.md` first-run path: `docker compose up --build`, copy `.env.example`, optional real keys vs `LLM_FAKE_MODE` for demos/tests
  - Short “V1 workflow” section: create project → pick model → generate → play/notation/piano roll → AI region edit → undo → save → export → restart/reopen
  - Point to `docs/testing.md` for acceptance commands; keep `docs/composition-v1.md` / persistence docs accurate if env or fake provider changes contracts
  - No separate long implementation report file
  - LOGGING: document that keys stay backend-only and must not appear in browser/logs/exports
  - Files: `README.md`, `docs/testing.md`, optionally `docs/v1-usage.md` only if README would become too long (prefer README + testing.md)
  <!-- Commit checkpoint: tasks 9-11 -->

## Definition of Done (plan gate)

- [x] Fake LLM mode exercises full generate + region-edit contracts without API credits
- [x] Shared valid Composition V1 fixtures exist and are used by tests/fake provider
- [x] Playwright V1 journey covers steps 1–18 of the required user journey (restart + reopen included)
- [x] Default automated tests spend no OpenAI/DeepSeek credits; real-provider tests remain opt-in
- [x] Backend unit tests, frontend tests, lint, and production build pass
- [x] Docker healthchecks and volume persistence verified
- [x] Malformed LLM responses do not destroy project state
- [x] Unsupported instruments degrade gracefully with notes retained
- [x] Exports are non-empty and structurally valid
- [x] No API keys exposed to browser, logs, exported projects, or committed configuration
- [x] Concise V1 usage docs updated; defects found during the suite are fixed
