"""Alembic environment for Mukit AI SQLite project database."""

from __future__ import annotations

import logging
import os
from logging.config import fileConfig
from pathlib import Path
from typing import Mapping

from alembic import context
from sqlalchemy import engine_from_config, pool

# Alembic Config object (provides access to alembic.ini values).
config = context.config

logger = logging.getLogger("alembic.env")

if config.attributes.get("configure_logger", True) and config.config_file_name is not None:
    # CLI only: preserve existing handlers; app-driven upgrades skip this so LOG_LEVEL stays intact.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# DDL-only migrations: no ORM models. Empty MetaData keeps autogenerate available later.
target_metadata = None

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PROJECT_DB_PATH = _BACKEND_ROOT / "data" / "projects.db"


def _resolve_db_path(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    raw = source.get("PROJECT_DB_PATH")
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_PROJECT_DB_PATH.resolve()


def sqlite_url_for_path(db_path: Path) -> str:
    """Build a SQLAlchemy SQLite URL for an absolute filesystem path."""
    resolved = db_path.resolve()
    # sqlite:////abs/path — three slashes from the scheme + leading slash of abs path
    return f"sqlite:///{resolved.as_posix()}"


def get_database_url() -> str:
    """Prefer Config sqlalchemy.url when set by the app; else PROJECT_DB_PATH."""
    configured = (config.get_main_option("sqlalchemy.url") or "").strip()
    placeholder_prefixes = ("driver://",)
    if configured and not configured.startswith(placeholder_prefixes):
        # Treat relative sqlite URLs from alembic.ini as unresolved; prefer env path
        # when the caller did not override via Config.set_main_option.
        if configured.startswith("sqlite:///") and not configured.startswith("sqlite:////"):
            # Relative or 3-slash form from ini — resolve via PROJECT_DB_PATH for CLI.
            path = _resolve_db_path()
            url = sqlite_url_for_path(path)
            logger.debug(
                "Resolved Alembic DB URL from PROJECT_DB_PATH",
                extra={"project_db_path": str(path), "source": "PROJECT_DB_PATH_or_default"},
            )
            return url
        logger.debug(
            "Using Alembic sqlalchemy.url from config",
            extra={"has_url": True},
        )
        return configured

    path = _resolve_db_path()
    url = sqlite_url_for_path(path)
    logger.debug(
        "Resolved Alembic DB URL from PROJECT_DB_PATH",
        extra={"project_db_path": str(path), "source": "PROJECT_DB_PATH_or_default"},
    )
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (SQL script emission)."""
    url = get_database_url()
    logger.info("Configuring Alembic offline migrations", extra={"mode": "offline"})
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (live SQLite engine)."""
    url = get_database_url()
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = url
    logger.info("Configuring Alembic online migrations", extra={"mode": "online"})
    try:
        connectable = engine_from_config(
            configuration,
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
    except Exception as exc:
        logger.error(
            "Alembic engine configuration failed",
            extra={
                "error_type": type(exc).__name__,
                "error_detail": str(exc)[:300],
            },
        )
        raise

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
