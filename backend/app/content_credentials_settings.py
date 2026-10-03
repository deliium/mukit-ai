"""Optional Content Credentials (C2PA) settings. Default off; not on /ready."""

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


@dataclass(frozen=True)
class ContentCredentialsSettings:
    enabled: bool
    fake_mode: bool
    credentials_root: Path


def _bool_env(source: Mapping[str, str], key: str, *, default: bool) -> bool:
    raw = (source.get(key) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _resolve_project_db_path(env: Mapping[str, str]) -> Path:
    raw = (env.get("PROJECT_DB_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_PROJECT_DB_PATH


def default_content_credentials_root(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    return _resolve_project_db_path(source).parent / "content_credentials"


def load_content_credentials_settings(
    env: Mapping[str, str] | None = None,
) -> ContentCredentialsSettings:
    source = env if env is not None else os.environ
    root_raw = (source.get("CONTENT_CREDENTIALS_ROOT") or "").strip()
    credentials_root = (
        Path(root_raw).expanduser() if root_raw else default_content_credentials_root(source)
    )
    dataset_raw = (source.get("DATASET_ROOT") or "").strip()
    dataset_root = Path(dataset_raw).expanduser() if dataset_raw else None
    reject_storage_root(
        credentials_root,
        dataset_root=dataset_root,
        project_db=_resolve_project_db_path(source),
    )
    settings = ContentCredentialsSettings(
        enabled=_bool_env(source, "CONTENT_CREDENTIALS_ENABLED", default=False),
        fake_mode=_bool_env(source, "CONTENT_CREDENTIALS_FAKE", default=False),
        credentials_root=credentials_root,
    )
    logger.debug(
        "Content credentials settings loaded",
        extra={
            "enabled": settings.enabled,
            "fake_mode": settings.fake_mode,
            "root_basename": settings.credentials_root.name,
        },
    )
    return settings
