"""SQLite personal composer rows."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.personal_composer_schemas import PersonalComposerError, PersonalTrainingManifestV1
from app.services import personal_composer_store as store


def _manifest(name: str = "MyComposer-v1") -> PersonalTrainingManifestV1:
    adapter_id = "pcomp_" + "ab" * 8
    return PersonalTrainingManifestV1.model_validate(
        {
            "schema_version": "personal.training_manifest.v1",
            "adapter_id": adapter_id,
            "display_name": name,
            "registry_model_id": f"personal:{adapter_id}",
            "project_ids": ["etude"],
            "rights": {"etude": {"status": "user_owned", "user_owned_attested": True}},
            "snapshot_version": "cd" * 32,
            "base_model_id": "fake:symbolic-tiny",
            "adapter_config": {
                "method": "lora",
                "rank": 4,
                "target_modules": ["qkv", "out_proj"],
                "freeze_base": True,
                "max_steps": 1,
            },
            "engine": "fake",
            "created_at": "2026-10-02T12:00:00Z",
        }
    )


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def test_insert_reads_registry_and_snapshot(project_db: Path):
    manifest = _manifest()
    row = store.insert_adapter(
        adapter_id=manifest.adapter_id,
        display_name=manifest.display_name,
        registry_model_id=manifest.registry_model_id,
        engine="fake",
        base_model_id="fake:symbolic-tiny",
        snapshot_version=manifest.snapshot_version,
        max_steps=1,
        manifest=manifest,
        owner_actor_id=None,
        db_path=project_db,
    )
    loaded = store.get_adapter(manifest.adapter_id, db_path=project_db)
    assert loaded.job.registry_model_id == row.job.registry_model_id
    assert loaded.job.snapshot_version == "cd" * 32
    assert len(loaded.job.snapshot_version) == 64


def test_second_display_name_is_taken(project_db: Path):
    manifest = _manifest()
    store.insert_adapter(
        adapter_id=manifest.adapter_id,
        display_name=manifest.display_name,
        registry_model_id=manifest.registry_model_id,
        engine="fake",
        base_model_id="fake:symbolic-tiny",
        snapshot_version=manifest.snapshot_version,
        max_steps=1,
        manifest=manifest,
        owner_actor_id=None,
        db_path=project_db,
    )
    other = _manifest()
    other_id = "pcomp_" + "ef" * 8
    duplicate = other.model_copy(
        update={"adapter_id": other_id, "registry_model_id": f"personal:{other_id}"}
    )
    with pytest.raises(PersonalComposerError) as exc:
        store.insert_adapter(
            adapter_id=duplicate.adapter_id,
            display_name=duplicate.display_name,
            registry_model_id=duplicate.registry_model_id,
            engine="fake",
            base_model_id="fake:symbolic-tiny",
            snapshot_version=duplicate.snapshot_version,
            max_steps=1,
            manifest=duplicate,
            owner_actor_id=None,
            db_path=project_db,
        )
    assert exc.value.code == "personal_name_taken"


def test_deleted_row_status_does_not_change(project_db: Path):
    manifest = _manifest()
    store.insert_adapter(
        adapter_id=manifest.adapter_id,
        display_name=manifest.display_name,
        registry_model_id=manifest.registry_model_id,
        engine="fake",
        base_model_id="fake:symbolic-tiny",
        snapshot_version=manifest.snapshot_version,
        max_steps=1,
        manifest=manifest,
        owner_actor_id=None,
        db_path=project_db,
    )
    store.update_adapter(manifest.adapter_id, status="deleted", db_path=project_db)
    again = store.update_adapter(manifest.adapter_id, status="complete", db_path=project_db)
    assert again.job.status == "deleted"
    with pytest.raises(PersonalComposerError) as exc:
        store.get_adapter(manifest.adapter_id, db_path=project_db)
    assert exc.value.code == "personal_not_found"
