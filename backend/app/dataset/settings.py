"""Bounded ``DATASET_*`` limits and pipeline defaults (independent of ``IMPORT_*`` / projects).

Dataset corpora live under ``DATASET_ROOT`` only — never ``PROJECT_DB_PATH``.
Runtime verbosity remains controlled by ``LOG_LEVEL``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.import_settings import IMPORT_DEFAULT_TARGET_PPQ, ImportSettings


logger = logging.getLogger(__name__)

# Bump when ingest/normalize/segment/dedup/split algorithms change identity.
DATASET_PIPELINE_VERSION = "1"
DATASET_FINGERPRINT_PROFILE = "dataset.fingerprint.v1"

_DEFAULT_ROOT = "./datasets"
_DEFAULT_MAX_SOURCE_BYTES = 50_000_000
_DEFAULT_MAX_NOTES = 500_000
_DEFAULT_MAX_BARS = 4096
_DEFAULT_MAX_TRACKS = 128
_DEFAULT_MAX_PPQ = 1920
_DEFAULT_MAX_EXPANDED_BYTES = 100_000_000
_DEFAULT_MAX_COMPRESSION_RATIO = 50.0
_DEFAULT_MAX_ARCHIVE_ENTRIES = 128
_DEFAULT_MAX_METADATA_CHANGES = 2048
_DEFAULT_MAX_ACTIVE_NOTES = 2048
_DEFAULT_SPLIT_SEED = 20260921
_DEFAULT_TRAIN_RATIO = 0.8
_DEFAULT_VAL_RATIO = 0.1
_DEFAULT_TEST_RATIO = 0.1


@dataclass(frozen=True)
class DatasetSettings:
    """Env-backed defaults; pipeline YAML may override per build."""

    dataset_root: Path
    target_ppq: int
    collapse_dup_notes: bool
    max_source_bytes: int
    max_notes: int
    max_bars: int
    max_tracks: int
    max_ppq: int
    max_expanded_bytes: int
    max_compression_ratio: float
    max_archive_entries: int
    max_metadata_changes: int
    max_active_notes: int
    split_seed: int
    train_ratio: float
    val_ratio: float
    test_ratio: float
    pipeline_version: str = DATASET_PIPELINE_VERSION
    fingerprint_profile: str = DATASET_FINGERPRINT_PROFILE
    create_root_on_build: bool = True


def load_dataset_settings(env: Mapping[str, str] | None = None) -> DatasetSettings:
    source = env if env is not None else os.environ
    raw_root = (source.get("DATASET_ROOT") or _DEFAULT_ROOT).strip() or _DEFAULT_ROOT
    dataset_root = Path(raw_root).expanduser()
    # Prefer absolute resolution for DEBUG; INFO logs basename only.
    try:
        resolved = dataset_root.resolve()
    except OSError:
        resolved = dataset_root

    settings = DatasetSettings(
        dataset_root=resolved,
        target_ppq=_int_env(
            source,
            "DATASET_TARGET_PPQ",
            IMPORT_DEFAULT_TARGET_PPQ,
            minimum=24,
            maximum=9600,
        ),
        collapse_dup_notes=_bool_env(source, "DATASET_COLLAPSE_DUP_NOTES", True),
        max_source_bytes=_int_env(
            source,
            "DATASET_MAX_SOURCE_BYTES",
            _DEFAULT_MAX_SOURCE_BYTES,
            minimum=1024,
            maximum=500 * 1024 * 1024,
        ),
        max_notes=_int_env(
            source,
            "DATASET_MAX_NOTES",
            _DEFAULT_MAX_NOTES,
            minimum=1,
            maximum=5_000_000,
        ),
        max_bars=_int_env(
            source,
            "DATASET_MAX_BARS",
            _DEFAULT_MAX_BARS,
            minimum=1,
            maximum=50_000,
        ),
        max_tracks=_int_env(
            source,
            "DATASET_MAX_TRACKS",
            _DEFAULT_MAX_TRACKS,
            minimum=1,
            maximum=512,
        ),
        max_ppq=_int_env(
            source,
            "DATASET_MAX_PPQ",
            _DEFAULT_MAX_PPQ,
            minimum=24,
            maximum=9600,
        ),
        max_expanded_bytes=_int_env(
            source,
            "DATASET_MAX_EXPANDED_BYTES",
            _DEFAULT_MAX_EXPANDED_BYTES,
            minimum=1024,
            maximum=1_000 * 1024 * 1024,
        ),
        max_compression_ratio=_float_env(
            source,
            "DATASET_MAX_COMPRESSION_RATIO",
            _DEFAULT_MAX_COMPRESSION_RATIO,
            minimum=1.0,
            maximum=1000.0,
        ),
        max_archive_entries=_int_env(
            source,
            "DATASET_MAX_ARCHIVE_ENTRIES",
            _DEFAULT_MAX_ARCHIVE_ENTRIES,
            minimum=1,
            maximum=10_000,
        ),
        max_metadata_changes=_int_env(
            source,
            "DATASET_MAX_METADATA_CHANGES",
            _DEFAULT_MAX_METADATA_CHANGES,
            minimum=0,
            maximum=100_000,
        ),
        max_active_notes=_int_env(
            source,
            "DATASET_MAX_ACTIVE_NOTES",
            _DEFAULT_MAX_ACTIVE_NOTES,
            minimum=1,
            maximum=50_000,
        ),
        split_seed=_int_env(
            source,
            "DATASET_SPLIT_SEED",
            _DEFAULT_SPLIT_SEED,
            minimum=0,
            maximum=2**31 - 1,
        ),
        train_ratio=_float_env(
            source,
            "DATASET_TRAIN_RATIO",
            _DEFAULT_TRAIN_RATIO,
            minimum=0.0,
            maximum=1.0,
        ),
        val_ratio=_float_env(
            source,
            "DATASET_VAL_RATIO",
            _DEFAULT_VAL_RATIO,
            minimum=0.0,
            maximum=1.0,
        ),
        test_ratio=_float_env(
            source,
            "DATASET_TEST_RATIO",
            _DEFAULT_TEST_RATIO,
            minimum=0.0,
            maximum=1.0,
        ),
        pipeline_version=DATASET_PIPELINE_VERSION,
    )

    root_exists = settings.dataset_root.exists()
    logger.info(
        "Dataset settings loaded",
        extra={
            "dataset_root_basename": settings.dataset_root.name,
            "root_exists": root_exists,
            "target_ppq": settings.target_ppq,
            "collapse_dup_notes": settings.collapse_dup_notes,
            "max_source_bytes": settings.max_source_bytes,
            "max_notes": settings.max_notes,
            "max_bars": settings.max_bars,
            "split_seed": settings.split_seed,
            "pipeline_version": settings.pipeline_version,
        },
    )
    logger.debug(
        "Dataset settings resolved paths",
        extra={
            "dataset_root": str(settings.dataset_root),
            "fingerprint_profile": settings.fingerprint_profile,
            "train_ratio": settings.train_ratio,
            "val_ratio": settings.val_ratio,
            "test_ratio": settings.test_ratio,
        },
    )
    if not root_exists:
        logger.warning(
            "DATASET_ROOT missing; create-on-build policy applies",
            extra={
                "dataset_root_basename": settings.dataset_root.name,
                "create_root_on_build": settings.create_root_on_build,
            },
        )
    return settings


def dataset_settings_as_import_settings(settings: DatasetSettings) -> ImportSettings:
    """Map dataset caps into ImportSettings for reuse of MIDI/MusicXML parsers."""
    return ImportSettings(
        max_upload_bytes=settings.max_source_bytes,
        max_expanded_bytes=settings.max_expanded_bytes,
        max_compression_ratio=settings.max_compression_ratio,
        max_archive_entries=settings.max_archive_entries,
        max_tracks=settings.max_tracks,
        max_notes=settings.max_notes,
        max_bars=settings.max_bars,
        max_ppq=settings.max_ppq,
        max_metadata_changes=settings.max_metadata_changes,
        max_active_notes=settings.max_active_notes,
        default_target_ppq=settings.target_ppq,
    )


def ensure_dataset_root(settings: DatasetSettings) -> Path:
    """Create ``DATASET_ROOT`` when create-on-build is enabled."""
    root = settings.dataset_root
    if root.exists():
        return root
    if not settings.create_root_on_build:
        raise FileNotFoundError(f"DATASET_ROOT does not exist: {root.name}")
    root.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Created DATASET_ROOT",
        extra={"dataset_root_basename": root.name},
    )
    return root


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    logger.warning(
        "Invalid boolean dataset setting; using default",
        extra={"setting_name": name, "fallback": default},
    )
    return default


def _int_env(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = env.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning(
            "Invalid integer dataset setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Dataset setting out of range; clamped",
            extra={
                "setting_name": name,
                "fallback": clamped,
                "minimum": minimum,
                "maximum": maximum,
            },
        )
        return clamped
    return value


def _float_env(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw_value = env.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        value = float(raw_value)
    except ValueError:
        logger.warning(
            "Invalid float dataset setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Dataset setting out of range; clamped",
            extra={
                "setting_name": name,
                "fallback": clamped,
                "minimum": minimum,
                "maximum": maximum,
            },
        )
        return clamped
    return value
