"""Deterministic fake live accompaniment chunks for CI / LLM_FAKE_MODE.

Hash of harmony symbol + clock tick → stable chord-tone pattern.
Never returns composition dumps; never writes SQLite.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from app.live_performance_schemas import (
    LiveAccompanimentChunkV1,
    LiveAccompanimentEvent,
    LiveAccompanimentPredictRequestV1,
    LiveLatencyMs,
)
from app.live_performance_settings import LivePerformanceSettings, load_live_performance_settings
from app.services.composition_tonality import parse_chord_symbol

logger = logging.getLogger(__name__)


def fake_live_accompaniment_chunk(
    request: LiveAccompanimentPredictRequestV1,
    *,
    settings: LivePerformanceSettings | None = None,
) -> LiveAccompanimentChunkV1:
    """Build a bounded deterministic chunk from active harmony."""
    started = time.perf_counter()
    cfg = settings if settings is not None else load_live_performance_settings()
    symbol = (request.active_harmony.symbol or "").strip()
    parsed = parse_chord_symbol(symbol) if symbol else None
    pcs = sorted(parsed.pitch_classes) if parsed and parsed.parseable else []

    seed_src = f"{symbol}|{request.clock.tick}|{request.horizon.bars}|{request.horizon.ms}"
    digest = hashlib.sha256(seed_src.encode("utf-8")).hexdigest()
    seed = int(digest[:8], 16)

    tpq = 480
    start_tick = max(0, int(request.clock.tick) + tpq)  # one beat ahead
    horizon_ticks = max(tpq, int(float(request.horizon.bars) * tpq * 4))
    step = max(tpq // 2, tpq)

    if not pcs:
        # Soft pad on C major when harmony empty — still ephemeral.
        pcs = [0, 4, 7]

    octave = 48 + (seed % 2) * 12
    events: list[LiveAccompanimentEvent] = []
    tick = start_tick
    idx = 0
    while tick < start_tick + horizon_ticks and len(events) < cfg.max_events_per_chunk:
        pc = pcs[(idx + seed) % len(pcs)]
        pitch = octave + int(pc)
        if pitch > 127:
            pitch = 48 + (pitch % 12)
        dur = max(1, step - 1)
        events.append(
            LiveAccompanimentEvent(
                pitch=pitch,
                start_tick=tick,
                duration_ticks=dur,
                velocity=64 + (seed % 20),
                track_role="accompaniment",
            )
        )
        tick += step
        idx += 1

    generation_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "Fake live accompaniment chunk",
        extra={
            "request_id": request.request_id[:16],
            "session_id": request.session_id[:16],
            "event_count": len(events),
            "latency_ms": round(generation_ms, 2),
            "has_harmony": bool(symbol),
        },
    )
    return LiveAccompanimentChunkV1(
        request_id=request.request_id,
        session_id=request.session_id,
        start_tick=start_tick,
        events=events,
        source="fake",
        generated_at_ms=time.time() * 1000.0,
        latency_ms=LiveLatencyMs(generation=generation_ms),
    )


def fake_chunk_to_dict(chunk: LiveAccompanimentChunkV1) -> dict[str, Any]:
    return chunk.model_dump(mode="json")
