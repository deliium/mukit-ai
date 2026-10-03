"""Dataset rights siblings and reference_only train exclusion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.dataset.pipeline import run_build
from app.dataset.provenance import compute_train_eligible
from app.dataset.rights_index import load_rights_index, load_rights_manifest
from app.dataset.schemas import DatasetProvenance
from app.dataset.sources import load_pipeline_config
from app.dataset.store import DatasetVersionStore


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "dataset"
PIPELINE_PATH = FIXTURE_DIR / "pipeline.yaml"


def test_reference_only_not_train_eligible_despite_train_shaped_status() -> None:
    provenance = DatasetProvenance.model_validate(
        {
            "status": "verified_redistributable",
            "license": "CC-BY-4.0",
            "license_spdx": "CC-BY-4.0",
            "source_url": "https://example.invalid/ref",
        }
    )
    assert compute_train_eligible(provenance) is True
    assert (
        compute_train_eligible(provenance, use_policy_override="reference_only") is False
    )


def test_build_writes_rights_siblings_and_excludes_reference_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATASET_ROOT", str(tmp_path / "datasets"))
    config = load_pipeline_config(PIPELINE_PATH)
    version_dir = run_build(PIPELINE_PATH, out_root=tmp_path / "datasets")
    store = DatasetVersionStore(version_dir)
    items = list(store.iter_items())
    assert items

    ref_items = [item for item in items if item.use_policy == "reference_only"]
    assert ref_items, "expected reference_only fixture in inventory"
    for item in ref_items:
        assert item.train_eligible is False
        assert item.provenance.status == "verified_redistributable"

    train_path = store.split_path("train")
    train_text = train_path.read_text(encoding="utf-8") if train_path.is_file() else ""
    for item in ref_items:
        assert item.item_id not in train_text

    index = load_rights_index(version_dir)
    assert len(index) == len(items)
    ref_entries = [row for row in index if row.use_policy == "reference_only"]
    assert ref_entries

    manifest = load_rights_manifest(version_dir)
    assert manifest is not None
    assert manifest.manifest_kind == "dataset_train_split"
    assert manifest.excluded_source_counts_by_use_policy.get("reference_only", 0) >= 1
    train_source_ids = {row.source_id for row in manifest.sources}
    for item in ref_items:
        assert item.item_id not in train_source_ids

    # Rights siblings are not required for dataset_version_id stability checks here;
    # manifest.json from dataset.manifest.v1 must still load.
    ds_manifest = store.load_manifest()
    assert ds_manifest.dataset_name == config.dataset_name
    assert (version_dir / "rights" / "index.jsonl").is_file()
    assert (version_dir / "rights" / "manifest.json").is_file()
    # Ensure rights bytes are not accidentally embedded in dataset manifest.
    encoded = json.dumps(ds_manifest.model_dump(mode="json"))
    assert "rights/index.jsonl" not in encoded
