# Implementation Plan: Local Project / Composition Persistence

Branch: feature/local-project-composition-persistence
Created: 2026-09-06

## Settings
- Testing: yes — deterministic backend pytest + frontend Node test runner coverage; no real provider calls required for persistence tests
- Logging: verbose — structured `logging` extras for project CRUD, autosave, DB path/migrations, open-time composition migration, and sanitized errors; never log API keys or full freeform prompts beyond existing sanitized previews
- Docs: yes — mandatory docs checkpoint; update README and add/update project-persistence guidance

## Current State

- Backend is a **stateless** FastAPI app (`backend/app/main.py`) with LLM generate, MusicXML preview/export, and MIDI export only. There is **no** SQLite/Postgres/SQLAlchemy/Alembic usage today.
- Canonical playable music lives in Pydantic `Composition` (`schema_version: composition.v1`) in `backend/app/schemas.py`.
- Legacy → V1 conversion already exists via `normalize_composition_json` in `backend/app/services/composition_normalizer.py`.
- Frontend (`frontend/src/App.jsx`) opens straight into `MusicGenerator`; Zustand `musicStore` holds `generatedMusicJson` / `editedMusicJson` **in memory only**.
- Docker Compose bind-mounts source code only; **no named data volume** for durable project storage.
- Frontend has no router dependency; tests use Node's built-in test runner (`frontend/package.json` → `node --test`).
- Generation responses already return `provider` + `model`; prompt parameters live in the Zustand `prompt` state.

## Goals

- Let a single local user create music, restart Docker containers, and reopen the same edited composition unchanged.
- Persist project metadata + canonical Composition V1 JSON in **SQLite**.
- Support create, rename, save, debounced autosave, open, duplicate, and delete-with-confirmation.
- Track created/updated timestamps and generation metadata (provider/model/prompt snapshot) without storing API keys.
- Add a frontend project browser/home screen and clear saved/saving/unsaved indicators.
- Persist DB data through a Docker volume; migrate older composition JSON on open.
- Cover with API/backend tests, frontend tests, logging, and docs.

## Non-Goals

- Multi-user auth, cloud sync, or collaborative editing.
- Replacing Composition V1 or the staged LLM composer.
- Storing provider API keys or secrets in the project database.
- Introducing Postgres/Redis or a heavy multi-service data stack for V1.
- Browser-only persistence (IndexedDB) as the source of truth — backend SQLite is authoritative so Docker restarts recover work.

## Proposed Design

### Storage Choice

Use **SQLite** (stdlib `sqlite3`) with a thin repository layer — no existing ORM, and this keeps the single-user local V1 lightweight.

- DB path from env `PROJECT_DB_PATH` (default: `backend/data/projects.db` locally; `/data/projects.db` in Docker).
- Explicit **DB schema migrations** via numbered SQL files + a `schema_migrations` table applied on startup (even without SQLAlchemy/Alembic).
- On open/save, run composition through `normalize_composition_json` / `Composition.model_validate` so older or legacy payloads upgrade to canonical V1 before returning to the client.

### Data Model

Table `projects`:

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | UUID string |
| `name` | TEXT NOT NULL | User-visible title |
| `composition_json` | TEXT NULL | Canonical Composition V1 JSON (null until first generate/save with music) |
| `generation_provider` | TEXT NULL | e.g. `openai` / `deepseek` |
| `generation_model` | TEXT NULL | model id only |
| `generation_prompt_json` | TEXT NULL | sanitized prompt parameters snapshot (no keys) |
| `created_at` | TEXT NOT NULL | UTC ISO-8601 |
| `updated_at` | TEXT NOT NULL | UTC ISO-8601 |

Indexes: `updated_at DESC` for browser listing.

### API Surface

Mount under `/projects` in `backend/app/main.py` (or a small router module imported from main):

