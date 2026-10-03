"""SQLite Model Lab experiment index rows."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.model_lab_schemas import ModelLabError, ModelLabRuntimeSummaryV1
from app.services import model_lab_store as store


def _insert(
    *,
    experiment_id: str = "mtlab_" + "ab" * 8,
    display_name: str = "TinyLab-v1",
    owner_actor_id: str | None = "actor-1",
    db_path: Path,
):
    return store.insert_experiment(
        experiment_id=experiment_id,
        display_name=display_name,
        status="queued",
        dataset_version_id="lab_fixture_tiny_v1",
        tokenizer_version="tokenizer.v1",
        tokenizer_vocab_hash_prefix="0123456789abcdef",
        architecture_digest_prefix="fedcba9876543210",
        train_digest_prefix="aabbccddeeff0011",
        seed=42,
        engine="fake",
        runtime=ModelLabRuntimeSummaryV1(device="fake", wall_ms=0),
        owner_actor_id=owner_actor_id,
        db_path=db_path,
    )


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def test_insert_reads_digests_seed_owner(project_db: Path):
    experiment_id = "mtlab_" + "ab" * 8
    row = _insert(experiment_id=experiment_id, db_path=project_db)
    loaded = store.get_experiment(experiment_id, db_path=project_db)
    assert loaded.id == experiment_id
    assert loaded.display_name == "TinyLab-v1"
    assert loaded.seed == 42
    assert loaded.architecture_digest_prefix == "fedcba9876543210"
    assert loaded.train_digest_prefix == "aabbccddeeff0011"
    assert loaded.tokenizer_expectation.vocab_hash_prefix == "0123456789abcdef"
    assert loaded.owner_actor_id == "actor-1"
    assert loaded.engine == row.engine


def test_second_display_name_is_taken(project_db: Path):
    _insert(db_path=project_db)
    with pytest.raises(ModelLabError) as exc:
        _insert(
            experiment_id="mtlab_" + "cd" * 8,
            display_name="TinyLab-v1",
            db_path=project_db,
        )
    assert exc.value.code == "model_lab_name_taken"


def test_deleted_row_status_does_not_change(project_db: Path):
    experiment_id = "mtlab_" + "ab" * 8
    _insert(experiment_id=experiment_id, db_path=project_db)
    store.update_experiment(experiment_id, status="deleted", db_path=project_db)
    again = store.update_experiment(experiment_id, status="complete", db_path=project_db)
    assert again.status == "deleted"
    with pytest.raises(ModelLabError) as exc:
        store.get_experiment(experiment_id, db_path=project_db)
    assert exc.value.code == "model_lab_not_found"


def test_list_omits_deleted(project_db: Path):
    a = "mtlab_" + "ab" * 8
    b = "mtlab_" + "cd" * 8
    _insert(experiment_id=a, display_name="TinyLab-v1", db_path=project_db)
    _insert(experiment_id=b, display_name="TinyLab-v2", db_path=project_db)
    store.update_experiment(a, status="deleted", db_path=project_db)
    listed = store.list_experiments(db_path=project_db)
    assert [item.id for item in listed] == [b]
