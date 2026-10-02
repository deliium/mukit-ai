"""Personal composer documents and storage-root refusal."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.personal_composer_schemas import PersonalTrainingManifestV1
from app.personal_composer_settings import assert_personal_composer_storage_root
from app.storage_root_policy import StorageRootError


def _manifest(**overrides):
    payload = {
        "schema_version": "personal.training_manifest.v1",
        "adapter_id": "pcomp_" + "ab" * 8,
        "display_name": "MyComposer-v1",
        "registry_model_id": "personal:pcomp_" + "ab" * 8,
        "project_ids": ["etude", "sketch"],
        "rights": {
            "etude": {"status": "user_owned", "user_owned_attested": True},
            "sketch": {"status": "user_owned", "user_owned_attested": True},
        },
        "snapshot_version": "cd" * 32,
        "base_model_id": "fake:symbolic-tiny",
        "base_checkpoint_basename": None,
        "adapter_config": {
            "schema_version": "personal.adapter_config.v1",
            "method": "lora",
            "rank": 4,
            "alpha": 8,
            "dropout": 0,
            "target_modules": ["qkv", "out_proj"],
            "freeze_base": True,
            "max_steps": 1,
        },
        "engine": "fake",
        "created_at": "2026-10-02T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_manifest_accepts_mycomposer_lora_targets():
    manifest = PersonalTrainingManifestV1.model_validate(_manifest())
    assert manifest.display_name == "MyComposer-v1"
    assert manifest.adapter_config.rank == 4
    assert manifest.adapter_config.target_modules == ["qkv", "out_proj"]
    assert manifest.adapter_config.freeze_base is True
    assert manifest.adapter_config.method == "lora"


def test_manifest_rejects_embedded_events():
    payload = _manifest()
    payload["events"] = [{"pitch": "C4"}]
    with pytest.raises(ValidationError) as exc:
        PersonalTrainingManifestV1.model_validate(payload)
    assert "embedded_note_material" in str(exc.value)


def test_manifest_rejects_spaced_display_name():
    with pytest.raises(ValidationError):
        PersonalTrainingManifestV1.model_validate(_manifest(display_name="my composer"))


def test_manifest_rejects_rank_32():
    payload = _manifest()
    payload["adapter_config"] = {**payload["adapter_config"], "rank": 32}
    with pytest.raises(ValidationError):
        PersonalTrainingManifestV1.model_validate(payload)


def test_manifest_rejects_unattested_user_owned():
    payload = _manifest()
    payload["rights"] = {
        "etude": {"status": "user_owned", "user_owned_attested": False},
        "sketch": {"status": "user_owned", "user_owned_attested": True},
    }
    with pytest.raises(ValidationError):
        PersonalTrainingManifestV1.model_validate(payload)


def test_settings_reject_dataset_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dataset = tmp_path / "corpus"
    dataset.mkdir()
    monkeypatch.setenv("DATASET_ROOT", str(dataset))
    monkeypatch.setenv("PERSONAL_COMPOSER_ROOT", str(dataset))
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    with pytest.raises(StorageRootError) as exc:
        assert_personal_composer_storage_root()
    assert exc.value.reason == "dataset_root"
