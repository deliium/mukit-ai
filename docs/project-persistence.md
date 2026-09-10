[← Composition V1](composition-v1.md) · [Back to README](../README.md) · [Testing →](testing.md)

# Local Project Persistence

Projects store metadata and operational **`composition.v2`** JSON in a backend **SQLite** database so edits survive Docker restarts. Stored V1 and legacy payloads migrate to V2 on open. Durable **revision history** and **named branches** sit beside the mutable working draft so AI experimentation can stay preview-first and recoverable.

## User workflow

1. Open the app — the **Projects** home screen is the default view.
2. Click **New Project** (or open an existing one). New and legacy projects bootstrap an `Original` branch and a root revision (empty projects use the explicit null snapshot).
3. Generate music and/or edit notes on the piano roll / JSON editor.
4. Watch the save chip: `Unsaved` → `Saving…` → `Saved` (or `Save error`).
5. Autosave (~900ms) updates the **active branch draft** and materialized `projects.composition_json`. It does **not** create an immutable revision every debounce.
6. Use **Save** / Versions **Save checkpoint** for an explicit durable revision when you want a named restore point.
7. Substantial AI results (generation, region edit, creative motif, develop, arrange, reharmonize) stay as **session candidates** until **Apply** or **Apply as new branch**. Reject discards a candidate without writing history.
8. Open **History** / the **Versions** tab to list metadata-only revisions, compare, audition a historical snapshot, switch branches, name a revision, or **Restore** (creates a new `revision-restore` child; never rewrites past nodes).
9. Restart containers with `docker compose restart` (do **not** use `docker compose down -v`).
10. Reopen the same project — active branch draft, branch names, and revision graph return from SQLite.

Compose loads backend secrets from `.env` (see root `.env.example`). Frontend nginx proxies `/projects` to the backend on the same origin in production-local Docker.

Rename, duplicate, and delete (with confirmation) are available on the home screen. The composer bar shows the project name and save status, plus a **Projects** back button and **History**.

## Snapshot and branch model

| Concept | Behavior |
|---------|----------|
| Snapshot | Content-addressed `composition.snapshot.v1` (sorted compact JSON + SHA-256), zlib-compressed BLOB; empty composition uses fingerprint `composition.snapshot.v1:null` |
| Revision | Immutable node with parent, monotonic `sequence`, `operation_type`, optional AI provider/model, bounded instruction (secret values rejected), affected ranges/tracks, optional label |
| Branch | Named pointer (`Original` by default) with `head_revision_id`, mutable working draft + `working_version` CAS |
| Active project | `active_branch_id` + `current_revision_id`; draft is always materialized in `composition_json` for cheap open/export |

Compare-and-swap preconditions (`branch_id`, `expected_active_branch_id`, `expected_working_version`, and for durable commits `expected_head_revision_id` + source fingerprint) return **`409`** `project_revision_conflict` with bounded IDs only — never silent forks or composition payloads in the error.

Restore requires a clean draft (equals durable head). If dirty, the UI checkpoints first (or the user cancels). Checkout switches context and clears session undo/redo after unsaved work is resolved.

Duplication starts a new project with one root snapshot and `Original`; it does not clone the full source graph. Branch deletion, merge, and snapshot GC are deferred.

## Storage and configuration

| Setting | Default (local) | Docker Compose |
|---------|-----------------|----------------|
| `PROJECT_DB_PATH` | `backend/data/projects.db` | `/data/projects.db` |

- Named volume: `mukit_project_data` mounted at `/data` on the backend service.
- Schema migrations run on startup (`schema_migrations` + numbered SQL under `backend/app/db/migrations/`, including `002_create_composition_history.sql`). Each migration’s DDL and registry insert run in one SQLite transaction.
- Provider **API keys stay in environment variables only** — never accepted or stored in the project DB. Free-text fields that look like credentials are rejected with `422` (`forbidden_secret_value`).
- Generation metadata stores provider, model id, and a sanitized prompt snapshot.
- Arrangement / development / reharmonize / generation / AI region / creative-motif **previews are session-only** and never write revisions. Only an explicitly **applied** (or checkpointed/imported/restored) composition becomes durable history. Catalog `instrument_id` and range metadata are not project columns or V2 fields.

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

