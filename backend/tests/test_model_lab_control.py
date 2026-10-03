"""Model Lab metrics, evaluate, listening, stop, delete (no resume)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.model_lab_schemas import ModelLabError, ModelLabRuntimeSummaryV1
from app.services import model_lab_service as service
from app.services import model_lab_store as store

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "model_lab" / "tiny"


@pytest.fixture
def lab_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
    reset_database_initialization_cache()
    initialize_database()
    return tmp_path


def _create(**overrides):
    payload = {
        "display_name": "TinyLab-v1",
        "dataset_version_id": "lab_fixture_tiny_v1",
        "tokenizer_preset": "core",
        "architecture_preset": "tiny_lab",
        "seed": 42,
        "train": {"steps": 4, "batch_size": 2, "device": "cpu"},
    }
    payload.update(overrides)
    return service.create_model_lab_experiment(payload)


def test_metrics_and_checkpoints_after_fake_train(lab_env: Path):
    exp = _create()
    metrics = service.get_model_lab_metrics(exp.id)
    assert metrics.musical_quality_claim is False
    assert metrics.rows
    assert metrics.rows[0].loss is not None
    assert metrics.final_val_loss is not None
    checkpoints = service.list_model_lab_checkpoints(exp.id)
    assert checkpoints
    assert checkpoints[0].card_present is True


def test_evaluate_sets_musical_quality_false(lab_env: Path):
    exp = _create(display_name="EvalLab")
    report = service.evaluate_model_lab_experiment(exp.id)
    assert report["musical_quality_claim"] is False
    assert report["schema_version"] == "music_transformer.eval_report.v1"
    assert report["examples"]


def test_listening_returns_prompt_digests(lab_env: Path):
    exp = _create(display_name="ListenLab")
    listening = service.get_model_lab_listening(exp.id)
    assert listening["musical_quality_claim"] is False
    assert listening["prompts"]
    assert "generation_digest" in listening["prompts"][0]


def test_stop_from_running(lab_env: Path):
    store.insert_experiment(
        experiment_id="mtlab_" + "33" * 8,
        display_name="StopMe",
        status="running",
        dataset_version_id="lab_fixture_tiny_v1",
        tokenizer_version="tokenizer.v1",
        tokenizer_vocab_hash_prefix="0123456789abcdef",
        architecture_digest_prefix="fedcba9876543210",
        train_digest_prefix="aabbccddeeff0011",
        seed=1,
        engine="fake",
        runtime=ModelLabRuntimeSummaryV1(device="fake"),
        owner_actor_id=None,
    )
    stopped = service.stop_model_lab_experiment("mtlab_" + "33" * 8)
    assert stopped.status == "stopped"
    # stopped is terminal except delete
    with pytest.raises(ModelLabError):
        service.stop_model_lab_experiment("mtlab_" + "33" * 8)


def test_delete_clears_registry_eligibility(lab_env: Path):
    exp = _create(display_name="DeleteLab")
    registered = store.register_checkpoint(exp.id, checkpoint_step=4)
    assert registered.registry_model_id == f"lab:{exp.id}"
    deleted = service.delete_model_lab_experiment(exp.id)
    assert deleted.status == "deleted"
    with pytest.raises(ModelLabError) as exc:
        service.get_model_lab_experiment(exp.id)
    assert exc.value.code == "model_lab_not_found"
    assert store.get_by_registry_id(f"lab:{exp.id}") is None
    assert not (lab_env / "experiments" / exp.id).exists()


def test_resume_route_absent():
    assert not hasattr(service, "resume_model_lab_experiment")
