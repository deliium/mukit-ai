"""SQLite connection helpers and Alembic schema upgrades."""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

logger = logging.getLogger(__name__)

# backend/app/db/connection.py → parents[2] == backend/
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT_DB_PATH = _BACKEND_ROOT / "data" / "projects.db"
ALEMBIC_INI_PATH = _BACKEND_ROOT / "alembic.ini"
ALEMBIC_SCRIPT_LOCATION = Path(__file__).resolve().parent / "alembic"

_initialized_paths: set[str] = set()


def sqlite_url_for_path(db_path: Path) -> str:
    """Build a SQLAlchemy SQLite URL for an absolute filesystem path."""
    resolved = Path(db_path).resolve()
    return f"sqlite:///{resolved.as_posix()}"


def get_project_db_path(env: Mapping[str, str] | None = None) -> Path:
    """Resolve PROJECT_DB_PATH from env or the local backend/data default."""
    source = env if env is not None else os.environ
    raw = source.get("PROJECT_DB_PATH")
    if raw:
        path = Path(raw).expanduser()
        logger.debug(
            "Resolved project DB path from env",
            extra={"project_db_path": str(path), "source": "PROJECT_DB_PATH"},
        )
        return path
    logger.debug(
        "Resolved project DB path from default",
        extra={"project_db_path": str(DEFAULT_PROJECT_DB_PATH), "source": "default"},
    )
    return DEFAULT_PROJECT_DB_PATH


def _alembic_config(db_path: Path) -> Config:
    """Build an Alembic Config bound to ``db_path``."""
    if not ALEMBIC_INI_PATH.is_file():
        raise FileNotFoundError(f"Alembic config not found: {ALEMBIC_INI_PATH}")
    cfg = Config(str(ALEMBIC_INI_PATH))
    cfg.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    url = sqlite_url_for_path(db_path)
    cfg.set_main_option("sqlalchemy.url", url)
    # Do not let alembic.ini fileConfig reset app/pytest LOG_LEVEL handlers.
    cfg.attributes["configure_logger"] = False
    logger.debug(
        "Built Alembic config",
        extra={
            "project_db_path": str(db_path.resolve()),
            "alembic_ini": str(ALEMBIC_INI_PATH),
            "script_location": str(ALEMBIC_SCRIPT_LOCATION),
        },
    )
    return cfg


def _current_revision(db_path: Path) -> str | None:
    """Return the current alembic_version revision for ``db_path``, if any."""
    engine = create_engine(sqlite_url_for_path(db_path))
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            return context.get_current_revision()
    finally:
        engine.dispose()


def run_alembic_upgrade(db_path: Path) -> str:
    """Upgrade ``db_path`` to Alembic head; return the head revision id.

    Empty databases receive the full baseline schema. Databases already at head
    are a no-op. There is no migration path from the legacy ``schema_migrations``
    registry — wipe the SQLite file before first Alembic apply.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = _alembic_config(path)
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("No Alembic head revision found")

    before = _current_revision(path)
    logger.info(
        "Running Alembic upgrade to head",
        extra={
            "project_db_path": str(path.resolve()),
            "alembic_revision_before": before,
            "alembic_head": head,
        },
    )
    try:
        command.upgrade(cfg, "head")
    except Exception as exc:
        logger.error(
            "Alembic upgrade failed",
            extra={
                "project_db_path": str(path.resolve()),
                "alembic_head": head,
                "error_type": type(exc).__name__,
                "error_detail": str(exc)[:300],
            },
        )
        raise

    after = _current_revision(path)
    logger.info(
        "Alembic upgrade complete",
        extra={
            "project_db_path": str(path.resolve()),
            "alembic_revision": after,
            "alembic_head": head,
            "upgraded": before != after,
        },
    )
    if after != head:
        raise RuntimeError(
            f"Alembic upgrade left database at {after!r}, expected head {head!r}"
        )
    return head


def ensure_database(env: Mapping[str, str] | None = None) -> Path:
    """Initialize the database once per resolved path (safe for TestClient and reload)."""
    db_path = get_project_db_path(env)
    key = str(db_path)
    if key in _initialized_paths:
        logger.debug(
            "Project database already initialized",
            extra={"project_db_path": key},
        )
        return db_path
    return initialize_database(env)


def reset_database_initialization_cache() -> None:
    """Clear the initialized-path cache (tests only)."""
    _initialized_paths.clear()
    logger.debug("Cleared project database initialization cache")


def initialize_database(env: Mapping[str, str] | None = None) -> Path:
    """Ensure parent dirs exist and upgrade the SQLite schema to Alembic head."""
    db_path = get_project_db_path(env)
    logger.info(
        "Initializing project database",
        extra={"project_db_path": str(db_path)},
    )
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug(
            "Ensured project DB parent directory exists",
            extra={"parent_dir": str(db_path.parent)},
        )
        revision = run_alembic_upgrade(db_path)
        _initialized_paths.add(str(db_path))
        logger.info(
            "Project database ready",
            extra={
                "project_db_path": str(db_path),
                "alembic_revision": revision,
            },
        )
        return db_path
    except Exception as exc:
        logger.error(
            "Project database initialization failed",
            extra={
                "project_db_path": str(db_path),
                "error_type": type(exc).__name__,
                "error_detail": str(exc)[:300],
            },
        )
        raise


@contextmanager
def get_connection(
    db_path: Path | str | None = None,
    *,
    ensure_initialized: bool = True,
) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with row factory and foreign keys enabled."""
    if db_path is None:
        path = ensure_database() if ensure_initialized else get_project_db_path()
    else:
        path = Path(db_path)
        if ensure_initialized and str(path) not in _initialized_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            run_alembic_upgrade(path)
            _initialized_paths.add(str(path))

    logger.debug("Opening SQLite connection", extra={"project_db_path": str(path)})
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
        logger.debug("SQLite connection committed", extra={"project_db_path": str(path)})
    except Exception:
        conn.rollback()
        logger.debug(
            "SQLite connection rolled back after error",
            extra={"project_db_path": str(path)},
        )
        raise
    finally:
        close_connection(conn)


def close_connection(conn: sqlite3.Connection) -> None:
    """Close a SQLite connection."""
    logger.debug("Closing SQLite connection")
    conn.close()
