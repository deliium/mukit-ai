"""Registered Lab checkpoints appear on ``/ai/models`` and hybrid generate."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai_runtime.routing import default_operation_routes
from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.main import app

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "model_lab" / "tiny"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
        yield http


def _prompt() -> dict:
    return {
        "genre": "classical",
        "mood": "calm",
        "time_signature": "4/4",
        "tempo_min": 90,
        "tempo_max": 120,
        "key": "C major",
        "instruments": ["piano", "bass"],
        "complexity": "simple",
        "duration_bars": 8,
    }


def test_register_appears_on_ai_models_and_hybrid_generate(client: TestClient):
    created = client.post(
        "/model-lab/experiments",
        json={
            "display_name": "TinyLab-v1",
            "dataset_version_id": "lab_fixture_tiny_v1",
            "tokenizer_preset": "core",
            "architecture_preset": "tiny_lab",
            "seed": 42,
            "train": {"steps": 4, "batch_size": 2, "device": "cpu"},
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    experiment_id = body["id"]
    assert body["registry_model_id"] is None

    unregistered = client.get(
        "/ai/models",
        params={"capability": "symbolic_composer", "status": "ready"},
    )
    assert unregistered.status_code == 200
    assert f"lab:{experiment_id}" not in {m["id"] for m in unregistered.json()["models"]}

    registered = client.post(
        f"/model-lab/experiments/{experiment_id}/register",
        json={"checkpoint_step": body["checkpoint_refs"][0]["step"]},
    )
    assert registered.status_code == 200, registered.text
    registry_id = registered.json()["registry_model_id"]
    assert registry_id == f"lab:{experiment_id}"

    models = client.get(
        "/ai/models",
        params={"capability": "symbolic_composer", "status": "ready"},
    )
    assert models.status_code == 200
    match = next(item for item in models.json()["models"] if item["id"] == registry_id)
    assert match["display_name"] == "TinyLab-v1"
    assert match["runtime"] == "model_lab"

    routes = default_operation_routes()
    assert routes.get("generate_composer") != registry_id

    generated = client.post(
        "/llm/generate-music-json",
        json={
            "prompt": _prompt(),
            "selection": {"provider": "fake", "model": "fake-v1"},
            "options": {
                "pipeline": "hybrid_plan_symbolic",
                "max_retries": 0,
                "composer_model_id": registry_id,
            },
        },
    )
    assert generated.status_code == 200, generated.text
    stages = generated.json()["stages"]
    composer = next(stage for stage in stages if stage["operation"] == "generate_composer")
    assert composer["model_id"] == registry_id
    assert generated.json()["music"]["schema_version"] == "composition.v2"


def test_incomplete_lab_id_unavailable(client: TestClient):
    created = client.post(
        "/model-lab/experiments",
        json={
            "display_name": "NotRegisteredLab",
            "dataset_version_id": "lab_fixture_tiny_v1",
            "tokenizer_preset": "core",
            "architecture_preset": "tiny_lab",
            "seed": 1,
            "train": {"steps": 2, "batch_size": 2, "device": "cpu"},
        },
    )
    assert created.status_code == 200
    experiment_id = created.json()["id"]
    generated = client.post(
        "/llm/generate-music-json",
        json={
            "prompt": _prompt(),
            "selection": {"provider": "fake", "model": "fake-v1"},
            "options": {
                "pipeline": "hybrid_plan_symbolic",
                "max_retries": 0,
                "composer_model_id": f"lab:{experiment_id}",
            },
        },
    )
    # Unregistered lab id is not in the catalog; hybrid should refuse or fall back.
    assert generated.status_code in {200, 422, 503, 404}
    if generated.status_code == 200:
        stages = generated.json().get("stages") or []
        composer = next(
            (stage for stage in stages if stage.get("operation") == "generate_composer"),
            None,
        )
        if composer is not None:
            assert composer["model_id"] != f"lab:{experiment_id}"