- `GET /projects` — list summaries (`id`, `name`, timestamps, optional bar/track/event counts, has_composition)
- `POST /projects` — create (`name`, optional initial composition/generation metadata)
- `GET /projects/{id}` — open full project; migrate composition schema on read
- `PATCH /projects/{id}` — rename and/or save composition + generation metadata; bump `updated_at`
- `POST /projects/{id}/duplicate` — copy row with new id/name (`… (copy)`) and fresh timestamps
- `DELETE /projects/{id}` — hard delete; 404 if missing

Pydantic request/response models in `backend/app/schemas.py` (or `backend/app/project_schemas.py` if schemas.py is already crowded). Never accept/store API keys.

### Frontend UX

- Add a **home / project browser** as the default view (`activeView: 'home' | 'composer'` in Zustand — avoid adding react-router unless needed).
- Actions: New Project, Open, Rename, Duplicate, Delete (confirm dialog), Back to projects.
- Persist status chip: `unsaved` | `saving` | `saved` | `error`.
- **Autosave**: after meaningful edits (note create/update/delete, JSON editor apply, post-generation load into an open project, rename), debounce ~800–1000ms then `PATCH` composition; cancel/reset debounce on rapid edits; skip if no `currentProjectId` or no dirty diff.
- Manual Save always available; mark dirty via composition revision / last-saved revision comparison (reuse `compositionRevisionKey` pattern).
- On generate success while a project is open: update in-memory composition, set generation metadata from response + current prompt, mark dirty, trigger autosave.
- Create flow: create empty project → open composer → generate/edit → autosave recovers after restart.

### Docker Persistence

- Named volume (e.g. `mukit_project_data:/data`) on the backend service.
- Set `PROJECT_DB_PATH=/data/projects.db`.
- Ensure `/data` exists in the image/entrypoint; document `docker compose down` vs `down -v` so users do not wipe projects accidentally.

### Logging

Structured extras (no secrets):

- Startup: DB path, migration version applied, open-ok/create-ok
- CRUD: project id, name length, composition present, event/track/bar counts, provider/model when present
- Autosave: dirty→saving→saved transitions, debounce skips, conflict/404
- Open migration: previous schema_version → composition.v1, warning count
- Errors: sanitized exception type/message; never keys or full freeform prompts

## Commit Plan

- **Commit 1** (after tasks 1–3): `feat: add SQLite project store and migrations`
- **Commit 2** (after tasks 4–5): `feat: expose project CRUD API with composition migration on open`
- **Commit 3** (after tasks 6–8): `feat: add project browser, save state, and debounced autosave`
- **Commit 4** (after tasks 9–11): `test: cover project persistence API and frontend save flows`
- **Commit 5** (after tasks 12–13): `docs: document local project persistence and Docker volume`

## Tasks

### Phase 1: Database Foundation

- [x] Task 1: Add SQLite project persistence configuration and schema migrations.
  Files: `backend/app/db/__init__.py`, `backend/app/db/connection.py`, `backend/app/db/migrations/001_create_projects.sql` (and runner), `backend/requirements.txt` only if a tiny helper is required (prefer stdlib `sqlite3`); `backend/Dockerfile` / `.gitignore` for `data/`.
  Deliverable: configurable `PROJECT_DB_PATH`; on app startup connect, create parent dirs, apply numbered migrations via `schema_migrations`, expose a connection helper for repositories. Default local path under `backend/data/projects.db`.
  Logging: INFO log DB path and applied migration versions; ERROR on migration failure with sanitized detail; DEBUG connection open/close.

- [x] Task 2: Implement project repository service (CRUD + duplicate).
  Files: `backend/app/services/project_store.py` (or `backend/app/db/project_repository.py`).
  Deliverable: functions/classes for list/create/get/update/duplicate/delete; store composition as JSON text; store generation_provider/model/prompt_json without keys; set UTC `created_at`/`updated_at`; update bumps `updated_at`; duplicate clones composition + generation metadata with new id and renamed title.
  Logging: INFO for create/update/duplicate/delete with project id and counts; DEBUG list size; WARNING missing id; ERROR SQL failures sanitized.

