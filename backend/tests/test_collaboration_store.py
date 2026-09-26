"""Actor seed, owner backfill, and membership store rules."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from alembic import command

from app.collaboration_schemas import CollaborationError
from app.db.connection import _alembic_config, get_connection, reset_database_initialization_cache
from app.services.collaboration_store import (
    LOCAL_ACTOR_ID,
    change_role,
    create_actor,
    ensure_owner,
    get_actor,
    grant_member,
    list_members,
    revoke_member,
)
from app.services.project_store import create_project, get_project


def test_upgrade_from_0013_seeds_local_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog):
    db_path = tmp_path / "legacy.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    command.upgrade(_alembic_config(db_path), "20260926_0013")
    now = "2026-09-26T00:00:00Z"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO projects (
                id, name, composition_json, generation_provider, generation_model,
                generation_prompt_json, created_at, updated_at
            ) VALUES ('proj-legacy', 'Legacy', NULL, NULL, NULL, NULL, ?, ?)
            """,
            (now, now),
        )
    caplog.set_level(logging.INFO)
    command.upgrade(_alembic_config(db_path), "20260926_0014")
    with get_connection(db_path, ensure_initialized=False) as conn:
        actor = conn.execute(
            "SELECT id, display_name FROM collaboration_actors WHERE id = 'local'"
        ).fetchone()
        membership = conn.execute(
            """
            SELECT role FROM project_memberships
            WHERE project_id = 'proj-legacy' AND actor_id = 'local'
            """
        ).fetchone()
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(projects)").fetchall()
        }
        revision_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(project_revisions)").fetchall()
        }
    assert actor["display_name"] == "Local"
    assert membership["role"] == "owner"
    assert "accepted_revision_id" in columns
    assert "actor_id" in revision_columns
    assert any(getattr(record, "membership_count", None) == 1 for record in caplog.records)
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    record = get_project("proj-legacy", db_path=db_path)
    assert record.id == "proj-legacy"


def test_ensure_owner_grant_change_and_revoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "members.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    created = create_project("Piece", db_path=db_path)
    with get_connection(db_path) as conn:
        ensure_owner(created.id, conn=conn)
        ensure_owner(created.id, conn=conn)
    members = list_members(created.id, db_path=db_path)
    assert [member.role for member in members] == ["owner"]
    assert members[0].actor_id == LOCAL_ACTOR_ID

    editor = create_actor("Ada", db_path=db_path)
    granted = grant_member(created.id, editor.id, "editor", db_path=db_path)
    assert granted.role == "editor"
    commenter = create_actor("Bea", db_path=db_path)
    grant_member(created.id, commenter.id, "commenter", db_path=db_path)
    viewer = create_actor("Cy", db_path=db_path)
    grant_member(created.id, viewer.id, "viewer", db_path=db_path)

    with pytest.raises(CollaborationError) as duplicate:
        grant_member(created.id, editor.id, "viewer", db_path=db_path)
    assert duplicate.value.code == "collaboration_member_exists"

    with pytest.raises(CollaborationError) as owner_grant:
        grant_member(created.id, editor.id, "owner", db_path=db_path)
    assert owner_grant.value.code == "collaboration_owner_required"

    changed = change_role(created.id, editor.id, "viewer", db_path=db_path)
    assert changed.role == "viewer"
    with pytest.raises(CollaborationError) as owner_patch:
        change_role(created.id, LOCAL_ACTOR_ID, "editor", db_path=db_path)
    assert owner_patch.value.code == "collaboration_owner_required"

    with pytest.raises(CollaborationError) as owner_delete:
        revoke_member(created.id, LOCAL_ACTOR_ID, db_path=db_path)
    assert owner_delete.value.code == "collaboration_owner_required"
    revoke_member(created.id, viewer.id, db_path=db_path)
    remaining = {member.actor_id for member in list_members(created.id, db_path=db_path)}
    assert viewer.id not in remaining
    assert get_actor(editor.id, db_path=db_path) is not None
