"""HTTP tests for rights-governance upsert / get / evaluate / status."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.main import app
from app.rights_governance_schemas import project_allowed_uses
from app.services.project_store import create_project


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("DATASET_ROOT", str(tmp_path / "dataset"))
    reset_database_initialization_cache()
    initialize_database()
    return TestClient(app), db_path


def _entry(source_id: str, *, use_policy: str = "reference_only") -> dict:
    return {
        "schema_version": "rights.registry.entry.v1",
        "entry_id": "rights_" + "ee" * 8,
        "source_kind": "project",
        "source_id": source_id,
        "ownership_class": "licensed",
        "use_policy": use_policy,
        "allowed_uses": project_allowed_uses(use_policy),  # type: ignore[arg-type]
        "license": None if use_policy == "reference_only" else "CC0-1.0",
        "license_spdx": None if use_policy == "reference_only" else "CC0-1.0",
        "verification_status": "verified",
        "entry_version": 1,
        "created_at": "2026-10-03T00:00:00Z",
        "updated_at": "2026-10-03T00:00:00Z",
    }


def test_status_not_on_ready(client):
    http, _ = client
    status = http.get("/rights-governance/status")
    assert status.status_code == 200
    body = status.json()
    assert body["registry_enabled"] is True
    ready = http.get("/ready")
    assert ready.status_code == 200
    ready_text = ready.text
    assert "rights_governance" not in ready_text
    assert "RIGHTS_GOVERNANCE" not in ready_text


def test_upsert_get_evaluate_and_cas(client):
    http, db_path = client
    project = create_project("Rights API", composition=None, db_path=db_path)
    put = http.put(
        f"/rights-governance/entries/project/{project.id}",
        json=_entry(project.id),
    )
    assert put.status_code == 200, put.text
    assert put.json()["use_policy"] == "reference_only"

    got = http.get(f"/rights-governance/entries/project/{project.id}")
    assert got.status_code == 200
    assert got.json()["entry_id"] == put.json()["entry_id"]

    train = http.post(
        "/rights-governance/evaluate",
        json={"use": "train", "entry": put.json()},
    )
    assert train.status_code == 200
    assert train.json()["allowed"] is False
    assert train.json()["code"] == "rights_train_refused"

    ref = http.post(
        "/rights-governance/evaluate",
        json={"use": "reference_analyze", "entry": put.json()},
    )
    assert ref.status_code == 200
    assert ref.json()["allowed"] is True

    # After first put, entry_version is 1; stale expected_version 0 must conflict.
    conflict = http.put(
        f"/rights-governance/entries/project/{project.id}",
        json={**_entry(project.id, use_policy="no_training"), "expected_version": 0},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "rights_cas_conflict"


def test_notes_in_body_refused(client):
    http, db_path = client
    project = create_project("Notes Refuse", composition=None, db_path=db_path)
    payload = _entry(project.id)
    payload["events"] = [{"pitch": "C4"}]
    response = http.put(
        f"/rights-governance/entries/project/{project.id}",
        json=payload,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "embedded_note_material"


def test_delete_project_gcs_via_api(client):
    http, db_path = client
    project = create_project("GC API", composition=None, db_path=db_path)
    assert http.put(
        f"/rights-governance/entries/project/{project.id}",
        json=_entry(project.id),
    ).status_code == 200
    deleted = http.delete(f"/projects/{project.id}")
    assert deleted.status_code in {200, 204}
    missing = http.get(f"/rights-governance/entries/project/{project.id}")
    assert missing.status_code == 404
