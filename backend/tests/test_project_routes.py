from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.main import app
from tests.test_composition_normalizer import legacy_music_json
from tests.test_composition_schema import valid_composition


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
    # Store legacy JSON through create (normalized), then overwrite DB with raw legacy via patch bypass:
    # create with empty then manually insert via store for true open-migration.
    from app.services import project_store as store

    created = client.post("/projects", json={"name": "Legacy Home"}).json()
    store.update_project(
        created["id"],
        composition=__import__("json").dumps(legacy_music_json()),
    )

    with caplog.at_level("INFO"):
        opened = client.get(f"/projects/{created['id']}")

    assert opened.status_code == 200
    body = opened.json()
    assert body["composition"]["schema_version"] == "composition.v2"
    assert body["composition_migrated"] is True
    assert body["migration_path"] == "legacy"
    assert "Composition migrated to composition.v2" in caplog.text

    # Second open should be canonical after rewrite
    reopen = client.get(f"/projects/{created['id']}")
    assert reopen.status_code == 200
    assert reopen.json()["composition_migrated"] is False
    assert reopen.json()["migration_path"] == "canonical"


def test_open_migrates_v1_composition_and_preserves_notes(client, caplog):
    from app.services import project_store as store

    created = client.post("/projects", json={"name": "V1 Home"}).json()
    v1_payload = valid_composition()
    v1_payload["tracks"][0]["events"][0]["id"] = "keep-me"
    store.update_project(created["id"], composition=__import__("json").dumps(v1_payload))

    with caplog.at_level("WARNING"):
        opened = client.get(f"/projects/{created['id']}")

    assert opened.status_code == 200
    body = opened.json()
    assert body["composition"]["schema_version"] == "composition.v2"
    assert body["composition_migrated"] is True
    assert body["migration_path"] == "v1_to_v2"
    assert body["composition"]["tracks"][0]["events"][0]["id"] == "keep-me"
    assert body["composition"]["tracks"][0]["events"][0]["pitch"] == "C4"
    assert "Composition migrated to composition.v2" in caplog.text

    stored = __import__("json").loads(store.get_project(created["id"]).composition_json)
    assert stored["schema_version"] == "composition.v2"

    reopen = client.get(f"/projects/{created['id']}")
    assert reopen.json()["composition_migrated"] is False
    assert reopen.json()["migration_path"] == "canonical"


def test_failed_migration_does_not_rewrite_stored_json(client, monkeypatch):
    from app.services import project_store as store
    from app.services.composition_migration import CompositionMigrationError

    created = client.post("/projects", json={"name": "Broken Migrate"}).json()
    original = valid_composition()
    store.update_project(created["id"], composition=__import__("json").dumps(original))

    def boom(*_args, **_kwargs):
        raise CompositionMigrationError()

    monkeypatch.setattr("app.services.composition_normalizer.migrate_v1_to_v2", boom)
    opened = client.get(f"/projects/{created['id']}")
    assert opened.status_code == 422
    stored = store.get_project(created["id"]).composition_json
    assert __import__("json").loads(stored)["schema_version"] == "composition.v1"
