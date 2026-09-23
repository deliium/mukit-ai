"""Deterministic fake live accompaniment chunks for CI / LLM_FAKE_MODE.

Hash of harmony symbol + clock tick (+ jam_mode when present) → stable
chord-tone pattern. Multi-role when jam_mode/role_mask/controls present;
single accompaniment role when jam fields absent (backward compat).

Never returns composition dumps; never writes SQLite.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from app.live_performance_schemas import (
    JamTrackRole,
    LiveAccompanimentChunkV1,
    LiveAccompanimentEvent,
    LiveAccompanimentPredictRequestV1,
    LiveLatencyMs,
    resolve_jam_role_partition,
)
from app.live_performance_settings import LivePerformanceSettings, load_live_performance_settings
from app.services.composition_tonality import parse_chord_symbol

logger = logging.getLogger(__name__)

# Approximate MIDI register bases per role (aligned with FE liveJamRoleEngine).
_ROLE_OCTAVE: dict[str, int] = {
    "bass": 36,
    "accompaniment": 48,
    "harmony": 48,
    "melody": 60,
    "texture": 72,
}

_DENSITY_FACTOR: dict[str, float] = {
    "low": 0.35,
    "medium": 0.55,
    "high": 0.8,
}


def fake_live_accompaniment_chunk(
    request: LiveAccompanimentPredictRequestV1,
    *,
    settings: LivePerformanceSettings | None = None,
) -> LiveAccompanimentChunkV1:
    """Build a bounded deterministic chunk from active harmony / jam roles."""
    started = time.perf_counter()
    cfg = settings if settings is not None else load_live_performance_settings()

    belief_symbol = None
    if request.belief is not None and request.belief.symbol:
        belief_symbol = request.belief.symbol.strip()
    symbol = belief_symbol or (request.active_harmony.symbol or "").strip()
    parsed = parse_chord_symbol(symbol) if symbol else None
    pcs = sorted(parsed.pitch_classes) if parsed and parsed.parseable else []
    if not pcs:
        # Soft pad on C major when harmony empty — still ephemeral.
        pcs = [0, 4, 7]

    jam_mode = request.jam_mode
    seed_src = (
        f"{symbol}|{request.clock.tick}|{request.horizon.bars}|{request.horizon.ms}"
        f"|{jam_mode or ''}"
    )
    digest = hashlib.sha256(seed_src.encode("utf-8")).hexdigest()
    seed = int(digest[:8], 16)

    tpq = 480
    start_tick = max(0, int(request.clock.tick) + tpq)  # one beat ahead
    horizon_ticks = max(tpq, int(float(request.horizon.bars) * tpq * 4))

    roles = _resolve_roles(request)
    density = _density_factor(request)
    style = (
        request.controls.style
        if request.controls is not None
        else "block"
    )

    events: list[LiveAccompanimentEvent] = []
    if jam_mode is None and not roles:
        # Backward-compat single accompaniment voice.
        events = _emit_role_events(
            role="accompaniment",
            pcs=pcs,
            seed=seed,
            start_tick=start_tick,
            horizon_ticks=horizon_ticks,
            tpq=tpq,
            density=0.5,
            style="arp" if density >= 0.55 else "block",
            max_events=cfg.max_events_per_chunk,
        )
    else:
        per_role_cap = max(1, cfg.max_events_per_chunk // max(1, len(roles)))
        for role_index, role in enumerate(roles):
            role_seed = seed ^ (role_index * 0x9E3779B9)
            role_events = _emit_role_events(
                role=role,
                pcs=pcs,
                seed=role_seed,
                start_tick=start_tick,
                horizon_ticks=horizon_ticks,
                tpq=tpq,
                density=density,
                style=style,
                max_events=per_role_cap,
            )
            events.extend(role_events)
            if len(events) >= cfg.max_events_per_chunk:
                events = events[: cfg.max_events_per_chunk]
                break

    generation_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "Fake live accompaniment chunk",
        extra={
            "request_id": request.request_id[:16],
            "session_id": request.session_id[:16],
            "event_count": len(events),
            "latency_ms": round(generation_ms, 2),
            "has_harmony": bool(symbol),
            "jam_mode": jam_mode,
            "role_count": len(roles) if roles else 1,
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


def _resolve_roles(request: LiveAccompanimentPredictRequestV1) -> list[JamTrackRole]:
    if request.role_mask:
        return list(request.role_mask)
    if request.jam_mode is None:
        return []
    complexity = (
        request.controls.complexity
        if request.controls is not None
        else "medium"
    )
    partition = resolve_jam_role_partition(request.jam_mode, complexity)
    return list(partition["ai_roles"])


def _density_factor(request: LiveAccompanimentPredictRequestV1) -> float:
    if request.controls is not None:
        return _DENSITY_FACTOR.get(request.controls.density, 0.55)
    if request.features.density is not None:
        return float(request.features.density)
    return 0.5


def _emit_role_events(
    *,
    role: str,
    pcs: list[int],
    seed: int,
    start_tick: int,
    horizon_ticks: int,
    tpq: int,
    density: float,
    style: str,
    max_events: int,
) -> list[LiveAccompanimentEvent]:
    octave = _ROLE_OCTAVE.get(role, 48) + (seed % 2) * 12
    if style == "pad":
        step = max(tpq * 2, horizon_ticks)
    elif style in ("arp", "alberti"):
        step = max(tpq // 2, int(tpq * (0.25 + (1.0 - density) * 0.5)))
    else:
        step = max(tpq // 2 if density >= 0.7 else tpq, tpq // 2)

    events: list[LiveAccompanimentEvent] = []
    tick = start_tick
    idx = 0
    tone_count = 1 if role == "bass" else min(3, len(pcs))

    while tick < start_tick + horizon_ticks and len(events) < max_events:
        if style == "pad":
            for i in range(tone_count):
                pc = pcs[(i + seed) % len(pcs)]
                pitch = _clamp_pitch(octave + int(pc))
                dur = max(1, min(horizon_ticks, start_tick + horizon_ticks - tick))
                events.append(
                    LiveAccompanimentEvent(
                        pitch=pitch,
                        start_tick=tick,
                        duration_ticks=dur,
                        velocity=48 + (seed % 12),
                        track_role=role,
                    )
                )
                if len(events) >= max_events:
                    break
            break

        if role == "bass":
            pc = pcs[0]
            pitch = _clamp_pitch(octave + int(pc))
            dur = max(1, step - 1)
            events.append(
                LiveAccompanimentEvent(
                    pitch=pitch,
                    start_tick=tick,
                    duration_ticks=dur,
                    velocity=56 + (seed % 16),
                    track_role=role,
                )
            )
        elif style == "alberti" and len(pcs) >= 2:
            pattern = [0, 2 % len(pcs), 1 % len(pcs), 2 % len(pcs)]
            pc = pcs[pattern[idx % len(pattern)]]
            pitch = _clamp_pitch(octave + int(pc))
            events.append(
                LiveAccompanimentEvent(
                    pitch=pitch,
                    start_tick=tick,
                    duration_ticks=max(1, step - 1),
                    velocity=64 + (seed % 20),
                    track_role=role,
                )
            )
        else:
            pc = pcs[(idx + seed) % len(pcs)]
            pitch = _clamp_pitch(octave + int(pc))
            events.append(
                LiveAccompanimentEvent(
                    pitch=pitch,
                    start_tick=tick,
                    duration_ticks=max(1, step - 1),
                    velocity=64 + (seed % 20),
                    track_role=role,
                )
            )
        tick += step
        idx += 1

    return events


def _clamp_pitch(pitch: int) -> int:
    if pitch > 127:
        return 48 + (pitch % 12)
    if pitch < 0:
        return 36
    return pitch


def fake_chunk_to_dict(chunk: LiveAccompanimentChunkV1) -> dict[str, Any]:
    return chunk.model_dump(mode="json")
