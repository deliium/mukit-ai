"""Pure adaptive-score transition scheduler.

Reads a compiled timeline and graph projections. Does not import SQLite,
FastAPI, or an LLM, and does not accept a Composition object.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_ERROR_CODES,
    AdaptiveMaterialRefV1,
    AdaptiveScheduleRealizationCrossfadeV1,
    AdaptiveScheduleRealizationCutV1,
    AdaptiveScheduleRealizationOverlapV1,
    AdaptiveScheduleRealizationPhraseV1,
    AdaptiveScheduleRealizationStingerV1,
    AdaptiveScheduleRealizationV1,
    AdaptiveScoreError,
    AdaptiveScoreStateV1,
    AdaptiveScoreTransitionV1,
    AdaptiveScoreV1,
    AdaptiveTransitionScheduleRequest,
    AdaptiveTransitionScheduleWarningV1,
)
from app.composition_schemas import round_half_away_from_zero
from app.services.composition_timeline import CompiledTimeline

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransitionSectionProjection:
    section_id: str
    start_bar: int
    bar_count: int
    start_tick: int


@dataclass(frozen=True)
class TransitionMarkerProjection:
    kind: str
    label: str
    tick: int


@dataclass(frozen=True)
class MusicalTransitionSchedule:
    """Grid result before a pending request id is assigned."""

    transition_id: str
    from_state_id: str
    to_state_id: str
    quantization: str
    boundary_tick: int
    boundary_bar: int
    latency_ticks: int
    latency_ms: int
    tempo_bpm: int
    time_signature: str
    aligned: bool
    realization: AdaptiveScheduleRealizationV1
    warnings: tuple[AdaptiveTransitionScheduleWarningV1, ...]


class _GridFailure(Exception):
    def __init__(self, code: str, *, target_id: str | None = None, message: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.target_id = target_id
        self.message = message


def schedule_musical_transition(
    score: AdaptiveScoreV1,
    timeline: CompiledTimeline,
    sections: tuple[TransitionSectionProjection, ...],
    markers: tuple[TransitionMarkerProjection, ...],
    request: AdaptiveTransitionScheduleRequest,
) -> MusicalTransitionSchedule:
    """Choose one authored edge and the next grid boundary at or after ``position_tick``."""
    logger.debug(
        "Adaptive transition schedule started",
        extra={
            "from_state_id": request.from_state_id,
            "to_state_id": request.to_state_id,
            "position_tick": request.position_tick,
            "flag_count": len(request.runtime.flags),
        },
    )
    try:
        transition = _select_transition(score, request)
        if request.position_tick > timeline.duration_ticks:
            raise _GridFailure("position_outside")
        warnings: list[AdaptiveTransitionScheduleWarningV1] = []
        boundary_tick, aligned = _boundary(
            score,
            transition,
            timeline,
            sections,
            markers,
            request.position_tick,
            warnings,
        )
        realization = _realization(
            score,
            transition,
            timeline,
            sections,
            request.position_tick,
            boundary_tick,
        )
        latency_ticks = boundary_tick - request.position_tick
        latency_ms = round_half_away_from_zero(
            (
                timeline.tick_to_seconds(boundary_tick)
                - timeline.tick_to_seconds(request.position_tick)
            )
            * 1000
        )
        plan = MusicalTransitionSchedule(
            transition_id=transition.id,
            from_state_id=transition.from_state_id,
            to_state_id=transition.to_state_id,
            quantization=transition.quantization,
            boundary_tick=boundary_tick,
            boundary_bar=timeline.bar_at_tick(boundary_tick),
            latency_ticks=latency_ticks,
            latency_ms=latency_ms,
            tempo_bpm=timeline.active_tempo(boundary_tick),
            time_signature=timeline.active_time_signature(boundary_tick),
            aligned=aligned,
            realization=realization,
            warnings=tuple(warnings[:8]),
        )
    except _GridFailure as exc:
        logger.error(
            "Adaptive transition schedule rejected",
            extra={
                "code": exc.code,
                "from_state_id": request.from_state_id,
                "to_state_id": request.to_state_id,
            },
        )
        details = {"target_id": exc.target_id} if exc.target_id else {}
        message = (exc.message or ADAPTIVE_SCORE_ERROR_CODES.get(exc.code, exc.code))[:200]
        status = 409 if exc.code == "adaptive_score_conflict" else 422
        raise AdaptiveScoreError(exc.code, message, http_status=status, details=details) from exc
    logger.debug(
        "Adaptive transition schedule resolved",
        extra={
            "transition_id": plan.transition_id,
            "quantization": plan.quantization,
            "position_tick": request.position_tick,
            "boundary_tick": plan.boundary_tick,
            "latency_ticks": plan.latency_ticks,
            "latency_ms": plan.latency_ms,
            "warning_codes": [item.code for item in plan.warnings],
            "realization_kind": plan.realization.kind,
        },
    )
    return plan


def _select_transition(
    score: AdaptiveScoreV1,
    request: AdaptiveTransitionScheduleRequest,
) -> AdaptiveScoreTransitionV1:
    states = {state.id: state for state in score.states}
    source = states.get(request.from_state_id)
    if source is None:
        raise _GridFailure(
            "dangling_state_ref",
            target_id=request.from_state_id,
            message="Transition source state does not exist.",
        )
    if request.to_state_id not in states:
        raise _GridFailure(
            "dangling_state_ref",
            target_id=request.to_state_id,
            message="Transition destination state does not exist.",
        )
    by_id = {item.id: item for item in score.transitions}
    if request.transition_id:
        chosen = by_id.get(request.transition_id)
        if chosen is None:
            raise _GridFailure(
                "dangling_state_ref",
                target_id=request.transition_id,
                message="Transition id was not found.",
            )
        if not _eligible(source, chosen, request.to_state_id):
            raise _GridFailure("transition_unsatisfied", target_id=chosen.id)
        if not _conditions_pass(chosen, request):
            raise _GridFailure("transition_unsatisfied", target_id=chosen.id)
        return chosen
    passing = [
        item
        for item in score.transitions
        if _eligible(source, item, request.to_state_id) and _conditions_pass(item, request)
    ]
    passing.sort(key=lambda item: (-item.priority, item.id))
    if not passing:
        raise _GridFailure("transition_unsatisfied")
    return passing[0]


def _eligible(
    source: AdaptiveScoreStateV1,
    transition: AdaptiveScoreTransitionV1,
    to_state_id: str,
) -> bool:
    return (
        transition.id in source.transition_ids
        and transition.from_state_id == source.id
        and transition.to_state_id == to_state_id
    )


def _conditions_pass(
    transition: AdaptiveScoreTransitionV1,
    request: AdaptiveTransitionScheduleRequest,
) -> bool:
    runtime = request.runtime
    for condition in transition.conditions:
        kind = condition.kind
        if kind == "manual":
            continue
        if kind == "flag_equals" and runtime.flags.get(condition.flag, False) != condition.value:
            return False
        if kind == "intensity_at_least" and runtime.intensity < condition.value:
            return False
        if kind == "intensity_at_most" and runtime.intensity > condition.value:
            return False
        if kind == "min_time_in_state_bars" and runtime.bars_in_state < condition.value:
            return False
    return True


def _boundary(
    score: AdaptiveScoreV1,
    transition: AdaptiveScoreTransitionV1,
    timeline: CompiledTimeline,
    sections: tuple[TransitionSectionProjection, ...],
    markers: tuple[TransitionMarkerProjection, ...],
    position: int,
    warnings: list[AdaptiveTransitionScheduleWarningV1],
) -> tuple[int, bool]:
    source = next(state for state in score.states if state.id == transition.from_state_id)
    token = transition.quantization
    if token == "immediate":
        return position, _on_beat(timeline, position)
    if token == "beat":
        return _next_beat(timeline, position), True
    if token == "bar":
        return _next_bar(timeline, position), True
    if token == "phrase":
        return _phrase(source, timeline, sections, position, warnings), True
    if token == "next_exit":
        tick = _exit_tick(source, timeline, sections)
        if position > tick:
            raise _GridFailure("exit_passed", target_id=source.id)
        if tick > timeline.duration_ticks:
            raise _GridFailure("boundary_unavailable", target_id=source.id)
        return tick, True
    if token == "loop_end":
        return _loop_end(source, timeline, position), True
    if token == "cue":
        return _cue(transition, timeline, markers, position), True
    return _custom(source, transition, timeline, sections, position), True


def _next_bar(timeline: CompiledTimeline, position: int) -> int:
    for tick in timeline.bar_boundaries:
        if position <= tick <= timeline.duration_ticks:
            return tick
    raise _GridFailure("boundary_unavailable")


def _next_beat(timeline: CompiledTimeline, position: int) -> int:
    if position == timeline.duration_ticks:
        if _on_beat(timeline, position):
            return position
        raise _GridFailure("boundary_unavailable")
    bar = timeline.bar_at_tick(position)
    for current in range(bar, timeline.bar_count + 1):
        start = timeline.bar_start_tick(current)
        end = timeline.bar_end_tick(current)
        length = _beat_length(timeline, current)
        tick = start
        while tick < end:
            if tick >= position:
                return tick
            tick += length
        if current == timeline.bar_count and end >= position and (end - start) % length == 0:
            return end
    raise _GridFailure("boundary_unavailable")


def _beat_length(timeline: CompiledTimeline, bar: int) -> int:
    start = timeline.bar_start_tick(bar)
    end = timeline.bar_end_tick(bar)
    numerator = int(timeline.active_time_signature(start).split("/", 1)[0])
    span = end - start
    if numerator <= 0 or span % numerator != 0:
        raise _GridFailure("meter_grid_indivisible")
    return span // numerator


def _on_beat(timeline: CompiledTimeline, tick: int) -> bool:
    if tick < 0 or tick > timeline.duration_ticks:
        return False
    if tick == timeline.duration_ticks:
        bar = timeline.bar_count
    else:
        bar = timeline.bar_at_tick(tick)
    start = timeline.bar_start_tick(bar)
    length = _beat_length(timeline, bar)
    return (tick - start) % length == 0


def _material_span(
    material: AdaptiveMaterialRefV1,
    sections: tuple[TransitionSectionProjection, ...],
) -> tuple[int, int] | None:
    if material.kind in {"bar_range", "revision_region"} and material.start_bar and material.end_bar:
        return material.start_bar, material.end_bar
    if material.kind == "section" and material.section_id:
        section = next((item for item in sections if item.section_id == material.section_id), None)
        if section is None or section.bar_count < 1:
            return None
        return section.start_bar, section.start_bar + section.bar_count - 1
    if material.kind == "track_range" and material.start_bar and material.end_bar:
        return material.start_bar, material.end_bar
    return None


def _phrase(
    source: AdaptiveScoreStateV1,
    timeline: CompiledTimeline,
    sections: tuple[TransitionSectionProjection, ...],
    position: int,
    warnings: list[AdaptiveTransitionScheduleWarningV1],
) -> int:
    span = _material_span(source.material, sections)
    if span is None:
        raise _GridFailure("phrase_unavailable", target_id=source.id)
    start_bar, end_bar = span
    candidates: list[int] = []
    saw_intersection = False
    for section in sections:
        section_end = section.start_bar + section.bar_count - 1
        if section_end < start_bar or section.start_bar > end_bar:
            continue
        saw_intersection = True
        try:
            compiled = timeline.bar_start_tick(section.start_bar)
        except ValueError:
            continue
        if section.start_tick != compiled:
            if len(warnings) < 8:
                warnings.append(
                    AdaptiveTransitionScheduleWarningV1(
                        code="section_tick_disagrees",
                        target_id=section.section_id,
                        message="Section start_tick disagrees with the compiled bar start.",
                    )
                )
            continue
        if position <= compiled <= timeline.duration_ticks:
            candidates.append(compiled)
    if candidates:
        return min(candidates)
    if not saw_intersection:
        raise _GridFailure("phrase_unavailable", target_id=source.id)
    raise _GridFailure("boundary_unavailable", target_id=source.id)


def _exit_tick(
    source: AdaptiveScoreStateV1,
    timeline: CompiledTimeline,
    sections: tuple[TransitionSectionProjection, ...],
) -> int:
    boundary = source.exit
    if boundary.kind == "bar":
        if boundary.bar is None:
            raise _GridFailure("exit_unavailable", target_id=source.id)
        try:
            return timeline.bar_start_tick(boundary.bar)
        except ValueError as exc:
            raise _GridFailure("exit_unavailable", target_id=source.id) from exc
    if boundary.kind == "tick":
        if boundary.tick is None:
            raise _GridFailure("exit_unavailable", target_id=source.id)
        if boundary.tick > timeline.duration_ticks or not _on_beat(timeline, boundary.tick):
            raise _GridFailure("exit_off_grid", target_id=source.id)
        return boundary.tick
    span = _material_span(source.material, sections)
    if span is None:
        raise _GridFailure("exit_unavailable", target_id=source.id)
    start_bar, end_bar = span
    try:
        if boundary.kind == "material_end":
            return timeline.bar_end_tick(end_bar)
        return timeline.bar_start_tick(start_bar)
    except ValueError as exc:
        raise _GridFailure("exit_unavailable", target_id=source.id) from exc


def _loop_end(source: AdaptiveScoreStateV1, timeline: CompiledTimeline, position: int) -> int:
    loop = source.loop
    if not loop.enabled or loop.end_bar is None:
        raise _GridFailure("loop_unavailable", target_id=source.id)
    try:
        tick = timeline.bar_end_tick(loop.end_bar)
    except ValueError as exc:
        raise _GridFailure("loop_unavailable", target_id=source.id) from exc
    if position > tick:
        raise _GridFailure("loop_end_passed", target_id=source.id)
    if tick > timeline.duration_ticks:
        raise _GridFailure("boundary_unavailable", target_id=source.id)
    return tick


def _cue(
    transition: AdaptiveScoreTransitionV1,
    timeline: CompiledTimeline,
    markers: tuple[TransitionMarkerProjection, ...],
    position: int,
) -> int:
    label = (transition.cue_label or "").strip()
    found = [
        marker.tick
        for marker in markers
        if marker.kind == "rehearsal"
        and marker.label.strip() == label
        and position <= marker.tick <= timeline.duration_ticks
        and _on_beat(timeline, marker.tick)
    ]
    if not found:
        raise _GridFailure("cue_not_found", target_id=transition.id)
    return min(found)


def _custom(
    source: AdaptiveScoreStateV1,
    transition: AdaptiveScoreTransitionV1,
    timeline: CompiledTimeline,
    sections: tuple[TransitionSectionProjection, ...],
    position: int,
) -> int:
    if not transition.custom_grid_bars:
        raise _GridFailure("boundary_unavailable", target_id=transition.id)
    span = _material_span(source.material, sections)
    if span is None:
        raise _GridFailure("boundary_unavailable", target_id=source.id)
    bar = span[0]
    while bar <= timeline.bar_count:
        try:
            tick = timeline.bar_start_tick(bar)
        except ValueError as exc:
            raise _GridFailure("boundary_unavailable", target_id=transition.id) from exc
        if tick > timeline.duration_ticks:
            break
        if tick >= position:
            return tick
        bar += transition.custom_grid_bars
    raise _GridFailure("boundary_unavailable", target_id=transition.id)


def _realization(
    score: AdaptiveScoreV1,
    transition: AdaptiveScoreTransitionV1,
    timeline: CompiledTimeline,
    sections: tuple[TransitionSectionProjection, ...],
    position: int,
    boundary_tick: int,
) -> AdaptiveScheduleRealizationV1:
    stored = transition.realization
    if stored.kind == "cut":
        logger.debug(
            "Adaptive transition realization",
            extra={"realization_kind": "cut", "transition_id": transition.id},
        )
        return AdaptiveScheduleRealizationCutV1()
    if stored.kind == "crossfade":
        tempo = timeline.active_tempo(boundary_tick)
        seconds_per_tick = 60.0 / tempo / timeline.ticks_per_quarter
        lead = round_half_away_from_zero((stored.crossfade_ms or 0) / 1000.0 / seconds_per_tick)
        fade_start = boundary_tick - lead
        if fade_start < position:
            fade_start = position
        logger.debug(
            "Adaptive transition realization",
            extra={
                "realization_kind": "crossfade",
                "transition_id": transition.id,
                "crossfade_ms": stored.crossfade_ms,
                "fade_start_tick": fade_start,
            },
        )
        return AdaptiveScheduleRealizationCrossfadeV1(
            kind="crossfade",
            crossfade_ms=stored.crossfade_ms or 0,
            fade_start_tick=fade_start,
        )
    if stored.kind == "phrase":
        material = stored.phrase_material
        if material is None:
            raise _GridFailure("realization_invalid", target_id=transition.id)
        logger.debug(
            "Adaptive transition realization",
            extra={"realization_kind": "phrase", "transition_id": transition.id},
        )
        return AdaptiveScheduleRealizationPhraseV1(
            kind="phrase",
            material_kind=material.kind,
            section_id=material.section_id,
            motif_id=material.motif_id,
            revision_id=material.revision_id,
            asset_id=material.asset_id,
            track_ids=list(material.track_ids),
        )
    if stored.kind == "stinger":
        stinger = next((item for item in score.stingers if item.id == stored.stinger_id), None)
        if stinger is None:
            raise _GridFailure("realization_invalid", target_id=stored.stinger_id)
        logger.debug(
            "Adaptive transition realization",
            extra={"realization_kind": "stinger", "transition_id": transition.id},
        )
        return AdaptiveScheduleRealizationStingerV1(
            kind="stinger",
            stinger_id=stinger.id,
            interrupt_policy=stinger.interrupt_policy,
        )
    if boundary_tick not in timeline.bar_boundaries:
        raise _GridFailure("realization_invalid", target_id=transition.id)
    release_bar = min(
        timeline.bar_count,
        timeline.bar_at_tick(boundary_tick) + (stored.overlap_bars or 1) - 1,
    )
    release = min(timeline.bar_end_tick(release_bar), timeline.duration_ticks)
    exit_tick = _optional_exit(score, transition, timeline, sections)
    if exit_tick is not None and boundary_tick <= exit_tick < release:
        release = exit_tick
    logger.debug(
        "Adaptive transition realization",
        extra={
            "realization_kind": "overlap",
            "transition_id": transition.id,
            "source_release_tick": release,
        },
    )
    return AdaptiveScheduleRealizationOverlapV1(
        kind="overlap",
        overlap_bars=stored.overlap_bars or 1,
        source_release_tick=release,
    )


def _optional_exit(
    score: AdaptiveScoreV1,
    transition: AdaptiveScoreTransitionV1,
    timeline: CompiledTimeline,
    sections: tuple[TransitionSectionProjection, ...],
) -> int | None:
    source = next(state for state in score.states if state.id == transition.from_state_id)
    try:
        return _exit_tick(source, timeline, sections)
    except _GridFailure:
        return None