- [x] Task 3: Wire Docker named volume for durable SQLite data.
  Files: `docker-compose.yml`, `backend/Dockerfile` if needed for `/data`.
  Deliverable: backend service mounts a named volume at `/data`, `PROJECT_DB_PATH=/data/projects.db`; local bind-mount of source still works for reload; data survives `docker compose restart` / recreate without `-v`.
  Logging: INFO at startup confirming resolved DB path inside container (no secrets).

### Phase 2: API And Composition Migration

- [x] Task 4: Add Pydantic project schemas and FastAPI routes.
  Files: `backend/app/schemas.py` and/or `backend/app/project_schemas.py`; `backend/app/main.py` (or `backend/app/routers/projects.py` imported by main).
  Deliverable: request/response models for list item, detail, create, patch (rename/save), duplicate; routes listed in Proposed Design; validate composition with `Composition` when present; reject payloads that include API-key-like fields if accidentally sent; 404 for missing projects; 422 for invalid composition JSON.
  Logging: INFO request op + project id; DEBUG payload sizes (JSON length, track/event counts); WARNING validation failures with field reasons; never log keys.

- [x] Task 5: Migrate older composition schema versions on open (and optionally on save).
  Files: `backend/app/services/project_store.py` and/or a thin helper; reuse `backend/app/services/composition_normalizer.py`.
  Deliverable: `GET /projects/{id}` and save paths normalize legacy/non-canonical JSON to `composition.v1` before response/persist; if already canonical, validate only; surface actionable 422 when migration/validation fails; optionally rewrite migrated JSON back to DB on open (log when rewritten).
  Logging: INFO migration path (`legacy` → `composition.v1` or `canonical`); WARNING migration corrections; ERROR unrecoverable invalid stored JSON with project id.

### Phase 3: Frontend Project Browser And Save State

- [x] Task 6: Add project API client and Zustand project/save state.
  Files: `frontend/src/api/musicApi.js` (or `frontend/src/api/projectApi.js`); `frontend/src/store/musicStore.js`.
  Deliverable: client helpers for list/create/get/patch/duplicate/delete; store fields such as `activeView`, `currentProjectId`, `currentProjectName`, `projectList`, `saveStatus`, `lastSavedRevision`, `generationMeta`; actions to load browser, open project into `editedMusicJson`/`generatedMusicJson`, mark dirty on edits, clear project on delete of active project; capture provider/model/prompt into generation meta on successful generate without storing keys.
  Logging: `console.debug`/`info`/`warn`/`error` for view transitions, dirty/saving/saved, open/create failures with sanitized messages.

- [x] Task 7: Build project browser/home UI with rename, duplicate, delete confirmation.
  Files: new `frontend/src/components/ProjectBrowser.jsx` (and small dialog/button pieces if needed); `frontend/src/App.jsx`; `frontend/src/components/Header.jsx` if navigation belongs there.
  Deliverable: home screen lists projects by `updated_at` with name + timestamps; New Project; Open enters composer; Rename inline or dialog; Duplicate; Delete requires explicit confirmation; empty state when no projects; composer gains “Projects” / back navigation and shows current project name + save status.
  Logging: debug user actions (open/rename/duplicate/delete confirm/cancel) with project id only.

- [x] Task 8: Implement manual save, debounced autosave, and saved/saving/unsaved UI.
  Files: `frontend/src/store/musicStore.js`; `frontend/src/components/MusicGenerator.jsx` and/or a small `SaveStatus.jsx`; hook into existing piano-roll / JSON edit paths that already mutate `editedMusicJson`.
  Deliverable: meaningful edits mark unsaved; debounce autosave (~800–1000ms) PATCHes composition (+ generation meta when present); manual Save flushes immediately; status UI clearly shows unsaved/saving/saved/error; autosave no-ops when no open project; after generate into an open project, persist composition and generation metadata; rename persists via PATCH.
  Logging: debug debounce schedule/cancel/fire; info successful save with event counts; warn/error save failures with status/detail.

### Phase 4: Tests

