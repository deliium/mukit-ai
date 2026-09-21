"""Unit and integration tests for the offline dataset pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.dataset.fingerprint import dataset_content_hash
from app.dataset.ingest import detect_source_format, ingest_source
from app.dataset.normalize import normalize_dataset_composition
from app.dataset.pipeline import run_build, run_verify
from app.dataset.provenance import compute_train_eligible
from app.dataset.schemas import (
    DatasetPipelineConfig,
    DatasetProvenance,
    DatasetEligibilityPolicy,
)
from app.dataset.segment import segment_item, slice_composition_window
from app.dataset.sources import iterate_catalogued_sources, load_pipeline_config
from app.dataset.split import verify_cluster_split_integrity
from app.composition_schemas import CompositionV2


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "dataset"
PIPELINE_PATH = FIXTURE_DIR / "pipeline.yaml"
SOURCES_DIR = FIXTURE_DIR / "sources"


@pytest.fixture()
def pipeline_config() -> DatasetPipelineConfig:
    return load_pipeline_config(PIPELINE_PATH)


def test_schema_provenance_requires_fields() -> None:
    with pytest.raises(Exception):
        DatasetProvenance.model_validate({"status": "verified_redistributable"})
    ok = DatasetProvenance.model_validate(
        {
            "status": "public_domain",
            "source_reference": "ref",
        }
    )
    assert ok.status == "public_domain"


def test_train_eligible_excludes_unknown() -> None:
    unknown = DatasetProvenance.model_validate({"status": "unknown"})
    assert compute_train_eligible(unknown) is False
    pd = DatasetProvenance.model_validate(
        {"status": "public_domain", "source_reference": "x"}
    )
    assert compute_train_eligible(pd) is True


def test_detect_formats() -> None:
    midi = (SOURCES_DIR / "public_domain_phrase.mid").read_bytes()
    assert detect_source_format(midi, filename="x.mid") == "midi"
    xml = (SOURCES_DIR / "licensed_score.musicxml").read_bytes()
    assert detect_source_format(xml, filename="x.musicxml") == "musicxml"
    js = (SOURCES_DIR / "v2_sample.json").read_bytes()
    assert detect_source_format(js, filename="x.json") == "composition_json"


def test_source_catalog_fail_closed(tmp_path: Path, pipeline_config: DatasetPipelineConfig) -> None:
    bare = tmp_path / "bare.mid"
    bare.write_bytes((SOURCES_DIR / "public_domain_phrase.mid").read_bytes())
    cfg = pipeline_config.model_copy(
        update={
            "sources": [
                pipeline_config.sources[0].model_copy(
                    update={"path": str(tmp_path), "default_provenance": None}
                )
            ],
            "default_provenance": None,
        }
    )
    with pytest.raises(Exception):
        iterate_catalogued_sources(cfg, base_dir=tmp_path)


def test_normalize_idempotent(pipeline_config: DatasetPipelineConfig) -> None:
    sources = iterate_catalogued_sources(pipeline_config, base_dir=FIXTURE_DIR)
    item = ingest_source(sources[0], config=pipeline_config)
    assert item.composition is not None
    first, _, _ = normalize_dataset_composition(item.composition, collapse_dup_notes=True)
    second, codes, counts = normalize_dataset_composition(first, collapse_dup_notes=True)
    assert dataset_content_hash(first) == dataset_content_hash(second)
    assert codes == [] or counts.get("duplicate_notes_collapsed", 0) == 0


def test_segmentation_modes_produce_valid_v2(pipeline_config: DatasetPipelineConfig) -> None:
    sources = iterate_catalogued_sources(pipeline_config, base_dir=FIXTURE_DIR)
    item = next(
        ingest_source(src, config=pipeline_config)
        for src in sources
        if src.path.suffix == ".json" and "near_dup" not in src.path.name
    )
    assert item.composition is not None
    examples = segment_item(item, config=pipeline_config.segmentation)
    assert examples
    for example in examples:
        assert example.end_tick > example.start_tick
        CompositionV2.model_validate(example.composition.model_dump(mode="json"))


def test_slice_window_valid() -> None:
    raw = json.loads((SOURCES_DIR / "v2_sample.json").read_text())
    doc = CompositionV2.model_validate(raw)
    sliced = slice_composition_window(doc, start_tick=0, end_tick=min(doc.duration_ticks, 1920))
    assert sliced.duration_ticks > 0
    assert sliced.bar_count >= 1


def test_cli_build_and_verify(tmp_path: Path, pipeline_config: DatasetPipelineConfig) -> None:
    out = tmp_path / "datasets"
    version_dir = run_build(PIPELINE_PATH, out_root=out)
    assert (version_dir / "manifest.json").exists()
    assert (version_dir / "stats.json").exists()
    assert (version_dir / "BUILD_ID").exists()
    assert (version_dir / "splits" / "train.jsonl").exists()
    run_verify(version_dir)

    # unknown must not appear in train
    train_lines = (version_dir / "splits" / "train.jsonl").read_text().splitlines()
    items = {
        path.stem: json.loads(path.read_text())
        for path in (version_dir / "items").glob("*.json")
    }
    for line in train_lines:
        if not line.strip():
            continue
        row = json.loads(line)
        parent = items[row["parent_item_id"]]
        assert parent["provenance"]["status"] != "unknown"
        assert parent["provenance"]["status"] != "restricted"
        assert parent["train_eligible"] is True

    # version stability
    version_dir2 = run_build(PIPELINE_PATH, out_root=out)
    assert version_dir2.name == version_dir.name

    # cluster leakage guard on written splits
    from app.dataset.schemas import SplitJsonlRow

    rows = {"train": [], "validation": [], "test": []}
    for split in rows:
        path = version_dir / "splits" / f"{split}.jsonl"
        for line in path.read_text().splitlines():
            if line.strip():
                rows[split].append(SplitJsonlRow.model_validate(json.loads(line)))
    verify_cluster_split_integrity(rows)

    stats = json.loads((version_dir / "stats.json").read_text())
    assert "inventory" in stats and "eligible" in stats
    assert "provenance_status_counts" in stats["inventory"]


def test_no_project_db_imports_in_dataset_package() -> None:
    package = Path(__file__).resolve().parents[1] / "app" / "dataset"
    forbidden_imports = (
        "from app.services.project_store",
        "import project_store",
        "from app.services.project_history",
        "os.environ.get(\"PROJECT_DB_PATH\"",
        "os.environ['PROJECT_DB_PATH']",
    )
    for path in package.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden_imports:
            assert token not in text, f"{path.name} references {token}"
