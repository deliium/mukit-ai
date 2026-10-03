"""Acceptance: Goal vector for Model Lab under MODEL_LAB_ENABLED + FAKE."""

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


def _create_body(*, seed: int = 42, name: str = "TinyLab-v1") -> dict:
    return {
        "display_name": name,
        "dataset_version_id": "lab_fixture_tiny_v1",
        "tokenizer_preset": "core",
        "architecture_preset": "tiny_lab",
        "seed": seed,
        "train": {"steps": 4, "batch_size": 2, "device": "cpu"},
    }


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


def test_goal_acceptance_fake_enabled_vector(client: TestClient):
    created = client.post("/model-lab/experiments", json=_create_body())
    assert created.status_code == 200, created.text
    body = created.json()
    experiment_id = body["id"]
    assert experiment_id.startswith("mtlab_")
    assert len(experiment_id) == len("mtlab_") + 16
    assert body["status"] == "complete"
    assert body["dataset_version_id"] == "lab_fixture_tiny_v1"
    assert body["tokenizer_expectation"]["expected_tokenizer_version"] == "tokenizer.v1"
    assert len(body["tokenizer_expectation"]["vocab_hash_prefix"]) >= 8
    assert len(body["architecture_digest_prefix"]) >= 8
    assert len(body["train_digest_prefix"]) >= 8
    assert body["seed"] == 42
    assert body["checkpoint_refs"]
    assert body["checkpoint_refs"][0]["card_present"] is True
    assert body["checkpoint_refs"][0]["weights_present"] is False
    assert body["runtime"]["device"] == "fake"
    assert body["runtime"]["wall_ms"] >= 0
    assert body["evaluation_version"] == "music_transformer.eval_report.v1"

    metrics = client.get(f"/model-lab/experiments/{experiment_id}/metrics")
    assert metrics.status_code == 200, metrics.text
    metrics_body = metrics.json()
    assert metrics_body["musical_quality_claim"] is False
    assert metrics_body["rows"]
    assert metrics_body["rows"][0]["loss"] is not None

    evaluated = client.post(f"/model-lab/experiments/{experiment_id}/evaluate")
    assert evaluated.status_code == 200, evaluated.text
    eval_body = evaluated.json()
    assert eval_body["musical_quality_claim"] is False
    assert eval_body["schema_version"] == "music_transformer.eval_report.v1"
    assert eval_body["examples"]
    assert eval_body["examples"][0].get("id") or eval_body["examples"][0].get("example_id")

    listening = client.get(f"/model-lab/experiments/{experiment_id}/listening")
    assert listening.status_code == 200, listening.text
    listen_body = listening.json()
    assert listen_body["musical_quality_claim"] is False
    assert listen_body["prompts"]
    assert "generation_digest" in listen_body["prompts"][0]

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
    assert default_operation_routes().get("generate_composer") != registry_id

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

    second = client.post(
        "/model-lab/experiments",
        json=_create_body(seed=7, name="TinyLab-seed7"),
    )
    assert second.status_code == 200, second.text
    compared = client.post(
        "/model-lab/compare",
        json={"experiment_ids": [experiment_id, second.json()["id"]]},
    )
    assert compared.status_code == 200, compared.text
    report = compared.json()
    assert report["musical_quality_claim"] is False
    assert "quality_winner" not in report
    assert set(report["experiment_ids"]) == {experiment_id, second.json()["id"]}


def test_disabled_mutates_refused_reads_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    lab_root = tmp_path / "experiments"
    dataset_root = tmp_path / "datasets"
    version = dataset_root / "lab_fixture_tiny" / "lab_fixture_tiny_v1"
    version.parent.mkdir(parents=True)
    shutil.copytree(FIXTURE, version)
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("DATASET_ROOT", str(dataset_root))
    monkeypatch.setenv("MODEL_LAB_ROOT", str(lab_root))
    monkeypatch.setenv("MODEL_LAB_ENABLED", "0")
    monkeypatch.setenv("MODEL_LAB_FAKE", "1")
    monkeypatch.setenv("COLLABORATION_ENABLED", "0")
    reset_database_initialization_cache()
    initialize_database()
    with TestClient(app) as http:
        status = http.get("/model-lab/status")
        assert status.status_code == 200
        assert status.json()["enabled"] is False

        datasets = http.get("/model-lab/datasets")
        assert datasets.status_code == 200

        experiments = http.get("/model-lab/experiments")
        assert experiments.status_code == 200

        refused = http.post("/model-lab/experiments", json=_create_body())
        assert refused.status_code == 503
        assert refused.json()["detail"]["code"] == "model_lab_disabled"


def test_payload_shell_keys_refused(client: TestClient):
    refused = client.post(
        "/model-lab/experiments",
        json={**_create_body(), "command": "rm -rf /"},
    )
    assert refused.status_code in {400, 422}
    detail = refused.json().get("detail")
    if isinstance(detail, dict):
        assert detail.get("code") == "model_lab_payload_refused"


def test_resume_route_absent_on_app():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert not any("/resume" in path for path in paths if path.startswith("/model-lab"))


def test_ready_does_not_include_model_lab(client: TestClient):
    ready = client.get("/ready")
    assert ready.status_code == 200
    payload = ready.json()
    assert "model_lab" not in payload
    assert "model_lab_disabled" not in ready.text
