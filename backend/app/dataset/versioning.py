"""Deterministic dataset version IDs (wall clock excluded from digest)."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.dataset.settings import DATASET_FINGERPRINT_PROFILE, DATASET_PIPELINE_VERSION
from app.dataset.schemas import (
    DATASET_EXAMPLE_SCHEMA,
    DATASET_ITEM_SCHEMA,
    DATASET_MANIFEST_SCHEMA,
    DATASET_PIPELINE_SCHEMA,
    DATASET_STATS_SCHEMA,
    DatasetPipelineConfig,
)


logger = logging.getLogger(__name__)


def canonical_json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def config_digest(config: DatasetPipelineConfig) -> str:
    payload = config.model_dump(mode="json")
    digest = hashlib.sha256(canonical_json_dumps(payload).encode("utf-8")).hexdigest()
    logger.debug(
        "Config digest computed",
        extra={"config_digest_prefix": digest[:12], "dataset_name": config.dataset_name},
    )
    return digest


def item_content_digests_sha256(content_hashes: list[str]) -> str:
    sorted_hashes = sorted(content_hashes)
    material = canonical_json_dumps(sorted_hashes)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def compute_dataset_version_id(
    *,
    pipeline_version: str,
    config_digest_hex: str,
    item_content_digests_sorted_hex: str,
    split_seed: int,
    schema_versions: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Return ``(version_id, version_payload_sha256)``.

    ``created_at`` is intentionally omitted so rebuilds with identical inputs
    reproduce the same version id.
    """
    schemas = schema_versions or default_schema_versions()
    payload = {
        "pipeline_version": pipeline_version,
        "config_digest": config_digest_hex,
        "item_content_digests_sorted": item_content_digests_sorted_hex,
        "split_seed": split_seed,
        "schema_versions": schemas,
        "fingerprint_profile": DATASET_FINGERPRINT_PROFILE,
    }
    version_payload_sha256 = hashlib.sha256(
        canonical_json_dumps(payload).encode("utf-8")
    ).hexdigest()
    # Stable short hex id (16 chars ≈ 64 bits).
    version_id = version_payload_sha256[:16]
    logger.info(
        "Dataset version id computed",
        extra={
            "version_id": version_id,
            "pipeline_version": pipeline_version,
            "split_seed": split_seed,
        },
    )
    return version_id, version_payload_sha256


def default_schema_versions() -> dict[str, str]:
    return {
        "pipeline": DATASET_PIPELINE_SCHEMA,
        "manifest": DATASET_MANIFEST_SCHEMA,
        "item": DATASET_ITEM_SCHEMA,
        "example": DATASET_EXAMPLE_SCHEMA,
        "stats": DATASET_STATS_SCHEMA,
        "pipeline_code": DATASET_PIPELINE_VERSION,
    }
