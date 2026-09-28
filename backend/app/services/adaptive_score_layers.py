"""Pure map from runtime intensity to an adaptive layer set.

Does not import SQLite, FastAPI, or an LLM. Does not accept a Composition
or copy note events. The same inputs always return the same rows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_ERROR_CODES,
    AdaptiveScoreError,
    AdaptiveTransitionScheduleWarningV1,
)
from app.composition_schemas import round_half_away_from_zero

logger = logging.getLogger(__name__)

_MAX_WARNINGS = 32
_MAX_TRACK_IDS = 16


@dataclass(frozen=True)
class LayerProjection:
    id: str
    state_id: str | None
    role: str
    intensity_min: float
    intensity_max: float
    exclusive_group: str | None
    priority: int
    in_policy: str
    out_policy: str
    fade_in_ms: int
    fade_out_ms: int
    span_start_tick: int | None
    span_end_tick: int | None
    track_ids: tuple[str, ...]
    material_kind: str


@dataclass(frozen=True)
class LayerTimelineProjection:
    bar_boundaries: tuple[int, ...]
    duration_ticks: int
    ticks_per_quarter: int
    tempo_bpm: int


@dataclass(frozen=True)
class MappedLayer:
    layer_id: str
    role: str
    material_kind: str
    track_ids: tuple[str, ...]
    active: bool
    audible: bool
    target_gain: int
    reason: str
    suppressed_by: str | None
    in_policy: str
    out_policy: str
    fade_ms: int
    fade_end_tick: int


@dataclass(frozen=True)
class _Selection:
    active: bool
    reason: str
    suppressed_by: str | None


@dataclass(frozen=True)
class LayerMapResult:
    layers: tuple[MappedLayer, ...]
    warnings: tuple[AdaptiveTransitionScheduleWarningV1, ...]


def map_adaptive_layers(
    layers: tuple[LayerProjection, ...],
    *,
    state_id: str,
    intensity: float,
    position_tick: int,
    previous_intensity: float | None,
    timeline: LayerTimelineProjection,
) -> LayerMapResult:
    """Return active rows in stored order. Raises ``position_outside`` with no rows."""
    if position_tick > timeline.duration_ticks:
        logger.debug(
            "Adaptive layer map rejected position",
            extra={"code": "position_outside", "position_tick": position_tick},
        )
        raise AdaptiveScoreError(
            "position_outside",
            ADAPTIVE_SCORE_ERROR_CODES["position_outside"],
            http_status=422,
        )

    current = _select(layers, state_id=state_id, intensity=intensity)
    previous = (
        _select(layers, state_id=state_id, intensity=previous_intensity)
        if previous_intensity is not None
        else None
    )
    warnings: list[AdaptiveTransitionScheduleWarningV1] = []
    rows: list[MappedLayer] = []
    first_fade_end: int | None = None
    for layer in layers:
        chosen = current[layer.id]
        was_active = previous[layer.id].active if previous is not None else None
        policy, fade_ms = _edge(layer, active=chosen.active, previous_active=was_active)
        fade_end, clamped = _fade_end_tick(
            policy,
            fade_ms=fade_ms,
            position_tick=position_tick,
            timeline=timeline,
        )
        audible, unchecked = _audible(layer, active=chosen.active, position_tick=position_tick)
        if unchecked and len(warnings) < _MAX_WARNINGS:
            warnings.append(
                AdaptiveTransitionScheduleWarningV1(
                    code="span_unchecked",
                    target_id=layer.id,
                    message="Layer span is unknown.",
                )
            )
        if clamped and len(warnings) < _MAX_WARNINGS:
            warnings.append(
                AdaptiveTransitionScheduleWarningV1(
                    code="fade_clamped",
                    target_id=layer.id,
                    message="Fade completion was clamped to the composition duration.",
                )
            )
        if chosen.active and first_fade_end is None:
            first_fade_end = fade_end
        rows.append(
            MappedLayer(
                layer_id=layer.id,
                role=layer.role,
                material_kind=layer.material_kind,
                track_ids=layer.track_ids[:_MAX_TRACK_IDS],
                active=chosen.active,
                audible=audible,
                target_gain=1 if chosen.active else 0,
                reason=chosen.reason,
                suppressed_by=chosen.suppressed_by,
                in_policy=layer.in_policy,
                out_policy=layer.out_policy,
                fade_ms=fade_ms,
                fade_end_tick=fade_end,
            )
        )

    active_count = sum(1 for row in rows if row.active)
    candidate_count = sum(
        1 for layer in layers if layer.state_id is None or layer.state_id == state_id
    )
    logger.debug(
        "Mapped adaptive layers",
        extra={
            "state_id": state_id,
            "intensity": intensity,
            "candidate_count": candidate_count,
            "active_count": active_count,
            "position_tick": position_tick,
            "fade_end_tick": first_fade_end,
            "warning_codes": [item.code for item in warnings],
        },
    )
    return LayerMapResult(layers=tuple(rows), warnings=tuple(warnings))


def _select(
    layers: tuple[LayerProjection, ...],
    *,
    state_id: str,
    intensity: float,
) -> dict[str, _Selection]:
    selected: dict[str, _Selection] = {}
    grouped: dict[str, list[LayerProjection]] = {}
    for layer in layers:
        if layer.state_id is not None and layer.state_id != state_id:
            selected[layer.id] = _Selection(False, "out_of_state", None)
            continue
        if not (layer.intensity_min <= intensity <= layer.intensity_max):
            selected[layer.id] = _Selection(False, "out_of_window", None)
            continue
        if layer.exclusive_group is None:
            selected[layer.id] = _Selection(True, "in_window", None)
            continue
        grouped.setdefault(layer.exclusive_group, []).append(layer)
    for members in grouped.values():
        ordered = sorted(
            members,
            key=lambda item: (-item.priority, -item.intensity_min, item.id),
        )
        winner = ordered[0]
        selected[winner.id] = _Selection(True, "in_window", None)
        for loser in ordered[1:]:
            selected[loser.id] = _Selection(False, "suppressed", winner.id)
    return selected


def _edge(
    layer: LayerProjection,
    *,
    active: bool,
    previous_active: bool | None,
) -> tuple[str, int]:
    if previous_active is not None and active and not previous_active:
        return layer.in_policy, layer.fade_in_ms
    if previous_active is not None and (not active) and previous_active:
        return layer.out_policy, layer.fade_out_ms
    if active:
        return layer.in_policy, layer.fade_in_ms
    return layer.out_policy, layer.fade_out_ms


def _audible(
    layer: LayerProjection,
    *,
    active: bool,
    position_tick: int,
) -> tuple[bool, bool]:
    if not active:
        return False, False
    if layer.span_start_tick is None or layer.span_end_tick is None:
        return True, True
    inside = layer.span_start_tick <= position_tick < layer.span_end_tick
    return inside, False


def _fade_end_tick(
    policy: str,
    *,
    fade_ms: int,
    position_tick: int,
    timeline: LayerTimelineProjection,
) -> tuple[int, bool]:
    if policy == "cut":
        return position_tick, False
    if policy == "linear":
        seconds_per_tick = 60.0 / timeline.tempo_bpm / timeline.ticks_per_quarter
        lead = round_half_away_from_zero(fade_ms / 1000.0 / seconds_per_tick)
        end = position_tick + lead
        if end > timeline.duration_ticks:
            return timeline.duration_ticks, True
        return end, False
    for boundary in timeline.bar_boundaries:
        if boundary >= position_tick and boundary <= timeline.duration_ticks:
            return boundary, False
    return timeline.duration_ticks, True