- [x] Task 9: Add backend persistence and route tests.
  Files: `backend/tests/test_project_store.py`, `backend/tests/test_project_routes.py` (names flexible); use temp SQLite path via env/monkeypatch.
  Deliverable: deterministic tests for migrations apply cleanly; create/list/get/rename/save/duplicate/delete; timestamps update on save; generation metadata round-trips without keys; open migrates legacy composition to V1; invalid composition → 422; missing id → 404; Docker path not required in unit tests.
  Logging: assert representative INFO/WARNING extras or messages with `caplog` where useful without brittle prompt text.

- [x] Task 10: Add frontend store/API tests for project flows and save status.
  Files: `frontend/src/store/musicStore.test.js` (extend) and/or `frontend/src/api/projectApi.test.js`; mock `fetch`/axios as existing patterns allow.
  Deliverable: tests cover dirty→saving→saved transitions, autosave debounce scheduling/cancellation (fake timers if practical), open project hydrates composition, delete confirmation gate is UI-level if component-tested lightly or store-level delete clears active project, generation metadata set from generate without keys.
  Logging: tests may spy on console debug for status transitions where valuable; avoid noisy output.

- [x] Task 11: Add an acceptance-oriented persistence regression test (backend-level).
  Files: preferably `backend/tests/test_project_persistence_acceptance.py`.
  Deliverable: simulate create → save edited composition (fixture with several note edits) → reopen from a new DB connection/session → assert composition JSON equality (canonical fields/events unchanged). Documents the Docker restart acceptance criterion at the data layer.
  Logging: INFO-style comments or caplog on reopen summary (bars/tracks/events).

### Phase 5: Documentation And Verification

- [x] Task 12: Document local project persistence, Docker volume, and ops caveats.
  Files: `README.md`; `docs/composition-v1.md` and/or new `docs/project-persistence.md`; `docs/testing.md` if present.
  Deliverable: user-facing steps for create/generate/edit/restart/reopen; `PROJECT_DB_PATH`; named volume behavior; warning that `docker compose down -v` deletes projects; note that API keys stay in env only; mention schema migration on open; list new API endpoints; testing commands for backend/frontend persistence tests.
  Logging: docs mention useful DEBUG/INFO fields and secret handling (no runtime logging in docs task).

- [x] Task 13: Run verification commands and fix failures.
  Files: code/tests/docs touched by earlier tasks.
  Deliverable: `cd backend && ../.venv/bin/python -m pytest` (or project-standard venv) and `cd frontend && npm test` pass; persistence tests included; no test requires provider API keys.
  Logging: capture failures in terminal output and fix until green.

## Acceptance Criteria

- User can create a project, generate music, edit several notes, restart application containers, reopen the project, and recover the edited composition unchanged.
- SQLite stores project metadata, canonical Composition V1 JSON, timestamps, and generation provider/model/prompt snapshot.
- Provider API keys are never stored in the project database.
- Project browser/home supports create, open, rename, duplicate, delete-with-confirmation.
- UI clearly indicates saved / saving / unsaved (and error) state.
- Autosave runs after meaningful changes with debounce; manual save works.
- Opening an older/legacy composition migrates to `composition.v1` (or fails with an actionable error).
- Docker named volume keeps the DB across container restarts.
- Backend and frontend tests cover the persistence flows; docs describe usage and volume caveats.

## Verification Commands

- `cd backend && ../.venv/bin/python -m pytest`
- `cd frontend && npm test`
- Manual Docker check: create/edit project → `docker compose restart` → reopen project and confirm notes unchanged (do not use `down -v`).

## Risks And Mitigations

- Risk: Bind-mounted `./backend` in Compose could confuse DB path if file is written under the bind mount vs named volume. Mitigation: force `PROJECT_DB_PATH=/data/projects.db` in Compose and mount only `/data` as the named volume.
- Risk: Autosave races with rapid edits or delete. Mitigation: debounce + ignore stale responses via revision/request id; disable autosave when project deleted.
- Risk: Invalid historical JSON blocks open. Mitigation: normalize via existing normalizer; return actionable 422; log project id + schema path.
- Risk: Large compositions slow list endpoint. Mitigation: list returns summaries only; full JSON only on GET-by-id.
- Risk: Accidental volume wipe. Mitigation: document `down -v` danger in README/docs.
`)