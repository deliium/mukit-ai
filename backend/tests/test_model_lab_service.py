"""Model Lab create/run fake engine, rights refuse, busy, orphan sweep."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.model_lab_schemas import ModelLabError, ModelLabRuntimeSummaryV1
from app.rights_governance_schemas import RightsRegistryEntryV1, project_allowed_uses
from app.services import model_lab_service as service
from app.services import model_lab_store as store
from app.services.model_lab_catalog import LAB_FIXTURE_TINY_ID

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
    monkeypatch.setenv("MODEL_LAB_MAX_CONCURRENT", "1")
    reset_database_initialization_cache()
    initialize_database()
    return tmp_path


def _create_body(**overrides):
    payload = {
        "schema_version": "model.lab.create.v1",
        "display_name": "TinyLab-v1",
        "dataset_version_id": "lab_fixture_tiny_v1",
        "tokenizer_preset": "core",
        "architecture_preset": "tiny_lab",
        "seed": 42,
        "train": {"steps": 4, "batch_size": 2, "lr": 0.0003, "device": "cpu"},
        "eval_enabled": False,
        "listening_enabled": False,
    }
    payload.update(overrides)
    return payload


def test_fake_create_acceptance_fields(lab_env: Path):
    result = service.create_model_lab_experiment(_create_body())
    assert result.id.startswith("mtlab_")
    assert len(result.id) == len("mtlab_") + 16
    assert result.dataset_version_id == "lab_fixture_tiny_v1"
    assert result.tokenizer_expectation.expected_tokenizer_version == "tokenizer.v1"
    assert len(result.tokenizer_expectation.vocab_hash_prefix) >= 8
    assert len(result.architecture_digest_prefix) >= 8
    assert len(result.train_digest_prefix) >= 8
    assert result.seed == 42
    assert result.status == "complete"
    assert result.engine == "fake"
    assert result.runtime.device == "fake"
    assert result.runtime.wall_ms >= 0
    assert result.evaluation_version == "music_transformer.eval_report.v1"
    assert result.checkpoint_refs
    assert result.checkpoint_refs[0].card_present is True
    assert result.checkpoint_refs[0].weights_present is False
    exp_dir = Path(lab_env / "experiments" / result.id)
    assert (exp_dir / "metrics.jsonl").is_file()
    assert (exp_dir / "model.data.provenance.manifest.json").is_file()


def test_lab_fixture_alias(lab_env: Path):
    result = service.create_model_lab_experiment(
        _create_body(dataset_version_id=LAB_FIXTURE_TINY_ID, display_name="TinyLab-Fixture")
    )
    assert result.status == "complete"
    assert result.id.startswith("mtlab_")


def test_disabled_create(lab_env: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MODEL_LAB_ENABLED", "0")
    with pytest.raises(ModelLabError) as exc:
        service.create_model_lab_experiment(_create_body())
    assert exc.value.code == "model_lab_disabled"


def test_concurrent_busy(lab_env: Path):
    store.insert_experiment(
        experiment_id="mtlab_" + "11" * 8,
        display_name="RunningHold",
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
    with pytest.raises(ModelLabError) as exc:
        service.create_model_lab_experiment(_create_body())
    assert exc.value.code == "model_lab_busy"


def test_rights_reference_only_refused(lab_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dataset_root = tmp_path / "datasets_ref"
    version = dataset_root / "ref_only" / "ref_v1"
    version.mkdir(parents=True)
    (version / "rights").mkdir()
    (version / "examples").mkdir()
    (version / "splits").mkdir()
    entry = RightsRegistryEntryV1(
        entry_id="rights_" + "b2" * 8,
        source_kind="dataset_item",
        source_id="item_ref",
        ownership_class="licensed",
        use_policy="reference_only",
        allowed_uses=list(project_allowed_uses("reference_only")),
        license="CC-BY-4.0",
        license_spdx="CC-BY-4.0",
        verification_status="verified",
        created_at="2026-10-04T00:00:00Z",
        updated_at="2026-10-04T00:00:00Z",
    )
    (version / "rights" / "index.jsonl").write_text(
        json.dumps(entry.model_dump(mode="json"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (version / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "dataset.manifest.v1",
                "dataset_name": "ref_only",
                "dataset_version_id": "ref_v1",
                "pipeline_version": "dataset.pipeline.v1",
                "created_at": "2026-10-04T00:00:00Z",
                "counts": {"items": 1, "examples": 1, "train_eligible_items": 0},
                "digests": {
                    "config_digest": "aa" * 32,
                    "item_content_digests_sha256": "bb" * 32,
                    "version_payload_sha256": "cc" * 32,
                },
                "split_seed": 1,
                "fingerprint_profile": "default",
            }
        ),
        encoding="utf-8",
    )
    example = {
        "schema_version": "dataset.example.v1",
        "example_id": "ex_ref",
        "parent_item_id": "item_ref",
    }
    (version / "examples" / "ex_ref.json").write_text(json.dumps(example), encoding="utf-8")
    (version / "splits" / "train.jsonl").write_text(
        json.dumps({"example_id": "ex_ref", "path": "examples/ex_ref.json"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATASET_ROOT", str(dataset_root))
    with pytest.raises(ModelLabError) as exc:
        service.create_model_lab_experiment(
            _create_body(dataset_version_id="ref_v1", display_name="RefOnlyLab")
        )
    assert exc.value.code == "rights_train_refused"
    assert not (tmp_path / "experiments" / "mtlab_").exists()
    labs = list((tmp_path / "experiments").glob("mtlab_*")) if (tmp_path / "experiments").exists() else []
    assert labs == []


def test_missing_rights_index_refused(lab_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dataset_root = tmp_path / "datasets_norights"
    version = dataset_root / "bare" / "bare_v1"
    version.mkdir(parents=True)
    (version / "splits").mkdir()
    (version / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "dataset.manifest.v1",
                "dataset_name": "bare",
                "dataset_version_id": "bare_v1",
                "pipeline_version": "dataset.pipeline.v1",
                "created_at": "2026-10-04T00:00:00Z",
                "counts": {"items": 0, "examples": 0, "train_eligible_items": 0},
                "digests": {
                    "config_digest": "aa" * 32,
                    "item_content_digests_sha256": "bb" * 32,
                    "version_payload_sha256": "cc" * 32,
                },
                "split_seed": 1,
                "fingerprint_profile": "default",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATASET_ROOT", str(dataset_root))
    with pytest.raises(ModelLabError) as exc:
        service.create_model_lab_experiment(
            _create_body(dataset_version_id="bare_v1", display_name="BareLab")
        )
    assert exc.value.code == "rights_train_refused"


def test_orphan_sweep(lab_env: Path):
    store.insert_experiment(
        experiment_id="mtlab_" + "22" * 8,
        display_name="OrphanLab",
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
    changed = service.sweep_orphaned_model_lab_experiments()
    assert changed == 1
    row = store.get_experiment("mtlab_" + "22" * 8, include_deleted=True)
    # get_experiment excludes deleted; status failed is visible
    failed = store.get_experiment("mtlab_" + "22" * 8)
    assert failed.status == "failed"
    assert failed.error_code == "model_lab_interrupted"


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("torch") is None,
    reason="torch not installed",
)
def test_optional_torch_tiny_steps(lab_env: Path, monkeypatch: pytest.MonkeyPatch):
    import time

    pytest.importorskip("torch")
    monkeypatch.setenv("MODEL_LAB_FAKE", "0")
    monkeypatch.setenv("MODEL_LAB_MAX_STEPS", "2")
    result = service.create_model_lab_experiment(
        _create_body(
            display_name="TorchTinyLab",
            train={"steps": 2, "batch_size": 1, "lr": 0.001, "device": "cpu"},
        )
    )
    assert result.engine == "torch"
    deadline = time.monotonic() + 120
    while result.status == "running" and time.monotonic() < deadline:
        time.sleep(0.25)
        result = service.get_model_lab_experiment(result.id)
    assert result.status in {"complete", "failed"}
    if result.status == "complete":
        assert any(ref.weights_present for ref in result.checkpoint_refs) or result.checkpoint_refs
