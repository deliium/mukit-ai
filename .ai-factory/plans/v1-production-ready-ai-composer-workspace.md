# Implementation Plan: Production-Ready Local V1 AI Composer Workspace

Branch: none (git.create_branches=false; work on current branch `main`)
Created: 2026-09-07

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Context Snapshot

Existing building blocks already cover the primary workflow (projects, LLM generate, piano roll, AI region edit, notation, Tone.js playback, MusicXML/MIDI/WAV export, SQLite persistence). Gaps are packaging and UX, not missing core features:

- Docker Compose runs a **dev** stack (Vite `dev`, bind mounts, `--reload`, Node 16).
- No `.env.example`, compose does not pass LLM keys, no healthchecks/restart policies.
- Frontend is a single vertical stack with marketing Header + System Status card; not a coherent laptop-sized workspace.
- Empty LLM providers show a short banner, but API can still look “Connected”.
- Duplicate generate is only UI-button-disabled (no store-level guard); no multi-stage progress.
- No separate statistical/training generator UI remains — only legacy JSON/playback paths and unused `models`/`training_data` mounts. Do **not** invent training UI; put advanced JSON (and any leftover legacy playback messaging) under Advanced.

Acceptance target: on a clean machine with Docker + one provider API key, `docker compose up --build` serves one usable AI Composer site with no extra manual dev commands.

## Commit Plan
- **Commit 1** (after tasks 1–3): `chore: productionize docker compose and env template`
- **Commit 2** (after tasks 4–5): `feat: production-ready backend health, CORS, and logging`
- **Commit 3** (after tasks 6–8): `feat: polish AI Composer workspace UX and LLM/export states`
- **Commit 4** (after tasks 9–11): `test: cover workspace readiness paths and document first-run`

## Tasks

### Phase 1: Production Docker & env foundation

- [x] Task 1: Add root `.env.example` and wire Compose to load LLM/settings without secrets
  - Create `.env.example` documenting (no real values): `OPENAI_API_KEY`, `OPENAI_MODEL`, `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `DEEPSEEK_BASE_URL`, `DEFAULT_LLM_PROVIDER`, `LLM_REQUEST_TIMEOUT_SECONDS`, `LLM_TEMPERATURE`, `PROJECT_DB_PATH`, `COMPOSITION_WAV_SOUNDFONT`, `LOG_LEVEL`, `CORS_ALLOW_ORIGINS`
  - Ensure `.env` is gitignored; never commit secrets
  - Update `docker-compose.yml` to use `env_file: .env` (optional file) and/or explicit `${VAR}` passthrough for backend LLM vars
  - Keep provider secrets backend-only (frontend must not receive API keys)
  - LOGGING: log at INFO when backend starts with configured provider names only (never key values); DEBUG which env keys are present (boolean)
  - Files: `.env.example`, `.gitignore`, `docker-compose.yml`, `README.md` (stub pointer only — full docs in Task 11)

- [x] Task 2: Productionize `docker-compose.yml` for a single `docker compose up --build` workflow
  - Replace Vite-dev compose with production-oriented services: backend API + frontend static/nginx
  - Persist SQLite via named volume `mukit_project_data` → `/data` (`PROJECT_DB_PATH=/data/projects.db`)
  - Drop legacy bind mounts (`./backend/models`, `./backend/training_data`, source bind mounts, anonymous `node_modules`) from the default compose file
  - Optional: keep `compose.override.yml` or `compose.dev.yml` for hot-reload local development (not required for V1 acceptance)
  - Add `healthcheck` for backend (`GET /health` or `/ready`) and frontend (HTTP on served port)
  - Add `restart: unless-stopped` on both services
  - Expose one primary site port (recommend `3000` or `80` for frontend); backend may stay internal or also expose `8888` for debugging
  - Verify `docker compose down` then `up` restores projects from the volume
  - LOGGING: document compose healthcheck failures via Docker status; backend logs container start with `LOG_LEVEL`
  - Files: `docker-compose.yml`, optionally `compose.dev.yml` / `compose.override.yml`, `.dockerignore` (root and/or per-service)

- [x] Task 3: Multi-stage production frontend image with reverse-proxied API
  - Upgrade base Node image to a current LTS compatible with Vite 6 (Node 20+)
  - Multi-stage Dockerfile: `npm ci` → `npm run build` → nginx (or equivalent) serving `dist/`
  - Nginx (or Caddy) config: SPA fallback; proxy `/health`, `/llm`, `/export`, `/projects` to backend service
  - Remove unused `REACT_APP_API_URL`; keep axios relative paths so same-origin proxy works
  - Add `frontend/.dockerignore` (node_modules, dist, tests if not needed in image)
  - LOGGING: nginx access/error logs at INFO-equivalent; build stage should fail loudly on compile errors
  - Files: `frontend/Dockerfile`, `frontend/nginx.conf` (or similar), `frontend/.dockerignore`, `frontend/vite.config.js` (keep `/projects` proxy for local `npm run dev`)

### Phase 2: Backend production readiness

- [x] Task 4: Env-driven CORS, `LOG_LEVEL`, and readiness endpoint
  - Make CORS origins configurable via `CORS_ALLOW_ORIGINS` (comma-separated); default include `http://localhost:3000` and same-origin via proxy if applicable
  - Wire `LOG_LEVEL` (DEBUG/INFO/WARNING/ERROR) into logging configuration at app startup
  - Keep `GET /health` as liveness; add `GET /ready` (or enrich `/health`) with: DB openable, LLM providers configured count (names only), WAV deps present/missing (boolean flags — no secrets)
  - Ensure missing LLM keys still allow app boot; generate/edit remain 503 with clear detail
  - LOGGING: DEBUG CORS origins list; INFO on ready checks; never log API keys, full prompts, or raw MusicXML/MIDI/WAV
  - Files: `backend/app/main.py`, `backend/app/llm_settings.py` (if needed), possibly small `backend/app/ready.py` / health helper, `backend/Dockerfile`

