"""Bounded picture-asset root and upload cap.

``VIDEO_ASSET_ROOT`` sits beside the project database and is refused when it
is ``DATASET_ROOT`` or ``PROJECT_DB_PATH``. Runtime verbosity stays on
``LOG_LEVEL``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.storage_root_policy import reject_storage_root

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PROJECT_DB_PATH = _BACKEND_ROOT / "data" / "projects.db"
_DEFAULT_MAX_UPLOAD_BYTES = 256 * 1024 * 1024
_MIN_UPLOAD_BYTES = 1024 * 1024
_MAX_UPLOAD_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True)
class VideoScoringSettings:
    asset_root: Path
    max_upload_bytes: int


def _resolve_project_db_path(env: Mapping[str, str]) -> Path:
    raw = (env.get("PROJECT_DB_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_PROJECT_DB_PATH


def default_video_asset_root(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    return _resolve_project_db_path(source).parent / "video_assets"


def load_video_scoring_settings(env: Mapping[str, str] | None = None) -> VideoScoringSettings:
    source = env if env is not None else os.environ
    root_raw = (source.get("VIDEO_ASSET_ROOT") or "").strip()
    asset_root = Path(root_raw).expanduser() if root_raw else default_video_asset_root(source)
    dataset_raw = (source.get("DATASET_ROOT") or "").strip()
    dataset_root = Path(dataset_raw).expanduser() if dataset_raw else None
    logger.debug("video scoring settings load", extra={"basename": asset_root.name})
    reject_storage_root(
        asset_root,
        dataset_root=dataset_root,
        project_db=_resolve_project_db_path(source),
    )
    logger.info("storage_root_accepted", extra={"settings": "video_scoring", "basename": asset_root.name})
    return VideoScoringSettings(
        asset_root=asset_root,
        max_upload_bytes=_int_env(
            source,
            "VIDEO_ASSET_MAX_UPLOAD_BYTES",
            _DEFAULT_MAX_UPLOAD_BYTES,
            minimum=_MIN_UPLOAD_BYTES,
            maximum=_MAX_UPLOAD_BYTES,
        ),
    )


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
            "Invalid integer video scoring setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Video scoring setting out of range; clamped",
            extra={"setting_name": name, "fallback": clamped, "minimum": minimum, "maximum": maximum},
        )
        return clamped
    return value
