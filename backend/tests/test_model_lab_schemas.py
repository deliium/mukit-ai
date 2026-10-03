"""Model Lab documents and storage-root refusal."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.model_lab_schemas import (
    ModelLabCreateV1,
    ModelLabError,
    reject_model_lab_payload,
)
from app.model_lab_settings import assert_model_lab_storage_root, hard_max_steps
from app.storage_root_policy import StorageRootError


def _create(**overrides):
    payload = {
        "schema_version": "model.lab.create.v1",
        "display_name": "TinyLab-v1",
        "dataset_version_id": "lab_fixture_tiny_v1",
        "tokenizer_preset": "core",
        "architecture_preset": "tiny_lab",
        "seed": 42,
        "train": {
            "steps": 8,
            "batch_size": 2,
            "lr": 0.0003,
            "device": "cpu",
        },
        "eval_enabled": False,
        "listening_enabled": False,
    }
    payload.update(overrides)
    return payload


def test_create_accepts_tinylab_seed_42():
    body = ModelLabCreateV1.model_validate(_create())
    assert body.display_name == "TinyLab-v1"
    assert body.seed == 42
    assert body.architecture_preset == "tiny_lab"
    assert body.eval_enabled is False
    assert body.listening_enabled is False


def test_create_rejects_embedded_events():
    payload = _create()
    payload["events"] = [{"pitch": "C4"}]
    with pytest.raises(ValidationError) as exc:
        ModelLabCreateV1.model_validate(payload)
    assert "embedded_note_material" in str(exc.value)


def test_create_rejects_shell_keys():
    for key in ("command", "argv", "shell"):
        payload = _create()
        payload[key] = "rm -rf /" if key != "argv" else ["rm", "-rf", "/"]
        with pytest.raises(ValidationError) as exc:
            ModelLabCreateV1.model_validate(payload)
        assert "model_lab_payload_refused" in str(exc.value)


def test_reject_payload_command_shaped_body():
    with pytest.raises(ModelLabError) as exc:
        reject_model_lab_payload({"command": "rm -rf /"})
    assert exc.value.code == "model_lab_payload_refused"


def test_create_rejects_steps_above_hard_max():
    payload = _create()
    payload["train"] = {**payload["train"], "steps": hard_max_steps() + 1}
    with pytest.raises(ValidationError):
        ModelLabCreateV1.model_validate(payload)


def test_create_rejects_empty_display_name():
    with pytest.raises(ValidationError):
        ModelLabCreateV1.model_validate(_create(display_name=""))


def test_create_rejects_free_form_checkpoint_path():
    payload = _create()
    payload["checkpoint_path"] = "/home/user/weights/model.pt"
    with pytest.raises(ValidationError) as exc:
        ModelLabCreateV1.model_validate(payload)
    assert "model_lab_payload_refused" in str(exc.value)


def test_settings_reject_dataset_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dataset = tmp_path / "corpus"
    dataset.mkdir()
    monkeypatch.setenv("DATASET_ROOT", str(dataset))
    monkeypatch.setenv("MODEL_LAB_ROOT", str(dataset))
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    with pytest.raises(StorageRootError) as exc:
        assert_model_lab_storage_root()
    assert exc.value.reason == "dataset_root"
