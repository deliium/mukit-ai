"""HTTP surface for Model Lab status, create, Collab C, and disabled mutates."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.main import app
from app.services.collaboration_store import create_actor, ensure_owner, grant_member
from app.services.project_store import create_project

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "model_lab" / "tiny"


@pytest.fixture
def lab_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    lab_root = tmp_path / "experiments"
    dataset_root = tmp_path / "datasets"
    version = dataset_root / "lab_fixture_tiny" / "lab_fixture_tiny_v1"
    version.parent.mkdir(parents=True)
    shutil.copytree(FIXTURE, version)
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("DATASET_ROOT", str(dataset_root))
    monkeypatch.setenv("MODEL_LAB_ROOT", str(lab_root))
    monkeypatch.setenv("MODEL_LAB_ENABLED", "1")
    monkeypatch.setenv("MODEL_LAB_FAKE", "1")
    monkeypatch.setenv("COLLABORATION_ENABLED", "0")
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reset_database_initialization_cache()
    initialize_database()
    with TestClient(app) as http:
        yield http, db_path, lab_root


def _create_body(**overrides):
    payload = {
        "display_name": "TinyLab-v1",
        "dataset_version_id": "lab_fixture_tiny_v1",
        "tokenizer_preset": "core",
        "architecture_preset": "tiny_lab",
        "seed": 42,
        "train": {"steps": 4, "batch_size": 2, "device": "cpu"},
    }
    payload.update(overrides)
    return payload


def test_status_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("MODEL_LAB_ENABLED", "0")
    monkeypatch.setenv("MODEL_LAB_FAKE", "0")
    monkeypatch.setenv("COLLABORATION_ENABLED", "0")
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reset_database_initialization_cache()
    initialize_database()
    with TestClient(app) as http:
        status = http.get("/model-lab/status")
        assert status.status_code == 200
        body = status.json()
        assert body["enabled"] is False
        assert body["schema_version"] == "model.lab.status.v1"
        listed = http.get("/model-lab/experiments")
        assert listed.status_code == 200
        refused = http.post("/model-lab/experiments", json=_create_body())
        assert refused.status_code == 503
        assert refused.json()["detail"]["code"] == "model_lab_disabled"


def test_fake_create_happy_path(lab_client):
    http, _db, _root = lab_client
    created = http.post("/model-lab/experiments", json=_create_body())
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "complete"
    assert body["id"].startswith("mtlab_")
    assert body["seed"] == 42
    metrics = http.get(f"/model-lab/experiments/{body['id']}/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["musical_quality_claim"] is False


def test_payload_refuse(lab_client):
    http, _db, _root = lab_client
    refused = http.post(
        "/model-lab/experiments",
        json={**_create_body(), "command": "rm -rf /"},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "model_lab_payload_refused"


def test_collab_c_editor_denied_owner_allowed(lab_client, monkeypatch: pytest.MonkeyPatch):
    http, db_path, _root = lab_client
    monkeypatch.setenv("COLLABORATION_ENABLED", "1")
    project = create_project("Lab Gate", composition=None, db_path=db_path)
    ensure_owner(project.id, "local", db_path=db_path)
    editor = create_actor("Editor", db_path=db_path)
    grant_member(project.id, editor.id, "editor", db_path=db_path)

    denied = http.post(
        "/model-lab/experiments",
        json=_create_body(display_name="EditorLab"),
        headers={"X-Mukit-Actor": editor.id},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "collaboration_role_denied"

    allowed = http.post(
        "/model-lab/experiments",
        json=_create_body(display_name="OwnerLab"),
        headers={"X-Mukit-Actor": "local"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["owner_actor_id"] == "local"
