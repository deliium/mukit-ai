"""SQLite connection helpers and numbered schema migrations."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

logger = logging.getLogger(__name__)

# backend/app/db/connection.py → parents[2] == backend/
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT_DB_PATH = _BACKEND_ROOT / "data" / "projects.db"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

_CREATE_TRIGGER_HEAD = re.compile(r"CREATE\s+TRIGGER\b", re.IGNORECASE)
_TRIGGER_END = re.compile(r"\bEND\s*;", re.IGNORECASE)

_initialized_paths: set[str] = set()


def split_sql_statements(script: str) -> list[str]:
    """Split a SQL script into executable statements.

    Handles ``CREATE TRIGGER ... BEGIN ... END;`` blocks that contain internal
    semicolons. Line comments starting with ``--`` are stripped outside strings.
    """
    statements: list[str] = []
    length = len(script)
    index = 0

    while index < length:
        while index < length and script[index].isspace():
            index += 1
        if index >= length:
            break

        if script.startswith("--", index):
            newline = script.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue

        trigger_match = _CREATE_TRIGGER_HEAD.match(script, index)
        if trigger_match is not None:
            end_match = _TRIGGER_END.search(script, trigger_match.end())
            if end_match is None:
                raise ValueError("Unterminated CREATE TRIGGER in migration SQL")
            statement = script[index : end_match.end()].strip()
            if statement:
                statements.append(statement)
            index = end_match.end()
            continue

        start = index
        in_single = False
        in_double = False
        while index < length:
            char = script[index]
            if char == "'" and not in_double:
                if in_single and index + 1 < length and script[index + 1] == "'":
                    index += 2
                    continue
                in_single = not in_single
            elif char == '"' and not in_single:
                in_double = not in_double
            elif char == ";" and not in_single and not in_double:
                statement = script[start:index].strip()
                if statement:
                    statements.append(statement)
                index += 1
                break
            index += 1
        else:
            statement = script[start:].strip()
            if statement:
                statements.append(statement)

    return statements


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
    """Ensure parent dirs exist, open SQLite, and apply pending migrations."""
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
        with get_connection(db_path, ensure_initialized=False) as conn:
            applied = apply_migrations(conn)
        _initialized_paths.add(str(db_path))
        logger.info(
            "Project database ready",
            extra={
                "project_db_path": str(db_path),
                "applied_migrations": applied,
                "applied_count": len(applied),
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
            bootstrap = sqlite3.connect(str(path))
            try:
                bootstrap.row_factory = sqlite3.Row
                apply_migrations(bootstrap)
                bootstrap.commit()
            finally:
                bootstrap.close()
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


def list_migration_files(migrations_dir: Path | None = None) -> list[Path]:
    """Return numbered ``*.sql`` migration files sorted by filename."""
    directory = migrations_dir if migrations_dir is not None else MIGRATIONS_DIR
    files = sorted(directory.glob("*.sql"))
    logger.debug(
        "Discovered SQL migration files",
        extra={"migrations_dir": str(directory), "count": len(files)},
    )
    return files


def apply_migrations(
    conn: sqlite3.Connection,
    migrations_dir: Path | None = None,
) -> list[str]:
    """Apply pending numbered SQL migrations; return versions applied this run.

    Each migration's DDL statements and its ``schema_migrations`` registry insert
    run in one explicit SQLite transaction so a mid-migration failure rolls back
    cleanly and can be retried.
    """
    # Ensure the registry table exists outside per-migration transactions.
    if conn.in_transaction:
        conn.commit()
    conn.execute(_SCHEMA_MIGRATIONS_DDL)
    conn.commit()

    already = {
        row["version"]
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    logger.debug(
        "Loaded applied migration versions",
        extra={"applied_count": len(already), "versions": sorted(already)},
    )

    applied_now: list[str] = []
    for migration_path in list_migration_files(migrations_dir):
        version = migration_path.stem
        if version in already:
            logger.debug(
                "Skipping already-applied migration",
                extra={"migration_version": version},
            )
            continue

        sql = migration_path.read_text(encoding="utf-8")
        statements = split_sql_statements(sql)
        logger.info(
            "Applying database migration",
            extra={
                "migration_version": version,
                "sql_length": len(sql),
                "statement_count": len(statements),
            },
        )
        stage = "begin"
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            for index, statement in enumerate(statements):
                stage = f"statement:{index}"
                logger.debug(
                    "Executing migration statement",
                    extra={
                        "migration_version": version,
                        "statement_index": index,
                        "statement_length": len(statement),
                    },
                )
                conn.execute(statement)
            stage = "registry_insert"
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
                (version,),
            )
            stage = "commit"
            conn.commit()
            applied_now.append(version)
            logger.info(
                "Database migration applied",
                extra={
                    "migration_version": version,
                    "statement_count": len(statements),
                },
            )
        except Exception as exc:
            if conn.in_transaction:
                conn.rollback()
            logger.error(
                "Database migration failed",
                extra={
                    "migration_version": version,
                    "migration_stage": stage,
                    "error_type": type(exc).__name__,
                    "error_detail": str(exc)[:300],
                },
            )
            raise

    return applied_now
