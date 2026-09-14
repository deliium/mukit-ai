# Implementation Plan: Alembic Schema Migrations

Branch: main (git.create_branches=false; no feature branch)
Created: 2026-09-14

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Roadmap Linkage
Milestone: "none"
Rationale: "Skipped by user (defaults); persistence milestone already complete — this is tooling refactor"

## Commit Plan
- **Commit 1** (after tasks 1–3): `chore(db): add Alembic and replace SQL migration runner`
- **Commit 2** (after tasks 4–6): `test(db): cover Alembic upgrades; document wipe + Alembic workflow`

## Tasks

### Phase 1: Dependencies & Alembic scaffold

- [x] **Task 1: Add Alembic/SQLAlchemy deps and scaffold config**
  - Add `alembic` and `sqlalchemy` (pin compatible versions for Python 3.14) to `backend/requirements.txt` and `backend/requirements-modern.txt` if that file mirrors runtime deps.
  - Scaffold under `backend/`:
    - `alembic.ini` (script location pointing at `app/db/alembic` or `alembic/`)
    - `backend/app/db/alembic/env.py` — read `PROJECT_DB_PATH` (same resolution as `get_project_db_path`), use SQLite URL `sqlite:////abs/path`, run in offline/online modes as needed
    - `backend/app/db/alembic/script.py.mako` + `versions/` package
  - Prefer **Alembic for DDL only**; do **not** rewrite `project_store` / `project_history_store` onto ORM models. Optional: empty/minimal SQLAlchemy `MetaData` in env for autogenerate later; initial revision may use `op.execute` / explicit `op.create_*` mirroring current DDL.
  - Document local CLI: `cd backend && alembic upgrade head` (with `PROJECT_DB_PATH` set).
  - **LOGGING:** DEBUG for resolved DB URL path (never log secrets); INFO when Alembic env configures engine; ERROR on env/config failure with `error_type` / truncated detail.

### Phase 2: Baseline revision & app wiring

- [x] **Task 2: Create single baseline revision for current final schema** (depends on 1)
  - Because existing DBs will be **deleted** before apply: one revision that creates the **final** schema (equivalent of today’s `001` + `002`), not a historical two-step with `ALTER TABLE`.
  - Include:
    - `projects` with all columns including `active_branch_id`, `current_revision_id`
    - `composition_snapshots`, `project_revisions`, `project_branches` with FKs/indexes/CHECKs matching `002_create_composition_history.sql`
    - The four SQLite triggers that enforce branch/revision locality on `projects`
  - Prefer creating `projects` complete in one `CREATE TABLE` (no post-create `ALTER`) for a clean greenfield DB.
  - Remove obsolete `backend/app/db/migrations/*.sql` and the custom registry table concept (`schema_migrations`).
  - **LOGGING:** INFO when revision applies (revision id); DEBUG for major op groups (tables/indexes/triggers); ERROR with revision id + stage on failure.

- [x] **Task 3: Replace custom SQL runner with Alembic upgrade on init** (depends on 2)
  - Refactor `backend/app/db/connection.py`:
    - Keep `get_project_db_path`, `ensure_database`, `initialize_database`, `get_connection`, cache helpers.
    - Remove `split_sql_statements`, `list_migration_files`, `apply_migrations`, `_SCHEMA_MIGRATIONS_DDL`, and SQL-dir constants tied to numbered `*.sql` files.
    - On init, programmatically run Alembic `upgrade(head)` against the resolved SQLite path (same transactional/idempotent expectations: empty DB → full schema; already-at-head → no-op).
    - Preserve foreign_keys pragma and connection semantics used by stores.
  - Update `backend/app/db/__init__.py` exports (drop any public `apply_migrations` if exported; today it is imported from `connection` in tests only).
  - Call sites `main.py` / `ready.py` should keep using `ensure_database()` unchanged.
  - **LOGGING:** keep INFO `Initializing project database` / `Project database ready`; replace applied SQL version list with Alembic head/revision id(s); DEBUG skip-when-cached; ERROR on upgrade failure without dumping SQL blobs.

### Phase 3: Tests

- [x] **Task 4: Update migration/persistence tests for Alembic** (depends on 3)
  - `backend/tests/test_project_store.py`: assert schema via `alembic_version` (or inspected tables) instead of `schema_migrations` / `001_*` / `002_*` stems; keep table presence checks (`projects`, history tables).
  - `backend/tests/test_project_history_store.py`:
    - Keep history schema/trigger assertions after `initialize_database()`.
    - Remove or rewrite `test_migration_failure_rolls_back_and_retries` and `test_split_sql_keeps_trigger_bodies_intact` — they target the custom SQL splitter/runner. Replace with Alembic-focused coverage: upgrade on empty DB creates triggers/tables; second init is idempotent; optional deliberate bad revision in a temp alembic versions dir only if practical without fragile packaging.
  - Grep for `schema_migrations`, `apply_migrations`, `split_sql_statements`, `001_create_projects` in tests and fix callers.
  - **LOGGING:** assert key INFO lines still appear on init where existing tests check logs; add DEBUG-friendly messages for idempotent re-init.

### Phase 4: Docs & agent context

- [x] **Task 5: Document wipe + Alembic workflow** (depends on 3)
  - Update `docs/project-persistence.md`: replace numbered SQL / `schema_migrations` with Alembic (`alembic_version`, location of revisions, startup `upgrade head`).
  - Explicit **breaking note**: delete existing `projects.db` (local `backend/data/projects.db` and/or Docker volume `mukit_project_data` via `docker compose down -v`) before first Alembic apply — no upgrade path from the old registry.
  - Touch README persistence bullet, `AGENTS.md` DB line/structure, `.ai-factory/DESCRIPTION.md`, and architecture folder blurb if they still say “numbered SQL migrations”.
  - **LOGGING:** N/A for docs; mention operators should rely on app INFO logs for migration apply confirmation.

- [x] **Task 6: Verify quality gate on persistence paths** (depends on 4, 5)
  - Run focused backend tests: `test_project_store.py`, `test_project_history_store.py`, and a thin slice of project route/history tests that call `initialize_database`.
  - Confirm app startup still initializes DB via `ensure_database` without referencing deleted SQL files.
  - **LOGGING:** confirm failure paths log ERROR with path + exception type; success logs include revision id at INFO.

## Design notes (for implementer)

| Topic | Decision |
|-------|----------|
| ORM scope | Alembic + SQLAlchemy engine/metadata for migrations only; keep raw `sqlite3` in stores |
| Old DB | Intentionally wiped; no `schema_migrations` → `alembic_version` stamp/migrate |
| Baseline shape | One revision = final schema (projects + history + triggers) |
| Autogenerate | Optional later; initial revision hand-written from existing DDL |
| Docker | No compose change required beyond documenting volume wipe |

## Out of scope

- Migrating application CRUD to SQLAlchemy ORM/models
- Preserving or converting existing production SQLite files
- Frontend changes
