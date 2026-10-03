"""SQLite singleton for ``scheduling.policy.v1``.

Does not import Composition, project_store, or FastAPI. A missing row is not
an error: callers materialize env defaults without inserting.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from app.db.connection import get_connection, get_project_db_path
from app.scheduling_schemas import SchedulingError, SchedulingPolicyV1
from app.scheduling_settings import SchedulingSettings, load_scheduling_settings

logger = logging.getLogger(__name__)

_POLICY_ID = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve(db_path: Path | str | None) -> Path:
    return Path(db_path) if db_path is not None else get_project_db_path()


def policy_from_settings(settings: SchedulingSettings) -> SchedulingPolicyV1:
    """Build a policy document from env defaults without touching SQLite."""
    return SchedulingPolicyV1(
        mode=settings.default_mode,
        allow_public_cloud=settings.allow_public_cloud,
        fixed_node_id=settings.fixed_node_id,
        max_attempts=settings.max_attempts,
        document_revision=1,
    )


def get_policy(
    *,
    db_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> SchedulingPolicyV1:
    """Return the stored policy, or env-materialized defaults when no row."""
    path = _resolve(db_path)
    logger.debug("scheduling policy read", extra={"table": "scheduling_policy"})
    try:
        with get_connection(path) as conn:
            row = conn.execute(
                "SELECT body_json FROM scheduling_policy WHERE id = ?",
                (_POLICY_ID,),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "scheduling policy read failed; using env defaults",
            extra={"error_type": type(exc).__name__},
        )
        return policy_from_settings(load_scheduling_settings(env))

    if row is None:
        policy = policy_from_settings(load_scheduling_settings(env))
        logger.debug(
            "scheduling policy default",
            extra={
                "mode": policy.mode,
                "allow_public_cloud": policy.allow_public_cloud,
                "max_attempts": policy.max_attempts,
            },
        )
        return policy
    return SchedulingPolicyV1.model_validate(json.loads(row["body_json"]))


def put_policy(
    policy: SchedulingPolicyV1,
    *,
    expected_revision: int,
    db_path: Path | str | None = None,
) -> SchedulingPolicyV1:
    """CAS-write the singleton policy.

    ``expected_revision`` is the revision the client last read (1 for an
    env-materialized default). ``policy.document_revision`` must be
    ``expected_revision + 1``.
    """
    if policy.document_revision != expected_revision + 1:
        raise SchedulingError(
            "scheduling_invalid_policy",
            "document_revision must be expected_revision + 1.",
            details={
                "expected_revision": expected_revision,
                "document_revision": policy.document_revision,
            },
        )

    path = _resolve(db_path)
    updated_at = _utc_now()
    next_policy = policy.model_copy(update={"updated_at": updated_at})
    body = json.dumps(
        next_policy.model_dump(mode="json"),
        separators=(",", ":"),
        sort_keys=True,
    )
    logger.debug(
        "scheduling policy write start",
        extra={
            "mode": next_policy.mode,
            "expected_revision": expected_revision,
            "document_revision": next_policy.document_revision,
        },
    )
    with get_connection(path) as conn:
        existing = conn.execute(
            "SELECT document_revision FROM scheduling_policy WHERE id = ?",
            (_POLICY_ID,),
        ).fetchone()
        if existing is None:
            if expected_revision != 1:
                raise SchedulingError(
                    "scheduling_conflict",
                    "Scheduling policy revision conflict.",
                    details={"expected_revision": expected_revision, "current_revision": None},
                )
            conn.execute(
                """
                INSERT INTO scheduling_policy (id, body_json, document_revision, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (_POLICY_ID, body, next_policy.document_revision, updated_at),
            )
        else:
            current_revision = int(existing["document_revision"])
            if current_revision != expected_revision:
                logger.debug(
                    "scheduling policy CAS conflict",
                    extra={
                        "expected_revision": expected_revision,
                        "current_revision": current_revision,
                    },
                )
                raise SchedulingError(
                    "scheduling_conflict",
                    "Scheduling policy revision conflict.",
                    details={
                        "expected_revision": expected_revision,
                        "current_revision": current_revision,
                    },
                )
            cursor = conn.execute(
                """
                UPDATE scheduling_policy
                SET body_json = ?, document_revision = ?, updated_at = ?
                WHERE id = ? AND document_revision = ?
                """,
                (
                    body,
                    next_policy.document_revision,
                    updated_at,
                    _POLICY_ID,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise SchedulingError(
                    "scheduling_conflict",
                    "Scheduling policy revision conflict.",
                    details={"expected_revision": expected_revision},
                )
    logger.info(
        "scheduling policy written",
        extra={
            "mode": next_policy.mode,
            "allow_public_cloud": next_policy.allow_public_cloud,
            "document_revision": next_policy.document_revision,
            "max_attempts": next_policy.max_attempts,
        },
    )
    return next_policy
