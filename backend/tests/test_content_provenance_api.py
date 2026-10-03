"""HTTP + storage-root + honesty API tests for content provenance."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.composition_schemas import CompositionV2
from app.content_credentials_settings import load_content_credentials_settings
from app.db.connection import get_connection, reset_database_initialization_cache, run_alembic_upgrade
from app.main import app
from app.project_history_schemas import DurableCommitRequest, RevisionOperationType
from app.services import content_provenance_store as store
from app.services import project_history as history
from app.services import project_store as project_store_mod
from app.storage_root_policy import StorageRootError
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("CONTENT_CREDENTIALS_ENABLED", "0")
    monkeypatch.setenv("CONTENT_CREDENTIALS_FAKE", "0")
    reset_database_initialization_cache()
    run_alembic_upgrade(db_path)
    with TestClient(app) as test_client:
        yield test_client


def _seed_revision(db_path: Path) -> tuple[str, str]:
    created = project_store_mod.create_project(
        "API", composition=minimal_v2(), project_id="prov-api", db_path=db_path
    )
    branches = history.list_branches(created.id, db_path=db_path)
    branch = branches.branches[0]
    payload = deepcopy(minimal_v2())
    payload["tempo"] = 105
    committed = history.commit_revision(
        created.id,
        DurableCommitRequest(
            branch_id=branch.id,
            expected_active_branch_id=branch.id,
            expected_working_version=branch.working_version,
            expected_head_revision_id=branch.head_revision_id,
            expected_source_fingerprint=branch.working_fingerprint,
            composition=CompositionV2.model_validate(payload),
            operation_type=RevisionOperationType.MANUAL_CHECKPOINT,
        ),
        db_path=db_path,
    )
    return created.id, committed.current_revision_id


def test_chain_manifest_download_and_status(client: TestClient, tmp_path: Path) -> None:
    project_id, revision_id = _seed_revision(tmp_path / "projects.db")
    chain = client.get(
        f"/content-provenance/projects/{project_id}/artifacts/composition_revision/{revision_id}/chain"
    )
    assert chain.status_code == 200
    assert chain.json()["records"]
    before = 0
    with get_connection(tmp_path / "projects.db") as conn:
        before = store.count_provenance_records(conn, project_id=project_id)
    download = client.get(
        f"/content-provenance/projects/{project_id}/artifacts/composition_revision/{revision_id}/manifest/download"
    )
    assert download.status_code == 200
    with get_connection(tmp_path / "projects.db") as conn:
        after = store.count_provenance_records(conn, project_id=project_id)
    assert after == before
    status = client.get("/content-provenance/status")
    assert status.json() == {
        "provenance_enabled": True,
        "credentials_enabled": False,
        "credentials_fake": False,
    }


def test_unknown_artifact_404(client: TestClient, tmp_path: Path) -> None:
    project_id, _revision_id = _seed_revision(tmp_path / "projects.db")
    response = client.get(
        f"/content-provenance/projects/{project_id}/artifacts/neural_render/missing/chain"
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "provenance_not_found"


def test_credentials_disabled(client: TestClient, tmp_path: Path) -> None:
    project_id, revision_id = _seed_revision(tmp_path / "projects.db")
    response = client.post(
        f"/content-provenance/projects/{project_id}/artifacts/composition_revision/{revision_id}/credentials"
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "content_credentials_disabled"


def test_credentials_fake_keeps_mukit_internal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("CONTENT_CREDENTIALS_ENABLED", "1")
    monkeypatch.setenv("CONTENT_CREDENTIALS_FAKE", "1")
    monkeypatch.setenv("CONTENT_CREDENTIALS_ROOT", str(tmp_path / "creds"))
    reset_database_initialization_cache()
    run_alembic_upgrade(db_path)
    project_id, revision_id = _seed_revision(db_path)
    # Seed a neural-shaped leaf for supported media.
    with get_connection(db_path) as conn:
        store.insert_provenance_record(
            conn,
            {
                "schema_version": "content.provenance.record.v1",
                "record_id": "cprov_aaaaaaaaaaaaaaaa",
                "project_id": project_id,
                "artifact_kind": "neural_render",
                "artifact_id": "nar_fake",
                "operation": "neural_render",
                "actor_kind": "ai",
                "parent_record_ids": [],
                "parent_artifacts": [
                    {"kind": "composition_revision", "id": revision_id, "fingerprint_prefix": None}
                ],
                "trust_class": "mukit_internal",
                "created_at": "2026-10-03T00:00:00Z",
            },
        )
    with TestClient(app) as client:
        posted = client.post(
            f"/content-provenance/projects/{project_id}/artifacts/neural_render/nar_fake/credentials"
        )
        assert posted.status_code == 200, posted.text
        body = posted.json()
        assert body["fake_mode"] is True
        assert body["attached"] is True
        manifest = client.get(
            f"/content-provenance/projects/{project_id}/artifacts/neural_render/nar_fake/manifest"
        )
        assert manifest.status_code == 200
        man = manifest.json()
        assert man["honesty"]["cryptographic"] is False
        assert man["honesty"]["c2pa"]["fake_mode"] is True
        leaf = next(record for record in man["records"] if record["artifact_id"] == "nar_fake")
        assert leaf["trust_class"] == "mukit_internal"
        assert "c2pa_signed" not in {record["trust_class"] for record in man["records"]}


def test_credentials_root_refuses_dataset_and_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "projects.db"
    dataset = tmp_path / "datasets"
    dataset.mkdir()
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("DATASET_ROOT", str(dataset))
    monkeypatch.setenv("CONTENT_CREDENTIALS_ROOT", str(dataset))
    with pytest.raises(StorageRootError):
        load_content_credentials_settings()
    monkeypatch.setenv("CONTENT_CREDENTIALS_ROOT", str(db_path))
    with pytest.raises(StorageRootError):
        load_content_credentials_settings()