- [x] Task 5: Confirm backend image is production-local ready
  - Dockerfile CMD without `--reload` for default compose
  - Ensure FluidSynth + SoundFont paths match compose env
  - Ensure `/data` exists and migrations run on startup (existing lifespan/`initialize_database`)
  - LOGGING: INFO lifespan start/stop; INFO DB path (not contents); WARNING if no LLM providers
  - Files: `backend/Dockerfile`, `backend/app/main.py` (lifespan only if needed), `backend/app/db/connection.py` (verify only)

### Phase 3: Frontend AI Composer workspace polish

- [x] Task 6: Restructure composer into a coherent laptop workspace layout
  - Preserve existing useful components; reorganize rather than rewrite
  - Make AI Composer the primary workflow after Projects home
  - Workspace chrome: project/title header (`ProjectComposerBar`), compact connection/LLM readiness indicator (demote marketing `Header` / always-on System Status card)
  - Layout sections usable at ~1366×768 / 1440×900:
    - Generation controls
    - Track list (extend `TrackPlaybackControls` into a clear track list / mixer strip)
    - Transport controls (sticky or always-visible, not buried at bottom)
    - Piano roll (primary editor)
    - Notation tab/view
    - Advanced JSON view (collapsed/tabbed — not always dominating)
    - AI edit controls
    - Export controls
  - Prefer tabs or collapsible panels for Notation vs Advanced JSON vs (optional) other advanced views
  - No separate statistical generator exists — do not add training UI; label Advanced clearly for JSON/legacy messaging only
  - LOGGING: use existing console/store patterns sparingly; DEBUG view switches (`activeView`, editor tab) if useful
  - Files: `frontend/src/App.jsx`, `frontend/src/components/Header.jsx`, `frontend/src/components/MusicGenerator.jsx`, `ProjectComposerBar.jsx`, `TrackPlaybackControls.jsx`, `PlaybackControls.jsx`, `PianoRollEditor.jsx`, `NotationViewer.jsx`, `PromptJsonEditor.jsx`, `AiRegionEditPanel.jsx`, `ExportControls.jsx`, `frontend/src/index.css` / styled components, possibly new `ComposerWorkspace.jsx` / `WorkspaceTabs.jsx`

