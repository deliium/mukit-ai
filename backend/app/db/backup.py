"""Offline SQLite backup and restore for PROJECT_DB_PATH.

Not an HTTP route. Revision restore stays a child revision on the live database.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Mapping

from app.db.connection import get_project_db_path
from app.storage_root_policy import StorageRootError, reject_storage_root

logger = logging.getLogger(__name__)


class BackupRefusal(Exception):
    """A backup or restore request that must exit 2."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Exit 0 on success, 2 on refusal, 1 on unexpected failure."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            byte_size = backup_database(dest=Path(args.dest))
        else:
            byte_size = restore_database(source=Path(args.source), dest=Path(args.dest))
    except BackupRefusal as exc:
        logger.warning("backup_refused", extra={"reason": exc.reason})
        return 2
    except StorageRootError as exc:
        logger.warning("backup_refused", extra={"reason": exc.reason})
        return 2
    except Exception as exc:
        logger.error("backup_failed", extra={"error_type": type(exc).__name__})
        return 1
    logger.info(
        "backup_finish",
        extra={"command": args.command, "basename": Path(args.dest).name, "byte_size": byte_size},
    )
    return 0


def backup_database(
    *,
    dest: Path,
    env: Mapping[str, str] | None = None,
) -> int:
    """Copy the live project database to ``dest`` using ``Connection.backup``."""
    source = get_project_db_path(env)
    logger.info(
        "backup_start",
        extra={"command": "backup", "basename": dest.name, "byte_size": _file_size(source)},
    )
    _require_source(source)
    _refuse_destination(dest, env=env, project_db=source)
    return _copy_sqlite(source, dest)


def restore_database(
    *,
    source: Path,
    dest: Path,
    env: Mapping[str, str] | None = None,
) -> int:
    """Copy a backup file onto ``dest``. ``dest`` must not be the live database."""
    project_db = get_project_db_path(env)
    logger.info(
        "backup_start",
        extra={"command": "restore", "basename": source.name, "byte_size": _file_size(source)},
    )
    _require_source(source)
    _refuse_destination(dest, env=env, project_db=project_db)
    return _copy_sqlite(source, dest)


def _refuse_destination(
    dest: Path,
    *,
    env: Mapping[str, str] | None,
    project_db: Path,
) -> None:
    source_env = env if env is not None else os.environ
    dataset_raw = (source_env.get("DATASET_ROOT") or "").strip()
    dataset_root = Path(dataset_raw).expanduser() if dataset_raw else None
    if _same_path(dest, project_db):
        logger.warning("backup_refused", extra={"reason": "project_db_path"})
        raise BackupRefusal("project_db_path")
    reject_storage_root(dest, dataset_root=dataset_root, project_db=project_db)


def _require_source(source: Path) -> None:
    if source.is_file():
        return
    logger.warning("backup_refused", extra={"reason": "missing_source"})
    raise BackupRefusal("missing_source")


def _copy_sqlite(source: Path, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.debug("sqlite_backup_copy", extra={"basename": dest.name})
    source_conn = sqlite3.connect(source)
    try:
        dest_conn = sqlite3.connect(dest)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()
    return dest.stat().st_size


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except OSError:
        return left.expanduser() == right.expanduser()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.db.backup")
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--dest", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--from", dest="source", required=True)
    restore.add_argument("--dest", required=True)
    return parser


if __name__ == "__main__":
    sys.exit(main())
