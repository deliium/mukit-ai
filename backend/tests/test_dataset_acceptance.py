"""Acceptance: licensed fixture directory → versioned dataset artifact."""

from __future__ import annotations

import json
from pathlib import Path

from app.dataset.pipeline import run_build, run_verify


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "dataset"
PIPELINE_PATH = FIXTURE_DIR / "pipeline.yaml"


def test_acceptance_licensed_dir_to_versioned_dataset(tmp_path: Path) -> None:
    out = tmp_path / "datasets"
    version_dir = run_build(PIPELINE_PATH, out_root=out)
    manifest = json.loads((version_dir / "manifest.json").read_text())
    assert manifest["schema_version"] == "dataset.manifest.v1"
    assert manifest["dataset_version_id"] == version_dir.name
    assert (version_dir / "stats.json").exists()

    run_verify(version_dir)

    train = (version_dir / "splits" / "train.jsonl").read_text().splitlines()
    items = {
        p.stem: json.loads(p.read_text())
        for p in (version_dir / "items").glob("*.json")
    }
    train_clusters: set[str] = set()
    val_clusters: set[str] = set()
    test_clusters: set[str] = set()
    for line in train:
        if not line.strip():
            continue
        row = json.loads(line)
        parent = items[row["parent_item_id"]]
        assert parent["provenance"]["status"] not in {"unknown", "restricted"}
        train_clusters.add(row["cluster_id"])

    for split_name, bucket in (
        ("validation", val_clusters),
        ("test", test_clusters),
    ):
        for line in (version_dir / "splits" / f"{split_name}.jsonl").read_text().splitlines():
            if line.strip():
                bucket.add(json.loads(line)["cluster_id"])

    assert train_clusters.isdisjoint(val_clusters)
    assert train_clusters.isdisjoint(test_clusters)
    assert val_clusters.isdisjoint(test_clusters)

    rebuilt = run_build(PIPELINE_PATH, out_root=out)
    assert rebuilt.name == version_dir.name