Migration is **source-immutable** and **idempotent**: repeated opens of an already-migrated project do not change note sequences. Failed migration leaves stored JSON and history bootstrap unchanged. Lazy `ensure_project_history()` bootstraps `Original` + root revision inside the first history-aware transaction.

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/projects` | List summaries (`id`, `name`, timestamps, counts, `has_composition`) |
| `POST` | `/projects` | Create project (optional composition + generation meta); initializes history |
| `GET` | `/projects/{id}` | Open full project (migrate composition to V2 on read; history fields included) |
| `PATCH` | `/projects/{id}` | Rename and/or autosave draft (+ optional CAS working-version fields) |
| `POST` | `/projects/{id}/duplicate` | Clone with new id and ` (copy)` name; fresh `Original` root only |
| `DELETE` | `/projects/{id}` | Hard delete (cascades revisions/branches; snapshot GC deferred) |
| `GET` | `/projects/{id}/revisions` | Metadata-only paginated history (`branch_id`, `limit`, `before_sequence`) |
| `GET` | `/projects/{id}/revisions/{revision_id}` | One validated snapshot (`CompositionV2 \| null`) + bounded metadata |
| `POST` | `/projects/{id}/revisions` | Durable commit (Save / AI Apply) with CAS + optional `checkpoint_dirty_draft` |
| `PATCH` | `/projects/{id}/revisions/{revision_id}` | Name/rename revision label only |
| `POST` | `/projects/{id}/revisions/{revision_id}/restore` | Restore-as-child on active branch |
| `GET` | `/projects/{id}/branches` | List branches (names, heads, active flag) |
| `POST` | `/projects/{id}/branches` | Create branch from a revision |
| `POST` | `/projects/{id}/branches/{branch_id}/checkout` | Activate branch + rematerialize draft |
| `POST` | `/projects/{id}/branches/apply-as-branch` | Atomic apply candidate onto a new named branch |
| `PATCH` | `/projects/{id}/branches/{branch_id}` | Rename branch (Unicode NFKC+casefold uniqueness) |

Payloads that include API-key-like fields are rejected with `422`. History list responses never include composition JSON.

## Logging

Useful structured fields (never API keys, full instructions, snapshot JSON, or event arrays):

- Startup: `project_db_path`, applied migration versions
- CRUD / history: `project_id`, branch/revision IDs, sequences, operation types, fingerprint prefixes, working_version, compression byte sizes
- Open migration: `source_version`, `target_version` (`composition.v2`), `migration_path`, `rewritten`
- Autosave (frontend console): dirty → saving → saved, debounce schedule/cancel/fire
- Conflicts: `project_revision_conflict` with bounded expected/current IDs only
- Errors: sanitized `error_type` / `error_detail`

Control verbosity with `LOG_LEVEL` on the backend.

## Testing

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_project_store.py \
  tests/test_project_history_store.py \
  tests/test_project_history_schemas.py \
  tests/test_project_history_routes.py \
  tests/test_project_routes.py \
  tests/test_project_persistence_acceptance.py
cd frontend && npm test -- --test-concurrency=1
npx playwright test e2e/project-version-history.spec.js
```

Opt-in Docker restart (composition + multi-branch + restore):

```bash
RUN_DOCKER_ACCEPTANCE=1 ./scripts/v1_docker_acceptance.sh
```

No persistence test requires provider API keys.

## See Also

- [Composition V2](composition-v2.md) — V2 contract and migrate-on-open fidelity rules (history lives outside the V2 document)
- [Composition Development](composition-development.md) — preview-first develop + Apply / Apply-as-branch
- [Composition Arrangement](composition-arrangement.md) — session-only previews; applied V2 only
- [Composition Analysis](composition-analysis.md) — derived reports are not stored in `composition_json`
- [Composition V1](composition-v1.md) — V1 parser compatibility
- [Testing](testing.md) — full test matrix including version-history E2E
