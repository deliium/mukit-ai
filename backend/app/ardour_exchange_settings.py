"""Deployment settings for Ardour session exchange packages.

``ARDOUR_EXCHANGE_ENABLED`` defaults off. Unrecognized truthy strings resolve
to off. ``ARDOUR_EXCHANGE_ROOT`` refuses ``DATASET_ROOT`` and ``PROJECT_DB_PATH``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.storage_root_policy import StorageRootError, reject_storage_root

logger = logging.getLogger(__name__)

ENABLED_ENV = "ARDOUR_EXCHANGE_ENABLED"
ROOT_ENV = "ARDOUR_EXCHANGE_ROOT"
MAX_PACKAGE_BYTES_ENV = "ARDOUR_EXCHANGE_MAX_PACKAGE_BYTES"
MAX_PACKAGES_ENV = "ARDOUR_EXCHANGE_MAX_PACKAGES"
PACKAGE_TTL_SECONDS_ENV = "ARDOUR_EXCHANGE_PACKAGE_TTL_SECONDS"

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_RECOGNIZED_OFF = frozenset({"", "0"})

_DEFAULT_MAX_PACKAGE_BYTES = 25 * 1024 * 1024
_DEFAULT_MAX_PACKAGES = 32
_DEFAULT_PACKAGE_TTL_SECONDS = 86_400

# Mirror backend/app/db/connection.py default without importing Alembic.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PROJECT_DB_PATH = _BACKEND_ROOT / "data" / "projects.db"

_default_logged = False


@dataclass(frozen=True)
class ArdourExchangeSettings:
    """Parsed ``ARDOUR_EXCHANGE_*`` environment values."""

    enabled: bool
    exchange_root: Path | None
    root_configured: bool
    max_package_bytes: int
    max_packages: int
    package_ttl_seconds: int


def _parse_truthy(raw: str | None) -> tuple[bool, bool]:
    text = "" if raw is None else str(raw).strip().lower()
    if text in _TRUTHY:
        return True, False
    if text in _RECOGNIZED_OFF:
        return False, False
    return False, True


def _clamp_int(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _resolve_project_db_path(env: Mapping[str, str]) -> Path:
    raw = (env.get("PROJECT_DB_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_PROJECT_DB_PATH


def _resolve_dataset_root(env: Mapping[str, str]) -> Path | None:
    raw = (env.get("DATASET_ROOT") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def load_ardour_exchange_settings(
    env: Mapping[str, str] | None = None,
) -> ArdourExchangeSettings:
    """Load exchange settings from ``env`` or the process environment."""
    source = env if env is not None else os.environ

    enabled, unrecognized = _parse_truthy(source.get(ENABLED_ENV))
    if unrecognized:
        logger.info(
            "Ardour exchange flag unrecognized; treating as off",
            extra={"code": "ardour_exchange_flag_unrecognized"},
        )
        enabled = False

    max_package_bytes = _clamp_int(
        source.get(MAX_PACKAGE_BYTES_ENV),
        default=_DEFAULT_MAX_PACKAGE_BYTES,
        minimum=64 * 1024,
        maximum=200 * 1024 * 1024,
    )
    max_packages = _clamp_int(
        source.get(MAX_PACKAGES_ENV),
        default=_DEFAULT_MAX_PACKAGES,
        minimum=1,
        maximum=500,
    )
    package_ttl_seconds = _clamp_int(
        source.get(PACKAGE_TTL_SECONDS_ENV),
        default=_DEFAULT_PACKAGE_TTL_SECONDS,
        minimum=60,
        maximum=30 * 86_400,
    )

    root_raw = (source.get(ROOT_ENV) or "").strip()
    exchange_root: Path | None = None
    root_configured = False
    if root_raw:
        candidate = Path(root_raw).expanduser()
        try:
            reject_storage_root(
                candidate,
                dataset_root=_resolve_dataset_root(source),
                project_db=_resolve_project_db_path(source),
            )
        except StorageRootError as exc:
            logger.warning(
                "Ardour exchange root refused",
                extra={"code": "storage_root_rejected", "reason": exc.reason},
            )
            exchange_root = None
            root_configured = False
        else:
            exchange_root = candidate
            root_configured = True

    settings = ArdourExchangeSettings(
        enabled=enabled,
        exchange_root=exchange_root,
        root_configured=root_configured,
        max_package_bytes=max_package_bytes,
        max_packages=max_packages,
        package_ttl_seconds=package_ttl_seconds,
    )

    root_basename = (
        settings.exchange_root.name if settings.exchange_root is not None else None
    )
    payload = {
        "enabled": settings.enabled,
        "root_basename": root_basename,
        "root_configured": settings.root_configured,
        "max_bytes": settings.max_package_bytes,
        "max_packages": settings.max_packages,
        "ttl_seconds": settings.package_ttl_seconds,
    }
    global _default_logged
    if env is None and not _default_logged:
        logger.debug("Ardour exchange settings loaded", extra=payload)
        _default_logged = True
    elif env is not None:
        logger.debug("Ardour exchange settings loaded", extra=payload)

    return settings
