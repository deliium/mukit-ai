"""Model Lab multi-experiment compare honesty."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.model_lab_schemas import ModelLabCompareV1, ModelLabError
from app.services import model_lab_service as service

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
    monkeypatch.setenv("MODEL_LAB_MAX_COMPARE", "8")
    reset_database_initialization_cache()
    initialize_database()
    return tmp_path


def _create(name: str, seed: int) -> str:
    exp = service.create_model_lab_experiment(
        {
            "display_name": name,
            "dataset_version_id": "lab_fixture_tiny_v1",
            "tokenizer_preset": "core",
            "architecture_preset": "tiny_lab",
            "seed": seed,
            "train": {"steps": 4, "batch_size": 2, "device": "cpu"},
        }
    )
    return exp.id


def test_compare_two_seeds(lab_env: Path):
    a = _create("CompareA", 42)
    b = _create("CompareB", 7)
    report = service.compare_model_lab_experiments([a, b])
    assert isinstance(report, ModelLabCompareV1)
    assert set(report.experiment_ids) == {a, b}
    assert report.musical_quality_claim is False
    assert report.tokenizer_equal is True
    assert report.architecture_equal is True
    assert "final_loss" in report.metric_deltas or report.sides[0].final_loss is not None
    assert not hasattr(report, "quality_winner")


def test_compare_single_id_refused(lab_env: Path):
    a = _create("CompareSolo", 1)
    with pytest.raises(ModelLabError) as exc:
        service.compare_model_lab_experiments([a])
    assert exc.value.http_status == 422


def test_compare_over_max_refused(lab_env: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MODEL_LAB_MAX_COMPARE", "2")
    ids = [_create(f"Cmp{i}", i + 1) for i in range(3)]
    with pytest.raises(ModelLabError) as exc:
        service.compare_model_lab_experiments(ids)
    assert exc.value.http_status == 422


def test_compare_schema_rejects_quality_winner():
    with pytest.raises(ValidationError):
        ModelLabCompareV1.model_validate(
            {
                "experiment_ids": ["mtlab_" + "ab" * 8, "mtlab_" + "cd" * 8],
                "sides": [
                    {"experiment_id": "mtlab_" + "ab" * 8, "seed": 1},
                    {"experiment_id": "mtlab_" + "cd" * 8, "seed": 2},
                ],
                "musical_quality_claim": False,
                "quality_winner": "mtlab_" + "ab" * 8,
            }
        )
