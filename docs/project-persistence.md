[← Composition V1](composition-v1.md) · [Back to README](../README.md) · [Testing →](testing.md)

# Local Project Persistence

Projects store metadata and operational **`composition.v2`** JSON in a backend **SQLite** database so edits survive Docker restarts. Stored V1 and legacy payloads migrate to V2 on open.

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

Opening or saving a project runs compositions through `normalize_composition_json`, which **always returns validated V2**:

| Source | Behavior |
|--------|----------|
| `composition.v2` | Validate only |
| `composition.v1` | Migrate to V2; optional DB rewrite on success |
| Unversioned legacy top-level `notes` | Legacy → V1 → V2 |
| Unknown explicit `schema_version` | HTTP `422` / domain error — no legacy fallback |
| V1→V2 fidelity mismatch | `v1_v2_migration_fidelity_failed` — row **not** rewritten |

Migration is **source-immutable** and **idempotent**: repeated opens of an already-migrated project do not change note sequences. Failed migration leaves stored JSON untouched.

Unrecoverable JSON → HTTP `422` with actionable detail (project id logged; no secrets).

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/projects` | List summaries (`id`, `name`, timestamps, counts, `has_composition`) |
| `POST` | `/projects` | Create project (optional composition + generation meta) |
| `GET` | `/projects/{id}` | Open full project (migrate composition to V2 on read) |
| `PATCH` | `/projects/{id}` | Rename and/or save composition + generation meta |
| `POST` | `/projects/{id}/duplicate` | Clone with new id and ` (copy)` name |
| `DELETE` | `/projects/{id}` | Hard delete (`404` if missing) |

Payloads that include API-key-like fields are rejected with `422`.

## Logging

Useful structured fields (never API keys or full freeform prompts):

- Startup: `project_db_path`, applied migration versions
- CRUD: `project_id`, name length, track/event/bar counts, provider/model
- Open migration: `source_version`, `target_version` (`composition.v2`), `migration_path`, `rewritten`
- Autosave (frontend console): dirty → saving → saved, debounce schedule/cancel/fire
- Errors: sanitized `error_type` / `error_detail`

Control verbosity with `LOG_LEVEL` on the backend.

## Testing

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_project_store.py tests/test_project_routes.py tests/test_project_persistence_acceptance.py tests/test_composition_v2_migration.py
cd frontend && npm test
```

No persistence test requires provider API keys.

## See Also

- [Composition V2](composition-v2.md) — V2 contract and migrate-on-open fidelity rules
- [Composition Analysis](composition-analysis.md) — derived reports are not stored in `composition_json`
- [Composition V1](composition-v1.md) — V1 parser compatibility
- [Testing](testing.md) — full test matrix including V2 suites
