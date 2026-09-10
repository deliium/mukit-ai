from pathlib import Path
import json

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.db.connection import get_connection
from app.main import app
from tests.test_composition_normalizer import legacy_music_json
from tests.test_composition_schema import valid_composition
from tests.test_composition_v2_schema import _motif_definition, _motif_track, minimal_v2


def _plant_raw_composition(project_id: str, payload: dict) -> None:
    """Write unvalidated composition JSON for open-migration tests."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE projects
            SET composition_json = ?,
                active_branch_id = NULL,
                current_revision_id = NULL
            WHERE id = ?
            """,
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), project_id),
        )
        conn.execute("DELETE FROM project_branches WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM project_revisions WHERE project_id = ?", (project_id,))


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    with TestClient(app) as test_client:
        yield test_client


def test_project_routes_crud_and_generation_meta(client, caplog):
    with caplog.at_level("INFO"):
        create = client.post(
            "/projects",
            json={
                "name": "Demo",
                "generation": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "prompt": {"genre": "ambient", "mood": "calm", "instruments": ["piano"]},
                },
            },
        )
    assert create.status_code == 201
    project_id = create.json()["id"]
    assert create.json()["generation_provider"] == "openai"
    assert create.json()["generation_model"] == "gpt-4o-mini"
    assert create.json()["generation_prompt"]["genre"] == "ambient"
    assert "api_key" not in create.text

    listed = client.get("/projects")
    assert listed.status_code == 200
    assert len(listed.json()["projects"]) == 1
    assert listed.json()["projects"][0]["has_composition"] is False

    patched = client.patch(
        f"/projects/{project_id}",
        json={"name": "Demo Renamed", "composition": valid_composition()},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Demo Renamed"
    assert patched.json()["composition"]["schema_version"] == "composition.v2"
    assert patched.json()["updated_at"] >= create.json()["updated_at"]

    opened = client.get(f"/projects/{project_id}")
    assert opened.status_code == 200
    assert opened.json()["composition"]["tracks"][0]["id"] == "piano-1"

    duplicated = client.post(f"/projects/{project_id}/duplicate")
    assert duplicated.status_code == 201
    assert duplicated.json()["name"] == "Demo Renamed (copy)"
    dup_id = duplicated.json()["id"]

    deleted = client.delete(f"/projects/{dup_id}")
    assert deleted.status_code == 204
    assert client.get(f"/projects/{dup_id}").status_code == 404


def test_project_routes_404_and_422(client):
    assert client.get("/projects/missing").status_code == 404
    created = client.post("/projects", json={"name": "Bad"})
    project_id = created.json()["id"]
    invalid = client.patch(
        f"/projects/{project_id}",
        json={"composition": {"schema_version": "composition.v1", "tempo": 120}},
    )
    assert invalid.status_code == 422


def test_project_routes_reject_api_keys(client):
    response = client.post(
        "/projects",
        json={"name": "Secrets", "generation": {"provider": "openai", "api_key": "sk-test"}},
    )
    assert response.status_code == 422


def test_open_migrates_legacy_composition(client, caplog):
    from app.services import project_store as store

    created = client.post("/projects", json={"name": "Legacy Home"}).json()
    _plant_raw_composition(created["id"], legacy_music_json())

    with caplog.at_level("INFO"):
        opened = client.get(f"/projects/{created['id']}")

    assert opened.status_code == 200
    body = opened.json()
    assert body["composition"]["schema_version"] == "composition.v2"
    # History bootstrap normalizes during open; subsequent reads are canonical.
    stored = json.loads(store.get_project(created["id"]).composition_json)
    assert stored["schema_version"] == "composition.v2"
    assert "Composition migrated to composition.v2" in caplog.text

    reopen = client.get(f"/projects/{created['id']}")
    assert reopen.status_code == 200
    assert reopen.json()["composition"]["schema_version"] == "composition.v2"


def test_open_migrates_v1_composition_and_preserves_notes(client, caplog):
    from app.services import project_store as store

    created = client.post("/projects", json={"name": "V1 Home"}).json()
    v1_payload = valid_composition()
    v1_payload["tracks"][0]["events"][0]["id"] = "keep-me"
    _plant_raw_composition(created["id"], v1_payload)

    with caplog.at_level("WARNING"):
        opened = client.get(f"/projects/{created['id']}")

    assert opened.status_code == 200
    body = opened.json()
    assert body["composition"]["schema_version"] == "composition.v2"
    assert body["composition"]["tracks"][0]["events"][0]["id"] == "keep-me"
    assert body["composition"]["tracks"][0]["events"][0]["pitch"] == "C4"
    assert "Composition migrated to composition.v2" in caplog.text

    stored = json.loads(store.get_project(created["id"]).composition_json)
    assert stored["schema_version"] == "composition.v2"

    reopen = client.get(f"/projects/{created['id']}")
    assert reopen.json()["composition"]["schema_version"] == "composition.v2"


def test_project_routes_persist_motif_metadata(client):
    payload = minimal_v2(tracks=[_motif_track()], motifs=[_motif_definition()])
    created = client.post("/projects", json={"name": "Motif Project"}).json()
    patched = client.patch(f"/projects/{created['id']}", json={"composition": payload})
    assert patched.status_code == 200
    assert patched.json()["composition"]["motifs"][0]["id"] == "motif-a"

    opened = client.get(f"/projects/{created['id']}")
    assert opened.status_code == 200
    assert opened.json()["composition"]["motifs"] == patched.json()["composition"]["motifs"]

    duplicated = client.post(f"/projects/{created['id']}/duplicate")
    assert duplicated.status_code == 201
    assert duplicated.json()["composition"]["motifs"] == patched.json()["composition"]["motifs"]


def test_failed_migration_does_not_rewrite_stored_json(client, monkeypatch):
    from app.services import project_store as store
    from app.services.composition_migration import CompositionMigrationError

    created = client.post("/projects", json={"name": "Broken Migrate"}).json()
    original = valid_composition()
    _plant_raw_composition(created["id"], original)

    def boom(*_args, **_kwargs):
        raise CompositionMigrationError()

    monkeypatch.setattr("app.services.composition_normalizer.migrate_v1_to_v2", boom)
    opened = client.get(f"/projects/{created['id']}")
    assert opened.status_code == 422
    stored = store.get_project(created["id"]).composition_json
    assert json.loads(stored)["schema_version"] == "composition.v1"


def _assert_history_fields(body: dict):
    assert body["active_branch_id"]
    assert body["active_branch_name"] == "Original"
    assert body["current_revision_id"]
    assert body["current_revision_sequence"] >= 1
    assert body["working_version"] is not None
    assert body["working_fingerprint"]


def test_project_detail_includes_history_fields(client):
    created = client.post("/projects", json={"name": "History Fields", "composition": minimal_v2()}).json()
    _assert_history_fields(created)
    opened = client.get(f"/projects/{created['id']}").json()
    _assert_history_fields(opened)
    duplicated = client.post(f"/projects/{created['id']}/duplicate").json()
    _assert_history_fields(duplicated)
    assert duplicated["active_branch_name"] == "Original"
    assert duplicated["id"] != created["id"]


def test_revision_and_branch_api_lifecycle(client):
    created = client.post(
        "/projects",
        json={"name": "Versions", "composition": minimal_v2()},
    ).json()
    project_id = created["id"]
    branch_id = created["active_branch_id"]
    head_id = created["current_revision_id"]
    working_version = created["working_version"]
    fingerprint = created["working_fingerprint"]

    listed = client.get(f"/projects/{project_id}/revisions")
    assert listed.status_code == 200
    assert len(listed.json()["revisions"]) >= 1
    assert "composition" not in listed.json()["revisions"][0]
    assert "user_instruction" not in listed.json()["revisions"][0]

    page = client.get(f"/projects/{project_id}/revisions", params={"limit": 1})
    assert page.status_code == 200
    assert len(page.json()["revisions"]) == 1

    detail = client.get(f"/projects/{project_id}/revisions/{head_id}")
    assert detail.status_code == 200
    assert detail.json()["revision"]["id"] == head_id
    assert detail.json()["composition"]["schema_version"] == "composition.v2"

    named = client.patch(
        f"/projects/{project_id}/revisions/{head_id}",
        json={"name": "Root keep"},
    )
    assert named.status_code == 200
    assert named.json()["name"] == "Root keep"

    edited = minimal_v2()
    edited["tracks"][0]["events"] = [
        {
            "type": "note",
            "pitch": "D4",
            "start_tick": 0,
            "duration_ticks": 480,
            "velocity": 80,
        }
    ]
    commit = client.post(
        f"/projects/{project_id}/revisions",
        json={
            "branch_id": branch_id,
            "expected_active_branch_id": branch_id,
            "expected_working_version": working_version,
            "expected_head_revision_id": head_id,
            "expected_source_fingerprint": fingerprint,
            "composition": edited,
            "operation_type": "manual-checkpoint",
            "name": "Checkpoint A",
        },
    )
    assert commit.status_code == 200
    body = commit.json()
    assert body["revision_created"] is True
    assert body["composition"]["tracks"][0]["events"][0]["pitch"] == "D4"
    assert "api_key" not in commit.text

    conflict = client.post(
        f"/projects/{project_id}/revisions",
        json={
            "branch_id": branch_id,
            "expected_active_branch_id": branch_id,
            "expected_working_version": working_version,
            "expected_head_revision_id": head_id,
            "expected_source_fingerprint": fingerprint,
            "composition": edited,
            "operation_type": "manual-checkpoint",
        },
    )
    assert conflict.status_code == 409
    detail_409 = conflict.json()["detail"]
    assert detail_409["code"] == "project_revision_conflict"
    assert "composition" not in detail_409
    assert "events" not in json.dumps(detail_409)

    secret = client.post(
        f"/projects/{project_id}/revisions",
        json={
            "branch_id": body["active_branch_id"],
            "expected_active_branch_id": body["active_branch_id"],
            "expected_working_version": body["working_version"],
            "expected_head_revision_id": body["current_revision_id"],
            "expected_source_fingerprint": body["working_fingerprint"],
            "composition": edited,
            "operation_type": "generate-apply",
            "ai": {
                "provider": "fake",
                "user_instruction": "sk-abcdefghijklmnopqrstuvwxyz012345",
            },
        },
    )
    assert secret.status_code == 422

    branches = client.get(f"/projects/{project_id}/branches")
    assert branches.status_code == 200
    assert any(item["is_active"] for item in branches.json()["branches"])

    alt = client.post(
        f"/projects/{project_id}/branches",
        json={"name": "Alt Path", "from_revision_id": head_id},
    )
    assert alt.status_code == 201
    assert alt.json()["name"] == "Alt Path"

    renamed = client.patch(
        f"/projects/{project_id}/branches/{alt.json()['id']}",
        json={"name": "Alt Renamed"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Alt Renamed"

    candidate = minimal_v2()
    candidate["tempo"] = 110
    apply_branch = client.post(
        f"/projects/{project_id}/branches/apply-as-branch",
        json={
            "name": "Chorus B",
            "source_branch_id": body["active_branch_id"],
            "expected_active_branch_id": body["active_branch_id"],
            "expected_working_version": body["working_version"],
            "expected_head_revision_id": body["current_revision_id"],
            "expected_source_fingerprint": body["working_fingerprint"],
            "composition": candidate,
            "operation_type": "development-apply",
        },
    )
    assert apply_branch.status_code == 200
    applied = apply_branch.json()
    assert applied["active_branch_name"] == "Chorus B"
    assert applied["composition"]["tempo"] == 110

    # Original branch head should remain the pre-apply head.
    listed_branches = client.get(f"/projects/{project_id}/branches").json()["branches"]
    original = next(item for item in listed_branches if item["name"] == "Original")
    assert original["head_revision_id"] == body["current_revision_id"]

    checkout = client.post(
        f"/projects/{project_id}/branches/{original['id']}/checkout",
        json={
            "expected_active_branch_id": applied["active_branch_id"],
            "expected_working_version": applied["working_version"],
            "expected_head_revision_id": applied["current_revision_id"],
        },
    )
    assert checkout.status_code == 200
    checked = checkout.json()
    assert checked["active_branch_id"] == original["id"]
    assert checked["composition"]["schema_version"] == "composition.v2"
    _assert_history_fields({**checked, "active_branch_name": "Original"})

    restored = client.post(
        f"/projects/{project_id}/revisions/{head_id}/restore",
        json={
            "branch_id": checked["active_branch_id"],
            "expected_active_branch_id": checked["active_branch_id"],
            "expected_working_version": checked["working_version"],
            "expected_head_revision_id": checked["current_revision_id"],
        },
    )
    assert restored.status_code == 200
    assert restored.json()["revision_created"] is True
    assert restored.json()["operation_type"] == "revision-restore"


def test_autosave_patch_cas_conflict(client):
    created = client.post(
        "/projects",
        json={"name": "CAS", "composition": minimal_v2()},
    ).json()
    edited = minimal_v2()
    edited["tempo"] = 130
    conflict = client.patch(
        f"/projects/{created['id']}",
        json={
            "composition": edited,
            "branch_id": created["active_branch_id"],
            "expected_active_branch_id": created["active_branch_id"],
            "expected_working_version": created["working_version"] + 5,
            "expected_source_fingerprint": created["working_fingerprint"],
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "project_revision_conflict"
