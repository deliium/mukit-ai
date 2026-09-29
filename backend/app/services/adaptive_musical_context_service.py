"""In-memory musical context that emits existing playback commands.

Reads the score once at start to check the revision and state ids. A sample
does not write the score, the composition, or the playback registry.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from app.adaptive_musical_context_schemas import (
    ADAPTIVE_MUSICAL_CONTEXT_SCHEMA,
    CONTEXT_NOT_RUNNING_MESSAGE,
    AdaptiveContextExternalV1,
    AdaptiveContextMappingV1,
    AdaptiveContextStartRequest,
    AdaptiveMusicalContextV1,
)
from app.adaptive_playback_schemas import (
    AdaptivePlaybackRequestStateCommand,
    AdaptivePlaybackSetFlagsCommand,
    AdaptivePlaybackSetIntensityCommand,
)
from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_ERROR_CODES,
    AdaptiveScoreError,
    AdaptiveTransitionScheduleWarningV1,
)
from app.services.adaptive_musical_context import (
    MusicalContextClock,
    begin_musical_context,
    musical_context_snapshot,
    step_musical_context,
)
from app.services.adaptive_musical_context_runtime import (
    AdaptiveMusicalContextRegistry,
    HeldContext,
    get_default_context_registry,
)
from app.services.adaptive_playback_runtime import get_default_playback_registry
from app.services.adaptive_playback_service import command_adaptive_playback
from app.services.adaptive_score_store import get_score

logger = logging.getLogger(__name__)

_MAX_WARNINGS = 8


def start_adaptive_musical_context(
    project_id: str,
    score_id: str,
    request: AdaptiveContextStartRequest,
    *,
    db_path: Path | None = None,
    registry: AdaptiveMusicalContextRegistry | None = None,
) -> AdaptiveMusicalContextV1:
    """Open a context session. Playback is not started."""
    slots = registry or get_default_context_registry()
    try:
        record = get_score(project_id, score_id, db_path=db_path)
        if record.document_revision != request.expected_document_revision:
            raise AdaptiveScoreError(
                "adaptive_score_conflict",
                ADAPTIVE_SCORE_ERROR_CODES["adaptive_score_conflict"],
                http_status=409,
            )
        _require_state_ids(record.score.states, request.mapping)
    except AdaptiveScoreError as exc:
        logger.error("Adaptive musical context load failed", extra={"code": exc.code})
        raise
    context_id = "actx_" + secrets.token_hex(4)
    clock = begin_musical_context(
        request.mapping,
        context_id=context_id,
        document_revision=record.document_revision,
    )
    slots.put(project_id, score_id, HeldContext(clock=clock, mapping=request.mapping))
    snapshot = musical_context_snapshot(clock)
    _log(project_id=project_id, score_id=score_id, snapshot=snapshot, state_changed=False)
    logger.debug(
        "Adaptive musical context session started",
        extra={
            "dwell_rule_id": snapshot.dwell_rule_id,
            "dwell_count": snapshot.dwell_count,
            "binding_count": len(request.mapping.bindings),
        },
    )
    return snapshot


def get_adaptive_musical_context(
    project_id: str,
    score_id: str,
    *,
    db_path: Path | None = None,
    registry: AdaptiveMusicalContextRegistry | None = None,
) -> AdaptiveMusicalContextV1 | None:
    get_score(project_id, score_id, db_path=db_path)
    held = (registry or get_default_context_registry()).get(project_id, score_id)
    if held is None:
        return None
    return musical_context_snapshot(held.clock)


def sample_adaptive_musical_context(
    project_id: str,
    score_id: str,
    sample: AdaptiveContextExternalV1,
    *,
    db_path: Path | None = None,
    registry: AdaptiveMusicalContextRegistry | None = None,
) -> AdaptiveMusicalContextV1:
    """Step one sample. Emit playback commands only when that clock is already running."""
    slots = registry or get_default_context_registry()
    held = slots.get(project_id, score_id)
    if held is None:
        raise AdaptiveScoreError(
            "context_not_running",
            CONTEXT_NOT_RUNNING_MESSAGE,
            http_status=404,
        )
    try:
        record = get_score(project_id, score_id, db_path=db_path)
    except AdaptiveScoreError as exc:
        logger.error("Adaptive musical context load failed", extra={"code": exc.code})
        raise
    if record.document_revision != held.clock.document_revision:
        rejected = _reject_stale(held.clock)
        slots.put(project_id, score_id, HeldContext(clock=rejected, mapping=held.mapping))
        snapshot = musical_context_snapshot(rejected)
        _log(project_id=project_id, score_id=score_id, snapshot=snapshot, state_changed=False)
        return snapshot
    previous_state = held.clock.musical_state_id
    stepped = step_musical_context(held.mapping, held.clock, sample)
    playback = get_default_playback_registry().get(project_id, score_id)
    playback_warnings: list[AdaptiveTransitionScheduleWarningV1] = []
    if playback is None:
        stepped.warnings = _union(
            [],
            [
                *stepped.warnings,
                AdaptiveTransitionScheduleWarningV1(
                    code="playback_not_running",
                    message=ADAPTIVE_SCORE_ERROR_CODES["playback_not_running"],
                ),
            ],
        )
    else:
        for item in stepped.emitted:
            result = command_adaptive_playback(
                project_id,
                score_id,
                _playback_command(item),
                db_path=db_path,
            )
            playback_warnings.extend(result.warnings)
        stepped.warnings = _union(playback_warnings, stepped.warnings)
    slots.put(project_id, score_id, HeldContext(clock=stepped, mapping=held.mapping))
    snapshot = musical_context_snapshot(stepped)
    _log(
        project_id=project_id,
        score_id=score_id,
        snapshot=snapshot,
        state_changed=snapshot.musical_state_id != previous_state,
    )
    logger.debug(
        "Adaptive musical context sample applied",
        extra={
            "dwell_rule_id": snapshot.dwell_rule_id,
            "dwell_count": snapshot.dwell_count,
        },
    )
    return snapshot


def stop_adaptive_musical_context(
    project_id: str,
    score_id: str,
    *,
    db_path: Path | None = None,
    registry: AdaptiveMusicalContextRegistry | None = None,
) -> None:
    """Clear a session. A missing session stays quiet."""
    get_score(project_id, score_id, db_path=db_path)
    slots = registry or get_default_context_registry()
    held = slots.get(project_id, score_id)
    slots.clear(project_id, score_id)
    if held is None:
        return
    snapshot = musical_context_snapshot(held.clock)
    _log(project_id=project_id, score_id=score_id, snapshot=snapshot, state_changed=False)


def _require_state_ids(states: list, mapping: AdaptiveContextMappingV1) -> None:
    known = {state.id for state in states}
    missing = [mapping.baseline_state_id]
    missing.extend(rule.target_state_id for rule in mapping.state_rules)
    absent = next((state_id for state_id in missing if state_id not in known), None)
    if absent is not None:
        raise AdaptiveScoreError(
            "dangling_state_ref",
            ADAPTIVE_SCORE_ERROR_CODES["dangling_state_ref"],
            http_status=422,
            details={"target_id": absent},
        )


def _reject_stale(clock: MusicalContextClock) -> MusicalContextClock:
    warnings = list(clock.warnings)
    if not any(item.code == "document_revision_conflict" for item in warnings):
        warnings.append(
            AdaptiveTransitionScheduleWarningV1(
                code="document_revision_conflict",
                message="Score revision changed after context started.",
            )
        )
    clock.warnings = _union([], warnings)
    clock.telemetry = clock.telemetry.model_copy(
        update={"rejected_sample_count": clock.telemetry.rejected_sample_count + 1}
    )
    return clock


def _union(
    playback_warnings: list[AdaptiveTransitionScheduleWarningV1],
    context_warnings: list[AdaptiveTransitionScheduleWarningV1],
) -> list[AdaptiveTransitionScheduleWarningV1]:
    chosen: list[AdaptiveTransitionScheduleWarningV1] = []
    seen: set[str] = set()
    for warning in [*playback_warnings, *context_warnings]:
        if warning.code in seen:
            continue
        seen.add(warning.code)
        chosen.append(warning)
        if len(chosen) >= _MAX_WARNINGS:
            break
    return chosen


def _playback_command(item):
    if item.op == "set_intensity":
        return AdaptivePlaybackSetIntensityCommand(op="set_intensity", intensity=item.intensity)
    if item.op == "set_flags":
        return AdaptivePlaybackSetFlagsCommand(op="set_flags", flags=dict(item.flags))
    return AdaptivePlaybackRequestStateCommand(op="request_state", to_state_id=item.to_state_id)


def _log(
    *,
    project_id: str,
    score_id: str,
    snapshot: AdaptiveMusicalContextV1,
    state_changed: bool,
) -> None:
    logger.info(
        "Adaptive musical context",
        extra={
            "adaptive_musical_context": True,
            "project_id": project_id,
            "score_id": score_id,
            "context_id": snapshot.context_id,
            "sample_index": snapshot.sample_index,
            "musical_state_id": snapshot.musical_state_id,
            "state_changed": state_changed,
            "emitted_ops": [item.op for item in snapshot.emitted],
            "warning_codes": [item.code for item in snapshot.warnings],
            "sample_count": snapshot.telemetry.sample_count,
            "state_change_count": snapshot.telemetry.state_change_count,
            "intensity_emit_count": snapshot.telemetry.intensity_emit_count,
            "rejected_sample_count": snapshot.telemetry.rejected_sample_count,
            "schema_version": ADAPTIVE_MUSICAL_CONTEXT_SCHEMA,
        },
    )
