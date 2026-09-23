"""HTTP routes for co-performance cold-path accompaniment predict."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from app.live_performance_schemas import (
    LiveAccompanimentChunkV1,
    LiveAccompanimentPredictRequestV1,
    LivePerformanceError,
    map_live_performance_error_to_http,
)
from app.services.live_accompaniment_predict import predict_live_accompaniment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live", tags=["live-performance"])


@router.post(
    "/accompaniment/predict",
    response_model=LiveAccompanimentChunkV1,
)
async def predict_accompaniment_route(
    request: LiveAccompanimentPredictRequestV1,
) -> LiveAccompanimentChunkV1:
    """Prefetch accompaniment chunk — session-only; never mutates composition.v2.

    Optional WS streaming is deferred until Task 8 measurement gate fails HTTP budget.
    """
    started = time.perf_counter()
    try:
        chunk = predict_live_accompaniment(request)
    except LivePerformanceError as exc:
        status, detail = map_live_performance_error_to_http(exc)
        raise HTTPException(status_code=status, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001 — sanitize unexpected
        logger.exception(
            "Live predict internal failure",
            extra={"request_id": request.request_id[:16]},
        )
        status, detail = map_live_performance_error_to_http(
            LivePerformanceError(
                "Live predict failed",
                code="live_predict_internal",
            )
        )
        raise HTTPException(status_code=status, detail=detail) from exc

    logger.debug(
        "Live predict route done",
        extra={
            "request_id": request.request_id[:16],
            "route_ms": round((time.perf_counter() - started) * 1000, 2),
            "event_count": len(chunk.events),
        },
    )
    return chunk
