"""Load one adaptive score and step a playback session.

Does not write ``body_json``, ``projects.composition_json``, or the authoring
pending slot. Does not call an LLM.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from app.adaptive_playback_schemas import (
    AdaptivePlaybackCommand,
    AdaptivePlaybackRuntimeV1,
    AdaptivePlaybackStartRequest,
    AdaptivePlaybackStopCommand,
)
from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_ERROR_CODES,
    AdaptiveScoreError,
    AdaptiveScoreV1,
    AdaptiveTransitionScheduleWarningV1,
)
from app.services.adaptive_playback import (
    PlaybackInputs,
    begin_adaptive_playback,
    step_adaptive_playback,
)
from app.services.adaptive_playback_runtime import (
    AdaptivePlaybackRegistry,
    HeldPlayback,
    get_default_playback_registry,
)
from app.services.adaptive_score_layers import LayerProjection
from app.services.adaptive_score_store import get_score
from app.services.adaptive_score_transition_service import _load_clock
from app.services.adaptive_score_transitions import (
    TransitionMarkerProjection,
    TransitionSectionProjection,
)
from app.services.adaptive_score_validation import bind_material_refs, raise_on_error_findings
from app.services.composition_timeline import CompiledTimeline, compile_timeline

logger = logging.getLogger(__name__)


def start_adaptive_playback(
    project_id: str,
    score_id: str,
    request: AdaptivePlaybackStartRequest,
    *,
    db_path: Path | None = None,
    registry: AdaptivePlaybackRegistry | None = None,
) -> AdaptivePlaybackRuntimeV1:
    """Open a session at tick 0. A revision mismatch does not create one."""
    slots = registry or get_default_playback_registry()
    try:
        record = get_score(project_id, score_id, db_path=db_path)
        if record.document_revision != request.expected_document_revision:
            raise AdaptiveScoreError(
                "adaptive_score_conflict",
                ADAPTIVE_SCORE_ERROR_CODES["adaptive_score_conflict"],
                http_status=409,
            )
        states = {state.id: state for state in record.score.states}
        if record.score.initial_state_id is None or record.score.initial_state_id not in states:
            raise AdaptiveScoreError(
                "dangling_state_ref",
                ADAPTIVE_SCORE_ERROR_CODES["dangling_state_ref"],
                http_status=422,
                details={"target_id": record.score.initial_state_id},
            )
        inputs = _load_inputs(project_id, record.score, db_path=db_path)
    except AdaptiveScoreError as exc:
        logger.error("Adaptive playback load failed", extra={"code": exc.code})
        raise
    playback_id = "pbr_" + secrets.token_hex(4)
    clock = begin_adaptive_playback(
        inputs,
        playback_id=playback_id,
        mode=request.mode,
        document_revision=record.document_revision,
    )
    slots.put(project_id, score_id, HeldPlayback(clock=clock, inputs=inputs))
    snapshot = _require_snapshot(clock.snapshot)
    logger.debug(
        "Adaptive playback projections loaded",
        extra={
            "state_count": len(record.score.states),
            "section_count": len(inputs.sections),
            "layer_count": len(inputs.layers),
            "mode": request.mode,
        },
    )
    log_adaptive_playback(project_id=project_id, score_id=score_id, snapshot=snapshot)
    return snapshot


def get_adaptive_playback(
    project_id: str,
    score_id: str,
    *,
    db_path: Path | None = None,
    registry: AdaptivePlaybackRegistry | None = None,
) -> AdaptivePlaybackRuntimeV1 | None:
    get_score(project_id, score_id, db_path=db_path)
    slots = registry or get_default_playback_registry()
    held = slots.get(project_id, score_id)
    if held is None or held.clock.snapshot is None:
        return None
    return held.clock.snapshot


def command_adaptive_playback(
    project_id: str,
    score_id: str,
    command: AdaptivePlaybackCommand,
    *,
    db_path: Path | None = None,
    registry: AdaptivePlaybackRegistry | None = None,
) -> AdaptivePlaybackRuntimeV1:
    slots = registry or get_default_playback_registry()
    held = slots.get(project_id, score_id)
    if held is None or held.clock.snapshot is None:
        raise AdaptiveScoreError(
            "playback_not_running",
            ADAPTIVE_SCORE_ERROR_CODES["playback_not_running"],
            http_status=404,
        )
    logger.debug(
        "Adaptive playback command",
        extra={"op": command.op, "mode": held.clock.mode},
    )
    try:
        record = get_score(project_id, score_id, db_path=db_path)
    except AdaptiveScoreError as exc:
        logger.error("Adaptive playback load failed", extra={"code": exc.code})
        raise
    if record.document_revision != held.clock.document_revision:
        snapshot = _with_warning(
            held.clock.snapshot,
            AdaptiveTransitionScheduleWarningV1(
                code="document_revision_conflict",
                message="Score revision changed after playback started.",
            ),
        )
        log_adaptive_playback(project_id=project_id, score_id=score_id, snapshot=snapshot)
        return snapshot
    step_adaptive_playback(held.inputs, held.clock, command)
    snapshot = _require_snapshot(held.clock.snapshot)
    log_adaptive_playback(project_id=project_id, score_id=score_id, snapshot=snapshot)
    return snapshot


def stop_adaptive_playback(
    project_id: str,
    score_id: str,
    *,
    db_path: Path | None = None,
    registry: AdaptivePlaybackRegistry | None = None,
) -> None:
    """Stop a live session. Missing sessions stay quiet."""
    get_score(project_id, score_id, db_path=db_path)
    slots = registry or get_default_playback_registry()
    held = slots.get(project_id, score_id)
    if held is None:
        return
    step_adaptive_playback(held.inputs, held.clock, AdaptivePlaybackStopCommand(op="stop"))
    snapshot = held.clock.snapshot
    slots.clear(project_id, score_id)
    if snapshot is not None:
        log_adaptive_playback(project_id=project_id, score_id=score_id, snapshot=snapshot)


def log_adaptive_playback(
    *,
    project_id: str,
    score_id: str,
    snapshot: AdaptivePlaybackRuntimeV1,
) -> None:
    """One INFO line for start, command, and stop. No names, flags, or notes."""
    logger.info(
        "Adaptive playback session",
        extra={
            "adaptive_playback": True,
            "project_id": project_id,
            "score_id": score_id,
            "playback_id": snapshot.playback_id,
            "last_event": snapshot.telemetry.last_event,
            "runtime_state_id": snapshot.runtime_state_id,
            "position_tick": snapshot.position_tick,
            "bar": snapshot.bar,
            "beat": snapshot.beat,
            "queue_depth": len(snapshot.queue),
            "warning_codes": [item.code for item in snapshot.warnings],
            "step_count": snapshot.telemetry.step_count,
            "rejected_request_count": snapshot.telemetry.rejected_request_count,
        },
    )


def _require_snapshot(snapshot: AdaptivePlaybackRuntimeV1 | None) -> AdaptivePlaybackRuntimeV1:
    if snapshot is None:
        raise AdaptiveScoreError(
            "playback_not_running",
            ADAPTIVE_SCORE_ERROR_CODES["playback_not_running"],
            http_status=404,
        )
    return snapshot


def _with_warning(
    snapshot: AdaptivePlaybackRuntimeV1,
    warning: AdaptiveTransitionScheduleWarningV1,
) -> AdaptivePlaybackRuntimeV1:
    warnings = list(snapshot.warnings)
    if len(warnings) < 8:
        warnings.append(warning)
    return snapshot.model_copy(update={"warnings": warnings})


def _load_inputs(
    project_id: str,
    score: AdaptiveScoreV1,
    *,
    db_path: Path | None,
) -> PlaybackInputs:
    composition = _load_clock(project_id, score, db_path=db_path)
    findings = bind_material_refs(score, composition)
    raise_on_error_findings(findings)
    timeline = compile_timeline(composition)
    sections = tuple(
        TransitionSectionProjection(
            section_id=section.id,
            start_bar=section.start_bar,
            bar_count=section.bar_count,
            start_tick=section.start_tick,
        )
        for section in composition.sections
        if section.id
    )
    markers = tuple(
        TransitionMarkerProjection(kind=marker.kind, label=marker.label, tick=marker.tick)
        for marker in composition.markers
    )
    by_id = {section.id: section for section in composition.sections if section.id}
    layers = tuple(_layer_projection(layer, timeline, by_id) for layer in score.layers)
    logger.debug(
        "Adaptive playback inputs compiled",
        extra={
            "state_count": len(score.states),
            "section_count": len(sections),
            "layer_count": len(layers),
        },
    )
    return PlaybackInputs(
        score=score,
        timeline=timeline,
        sections=sections,
        markers=markers,
        layers=layers,
    )


def _layer_projection(layer, timeline: CompiledTimeline, sections: dict) -> LayerProjection:
    material = layer.material
    start: int | None = None
    end: int | None = None
    if material.kind == "section" and material.section_id in sections:
        section = sections[material.section_id]
        end_bar = section.start_bar + section.bar_count - 1
        start = timeline.bar_start_tick(section.start_bar)
        end = timeline.bar_end_tick(end_bar)
    elif material.start_bar is not None and material.end_bar is not None:
        start, end = timeline.bar_range_ticks(material.start_bar, material.end_bar)
    elif material.kind == "track_range":
        start, end = 0, timeline.duration_ticks
    return LayerProjection(
        id=layer.id,
        state_id=layer.state_id,
        role=layer.role,
        intensity_min=layer.intensity_min,
        intensity_max=layer.intensity_max,
        exclusive_group=layer.exclusive_group,
        priority=layer.priority,
        in_policy=layer.fade.in_policy,
        out_policy=layer.fade.out_policy,
        fade_in_ms=layer.fade.fade_in_ms,
        fade_out_ms=layer.fade.fade_out_ms,
        span_start_tick=start,
        span_end_tick=end,
        track_ids=tuple(layer.material.track_ids[:16]),
        material_kind=layer.material.kind,
    )
