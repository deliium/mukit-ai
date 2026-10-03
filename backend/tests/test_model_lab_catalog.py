"""Model Lab dataset catalog, presets, and payload refuse."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.model_lab_schemas import ModelLabError, reject_model_lab_payload
from app.music_transformer.schemas import tiny_test_config
from app.services import model_lab_catalog as catalog


def _write_version(root: Path, *, name: str = "fixture_mini", version_id: str = "ver_abc") -> Path:
    version_dir = root / name / version_id
    version_dir.mkdir(parents=True)
    (version_dir / "rights").mkdir()
    (version_dir / "rights" / "index.jsonl").write_text(
        json.dumps(
            {
                "item_id": "item_1",
                "use_policy": "training_allowed",
                "ownership_class": "user_owned",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "dataset.manifest.v1",
        "dataset_name": name,
        "dataset_version_id": version_id,
        "pipeline_version": "dataset.pipeline.v1",
        "created_at": "2026-10-04T00:00:00Z",
        "counts": {
            "sources": 1,
            "items": 2,
            "examples": 3,
            "train_eligible_items": 2,
            "excluded_items": 0,
            "clusters": 1,
            "train_examples": 2,
            "validation_examples": 1,
            "test_examples": 0,
        },
        "digests": {
            "config_digest": "aa" * 32,
            "item_content_digests_sha256": "bb" * 32,
            "version_payload_sha256": "cc" * 32,
        },
        "split_seed": 1,
        "fingerprint_profile": "default",
    }
    (version_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return version_dir


def test_catalog_lists_version_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "datasets"
    _write_version(root)
    monkeypatch.setenv("DATASET_ROOT", str(root))
    entries = catalog.list_dataset_catalog()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.dataset_name == "fixture_mini"
    assert entry.dataset_version_id == "ver_abc"
    assert entry.item_count == 2
    assert entry.example_count == 3
    assert entry.train_eligible_items == 2
    assert entry.has_rights_index is True


def test_resolve_dataset_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "datasets"
    version_dir = _write_version(root)
    monkeypatch.setenv("DATASET_ROOT", str(root))
    resolved, entry = catalog.resolve_dataset_dir("ver_abc")
    assert resolved == version_dir.resolve()
    assert entry.dataset_name == "fixture_mini"


def test_resolve_refuses_path_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "datasets"
    _write_version(root)
    monkeypatch.setenv("DATASET_ROOT", str(root))
    with pytest.raises(ModelLabError) as exc:
        catalog.resolve_dataset_dir("../etc/passwd")
    assert exc.value.code == "model_lab_dataset_refused"
    with pytest.raises(ModelLabError) as exc2:
        catalog.resolve_dataset_dir("/tmp/evil")
    assert exc2.value.code == "model_lab_dataset_refused"


def test_tiny_lab_matches_tiny_test_shape():
    preset = catalog.architecture_preset("tiny_lab")
    tiny = tiny_test_config(vocab_size=1075)
    assert preset.n_layers == tiny.n_layers
    assert preset.d_model == tiny.d_model
    assert preset.n_heads == tiny.n_heads


def test_payload_rejects_command_shaped_body():
    with pytest.raises(ModelLabError) as exc:
        reject_model_lab_payload({"command": "rm -rf /"})
    assert exc.value.code == "model_lab_payload_refused"
    with pytest.raises(ModelLabError):
        catalog.refuse_lab_payload({"argv": ["python", "-c", "print(1)"]})