- [x] Task 7: LLM empty-provider UX, loading/progress, and duplicate-request prevention
  - When `/llm/models` is empty: show setup instructions (copy `.env.example` → `.env`, set one key, `docker compose up --build`) instead of a broken generate form
  - Distinguish **API healthy** vs **LLM ready** in status UI
  - Store-level guard: ignore/reject second `startGeneration` while `generationStatus === 'loading'`; same for `startAiEdit` while AI edit loading
  - Disable generate/edit controls while in-flight; surface clear progress/busy text for LLM ops (stage label if backend already returns useful status; otherwise indeterminate progress + elapsed hint)
  - Export: keep busy/error/success states; prefer per-format status or at least clear shared progress + errors; prevent double-clicks while exporting
  - LOGGING: INFO/DEBUG generation start/complete/fail with provider/model names only; WARN duplicate blocked requests
  - Files: `frontend/src/store/musicStore.js`, `MusicGenerator.jsx`, `AiRegionEditPanel.jsx`, `ExportControls.jsx`, `App.jsx`, `frontend/src/api/musicApi.js`

- [x] Task 8: Production API client path + local Vite proxy parity
  - Keep relative axios paths for production (nginx proxy)
  - Fix `frontend/vite.config.js` proxy to include `/projects` for local `npm run dev`
  - Remove dead CRA env usage
  - LOGGING: DEBUG API base/proxy assumptions only in dev; never log response bodies for MusicXML/MIDI/WAV
  - Files: `frontend/vite.config.js`, `frontend/src/api/musicApi.js`, `frontend/src/api/projectApi.js`

### Phase 4: Tests, verification, docs

- [x] Task 9: Frontend tests for generation guards, empty models, and export helpers
  - Add/extend Node test runner coverage fitting existing `*.test.js` patterns
  - Cover: `startGeneration` ignored while loading; empty models UX prerequisites in store/API mocks; at least one export download helper path if pure
  - LOGGING: tests should assert status transitions; no noisy console required
  - Files: `frontend/src/store/musicStore.generation.test.js` (new), `frontend/src/api/musicApi.test.js`, possibly `musicStore.test.js`

- [x] Task 10: Backend tests for health/ready, CORS, and missing-LLM 503
  - Extend pytest: `GET /health` (and `/ready` if added); CORS allow/deny origins; empty providers → models `[]` + generate/edit 503 (existing coverage in `test_llm_routes.py` — keep/extend)
  - Optional smoke note for compose healthchecks in docs/testing (not a fragile CI docker e2e unless already easy)
  - LOGGING: assert log-safe error details in responses (no secrets)
  - Files: `backend/tests/test_health_and_cors.py` (new), `backend/tests/test_llm_routes.py`

- [x] Task 11: README + docs first-run for production-local Docker V1
  - Document exact first-run:
    1. Copy `.env.example` → `.env`
    2. Set at least one provider API key
    3. `docker compose up --build`
    4. Open the documented site URL
    5. Walk primary workflow: Projects → New/Open → Generate → playback → piano roll → notation → AI partial edit → save → export
  - Document volume persistence, restart behavior, and that secrets stay backend-only
  - Fix outdated README bits (`npm start` → `npm run dev` for host-local; Docker as primary V1 path)
  - Update `docs/testing.md` / `docs/project-persistence.md` / `docs/CODEBASE_MAP.md` where Docker/Node details drift
  - LOGGING: document `LOG_LEVEL` in README/env example
  - Files: `README.md`, `docs/testing.md`, `docs/project-persistence.md`, `docs/CODEBASE_MAP.md` as needed

## Implementation Notes

- Prefer extending `routers/` + `services/` over growing unrelated logic in `main.py` if new readiness helpers are non-trivial.
- Treat `composition.v1` `tracks[].events[]` as the only playable source.
- Do not rewrite the piano roll, OSMD, or Tone.js engines — layout and packaging only unless a bug blocks the workflow.
- Verify acceptance manually: fresh compose build, one key in `.env`, full workflow without opening DevTools or editing source files.
