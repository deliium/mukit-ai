# Local Project Persistence

Projects store metadata and canonical `composition.v1` JSON in a backend **SQLite** database so edits survive Docker restarts.

## User workflow

1. Open the app — the **Projects** home screen is the default view.
2. Click **New Project** (or open an existing one).
3. Generate music and/or edit notes on the piano roll / JSON editor.
4. Watch the save chip: `Unsaved` → `Saving…` → `Saved` (or `Save error`).
5. Autosave runs ~900ms after meaningful edits; **Save** flushes immediately.
6. Restart containers with `docker compose restart` (do **not** use `docker compose down -v`).
7. Reopen the same project — edited notes and generation metadata return unchanged.

Compose loads backend secrets from `.env` (see root `.env.example`). Frontend nginx proxies `/projects` to the backend on the same origin in production-local Docker.

Rename, duplicate, and delete (with confirmation) are available on the home screen. The composer bar shows the project name and save status, plus a **Projects** back button.

## Storage and configuration

| Setting | Default (local) | Docker Compose |
|---------|-----------------|----------------|
| `PROJECT_DB_PATH` | `backend/data/projects.db` | `/data/projects.db` |

- Named volume: `mukit_project_data` mounted at `/data` on the backend service.
- Schema migrations run on startup (`schema_migrations` + numbered SQL under `backend/app/db/migrations/`).
- Provider **API keys stay in environment variables only** — never accepted or stored in the project DB.
- Generation metadata stores provider, model id, and a sanitized prompt snapshot.

### Volume caveat

- `docker compose down` keeps the named volume (projects remain).
- `docker compose down -v` **deletes** `mukit_project_data` and permanently wipes saved projects.

## Composition migration on open

Opening or saving a project runs compositions through `normalize_composition_json` / `Composition` validation:

- Already-canonical `composition.v1` → validate only.
- Legacy / non-canonical JSON → migrate to `composition.v1`, then optionally rewrite the DB row.
- Unrecoverable JSON → HTTP `422` with an actionable detail (project id logged; no secrets).

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/projects` | List summaries (`id`, `name`, timestamps, counts, `has_composition`) |
| `POST` | `/projects` | Create project (optional composition + generation meta) |
| `GET` | `/projects/{id}` | Open full project (migrate composition on read) |
| `PATCH` | `/projects/{id}` | Rename and/or save composition + generation meta |
| `POST` | `/projects/{id}/duplicate` | Clone with new id and ` (copy)` name |
| `DELETE` | `/projects/{id}` | Hard delete (`404` if missing) |

Payloads that include API-key-like fields are rejected with `422`.

## Logging

Useful structured fields (never API keys or full freeform prompts):

- Startup: `project_db_path`, applied migration versions
- CRUD: `project_id`, name length, track/event/bar counts, provider/model
- Open migration: `migration_path` (`legacy` / `canonical`), `rewritten`
- Autosave (frontend console): dirty → saving → saved, debounce schedule/cancel/fire
- Errors: sanitized `error_type` / `error_detail`

Control verbosity with `LOG_LEVEL` on the backend.

## Testing

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_project_store.py tests/test_project_routes.py tests/test_project_persistence_acceptance.py
cd frontend && npm test
```

No persistence test requires provider API keys.

See also: [composition-v1.md](./composition-v1.md), [testing.md](./testing.md).
