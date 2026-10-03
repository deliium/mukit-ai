"""Optional Adaptive Engine proxy for continuous maintain.

Imports the studio continuation service. Engine modules listed in the
architecture import ban do not import this module's dependencies directly;
the HTTP router imports this facade when both continuous flags are on.
"""

from __future__ import annotations

import asyncio
import logging

from app.adaptive_engine_schemas import AdaptiveEngineError
from app.adaptive_runtime_continuation_schemas import (
    AdaptiveRuntimeBufferV1,
    AdaptiveRuntimeContinuationStartRequest,
    AdaptiveRuntimeContinuationV1,
)
from app.adaptive_runtime_continuation_settings import (
    load_adaptive_runtime_continuation_settings,
)
from app.adaptive_score_schemas import AdaptiveScoreError
from app.services.adaptive_engine_service import get_default_engine_registry
from app.services.adaptive_runtime_continuation_service import (
    get_adaptive_runtime_buffer,
    get_adaptive_runtime_continuation,
    maintain_adaptive_runtime_continuation,
    start_adaptive_runtime_continuation,
)

logger = logging.getLogger(__name__)


def _schedule(coro):
    return asyncio.get_running_loop().create_task(coro)


def _require_engine_continuous(session_id: str) -> tuple[str, str, int]:
    settings = load_adaptive_runtime_continuation_settings()
    if (
        not settings.adaptive_continuous_enabled
        or not settings.adaptive_engine_continuous_enabled
    ):
        logger.info(
            "Engine continuous refused; flag off",
            extra={
                "code": "adaptive_engine_continuous_disabled",
                "session_id": session_id if session_id.startswith("aeng_") else "invalid",
            },
        )
        raise AdaptiveEngineError(
            "adaptive_engine_continuous_disabled",
            session_id=session_id if session_id.startswith("aeng_") else None,
        )
    held = get_default_engine_registry().by_id(session_id)
    if held is None:
        raise AdaptiveEngineError("engine_session_missing", session_id=session_id)
    return held.project_id, held.score_id, held.document_revision


def maintain_engine_continuous(session_id: str) -> AdaptiveRuntimeContinuationV1:
    """Arm continuous continuation for the engine-bound score and maintain once."""
    project_id, score_id, revision = _require_engine_continuous(session_id)
    existing = get_adaptive_runtime_continuation(project_id, score_id)
    try:
        if existing is None or not existing.continuous:
            start_adaptive_runtime_continuation(
                project_id,
                score_id,
                AdaptiveRuntimeContinuationStartRequest(
                    expected_document_revision=revision,
                    mode="continuation",
                    continuous=True,
                ),
            )
        snapshot = maintain_adaptive_runtime_continuation(
            project_id,
            score_id,
            scheduler=_schedule,
        )
    except AdaptiveScoreError as exc:
        if exc.code == "adaptive_continuous_disabled":
            raise AdaptiveEngineError(
                "adaptive_engine_continuous_disabled",
                session_id=session_id,
            ) from exc
        if exc.code == "continuation_not_running":
            raise AdaptiveEngineError(
                "engine_playback_stopped",
                session_id=session_id,
            ) from exc
        raise AdaptiveEngineError(
            "engine_payload_invalid",
            session_id=session_id,
            details={"code": exc.code},
        ) from exc
    logger.info(
        "Engine continuous maintain",
        extra={
            "code": "engine_continuous_maintain",
            "session_id": session_id,
            "continuous": snapshot.continuous,
            "job_status": snapshot.job_status,
            "fallback_kind": snapshot.fallback_kind,
            "source": snapshot.source,
        },
    )
    return snapshot


def get_engine_continuous_buffer(session_id: str) -> AdaptiveRuntimeBufferV1 | None:
    project_id, score_id, _revision = _require_engine_continuous(session_id)
    return get_adaptive_runtime_buffer(project_id, score_id)
