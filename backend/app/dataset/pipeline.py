"""Orchestrate ingest → normalize → segment → dedup → split → stats → manifest."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from app.composition_schemas import CompositionV2
from app.dataset.dedup import cluster_items
from app.dataset.errors import DatasetError, DatasetIngestError
from app.dataset.ingest import ingest_source
from app.dataset.manifest import build_manifest, compute_build_digests, verify_dataset_dir
from app.dataset.provenance import summarize_eligibility
from app.dataset.schemas import (
    DatasetExampleV1,
    DatasetItemV1,
    DatasetManifestCounts,
    DatasetPipelineConfig,
)
from app.dataset.segment import segment_item
from app.dataset.settings import (
    DatasetSettings,
    ensure_dataset_root,
    load_dataset_settings,
)
from app.dataset.sources import iterate_catalogued_sources, load_pipeline_config
from app.dataset.split import assign_splits, verify_cluster_split_integrity
from app.dataset.stats import compute_stats
from app.dataset.store import DatasetVersionStore


logger = logging.getLogger(__name__)


def run_build(
    config_path: Path,
    *,
    out_root: Path | None = None,
    settings: DatasetSettings | None = None,
) -> Path:
    """Full reproducible build; returns the versioned dataset directory."""
    cfg = settings or load_dataset_settings()
    ensure_dataset_root(cfg)
    config = load_pipeline_config(config_path)
    base_dir = config_path.parent.resolve()
    root = Path(out_root) if out_root else (
        Path(config.output_root).expanduser()
        if config.output_root
        else cfg.dataset_root
    )
    if not root.is_absolute():
        root = (base_dir / root).resolve()

    build_started = time.perf_counter()
    logger.info(
        "Dataset build start",
        extra={
            "dataset_name": config.dataset_name,
            "config_basename": config_path.name,
            "out_root_basename": root.name,
        },
    )

    sources = iterate_catalogued_sources(config, base_dir=base_dir)
    items: list[DatasetItemV1] = []
    compositions: dict[str, CompositionV2] = {}
    skipped = 0

    phase_started = time.perf_counter()
    logger.info("Phase start", extra={"phase": "ingest", "source_count": len(sources)})
    for source in sources:
        try:
            item = ingest_source(source, config=config, settings=cfg)
        except DatasetIngestError as exc:
            skipped += 1
            logger.warning(
                "Source skipped",
                extra={
                    "source_id": source.source_id,
                    "issue_code": exc.code,
                },
            )
            continue
        assert item.composition is not None
        compositions[item.item_id] = item.composition
        items.append(item)
    logger.info(
        "Phase end",
        extra={
            "phase": "ingest",
            "elapsed_ms": round((time.perf_counter() - phase_started) * 1000, 3),
            "item_count": len(items),
            "skipped": skipped,
        },
    )
    if not items:
        raise DatasetError("empty_dataset", "No items ingested from configured sources")

    summarize_eligibility(
        [(item.provenance.status, item.train_eligible) for item in items]
    )

    # Dedup / cluster
    phase_started = time.perf_counter()
    logger.info("Phase start", extra={"phase": "dedup"})
    items = cluster_items(
        items,
        compositions=compositions,
        near_dup=config.near_dup,
    )
    # Refresh composition map keys unchanged
    logger.info(
        "Phase end",
        extra={
            "phase": "dedup",
            "elapsed_ms": round((time.perf_counter() - phase_started) * 1000, 3),
        },
    )

    # Segment
    phase_started = time.perf_counter()
    logger.info("Phase start", extra={"phase": "segment"})
    examples: list[DatasetExampleV1] = []
    for item in items:
        doc = compositions[item.item_id]
        examples.extend(
            segment_item(item, config=config.segmentation, composition=doc)
        )
    logger.info(
        "Phase end",
        extra={
            "phase": "segment",
            "elapsed_ms": round((time.perf_counter() - phase_started) * 1000, 3),
            "example_count": len(examples),
        },
    )

    # Version id from content (before writing layout)
    content_hashes = [item.content_hash or "" for item in items]
    version_id, digests = compute_build_digests(config, content_hashes)
    version_dir = root / config.dataset_name / version_id
    store = DatasetVersionStore(version_dir, dataset_root=root)
    store.ensure_layout()

    # Persist items / blobs / examples
    for item in items:
        doc = compositions[item.item_id]
        if config.normalization.store_cas_blob and item.content_hash:
            store.put_v2_blob(item.content_hash, doc.model_dump(mode="json"))
        store.write_item(item)
    for example in examples:
        store.write_example(example)

    # Splits
    phase_started = time.perf_counter()
    logger.info("Phase start", extra={"phase": "split"})
    split_rows = assign_splits(
        items,
        examples,
        split_seed=config.split_seed,
        train_ratio=config.train_ratio,
        val_ratio=config.val_ratio,
        test_ratio=config.test_ratio,
        eligibility=config.eligibility,
    )
    verify_cluster_split_integrity(split_rows)
    for split_name, rows in split_rows.items():
        store.write_split_jsonl(split_name, rows)
    logger.info(
        "Phase end",
        extra={
            "phase": "split",
            "elapsed_ms": round((time.perf_counter() - phase_started) * 1000, 3),
        },
    )

    # Stats
    phase_started = time.perf_counter()
    logger.info("Phase start", extra={"phase": "stats"})
    stats = compute_stats(
        dataset_name=config.dataset_name,
        dataset_version_id=version_id,
        items=items,
        examples=examples,
        compositions=compositions,
        file_count=len(sources),
    )
    store.write_stats(stats)
    logger.info(
        "Phase end",
        extra={
            "phase": "stats",
            "elapsed_ms": round((time.perf_counter() - phase_started) * 1000, 3),
        },
    )

    # Manifest + snapshot + BUILD_ID
    counts = DatasetManifestCounts(
        sources=len(sources),
        items=len(items),
        examples=len(examples),
        train_eligible_items=sum(1 for item in items if item.train_eligible),
        excluded_items=sum(1 for item in items if not item.train_eligible),
        clusters=len({item.cluster_id for item in items if item.cluster_id}),
        train_examples=len(split_rows["train"]),
        validation_examples=len(split_rows["validation"]),
        test_examples=len(split_rows["test"]),
    )
    manifest = build_manifest(
        config=config,
        version_id=version_id,
        digests=digests,
        counts=counts,
    )
    store.write_manifest(manifest)
    store.write_yaml(store.version_dir / "config.snapshot.yaml", config.model_dump(mode="json"))
    store.write_build_id(version_id)

    # Self-verify
    verify_dataset_dir(store)

    elapsed_ms = round((time.perf_counter() - build_started) * 1000, 3)
    logger.info(
        "Dataset build complete",
        extra={
            "dataset_name": config.dataset_name,
            "version_id": version_id,
            "item_count": len(items),
            "example_count": len(examples),
            "train_examples": counts.train_examples,
            "elapsed_ms": elapsed_ms,
        },
    )
    return version_dir


def run_verify(dataset_dir: Path) -> None:
    store = DatasetVersionStore(dataset_dir)
    verify_dataset_dir(store)
