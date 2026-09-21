"""Manifest generation and verification helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.dataset.errors import DatasetVerifyError
from app.dataset.schemas import (
    DatasetManifestCounts,
    DatasetManifestDigests,
    DatasetManifestV1,
    DatasetPipelineConfig,
)
from app.dataset.settings import DATASET_FINGERPRINT_PROFILE, DATASET_PIPELINE_VERSION
from app.dataset.store import DatasetVersionStore
from app.dataset.versioning import (
    compute_dataset_version_id,
    config_digest,
    default_schema_versions,
    item_content_digests_sha256,
)


logger = logging.getLogger(__name__)


def build_manifest(
    *,
    config: DatasetPipelineConfig,
    version_id: str,
    digests: DatasetManifestDigests,
    counts: DatasetManifestCounts,
    created_at: datetime | None = None,
) -> DatasetManifestV1:
    stamp = created_at or datetime.now(timezone.utc)
    return DatasetManifestV1.build_logged(
        dataset_name=config.dataset_name,
        dataset_version_id=version_id,
        pipeline_version=DATASET_PIPELINE_VERSION,
        created_at=stamp,
        counts=counts,
        digests=digests,
        schema_versions=default_schema_versions(),
        split_seed=config.split_seed,
        fingerprint_profile=DATASET_FINGERPRINT_PROFILE,
    )


def compute_build_digests(
    config: DatasetPipelineConfig,
    content_hashes: list[str],
) -> tuple[str, DatasetManifestDigests]:
    cfg_digest = config_digest(config)
    items_digest = item_content_digests_sha256(content_hashes)
    version_id, version_payload = compute_dataset_version_id(
        pipeline_version=DATASET_PIPELINE_VERSION,
        config_digest_hex=cfg_digest,
        item_content_digests_sorted_hex=items_digest,
        split_seed=config.split_seed,
    )
    digests = DatasetManifestDigests(
        config_digest=cfg_digest,
        item_content_digests_sha256=items_digest,
        version_payload_sha256=version_payload,
    )
    return version_id, digests


def verify_dataset_dir(store: DatasetVersionStore) -> DatasetManifestV1:
    """Recompute digests from on-disk items + config snapshot and compare."""
    manifest = store.load_manifest()
    snapshot_path = store.version_dir / "config.snapshot.yaml"
    if not snapshot_path.exists():
        raise DatasetVerifyError(
            "config_snapshot_missing",
            "config.snapshot.yaml missing from dataset version dir",
        )
    from app.dataset.sources import load_pipeline_config

    config = load_pipeline_config(snapshot_path)
    content_hashes = [
        item.content_hash or ""
        for item in store.iter_items()
        if item.content_hash
    ]
    version_id, digests = compute_build_digests(config, content_hashes)
    if version_id != manifest.dataset_version_id:
        logger.error(
            "Dataset verify version mismatch",
            extra={
                "expected_prefix": manifest.dataset_version_id[:12],
                "actual_prefix": version_id[:12],
            },
        )
        raise DatasetVerifyError(
            "version_id_mismatch",
            "Recomputed dataset_version_id does not match manifest",
            details={
                "expected": manifest.dataset_version_id,
                "actual": version_id,
            },
        )
    if digests.version_payload_sha256 != manifest.digests.version_payload_sha256:
        raise DatasetVerifyError(
            "version_payload_mismatch",
            "Recomputed version payload digest does not match manifest",
        )
    logger.info(
        "Dataset verify passed",
        extra={
            "version_id_prefix": version_id[:12],
            "item_count": manifest.counts.items,
            "example_count": manifest.counts.examples,
        },
    )
    return manifest
