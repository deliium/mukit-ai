"""Tests for composition history schema, bootstrap, and migration atomicity."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.db import initialize_database, reset_database_initialization_cache
from app.db.connection import (
    MIGRATIONS_DIR,
    apply_migrations,
    get_connection,
    split_sql_statements,
)
from app.services import project_store as store
from app.services.composition_snapshot_encoding import (
    NULL_SNAPSHOT_FINGERPRINT,
    decode_composition_snapshot,
)
from app.services.project_composition import ProjectCompositionError
from app.services.project_history_store import (
    ORIGINAL_BRANCH_NAME,
    ProjectHistoryError,
    assert_active_materialization,
    ensure_project_history,
    get_snapshot_composition,
    normalize_branch_name,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "fixtures" / "composition_v2_expressive.json"
)


@pytest.fixture
def project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


@pytest.fixture
def expressive_payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_migration_002_registered_and_tables_exist(project_db):
    with get_connection(project_db) as conn:
        versions = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        triggers = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()
        }

    assert "001_create_projects" in versions
    assert "002_create_composition_history" in versions
    assert "composition_snapshots" in tables
    assert "project_revisions" in tables
    assert "project_branches" in tables
    assert "active_branch_id" in columns
    assert "current_revision_id" in columns
    assert "projects_active_branch_update_check" in triggers
    assert "projects_current_revision_update_check" in triggers


def test_migration_failure_rolls_back_and_retries(tmp_path, monkeypatch):
    db_path = tmp_path / "partial.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_create_projects.sql").write_text(
        (MIGRATIONS_DIR / "001_create_projects.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    boom_sql = """
