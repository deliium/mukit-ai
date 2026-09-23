"""Cold-path live accompaniment predict orchestration.

Bounded DTO only — never awaits on Transport. Fake mode is always available
for CI; optional non-fake path returns unavailable in v1 (no new AiOperation).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Mapping

from app.live_performance_schemas import (
    LIVE_PREDICT_TIMEOUT,
    LIVE_PREDICT_UNAVAILABLE,
    LiveAccompanimentChunkV1,
    LiveAccompanimentPredictRequestV1,
    LivePerformanceError,
    validate_predict_request_domain,
)
from app.live_performance_settings import load_live_performance_settings
from app.services.fake_live_accompaniment import fake_live_accompaniment_chunk

logger = logging.getLogger(__name__)


def _truthy(raw: str | None) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def predict_live_accompaniment(
    request: LiveAccompanimentPredictRequestV1,
    *,
    env: Mapping[str, str] | None = None,
) -> LiveAccompanimentChunkV1:
    """Validate + generate a chunk. Prefer fake when LLM_FAKE_MODE (or always for v1)."""
    started = time.perf_counter()
    source = env if env is not None else os.environ
    settings = load_live_performance_settings(source)
    validate_predict_request_domain(request, settings)

    timeout_s = settings.predict_timeout_ms / 1000.0
    fake_mode = _truthy(source.get("LLM_FAKE_MODE")) or _truthy(
        source.get("LIVE_PREDICT_FORCE_FAKE")
    )
    # v1: fake is the only shipped cold-path engine (no AiOperation).
    if not fake_mode:
        # Still serve fake when explicitly unset in local CI-friendly default:
        # LIVE_PREDICT_ALLOW_WITHOUT_FAKE=0 (default) → use fake anyway.
        allow_without = _truthy(source.get("LIVE_PREDICT_ALLOW_WITHOUT_FAKE"))
        if allow_without:
            raise LivePerformanceError(
                "Live predict symbolic/LLM engine is not configured",
                code=LIVE_PREDICT_UNAVAILABLE,
            )
        fake_mode = True

    logger.info(
        "Live predict accepted",
        extra={
            "request_id": request.request_id[:16],
            "session_id": request.session_id[:16],
            "fake_mode": fake_mode,
            "horizon_bars": request.horizon.bars,
            "horizon_ms": request.horizon.ms,
        },
    )

    chunk = fake_live_accompaniment_chunk(request, settings=settings)
    elapsed = time.perf_counter() - started
    if elapsed > timeout_s:
        raise LivePerformanceError(
            "Live predict exceeded LIVE_PREDICT_TIMEOUT_MS",
            code=LIVE_PREDICT_TIMEOUT,
            details={"elapsed_ms": round(elapsed * 1000, 2)},
        )

    logger.info(
        "Live predict completed",
        extra={
            "request_id": request.request_id[:16],
            "latency_ms": round(elapsed * 1000, 2),
            "event_count": len(chunk.events),
            "source": chunk.source,
        },
    )
    return chunk
