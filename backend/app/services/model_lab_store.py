"""SQLite index for ``model.lab.experiment.v1``.

Does not store note events, weight blobs, or dataset corpora.
``ai_agents`` must not import this module.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.db.connection import get_connection, get_project_db_path
from app.model_lab_schemas import (
    EXPERIMENT_SCHEMA,
    ModelLabError,
    ModelLabExperimentV1,
    ModelLabRuntimeSummaryV1,
    ModelLabTokenizerExpectationV1,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

_COLUMNS = (
    "id",
    "schema_version",
    "display_name",
    "status",
    "dataset_version_id",
    "tokenizer_version",
    "tokenizer_vocab_hash_prefix",
    "architecture_digest_prefix",
    "train_digest_prefix",
    "seed",
    "evaluation_version",
    "registered_checkpoint_step",
    "registry_model_id",
    "engine",
    "runtime_json",
    "owner_actor_id",
    "error_code",
    "created_at",
    "updated_at",
)

_TERMINAL_NO_TRANSITION = frozenset({"deleted"})


def _refuse(
    code: str,
    message: str,
    *,
    http_status: int = 422,
    details: dict[str, Any] | None = None,
) -> ModelLabError:
    logger.warning("Model Lab store refused", extra={"code": code})
    return ModelLabError(code, message, http_status=http_status, details=details)


def _parse_runtime(raw: str) -> ModelLabRuntimeSummaryV1:
    try:
        return ModelLabRuntimeSummaryV1.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.debug(
            "Model Lab runtime parse failed",
            extra={"field_name": "runtime_json", "code": "model_lab_runtime_invalid"},
        )
        raise _refuse(
            "model_lab_runtime_invalid",
            "Stored Model Lab runtime summary is not valid.",
            http_status=500,
        ) from exc


def _row_to_experiment(row: sqlite3.Row) -> ModelLabExperimentV1:
    return ModelLabExperimentV1(
        id=row["id"],
        display_name=row["display_name"],
        status=row["status"],
        dataset_version_id=row["dataset_version_id"],
        dataset_name="",
        tokenizer_expectation=ModelLabTokenizerExpectationV1(
            expected_tokenizer_version=row["tokenizer_version"],
            vocab_hash_prefix=row["tokenizer_vocab_hash_prefix"],
        ),
        architecture_digest_prefix=row["architecture_digest_prefix"],
        train_digest_prefix=row["train_digest_prefix"],
        seed=int(row["seed"]),
        checkpoint_refs=[],
        runtime=_parse_runtime(row["runtime_json"]),
        evaluation_version=row["evaluation_version"],
        listening_set_digest=None,
        registered_checkpoint_step=row["registered_checkpoint_step"],
        registry_model_id=row["registry_model_id"],
        engine=row["engine"],
        owner_actor_id=row["owner_actor_id"],
        error_code=row["error_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _fetch(conn: sqlite3.Connection, experiment_id: str) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM model_lab_experiments WHERE id = ?",
        (experiment_id,),
    ).fetchone()


def insert_experiment(
    *,
    experiment_id: str,
    display_name: str,
    status: str = "queued",
    dataset_version_id: str,
    tokenizer_version: str,
    tokenizer_vocab_hash_prefix: str,
    architecture_digest_prefix: str,
    train_digest_prefix: str,
    seed: int,
    engine: str,
    runtime: ModelLabRuntimeSummaryV1 | None = None,
    evaluation_version: str | None = None,
    owner_actor_id: str | None = None,
    db_path: Path | str | None = None,
) -> ModelLabExperimentV1:
    """Insert one experiment index row. Live display names must be unique."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    now = utc_now_iso()
    runtime_doc = runtime or ModelLabRuntimeSummaryV1()
    runtime_text = json.dumps(
        runtime_doc.model_dump(mode="json"),
        separators=(",", ":"),
        sort_keys=True,
    )
    logger.info(
        "Model Lab insert started",
        extra={"experiment_id": experiment_id, "status": status, "engine": engine},
    )
    try:
        with get_connection(path) as conn:
            taken = conn.execute(
                """
                SELECT id FROM model_lab_experiments
                WHERE display_name = ? AND status != 'deleted'
                """,
                (display_name,),
            ).fetchone()
            if taken is not None:
                raise _refuse(
                    "model_lab_name_taken",
                    "That display name already belongs to a Model Lab experiment.",
                    http_status=409,
                )
            conn.execute(
                """
                INSERT INTO model_lab_experiments (
                    id, schema_version, display_name, status, dataset_version_id,
                    tokenizer_version, tokenizer_vocab_hash_prefix,
                    architecture_digest_prefix, train_digest_prefix, seed,
                    evaluation_version, registered_checkpoint_step, registry_model_id,
                    engine, runtime_json, owner_actor_id, error_code,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    experiment_id,
                    EXPERIMENT_SCHEMA,
                    display_name,
                    status,
                    dataset_version_id,
                    tokenizer_version,
                    tokenizer_vocab_hash_prefix[:16],
                    architecture_digest_prefix[:16],
                    train_digest_prefix[:16],
                    int(seed),
                    evaluation_version,
                    engine,
                    runtime_text,
                    owner_actor_id,
                    now,
                    now,
                ),
            )
            stored = _fetch(conn, experiment_id)
    except sqlite3.IntegrityError as exc:
        logger.warning("Model Lab insert conflict", extra={"code": "model_lab_name_taken"})
        raise _refuse(
            "model_lab_name_taken",
            "That display name already belongs to a Model Lab experiment.",
            http_status=409,
        ) from exc
    if stored is None:
        raise _refuse("model_lab_not_found", "Model Lab row was not stored.", http_status=500)
    record = _row_to_experiment(stored)
    logger.info(
        "Model Lab inserted",
        extra={
            "experiment_id": experiment_id,
            "status": record.status,
            "engine": record.engine,
        },
    )
    return record


def get_experiment(
    experiment_id: str,
    *,
    db_path: Path | str | None = None,
    include_deleted: bool = False,
) -> ModelLabExperimentV1:
    """Return one experiment. Deleted rows are not found unless requested."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        row = _fetch(conn, experiment_id)
    if row is None:
        raise _refuse(
            "model_lab_not_found",
            "Model Lab experiment was not found.",
            http_status=404,
            details={"experiment_id": experiment_id},
        )
    if row["status"] == "deleted" and not include_deleted:
        raise _refuse(
            "model_lab_not_found",
            "Model Lab experiment was not found.",
            http_status=404,
            details={"experiment_id": experiment_id},
        )
    return _row_to_experiment(row)


def get_by_registry_id(
    registry_model_id: str,
    *,
    db_path: Path | str | None = None,
) -> ModelLabExperimentV1 | None:
    """Return a non-deleted registered experiment, or None."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        row = conn.execute(
            f"""
            SELECT {', '.join(_COLUMNS)}
            FROM model_lab_experiments
            WHERE registry_model_id = ? AND status != 'deleted'
            """,
            (registry_model_id,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_experiment(row)


def list_experiments(
    *,
    owner_actor_id: str | None = None,
    filter_owner: bool = False,
    db_path: Path | str | None = None,
) -> list[ModelLabExperimentV1]:
    """List non-deleted experiments. This read does not change status."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    logger.debug(
        "Model Lab list started",
        extra={"filter_owner": filter_owner},
    )
    sql = f"""
        SELECT {', '.join(_COLUMNS)}
        FROM model_lab_experiments
        WHERE status != 'deleted'
    """
    params: tuple[Any, ...] = ()
    if filter_owner:
        sql += " AND owner_actor_id = ?"
        params = (owner_actor_id,)
    sql += " ORDER BY created_at ASC"
    with get_connection(path) as conn:
        rows = conn.execute(sql, params).fetchall()
    records = [_row_to_experiment(row) for row in rows]
    logger.debug("Model Lab list finished", extra={"count": len(records)})
    return records