CREATE TABLE IF NOT EXISTS composition_snapshots (
    fingerprint TEXT PRIMARY KEY,
    encoding_profile TEXT NOT NULL,
    compression_profile TEXT NOT NULL,
    payload_zlib BLOB NOT NULL,
    uncompressed_byte_size INTEGER NOT NULL,
    compressed_byte_size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS this_should_fail (
    id TEXT PRIMARY KEY,
    bad INTEGER NOT NULL,
    CHECK (bad > 0)
);
INSERT INTO this_should_fail (id, bad) VALUES ('x', 0);
"""
    (migrations_dir / "002_create_composition_history.sql").write_text(
        boom_sql,
        encoding="utf-8",
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            apply_migrations(conn, migrations_dir)

        versions = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "001_create_projects" in versions
        assert "002_create_composition_history" not in versions
        assert "composition_snapshots" not in tables
        assert "this_should_fail" not in tables

        # Replace with valid migration and retry.
        (migrations_dir / "002_create_composition_history.sql").write_text(
            (MIGRATIONS_DIR / "002_create_composition_history.sql").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        applied = apply_migrations(conn, migrations_dir)
        assert "002_create_composition_history" in applied
        versions = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "002_create_composition_history" in versions
        assert "composition_snapshots" in tables
    finally:
        conn.close()


def test_split_sql_keeps_trigger_bodies_intact():
    sql = (MIGRATIONS_DIR / "002_create_composition_history.sql").read_text(encoding="utf-8")
    statements = split_sql_statements(sql)
    triggers = [stmt for stmt in statements if stmt.upper().startswith("CREATE TRIGGER")]
    assert len(triggers) == 4
    for trigger in triggers:
        assert "BEGIN" in trigger.upper()
        assert trigger.rstrip().upper().endswith("END;")


def test_empty_project_bootstraps_null_root(project_db):
    created = store.create_project("Empty Song", db_path=project_db)
    assert created.active_branch_id is not None
    assert created.current_revision_id is not None

    with get_connection(project_db) as conn:
        branch = conn.execute(
            """
            SELECT name, normalized_name, working_fingerprint, working_composition_json,
                   head_revision_id
            FROM project_branches
            WHERE id = ?
            """,
            (created.active_branch_id,),
        ).fetchone()
        revision = conn.execute(
            """
            SELECT parent_revision_id, sequence, operation_type, snapshot_fingerprint
            FROM project_revisions
            WHERE id = ?
            """,
            (created.current_revision_id,),
        ).fetchone()
        snap = conn.execute(
            """
            SELECT fingerprint, encoding_profile, compression_profile, payload_zlib,
                   uncompressed_byte_size
            FROM composition_snapshots
            WHERE fingerprint = ?
            """,
            (NULL_SNAPSHOT_FINGERPRINT,),
        ).fetchone()
        assert_active_materialization(conn, created.id)

    assert branch["name"] == ORIGINAL_BRANCH_NAME
    assert branch["normalized_name"] == normalize_branch_name(ORIGINAL_BRANCH_NAME)
    assert branch["working_fingerprint"] == NULL_SNAPSHOT_FINGERPRINT
    assert branch["working_composition_json"] is None
    assert revision["parent_revision_id"] is None
    assert revision["sequence"] == 1
    assert revision["operation_type"] == "project-create"
    assert revision["snapshot_fingerprint"] == NULL_SNAPSHOT_FINGERPRINT
    assert decode_composition_snapshot(
        fingerprint=snap["fingerprint"],
        encoding_profile=snap["encoding_profile"],
        compression_profile=snap["compression_profile"],
        payload_zlib=snap["payload_zlib"],
        uncompressed_byte_size=snap["uncompressed_byte_size"],
    ) is None


def test_v2_project_bootstrap_and_idempotent_ensure(project_db, expressive_payload):
    created = store.create_project(
        "Expressive",
        composition=expressive_payload,
        db_path=project_db,
    )
    assert created.active_branch_id is not None
    first_revision = created.current_revision_id

    with get_connection(project_db) as conn:
        first = ensure_project_history(conn, created.id)
        second = ensure_project_history(conn, created.id)
        composition = get_snapshot_composition(conn, first.snapshot_fingerprint)

    assert first.bootstrapped is False
    assert second.bootstrapped is False
    assert first.current_revision_id == first_revision
    assert second.current_revision_id == first_revision
    assert composition is not None
    assert composition.schema_version == "composition.v2"
    assert composition.bar_count == expressive_payload["bar_count"]


def test_legacy_project_lazy_backfill(project_db, expressive_payload):
    with get_connection(project_db) as conn:
        conn.execute(
            """
            INSERT INTO projects (
                id, name, composition_json, generation_provider, generation_model,
                generation_prompt_json, created_at, updated_at,
                active_branch_id, current_revision_id
            ) VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?, NULL, NULL)
            """,
            (
                "legacy-1",
                "Legacy",
                json.dumps(expressive_payload, ensure_ascii=False, separators=(",", ":")),
                "2026-09-10T00:00:00Z",
                "2026-09-10T00:00:00Z",
            ),
        )

    opened = store.get_project("legacy-1", db_path=project_db)
    assert opened.active_branch_id is not None
    assert opened.current_revision_id is not None

    with get_connection(project_db) as conn:
        revision = conn.execute(
            "SELECT operation_type, sequence FROM project_revisions WHERE id = ?",
            (opened.current_revision_id,),
        ).fetchone()
        branch = conn.execute(
            "SELECT name FROM project_branches WHERE id = ?",
            (opened.active_branch_id,),
        ).fetchone()
        assert_active_materialization(conn, opened.id)

    assert revision["operation_type"] == "migration"
    assert revision["sequence"] == 1
    assert branch["name"] == ORIGINAL_BRANCH_NAME


def test_failed_composition_leaves_history_unbootstrapped(project_db):
    created = store.create_project(
        "Broken",
        composition={"schema_version": "composition.v1", "tracks": [], "sections": []},
        db_path=project_db,
    )
    assert created.active_branch_id is None
    assert created.current_revision_id is None

    with get_connection(project_db) as conn:
        with pytest.raises(ProjectCompositionError):
            ensure_project_history(conn, created.id)
        row = conn.execute(
            "SELECT active_branch_id, current_revision_id FROM projects WHERE id = ?",
            (created.id,),
        ).fetchone()
        revisions = conn.execute(
            "SELECT COUNT(*) AS c FROM project_revisions WHERE project_id = ?",
            (created.id,),
        ).fetchone()["c"]
        branches = conn.execute(
            "SELECT COUNT(*) AS c FROM project_branches WHERE project_id = ?",
            (created.id,),
        ).fetchone()["c"]

    assert row["active_branch_id"] is None
    assert row["current_revision_id"] is None
    assert revisions == 0
    assert branches == 0


def test_project_local_trigger_rejects_cross_project_pointers(project_db, expressive_payload):
    first = store.create_project("A", composition=expressive_payload, db_path=project_db)
    second = store.create_project("B", db_path=project_db)

    with get_connection(project_db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE projects SET active_branch_id = ? WHERE id = ?",
                (first.active_branch_id, second.id),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE projects SET current_revision_id = ? WHERE id = ?",
                (first.current_revision_id, second.id),
            )


def test_delete_cascades_revisions_and_branches(project_db, expressive_payload):
    created = store.create_project(
        "Cascade",
        composition=expressive_payload,
        db_path=project_db,
    )
    fingerprint = None
    with get_connection(project_db) as conn:
        fingerprint = conn.execute(
            "SELECT snapshot_fingerprint FROM project_revisions WHERE id = ?",
            (created.current_revision_id,),
        ).fetchone()["snapshot_fingerprint"]

    store.delete_project(created.id, db_path=project_db)

    with get_connection(project_db) as conn:
        revisions = conn.execute(
            "SELECT COUNT(*) AS c FROM project_revisions WHERE project_id = ?",
            (created.id,),
        ).fetchone()["c"]
        branches = conn.execute(
            "SELECT COUNT(*) AS c FROM project_branches WHERE project_id = ?",
            (created.id,),
        ).fetchone()["c"]
        snapshot = conn.execute(
            "SELECT COUNT(*) AS c FROM composition_snapshots WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()["c"]

    assert revisions == 0
    assert branches == 0
    # Snapshot GC is deferred; content-addressed rows may remain.
    assert snapshot == 1


def test_snapshot_dedupe_across_projects(project_db, expressive_payload):
    first = store.create_project("One", composition=expressive_payload, db_path=project_db)
    second = store.create_project("Two", composition=expressive_payload, db_path=project_db)

    with get_connection(project_db) as conn:
        fps = [
            row["snapshot_fingerprint"]
            for row in conn.execute(
                """
                SELECT snapshot_fingerprint
                FROM project_revisions
                WHERE project_id IN (?, ?)
                ORDER BY project_id
                """,
                (first.id, second.id),
            ).fetchall()
        ]
        snap_count = conn.execute(
            "SELECT COUNT(*) AS c FROM composition_snapshots WHERE fingerprint = ?",
            (fps[0],),
        ).fetchone()["c"]

    assert fps[0] == fps[1]
    assert snap_count == 1


def test_duplicate_initializes_fresh_original_not_full_graph(project_db, expressive_payload):
    source = store.create_project(
        "Source",
        composition=expressive_payload,
        db_path=project_db,
    )
    duplicated = store.duplicate_project(source.id, db_path=project_db)

    assert duplicated.id != source.id
    assert duplicated.active_branch_id is not None
    assert duplicated.current_revision_id is not None
    assert duplicated.active_branch_id != source.active_branch_id
    assert duplicated.current_revision_id != source.current_revision_id

    with get_connection(project_db) as conn:
        source_count = conn.execute(
            "SELECT COUNT(*) AS c FROM project_revisions WHERE project_id = ?",
            (source.id,),
        ).fetchone()["c"]
        dup_count = conn.execute(
            "SELECT COUNT(*) AS c FROM project_revisions WHERE project_id = ?",
            (duplicated.id,),
        ).fetchone()["c"]
        dup_branch = conn.execute(
            "SELECT name FROM project_branches WHERE id = ?",
            (duplicated.active_branch_id,),
        ).fetchone()

    assert source_count == 1
    assert dup_count == 1
    assert dup_branch["name"] == ORIGINAL_BRANCH_NAME


def test_ensure_missing_project_raises(project_db):
    with get_connection(project_db) as conn:
        with pytest.raises(ProjectHistoryError, match="not found"):
            ensure_project_history(conn, "missing-project")

def _branch_state(conn, project_id: str):
    return conn.execute(
        """
        SELECT
            p.active_branch_id,
            p.current_revision_id,
            b.working_version,
            b.working_fingerprint,
            b.head_revision_id
        FROM projects p
        JOIN project_branches b ON b.id = p.active_branch_id
        WHERE p.id = ?
        """,
        (project_id,),
    ).fetchone()


def test_two_writers_draft_cas_one_wins(project_db, expressive_payload):
    from app.services.project_history_store import (
        ProjectRevisionConflictError,
        save_branch_draft,
    )

    created = store.create_project("CAS", composition=expressive_payload, db_path=project_db)
    with get_connection(project_db) as conn:
        state = _branch_state(conn, created.id)
        branch_id = state["active_branch_id"]
        version = int(state["working_version"])
        fp = state["working_fingerprint"]

        first = save_branch_draft(
            conn,
            created.id,
            branch_id=branch_id,
            expected_active_branch_id=branch_id,
            expected_working_version=version,
            expected_source_fingerprint=fp,
            composition=expressive_payload,
        )
        assert first.working_version == version + 1

        with pytest.raises(ProjectRevisionConflictError) as exc_info:
            save_branch_draft(
                conn,
                created.id,
                branch_id=branch_id,
                expected_active_branch_id=branch_id,
                expected_working_version=version,
                expected_source_fingerprint=fp,
                composition=expressive_payload,
            )
        detail = exc_info.value.bounded_detail()
        assert detail["code"] == "project_revision_conflict"
        assert "composition" not in detail
        assert detail["expected_working_version"] == version
        assert detail["current_working_version"] == version + 1


def test_rename_does_not_clobber_composition(project_db, expressive_payload):
    created = store.create_project(
        "Rename Me",
        composition=expressive_payload,
        db_path=project_db,
    )
    before = store.get_project(created.id, db_path=project_db)
    renamed = store.update_project(created.id, name="Renamed", db_path=project_db)
    assert renamed.name == "Renamed"
    assert renamed.composition_json == before.composition_json
    assert renamed.active_branch_id == before.active_branch_id
    assert renamed.current_revision_id == before.current_revision_id

    with get_connection(project_db) as conn:
        revisions = conn.execute(
            "SELECT COUNT(*) AS c FROM project_revisions WHERE project_id = ?",
            (created.id,),
        ).fetchone()["c"]
        version = conn.execute(
            "SELECT working_version FROM project_branches WHERE id = ?",
            (created.active_branch_id,),
        ).fetchone()["working_version"]
    assert revisions == 1
    assert version == 0


def test_noop_manual_checkpoint(project_db, expressive_payload):
    from app.services.project_history_store import commit_durable_revision

    created = store.create_project(
        "Noop",
        composition=expressive_payload,
        db_path=project_db,
    )
    with get_connection(project_db) as conn:
        state = _branch_state(conn, created.id)
        result = commit_durable_revision(
            conn,
            created.id,
            branch_id=state["active_branch_id"],
            expected_active_branch_id=state["active_branch_id"],
            expected_working_version=int(state["working_version"]),
            expected_head_revision_id=state["head_revision_id"],
            expected_source_fingerprint=state["working_fingerprint"],
            composition=expressive_payload,
            operation_type="manual-checkpoint",
        )
        assert result.revision_created is False
        assert result.created_revision_ids == ()
        assert result.head_revision_id == state["head_revision_id"]
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM project_revisions WHERE project_id = ?",
            (created.id,),
        ).fetchone()["c"]
    assert count == 1


def test_dirty_draft_ai_apply_creates_two_revisions(project_db, expressive_payload):
    from copy import deepcopy

    from app.services.project_history_store import (
        commit_durable_revision,
        save_branch_draft,
    )

    created = store.create_project(
        "AI Apply",
        composition=expressive_payload,
        db_path=project_db,
    )
    dirty = deepcopy(expressive_payload)
    dirty["tempo"] = 112
    candidate = deepcopy(expressive_payload)
    candidate["tempo"] = 128

    with get_connection(project_db) as conn:
        state = _branch_state(conn, created.id)
        draft = save_branch_draft(
            conn,
            created.id,
            branch_id=state["active_branch_id"],
            expected_active_branch_id=state["active_branch_id"],
            expected_working_version=int(state["working_version"]),
            expected_source_fingerprint=state["working_fingerprint"],
            composition=dirty,
        )
        result = commit_durable_revision(
            conn,
            created.id,
            branch_id=state["active_branch_id"],
            expected_active_branch_id=state["active_branch_id"],
            expected_working_version=draft.working_version,
            expected_head_revision_id=state["head_revision_id"],
            expected_source_fingerprint=draft.working_fingerprint,
            composition=candidate,
            operation_type="arrangement-apply",
            checkpoint_dirty_draft=True,
        )
        rows = conn.execute(
            """
            SELECT sequence, operation_type
            FROM project_revisions
            WHERE project_id = ?
            ORDER BY sequence
            """,
            (created.id,),
        ).fetchall()
        assert_active_materialization(conn, created.id)

    assert result.revision_created is True
    assert len(result.created_revision_ids) == 2
    assert [row["operation_type"] for row in rows] == [
        "project-create",
        "pre-ai-checkpoint",
        "arrangement-apply",
    ]
    assert [row["sequence"] for row in rows] == [1, 2, 3]


def test_restore_requires_clean_draft_and_creates_child(project_db, expressive_payload):
    from copy import deepcopy

    from app.services.project_history_store import (
        ProjectHistoryError,
        commit_durable_revision,
        restore_revision,
        save_branch_draft,
    )

    created = store.create_project(
        "Restore",
        composition=expressive_payload,
        db_path=project_db,
    )
    altered = deepcopy(expressive_payload)
    altered["tempo"] = 90

    with get_connection(project_db) as conn:
        state = _branch_state(conn, created.id)
        root_revision = state["current_revision_id"]
        draft = save_branch_draft(
            conn,
            created.id,
            branch_id=state["active_branch_id"],
            expected_active_branch_id=state["active_branch_id"],
            expected_working_version=int(state["working_version"]),
            composition=altered,
        )
        with pytest.raises(ProjectHistoryError, match="clean branch draft"):
            restore_revision(
                conn,
                created.id,
                revision_id=root_revision,
                branch_id=state["active_branch_id"],
                expected_active_branch_id=state["active_branch_id"],
                expected_working_version=draft.working_version,
                expected_head_revision_id=state["head_revision_id"],
            )

        checkpoint = commit_durable_revision(
            conn,
            created.id,
            branch_id=state["active_branch_id"],
            expected_active_branch_id=state["active_branch_id"],
            expected_working_version=draft.working_version,
            expected_head_revision_id=state["head_revision_id"],
            expected_source_fingerprint=draft.working_fingerprint,
            composition=altered,
            operation_type="manual-checkpoint",
        )
        restored = restore_revision(
            conn,
            created.id,
            revision_id=root_revision,
            branch_id=state["active_branch_id"],
            expected_active_branch_id=state["active_branch_id"],
            expected_working_version=checkpoint.working_version,
            expected_head_revision_id=checkpoint.head_revision_id,
        )
        ops = [
            row["operation_type"]
            for row in conn.execute(
                """
                SELECT operation_type FROM project_revisions
                WHERE project_id = ? ORDER BY sequence
                """,
                (created.id,),
            ).fetchall()
        ]
        assert_active_materialization(conn, created.id)

    assert restored.operation_type == "revision-restore"
    assert ops == ["project-create", "manual-checkpoint", "revision-restore"]


def test_checkout_and_apply_as_new_branch(project_db, expressive_payload):
    from copy import deepcopy

    from app.services.project_history_store import (
        apply_as_new_branch,
        checkout_branch,
    )

    created = store.create_project(
        "Branches",
        composition=expressive_payload,
        db_path=project_db,
    )
    candidate = deepcopy(expressive_payload)
    candidate["tempo"] = 140

    with get_connection(project_db) as conn:
        state = _branch_state(conn, created.id)
        source_head = state["head_revision_id"]
        applied = apply_as_new_branch(
            conn,
            created.id,
            source_branch_id=state["active_branch_id"],
            expected_active_branch_id=state["active_branch_id"],
            expected_working_version=int(state["working_version"]),
            expected_head_revision_id=source_head,
            expected_source_fingerprint=state["working_fingerprint"],
            branch_name="Darker harmony",
            composition=candidate,
            operation_type="development-apply",
        )
        source_after = conn.execute(
            "SELECT head_revision_id FROM project_branches WHERE id = ?",
            (state["active_branch_id"],),
        ).fetchone()
        assert source_after["head_revision_id"] == source_head
        assert applied.new_branch_id != state["active_branch_id"]

        # Switch back to Original
        current = _branch_state(conn, created.id)
        checked = checkout_branch(
            conn,
            created.id,
            branch_id=state["active_branch_id"],
            expected_active_branch_id=current["active_branch_id"],
            expected_working_version=int(current["working_version"]),
            expected_head_revision_id=current["head_revision_id"],
        )
        assert checked.active_branch_id == state["active_branch_id"]
        assert checked.head_revision_id == source_head
        assert_active_materialization(conn, created.id)


def test_stale_checkout_conflicts(project_db, expressive_payload):
    from app.services.project_history_store import (
        ProjectRevisionConflictError,
        apply_as_new_branch,
        checkout_branch,
        save_branch_draft,
    )
    from copy import deepcopy

    created = store.create_project(
        "Stale",
        composition=expressive_payload,
        db_path=project_db,
    )
    alt = deepcopy(expressive_payload)
    alt["tempo"] = 99

    with get_connection(project_db) as conn:
        state = _branch_state(conn, created.id)
        applied = apply_as_new_branch(
            conn,
            created.id,
            source_branch_id=state["active_branch_id"],
            expected_active_branch_id=state["active_branch_id"],
            expected_working_version=int(state["working_version"]),
            expected_head_revision_id=state["head_revision_id"],
            expected_source_fingerprint=state["working_fingerprint"],
            branch_name="Alt",
            composition=alt,
            operation_type="development-apply",
        )
        # Stale checkout using pre-apply active-branch preconditions.
        with pytest.raises(ProjectRevisionConflictError):
            checkout_branch(
                conn,
                created.id,
                branch_id=state["active_branch_id"],
                expected_active_branch_id=state["active_branch_id"],
                expected_working_version=int(state["working_version"]),
                expected_head_revision_id=state["head_revision_id"],
            )
        assert applied.new_branch_name == "Alt"


def test_inactive_branch_draft_rejected(project_db, expressive_payload):
    from copy import deepcopy

    from app.services.project_history_store import (
        ProjectRevisionConflictError,
        apply_as_new_branch,
        save_branch_draft,
    )

    created = store.create_project(
        "Inactive",
        composition=expressive_payload,
        db_path=project_db,
    )
    alt = deepcopy(expressive_payload)
    alt["tempo"] = 80

    with get_connection(project_db) as conn:
        state = _branch_state(conn, created.id)
        source_branch = state["active_branch_id"]
        apply_as_new_branch(
            conn,
            created.id,
            source_branch_id=source_branch,
            expected_active_branch_id=source_branch,
            expected_working_version=int(state["working_version"]),
            expected_head_revision_id=state["head_revision_id"],
            expected_source_fingerprint=state["working_fingerprint"],
            branch_name="Other",
            composition=alt,
            operation_type="development-apply",
        )
        with pytest.raises(ProjectRevisionConflictError, match="Active branch mismatch|inactive"):
            save_branch_draft(
                conn,
                created.id,
                branch_id=source_branch,
                expected_active_branch_id=source_branch,
                expected_working_version=0,
                composition=expressive_payload,
            )


def test_injected_failure_rolls_back_durable_commit(project_db, expressive_payload):
    from copy import deepcopy

    from app.services.project_history_store import (
        _insert_revision,
        commit_durable_revision,
        save_branch_draft,
    )
    import app.services.project_history_store as history_mod

    created = store.create_project(
        "Rollback",
        composition=expressive_payload,
        db_path=project_db,
    )
    dirty = deepcopy(expressive_payload)
    dirty["tempo"] = 101

    original_insert = _insert_revision

    def boom(*args, **kwargs):
        raise RuntimeError("injected failure")

    try:
        with get_connection(project_db) as conn:
            state = _branch_state(conn, created.id)
            draft = save_branch_draft(
                conn,
                created.id,
                branch_id=state["active_branch_id"],
                expected_active_branch_id=state["active_branch_id"],
                expected_working_version=int(state["working_version"]),
                composition=dirty,
            )
            before = conn.execute(
                "SELECT COUNT(*) AS c FROM project_revisions WHERE project_id = ?",
                (created.id,),
            ).fetchone()["c"]
            history_mod._insert_revision = boom  # type: ignore[assignment]
            with pytest.raises(RuntimeError, match="injected failure"):
                commit_durable_revision(
                    conn,
                    created.id,
                    branch_id=state["active_branch_id"],
                    expected_active_branch_id=state["active_branch_id"],
                    expected_working_version=draft.working_version,
                    expected_head_revision_id=state["head_revision_id"],
                    expected_source_fingerprint=draft.working_fingerprint,
                    composition=dirty,
                    operation_type="manual-checkpoint",
                )
            # pytest.raises swallows the error; explicitly roll back the draft too.
            conn.rollback()
            after = conn.execute(
                "SELECT COUNT(*) AS c FROM project_revisions WHERE project_id = ?",
                (created.id,),
            ).fetchone()["c"]
            version = conn.execute(
                "SELECT working_version FROM project_branches WHERE id = ?",
                (created.active_branch_id,),
            ).fetchone()["working_version"]
    finally:
        history_mod._insert_revision = original_insert  # type: ignore[assignment]

    assert after == before == 1
    assert version == 0
