"""Music Transformer train rights verification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.dataset.pipeline import run_build
from app.dataset.sources import load_pipeline_config
from app.music_transformer.errors import MusicTransformerTrainError
from app.music_transformer.rights_gate import verify_train_paths_against_rights

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "dataset"
PIPELINE_PATH = FIXTURE_DIR / "pipeline.yaml"


def test_verify_accepts_clean_train_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATASET_ROOT", str(tmp_path / "datasets"))
    version_dir = run_build(PIPELINE_PATH, out_root=tmp_path / "datasets")
    train_split = version_dir / "splits" / "train.jsonl"
    paths: list[Path] = []
    for line in train_split.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        paths.append(version_dir / row["path"])
    manifest = verify_train_paths_against_rights(version_dir, paths)
    assert manifest.manifest_kind == "music_transformer_train"
    assert all(src.use_policy == "training_allowed" for src in manifest.sources)


def test_verify_refuses_poisoned_train_jsonl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATASET_ROOT", str(tmp_path / "datasets"))
    version_dir = run_build(PIPELINE_PATH, out_root=tmp_path / "datasets")
    # Find a reference_only item and craft a poisoned train path list.
    from app.dataset.store import DatasetVersionStore

    store = DatasetVersionStore(version_dir)
    ref_items = [item for item in store.iter_items() if item.use_policy == "reference_only"]
    assert ref_items
    # Pick any example belonging to a reference_only parent if present; else forge path.
    examples = list(store.iter_examples())
    poisoned_paths: list[Path] = []
    for example in examples:
        if example.parent_item_id == ref_items[0].item_id:
            poisoned_paths.append(store.example_path(example.example_id))
            break
    if not poisoned_paths:
        # Write a synthetic example pointing at the reference_only item.
        forged = version_dir / "examples" / "poisoned_ref.json"
        forged.write_text(
            json.dumps(
                {
                    "schema_version": "dataset.example.v1",
                    "example_id": "poisoned_ref",
                    "parent_item_id": ref_items[0].item_id,
                    "composition": ref_items[0].composition.model_dump(mode="json")
                    if ref_items[0].composition
                    else {},
                }
            ),
            encoding="utf-8",
        )
        poisoned_paths = [forged]

    with pytest.raises(MusicTransformerTrainError) as raised:
        verify_train_paths_against_rights(version_dir, poisoned_paths)
    assert raised.value.code == "rights_train_refused"


def test_pipeline_config_loads() -> None:
    cfg = load_pipeline_config(PIPELINE_PATH)
    assert cfg.dataset_name == "fixture_mini"