def list_registered(
    *,
    db_path: Path | str | None = None,
) -> list[ModelLabExperimentV1]:
    """Return complete experiments with a registry model id."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        rows = conn.execute(
            f"""
            SELECT {', '.join(_COLUMNS)}
            FROM model_lab_experiments
            WHERE status = 'complete'
              AND registry_model_id IS NOT NULL
              AND registered_checkpoint_step IS NOT NULL
            ORDER BY updated_at ASC
            """
        ).fetchall()
    return [_row_to_experiment(row) for row in rows]


def count_running(*, db_path: Path | str | None = None) -> int:
    """Count rows with status ``running`` (for concurrent-run caps)."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM model_lab_experiments WHERE status = 'running'"
        ).fetchone()
    return int(row["n"] if row is not None else 0)


def update_experiment(
    experiment_id: str,
    *,
    status: str | None = None,
    evaluation_version: str | None = None,
    runtime: ModelLabRuntimeSummaryV1 | None = None,
    registered_checkpoint_step: int | None = None,
    registry_model_id: str | None = None,
    clear_registry: bool = False,
    error_code: str | None = None,
    clear_error: bool = False,
    db_path: Path | str | None = None,
) -> ModelLabExperimentV1:
    """Update status / runtime / register fields. Deleted rows stay deleted."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        current = _fetch(conn, experiment_id)
        if current is None:
            raise _refuse(
                "model_lab_not_found",
                "Model Lab experiment was not found.",
                http_status=404,
                details={"experiment_id": experiment_id},
            )
        if current["status"] in _TERMINAL_NO_TRANSITION:
            logger.warning(
                "Model Lab transition refused",
                extra={
                    "experiment_id": experiment_id,
                    "code": "model_lab_deleted",
                    "status": current["status"],
                },
            )
            return _row_to_experiment(current)

        next_status = status if status is not None else current["status"]
        next_eval = (
            evaluation_version
            if evaluation_version is not None
            else current["evaluation_version"]
        )
        next_runtime = current["runtime_json"]
        if runtime is not None:
            next_runtime = json.dumps(
                runtime.model_dump(mode="json"),
                separators=(",", ":"),
                sort_keys=True,
            )
        next_reg_step = current["registered_checkpoint_step"]
        next_reg_id = current["registry_model_id"]
        if clear_registry:
            next_reg_step = None
            next_reg_id = None
        if registered_checkpoint_step is not None:
            next_reg_step = int(registered_checkpoint_step)
        if registry_model_id is not None:
            next_reg_id = registry_model_id
        next_error = None if clear_error else current["error_code"]
        if error_code is not None:
            next_error = error_code
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE model_lab_experiments
            SET status = ?,
                evaluation_version = ?,
                runtime_json = ?,
                registered_checkpoint_step = ?,
                registry_model_id = ?,
                error_code = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                next_status,
                next_eval,
                next_runtime,
                next_reg_step,
                next_reg_id,
                next_error,
                now,
                experiment_id,
            ),
        )
        stored = _fetch(conn, experiment_id)
    if stored is None:
        raise _refuse("model_lab_not_found", "Model Lab experiment was not found.", http_status=404)
    logger.info(
        "Model Lab status changed",
        extra={
            "experiment_id": experiment_id,
            "status": stored["status"],
            "engine": stored["engine"],
        },
    )
    return _row_to_experiment(stored)


def register_checkpoint(
    experiment_id: str,
    *,
    checkpoint_step: int,
    db_path: Path | str | None = None,
) -> ModelLabExperimentV1:
    """Set registry fields for a complete experiment. Id is ``lab:{id}``."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    current = get_experiment(experiment_id, db_path=path)
    if current.status != "complete":
        raise _refuse(
            "model_lab_not_registerable",
            "Only complete Model Lab experiments can be registered.",
            http_status=409,
            details={"status": current.status},
        )
    registry_model_id = f"lab:{experiment_id}"
    logger.info(
        "Model Lab register",
        extra={
            "experiment_id": experiment_id,
            "status": "complete",
            "engine": current.engine,
            "registry_model_id": registry_model_id,
        },
    )
    return update_experiment(
        experiment_id,
        registered_checkpoint_step=int(checkpoint_step),
        registry_model_id=registry_model_id,
        clear_error=True,
        db_path=path,
    )
