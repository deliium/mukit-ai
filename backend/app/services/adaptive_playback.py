"""Pure adaptive playback clock.

Calls the existing transition scheduler and layer map. Does not import
SQLite, FastAPI, or an LLM, and does not copy note events.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from app.adaptive_playback_schemas import (
    AdaptivePlaybackCommand,
    AdaptivePlaybackInstructionsV1,
    AdaptivePlaybackLastEvent,
    AdaptivePlaybackLoopV1,
    AdaptivePlaybackPendingV1,
    AdaptivePlaybackPhraseV1,
    AdaptivePlaybackQueueItemV1,
    AdaptivePlaybackRuntimeV1,
    AdaptivePlaybackTelemetryV1,
    AdaptivePlaybackTrackGainV1,
)
from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_ERROR_CODES,
    AdaptiveMaterialRefV1,
    AdaptiveScoreError,
    AdaptiveScoreStateV1,
    AdaptiveScoreV1,
    AdaptiveTransitionRuntimeV1,
    AdaptiveTransitionScheduleRequest,
    AdaptiveTransitionScheduleWarningV1,
)
from app.services.adaptive_score_layers import (
    LayerProjection,
    LayerTimelineProjection,
    MappedLayer,
    map_adaptive_layers,
)
from app.services.adaptive_score_transitions import (
    MusicalTransitionSchedule,
    TransitionMarkerProjection,
    TransitionSectionProjection,
    schedule_musical_transition,
)
from app.services.composition_timeline import CompiledTimeline

logger = logging.getLogger(__name__)

_MAX_WARNINGS = 8
_MAX_QUEUE = 4
_WALK_GUARD = 128


@dataclass(frozen=True)
class PlaybackInputs:
    """Read-only graph and timeline. No tracks and no note events."""

    score: AdaptiveScoreV1
    timeline: CompiledTimeline
    sections: tuple[TransitionSectionProjection, ...]
    markers: tuple[TransitionMarkerProjection, ...]
    layers: tuple[LayerProjection, ...]


@dataclass
class _Pending:
    transition_id: str
    to_state_id: str
    boundary_tick: int
    quantization: str
    realization_kind: str
    fade_start_tick: int | None = None
    source_release_tick: int | None = None
    stinger_id: str | None = None
    interrupt_policy: str | None = None


@dataclass
class _QueueItem:
    to_state_id: str
    transition_id: str | None = None


@dataclass
class PlaybackClock:
    """Session extras that are not fields on the public snapshot."""

    playback_id: str
    mode: str
    document_revision: int
    transport: str = "playing"
    runtime_state_id: str = ""
    position_tick: int = 0
    intensity: float = 0.0
    pending: _Pending | None = None
    queue: list[_QueueItem] = field(default_factory=list)
    phase: str = "bed"
    phrase_start: int | None = None
    phrase_end: int | None = None
    phrase_to_state_id: str | None = None
    phrase_transition_id: str | None = None
    active_stinger_id: str | None = None
    stinger_end_tick: int | None = None
    stinger_gain_tick: int = 0
    duck_bed: bool = False
    wait_exit_tick: int | None = None
    waiting_stinger_id: str | None = None
    overlap_release_tick: int | None = None
    rows: tuple[MappedLayer, ...] = ()
    flags: dict[str, bool] = field(default_factory=dict)
    bars_in_state: int = 0
    warnings: list[AdaptiveTransitionScheduleWarningV1] = field(default_factory=list)
    step_count: int = 0
    rejected_request_count: int = 0
    last_event: str = "started"
    seek_tick: int | None = None
    stop: bool = False
    fade_start_tick: int | None = None
    jumped: bool = False
    snapshot: AdaptivePlaybackRuntimeV1 | None = None


def begin_adaptive_playback(
    inputs: PlaybackInputs,
    *,
    playback_id: str,
    mode: str,
    document_revision: int,
) -> PlaybackClock:
    """Create a clock at tick 0 in the score's initial state."""
    initial = inputs.score.initial_state_id
    states = {state.id: state for state in inputs.score.states}
    if initial is None or initial not in states:
        raise AdaptiveScoreError(
            "dangling_state_ref",
            ADAPTIVE_SCORE_ERROR_CODES["dangling_state_ref"],
            http_status=422,
            details={"target_id": initial},
        )
    clock = PlaybackClock(
        playback_id=playback_id,
        mode=mode,
        document_revision=document_revision,
        runtime_state_id=initial,
        last_event="started",
        step_count=1,
    )
    _map_layers(clock, inputs, previous_intensity=None)
    _publish(clock, inputs)
    _debug(clock)
    return clock


def step_adaptive_playback(
    inputs: PlaybackInputs,
    clock: PlaybackClock,
    command: AdaptivePlaybackCommand,
) -> PlaybackClock:
    """Apply one command. A bad state id leaves transport playing."""
    clock.warnings = []
    clock.seek_tick = None
    clock.stop = False
    clock.fade_start_tick = None
    clock.jumped = False
    clock.step_count += 1
    op = command.op
    if op == "stop":
        clock.transport = "stopped"
        clock.stop = True
        clock.last_event = "stopped"
        _publish(clock, inputs)
        _debug(clock)
        return clock
    if clock.transport == "stopped":
        _publish(clock, inputs)
        _debug(clock)
        return clock
    if op == "advance":
        _advance(clock, inputs, command.advance_ticks)
    elif op == "observe":
        _observe(clock, inputs, command.position_tick)
    elif op == "request_state":
        _request_state(clock, inputs, command.to_state_id, command.transition_id)
    elif op == "set_intensity":
        previous = clock.intensity
        clock.intensity = float(command.intensity)
        _map_layers(clock, inputs, previous_intensity=previous)
        clock.last_event = "intensity_changed"
    elif op == "set_flags":
        clock.flags = dict(command.flags)
    if clock.jumped:
        clock.seek_tick = clock.position_tick
    _publish(clock, inputs)
    _debug(clock)
    return clock


def _advance(clock: PlaybackClock, inputs: PlaybackInputs, delta: int) -> None:
    if clock.mode != "simulation":
        _warn(clock, "advance_ignored", message="advance is simulation only.")
        return
    if clock.transport == "held":
        clock.last_event = "held"
        return
    special = _walk(clock, inputs, delta)
    clock.last_event = special or "advanced"


def _observe(clock: PlaybackClock, inputs: PlaybackInputs, tick: int) -> None:
    if clock.mode != "live":
        _warn(clock, "observe_ignored", message="observe is live only.")
        return
    if clock.transport == "held":
        clock.last_event = "held"
        return
    if tick < clock.position_tick and _accept_loop_wrap(clock, inputs, tick):
        clock.position_tick = tick
        clock.bars_in_state += 1
        clock.last_event = "loop_wrapped"
        return
    if tick < clock.position_tick:
        _warn(clock, "observation_behind", message="Observed tick is behind the clock.")
        return
    horizon = _current_bar_ticks(inputs.timeline, clock.position_tick)
    farther = horizon + horizon
    target = tick
    if tick > clock.position_tick + farther:
        _warn(clock, "observation_clamped", message="Observed tick was clamped to the horizon.")
        target = clock.position_tick + horizon
    special = _walk(clock, inputs, target - clock.position_tick)
    clock.last_event = special or "advanced"


def _accept_loop_wrap(clock: PlaybackClock, inputs: PlaybackInputs, tick: int) -> bool:
    state = _state(inputs, clock.runtime_state_id)
    window = _loop_window(state, inputs.timeline)
    if not window.enabled:
        return False
    if clock.pending is not None and clock.pending.boundary_tick == window.end_tick:
        return False
    if not (window.start_tick <= tick < window.end_tick):
        return False
    beat = _beat_ticks(inputs.timeline, max(window.end_tick - 1, window.start_tick))
    last_beat = window.end_tick - beat
    return last_beat <= clock.position_tick < window.end_tick


def _request_state(
    clock: PlaybackClock,
    inputs: PlaybackInputs,
    to_state_id: str,
    transition_id: str | None,
) -> None:
    states = {state.id: state for state in inputs.score.states}
    if to_state_id not in states:
        _reject_request(clock, inputs, "dangling_state_ref", to_state_id)
        return
    if _can_schedule_now(clock):
        if not _schedule_one(clock, inputs, to_state_id, transition_id, clock.position_tick):
            return
        _consume_due(clock, inputs)
        return
    if len(clock.queue) >= _MAX_QUEUE:
        _warn(clock, "playback_queue_full", message="Playback queue is full.")
        clock.last_event = "request_rejected"
        return
    clock.queue.append(_QueueItem(to_state_id=to_state_id, transition_id=transition_id))
    clock.last_event = "request_queued"


def _can_schedule_now(clock: PlaybackClock) -> bool:
    return (
        clock.pending is None
        and clock.phase == "bed"
        and clock.phrase_end is None
        and clock.overlap_release_tick is None
        and clock.transport == "playing"
    )


def _reject_request(
    clock: PlaybackClock,
    inputs: PlaybackInputs,
    code: str,
    target_id: str | None,
) -> None:
    clock.rejected_request_count += 1
    _warn(clock, code, target_id=target_id, message=ADAPTIVE_SCORE_ERROR_CODES.get(code, code))
    clock.last_event = "request_rejected"
    clock.transport = "playing" if clock.transport != "held" else clock.transport
    _apply_invalid_policy(clock, inputs)


def _apply_invalid_policy(clock: PlaybackClock, inputs: PlaybackInputs) -> None:
    policy = inputs.score.fallback.on_invalid_transition
    if policy != "default_state":
        return
    default_id = inputs.score.default_state_id
    states = {state.id: state for state in inputs.score.states}
    if default_id is None or default_id not in states:
        return
    if not _can_schedule_now(clock):
        if len(clock.queue) < _MAX_QUEUE and all(item.to_state_id != default_id for item in clock.queue):
            clock.queue.append(_QueueItem(to_state_id=default_id, transition_id=None))
        return
    _schedule_one(clock, inputs, default_id, None, clock.position_tick, count_reject=False)


def _schedule_one(
    clock: PlaybackClock,
    inputs: PlaybackInputs,
    to_state_id: str,
    transition_id: str | None,
    position_tick: int,
    *,
    count_reject: bool = True,
) -> bool:
    request = AdaptiveTransitionScheduleRequest(
        expected_document_revision=max(1, clock.document_revision),
        from_state_id=clock.runtime_state_id,
        to_state_id=to_state_id,
        transition_id=transition_id,
        position_tick=max(0, position_tick),
        runtime=AdaptiveTransitionRuntimeV1(
            intensity=clock.intensity,
            flags=dict(clock.flags),
            bars_in_state=clock.bars_in_state,
        ),
    )
    try:
        plan = schedule_musical_transition(
            inputs.score,
            inputs.timeline,
            inputs.sections,
            inputs.markers,
            request,
        )
    except AdaptiveScoreError as exc:
        if count_reject:
            clock.rejected_request_count += 1
        _warn(clock, exc.code, target_id=to_state_id, message=exc.message)
        clock.last_event = "request_rejected"
        if clock.transport != "held":
            clock.transport = "playing"
        if count_reject:
            _apply_invalid_policy(clock, inputs)
        return False
    clock.pending = _pending_from_plan(plan)
    return True


def _pending_from_plan(plan: MusicalTransitionSchedule) -> _Pending:
    realization = plan.realization
    fade_start = getattr(realization, "fade_start_tick", None)
    release = getattr(realization, "source_release_tick", None)
    stinger_id = getattr(realization, "stinger_id", None)
    interrupt = getattr(realization, "interrupt_policy", None)
    return _Pending(
        transition_id=plan.transition_id,
        to_state_id=plan.to_state_id,
        boundary_tick=plan.boundary_tick,
        quantization=plan.quantization,
        realization_kind=realization.kind,
        fade_start_tick=fade_start if isinstance(fade_start, int) else None,
        source_release_tick=release if isinstance(release, int) else None,
        stinger_id=stinger_id if isinstance(stinger_id, str) else None,
        interrupt_policy=interrupt if isinstance(interrupt, str) else None,
    )


def _consume_due(clock: PlaybackClock, inputs: PlaybackInputs) -> None:
    guard = 0
    while clock.pending is not None and clock.pending.boundary_tick <= clock.position_tick and guard < 8:
        guard += 1
        _handle_boundary(clock, inputs)


def _walk(clock: PlaybackClock, inputs: PlaybackInputs, delta: int) -> str | None:
    remaining = max(0, delta)
    guard = 0
    special: str | None = None
    while remaining > 0 and guard < _WALK_GUARD:
        guard += 1
        events = _events(clock, inputs, remaining)
        if not events:
            _move_by(clock, inputs, remaining)
            remaining = 0
            break
        tick, kind = min(events, key=lambda item: (item[0], _kind_rank(item[1])))
        distance = tick - clock.position_tick
        if distance > 0:
            _move_by(clock, inputs, distance)
            remaining -= distance
        handled = _handle_event(clock, inputs, kind)
        if handled:
            special = handled
    return special


def _events(clock: PlaybackClock, inputs: PlaybackInputs, remaining: int) -> list[tuple[int, str]]:
    limit = clock.position_tick + remaining
    events: list[tuple[int, str]] = []
    position = clock.position_tick
    if clock.pending is not None and position < clock.pending.boundary_tick <= limit:
        events.append((clock.pending.boundary_tick, "boundary"))
    if clock.phase == "phrase" and clock.phrase_end is not None and position < clock.phrase_end <= limit:
        events.append((clock.phrase_end, "phrase_end"))
    if (
        clock.active_stinger_id
        and clock.stinger_end_tick is not None
        and position < clock.stinger_end_tick <= limit
    ):
        events.append((clock.stinger_end_tick, "stinger_end"))
    if (
        clock.wait_exit_tick is not None
        and clock.active_stinger_id is None
        and position < clock.wait_exit_tick <= limit
    ):
        events.append((clock.wait_exit_tick, "arm_stinger"))
    if clock.overlap_release_tick is not None and position < clock.overlap_release_tick <= limit:
        events.append((clock.overlap_release_tick, "overlap_release"))
    window = _loop_window(_state(inputs, clock.runtime_state_id), inputs.timeline)
    pending_at_loop = (
        clock.pending is not None and window.enabled and clock.pending.boundary_tick == window.end_tick
    )
    open_phrase = clock.phase == "phrase" or clock.phrase_end is not None
    open_stinger = clock.active_stinger_id is not None or clock.phase == "stinger"
    if window.enabled and not open_phrase and not open_stinger and not pending_at_loop:
        if position < window.end_tick <= limit:
            events.append((window.end_tick, "loop"))
    return events


def _kind_rank(kind: str) -> int:
    order = {
        "boundary": 0,
        "phrase_end": 1,
        "arm_stinger": 2,
        "stinger_end": 3,
        "overlap_release": 4,
        "loop": 5,
    }
    return order.get(kind, 9)


def _handle_event(clock: PlaybackClock, inputs: PlaybackInputs, kind: str) -> str | None:
    if kind == "loop":
        window = _loop_window(_state(inputs, clock.runtime_state_id), inputs.timeline)
        clock.position_tick = window.start_tick
        clock.jumped = True
        return "loop_wrapped"
    if kind == "boundary":
        return _handle_boundary(clock, inputs)
    if kind == "phrase_end":
        return _finish_phrase(clock, inputs)
    if kind == "stinger_end":
        return _finish_stinger(clock)
    if kind == "arm_stinger":
        return _arm_waiting_stinger(clock, inputs)
    if kind == "overlap_release":
        return _finish_overlap(clock, inputs)
    return None


def _handle_boundary(clock: PlaybackClock, inputs: PlaybackInputs) -> str | None:
    pending = clock.pending
    if pending is None:
        return None
    if pending.realization_kind == "overlap" and clock.overlap_release_tick == pending.boundary_tick:
        return _finish_overlap(clock, inputs)
    clock.pending = None
    if pending.realization_kind == "phrase":
        span = _phrase_span(inputs, pending.transition_id)
        if span is None:
            _warn(
                clock,
                "phrase_span_unchecked",
                target_id=pending.transition_id,
                message="Phrase span could not be compiled.",
            )
            return _commit_bed(clock, inputs, pending, phrase_fallback=True)
        clock.phase = "phrase"
        clock.phrase_start, clock.phrase_end = span
        clock.phrase_to_state_id = pending.to_state_id
        clock.phrase_transition_id = pending.transition_id
        if span[0] != clock.position_tick:
            _teleport(clock, span[0])
        return "phrase_started"
    return _commit_bed(clock, inputs, pending, phrase_fallback=False)


def _commit_bed(
    clock: PlaybackClock,
    inputs: PlaybackInputs,
    pending: _Pending,
    *,
    phrase_fallback: bool,
) -> str | None:
    if pending.realization_kind == "overlap" and pending.source_release_tick is not None:
        return _begin_overlap(clock, inputs, pending)
    span = _material_ticks(_state_by_id(inputs, pending.to_state_id), inputs)
    if span is None:
        return _missing_destination(clock, inputs)
    entry = _entry_tick(_state_by_id(inputs, pending.to_state_id), inputs)
    if entry is None:
        return _missing_destination(clock, inputs)
    clock.runtime_state_id = pending.to_state_id
    clock.bars_in_state = 0
    clock.phase = "bed"
    clock.phrase_start = None
    clock.phrase_end = None
    clock.phrase_to_state_id = None
    clock.phrase_transition_id = None
    if entry != clock.position_tick:
        _teleport(clock, entry)
    if pending.realization_kind == "crossfade" and pending.fade_start_tick is not None:
        clock.fade_start_tick = pending.fade_start_tick
    event: str = "state_committed"
    if pending.realization_kind == "stinger":
        armed = _begin_stinger(clock, inputs, pending)
        if armed == "stinger_started":
            event = "stinger_started"
    _map_layers(clock, inputs, previous_intensity=clock.intensity)
    _pump_queue(clock, inputs, clock.position_tick + 1)
    if phrase_fallback:
        return "state_committed"
    return event


def _begin_overlap(clock: PlaybackClock, inputs: PlaybackInputs, pending: _Pending) -> str:
    span = _material_ticks(_state_by_id(inputs, pending.to_state_id), inputs)
    if span is None:
        result = _missing_destination(clock, inputs)
        return result or "held"
    clock.runtime_state_id = pending.to_state_id
    clock.bars_in_state = 0
    clock.phase = "bed"
    release = pending.source_release_tick or clock.position_tick
    clock.overlap_release_tick = release
    clock.pending = _Pending(
        transition_id=pending.transition_id,
        to_state_id=pending.to_state_id,
        boundary_tick=release,
        quantization=pending.quantization,
        realization_kind="overlap",
        source_release_tick=release,
    )
    _map_layers(clock, inputs, previous_intensity=clock.intensity)
    return "state_committed"


def _finish_overlap(clock: PlaybackClock, inputs: PlaybackInputs) -> str:
    clock.overlap_release_tick = None
    clock.pending = None
    entry = _entry_tick(_state(inputs, clock.runtime_state_id), inputs)
    if entry is not None and entry > clock.position_tick:
        _teleport(clock, entry)
    _pump_queue(clock, inputs, clock.position_tick + 1)
    return "state_committed"


def _begin_stinger(clock: PlaybackClock, inputs: PlaybackInputs, pending: _Pending) -> str | None:
    stinger_id = pending.stinger_id
    if not stinger_id:
        return None
    span = _stinger_span(inputs, stinger_id)
    policy = pending.interrupt_policy or "overlay"
    if policy == "wait_for_exit":
        source_id = _transition_source(inputs, pending.transition_id)
        exit_tick = _exit_tick(inputs, source_id) if source_id else None
        if exit_tick is None or exit_tick <= clock.position_tick:
            return _activate_stinger(clock, inputs, stinger_id, span, policy)
        clock.wait_exit_tick = exit_tick
        clock.waiting_stinger_id = stinger_id
        clock.duck_bed = False
        return None
    return _activate_stinger(clock, inputs, stinger_id, span, policy)


def _activate_stinger(
    clock: PlaybackClock,
    inputs: PlaybackInputs,
    stinger_id: str,
    span: tuple[int, int] | None,
    policy: str,
) -> str | None:
    if span is None:
        _warn(clock, "realization_invalid", target_id=stinger_id, message="Stinger span is missing.")
        return None
    clock.active_stinger_id = stinger_id
    clock.stinger_end_tick = span[1]
    clock.stinger_gain_tick = clock.position_tick
    clock.phase = "stinger"
    clock.duck_bed = policy == "duck_bed"
    clock.wait_exit_tick = None
    clock.waiting_stinger_id = None
    if span[0] != clock.position_tick and policy != "overlay":
        return "stinger_started"
    return "stinger_started"


def _arm_waiting_stinger(clock: PlaybackClock, inputs: PlaybackInputs) -> str | None:
    stinger_id = clock.waiting_stinger_id
    clock.wait_exit_tick = None
    clock.waiting_stinger_id = None
    if not stinger_id:
        return None
    span = _stinger_span(inputs, stinger_id)
    return _activate_stinger(clock, inputs, stinger_id, span, "overlay")


def _finish_stinger(clock: PlaybackClock) -> str:
    clock.active_stinger_id = None
    clock.stinger_end_tick = None
    clock.stinger_gain_tick = clock.position_tick
    clock.duck_bed = False
    clock.phase = "bed"
    return "stinger_finished"


def _finish_phrase(clock: PlaybackClock, inputs: PlaybackInputs) -> str | None:
    to_state = clock.phrase_to_state_id
    transition_id = clock.phrase_transition_id or ""
    clock.phase = "bed"
    clock.phrase_start = None
    clock.phrase_end = None
    clock.phrase_to_state_id = None
    clock.phrase_transition_id = None
    if to_state is None:
        return "phrase_finished"
    span = _material_ticks(_state_by_id(inputs, to_state), inputs)
    entry = _entry_tick(_state_by_id(inputs, to_state), inputs)
    if span is None or entry is None:
        result = _missing_destination(clock, inputs)
        return result
    inside = span[0] <= clock.position_tick < span[1] or clock.position_tick == span[1]
    clock.runtime_state_id = to_state
    clock.bars_in_state = 0
    if entry > clock.position_tick or not inside:
        if entry != clock.position_tick:
            _teleport(clock, entry)
    _map_layers(clock, inputs, previous_intensity=clock.intensity)
    _pump_queue(clock, inputs, clock.position_tick + 1)
    return "state_committed"


def _missing_destination(clock: PlaybackClock, inputs: PlaybackInputs) -> str:
    policy = inputs.score.fallback.on_missing_material
    if policy == "silence":
        clock.transport = "held"
        clock.last_event = "held"
        return "held"
    if policy == "default_state":
        _apply_invalid_policy(clock, inputs)
        return "request_rejected"
    clock.transport = "playing"
    return "held"


def _pump_queue(clock: PlaybackClock, inputs: PlaybackInputs, schedule_tick: int) -> None:
    if clock.queue and _can_schedule_now(clock):
        item = clock.queue.pop(0)
        _schedule_one(
            clock,
            inputs,
            item.to_state_id,
            item.transition_id,
            schedule_tick,
            count_reject=True,
        )


def _teleport(clock: PlaybackClock, tick: int) -> None:
    clock.position_tick = max(0, tick)
    clock.jumped = True


def _move_by(clock: PlaybackClock, inputs: PlaybackInputs, delta: int) -> None:
    if delta <= 0:
        return
    target = min(inputs.timeline.duration_ticks, clock.position_tick + delta)
    clock.bars_in_state += _bars_crossed(inputs.timeline, clock.position_tick, target)
    clock.position_tick = target


def _bars_crossed(timeline: CompiledTimeline, start: int, end: int) -> int:
    if end <= start:
        return 0
    return sum(1 for boundary in timeline.bar_boundaries if start < boundary <= end)


def _map_layers(
    clock: PlaybackClock,
    inputs: PlaybackInputs,
    *,
    previous_intensity: float | None,
) -> None:
    timeline = LayerTimelineProjection(
        bar_boundaries=inputs.timeline.bar_boundaries,
        duration_ticks=inputs.timeline.duration_ticks,
        ticks_per_quarter=inputs.timeline.ticks_per_quarter,
        tempo_bpm=inputs.timeline.active_tempo(min(clock.position_tick, inputs.timeline.duration_ticks)),
    )
    try:
        mapped = map_adaptive_layers(
            inputs.layers,
            state_id=clock.runtime_state_id,
            intensity=clock.intensity,
            position_tick=min(clock.position_tick, inputs.timeline.duration_ticks),
            previous_intensity=previous_intensity,
            timeline=timeline,
        )
    except AdaptiveScoreError as exc:
        _warn(clock, exc.code, message=exc.message)
        return
    clock.rows = mapped.layers
    for item in mapped.warnings:
        _warn(clock, item.code, target_id=item.target_id, message=item.message)


def _publish(clock: PlaybackClock, inputs: PlaybackInputs) -> None:
    timeline = inputs.timeline
    position = min(max(clock.position_tick, 0), timeline.duration_ticks)
    clock.position_tick = position
    bar = timeline.bar_at_tick(position)
    beat = _beat_number(timeline, position)
    state = _state(inputs, clock.runtime_state_id)
    loop = _loop_window(state, timeline)
    horizon = _current_bar_ticks(timeline, position)
    horizon_end = min(timeline.duration_ticks, position + horizon)
    arm = (
        clock.pending is not None
        and position <= clock.pending.boundary_tick <= horizon_end
    )
    instruction_loop = AdaptivePlaybackLoopV1(
        enabled=loop.enabled and not (arm and clock.pending is not None and clock.pending.boundary_tick == loop.end_tick),
        start_bar=loop.start_bar,
        end_bar=loop.end_bar,
        start_tick=loop.start_tick,
        end_tick=loop.end_tick,
    )
    phrase = None
    if clock.phrase_start is not None and clock.phrase_end is not None and clock.phase == "phrase":
        phrase = AdaptivePlaybackPhraseV1(start_tick=clock.phrase_start, end_tick=clock.phrase_end)
    pending = None
    if clock.pending is not None:
        pending = AdaptivePlaybackPendingV1(
            transition_id=clock.pending.transition_id,
            to_state_id=clock.pending.to_state_id,
            boundary_tick=clock.pending.boundary_tick,
            quantization=clock.pending.quantization,  # type: ignore[arg-type]
            realization_kind=clock.pending.realization_kind,  # type: ignore[arg-type]
        )
    active = [row.layer_id for row in clock.rows if row.active]
    last_event: AdaptivePlaybackLastEvent = clock.last_event  # type: ignore[assignment]
    clock.snapshot = AdaptivePlaybackRuntimeV1(
        playback_id=clock.playback_id,
        mode=clock.mode,  # type: ignore[arg-type]
        transport=clock.transport,  # type: ignore[arg-type]
        runtime_state_id=clock.runtime_state_id,
        position_tick=position,
        bar=bar,
        beat=beat,
        intensity=clock.intensity,
        loop=instruction_loop,
        active_layer_ids=active,
        pending_transition=pending,
        queue=[
            AdaptivePlaybackQueueItemV1(to_state_id=item.to_state_id, transition_id=item.transition_id)
            for item in clock.queue[:_MAX_QUEUE]
        ],
        horizon_end_tick=horizon_end,
        arm_boundary=arm,
        phase=clock.phase,  # type: ignore[arg-type]
        phrase=phrase,
        active_stinger_id=clock.active_stinger_id,
        instructions=AdaptivePlaybackInstructionsV1(
            seek_tick=clock.seek_tick,
            loop=instruction_loop,
            track_gains=_track_gains(clock, inputs),
            stop=clock.stop,
            fade_start_tick=clock.fade_start_tick,
        ),
        warnings=list(clock.warnings[:_MAX_WARNINGS]),
        telemetry=AdaptivePlaybackTelemetryV1(
            step_count=clock.step_count,
            rejected_request_count=clock.rejected_request_count,
            last_event=last_event,
        ),
        document_revision=clock.document_revision,
    )


def _track_gains(clock: PlaybackClock, inputs: PlaybackInputs) -> list[AdaptivePlaybackTrackGainV1]:
    order: list[str] = []
    chosen: dict[str, dict[str, int | bool]] = {}
    for row in clock.rows:
        if row.material_kind != "track_range":
            continue
        gain = 0 if clock.duck_bed else int(row.target_gain)
        for track_id in row.track_ids:
            slot = chosen.get(track_id)
            if slot is None:
                order.append(track_id)
                chosen[track_id] = {"gain": gain, "fade": row.fade_end_tick, "layer": True}
            elif gain == 1 and not clock.duck_bed:
                slot["gain"] = 1
                slot["fade"] = row.fade_end_tick
    for stinger in inputs.score.stingers:
        if stinger.material.kind != "track_range":
            continue
        active = clock.active_stinger_id == stinger.id
        for track_id in stinger.material.track_ids:
            slot = chosen.get(track_id)
            if slot is None:
                order.append(track_id)
                slot = {"gain": 0, "fade": clock.position_tick, "layer": False}
                chosen[track_id] = slot
            if active:
                slot["gain"] = 1
                slot["fade"] = clock.stinger_gain_tick
            elif not slot["layer"]:
                slot["gain"] = 0
                slot["fade"] = clock.position_tick
    return [
        AdaptivePlaybackTrackGainV1(
            track_id=track_id,
            target_gain=1 if chosen[track_id]["gain"] else 0,
            fade_end_tick=max(0, int(chosen[track_id]["fade"])),
        )
        for track_id in order
    ]


def _warn(
    clock: PlaybackClock,
    code: str,
    *,
    target_id: str | None = None,
    message: str = "",
) -> None:
    if len(clock.warnings) >= _MAX_WARNINGS:
        return
    clock.warnings.append(
        AdaptiveTransitionScheduleWarningV1(
            code=code[:80],
            target_id=None if target_id is None else target_id[:80],
            message=message[:200],
        )
    )


def _debug(clock: PlaybackClock) -> None:
    active = sum(1 for row in clock.rows if row.active)
    fade_end = next((row.fade_end_tick for row in clock.rows if row.active), None)
    logger.debug(
        "Adaptive playback stepped",
        extra={
            "last_event": clock.last_event,
            "position_tick": clock.position_tick,
            "runtime_state_id": clock.runtime_state_id,
            "queue_depth": len(clock.queue),
            "intensity": clock.intensity,
            "active_count": active,
            "fade_end_tick": fade_end,
        },
    )


def _state(inputs: PlaybackInputs, state_id: str) -> AdaptiveScoreStateV1:
    found = next((state for state in inputs.score.states if state.id == state_id), None)
    if found is None:
        raise AdaptiveScoreError(
            "dangling_state_ref",
            ADAPTIVE_SCORE_ERROR_CODES["dangling_state_ref"],
            http_status=422,
            details={"target_id": state_id},
        )
    return found


def _state_by_id(inputs: PlaybackInputs, state_id: str) -> AdaptiveScoreStateV1:
    return _state(inputs, state_id)


def _loop_window(state: AdaptiveScoreStateV1, timeline: CompiledTimeline) -> AdaptivePlaybackLoopV1:
    loop = state.loop
    if not loop.enabled or loop.start_bar is None or loop.end_bar is None:
        return AdaptivePlaybackLoopV1()
    try:
        start = timeline.bar_start_tick(loop.start_bar)
        end = timeline.bar_end_tick(loop.end_bar)
    except ValueError:
        return AdaptivePlaybackLoopV1(enabled=False, start_bar=loop.start_bar, end_bar=loop.end_bar)
    return AdaptivePlaybackLoopV1(
        enabled=True,
        start_bar=loop.start_bar,
        end_bar=loop.end_bar,
        start_tick=start,
        end_tick=end,
    )


def _current_bar_ticks(timeline: CompiledTimeline, position: int) -> int:
    safe = min(max(position, 0), timeline.duration_ticks)
    if safe == timeline.duration_ticks and timeline.bar_count:
        bar = timeline.bar_count
    else:
        bar = timeline.bar_at_tick(safe)
    return max(1, timeline.bar_end_tick(bar) - timeline.bar_start_tick(bar))


def _beat_ticks(timeline: CompiledTimeline, position: int) -> int:
    safe = min(max(position, 0), max(timeline.duration_ticks - 1, 0))
    bar = timeline.bar_at_tick(safe)
    start = timeline.bar_start_tick(bar)
    end = timeline.bar_end_tick(bar)
    numerator = int(str(timeline.active_time_signature(start)).split("/", 1)[0] or "4")
    span = end - start
    if numerator <= 0 or span <= 0:
        return 1
    if span % numerator == 0:
        return span // numerator
    return max(1, span // numerator)


def _beat_number(timeline: CompiledTimeline, position: int) -> int:
    if position >= timeline.duration_ticks and timeline.bar_count:
        return 1
    bar = timeline.bar_at_tick(position)
    start = timeline.bar_start_tick(bar)
    length = _beat_ticks(timeline, position)
    if length <= 0:
        return 1
    return int(math.floor((position - start) / length)) + 1


def _material_span_bars(
    material: AdaptiveMaterialRefV1,
    sections: tuple[TransitionSectionProjection, ...],
) -> tuple[int, int] | None:
    if material.kind in {"bar_range", "track_range", "revision_region"} and material.start_bar and material.end_bar:
        return material.start_bar, material.end_bar
    if material.kind == "section" and material.section_id:
        section = next((item for item in sections if item.section_id == material.section_id), None)
        if section is None or section.bar_count < 1:
            return None
        return section.start_bar, section.start_bar + section.bar_count - 1
    return None


def _material_ticks(state: AdaptiveScoreStateV1, inputs: PlaybackInputs) -> tuple[int, int] | None:
    bars = _material_span_bars(state.material, inputs.sections)
    if bars is None:
        return None
    try:
        return inputs.timeline.bar_range_ticks(bars[0], bars[1])
    except ValueError:
        return None


def _entry_tick(state: AdaptiveScoreStateV1, inputs: PlaybackInputs) -> int | None:
    boundary = state.entry
    if boundary.kind == "bar" and boundary.bar is not None:
        try:
            return inputs.timeline.bar_start_tick(boundary.bar)
        except ValueError:
            return None
    if boundary.kind == "tick" and boundary.tick is not None:
        return boundary.tick
    span = _material_ticks(state, inputs)
    if span is None:
        return None
    if boundary.kind == "material_end":
        return span[1]
    return span[0]


def _exit_tick(inputs: PlaybackInputs, state_id: str | None) -> int | None:
    if state_id is None:
        return None
    try:
        state = _state(inputs, state_id)
    except AdaptiveScoreError:
        return None
    boundary = state.exit
    if boundary.kind == "bar" and boundary.bar is not None:
        try:
            return inputs.timeline.bar_start_tick(boundary.bar)
        except ValueError:
            return None
    if boundary.kind == "tick":
        return boundary.tick
    span = _material_ticks(state, inputs)
    if span is None:
        return None
    if boundary.kind == "material_start":
        return span[0]
    return span[1]


def _phrase_span(inputs: PlaybackInputs, transition_id: str) -> tuple[int, int] | None:
    transition = next((item for item in inputs.score.transitions if item.id == transition_id), None)
    if transition is None or transition.realization.phrase_material is None:
        return None
    bars = _material_span_bars(transition.realization.phrase_material, inputs.sections)
    if bars is None:
        return None
    try:
        return inputs.timeline.bar_range_ticks(bars[0], bars[1])
    except ValueError:
        return None


def _stinger_span(inputs: PlaybackInputs, stinger_id: str) -> tuple[int, int] | None:
    stinger = next((item for item in inputs.score.stingers if item.id == stinger_id), None)
    if stinger is None:
        return None
    bars = _material_span_bars(stinger.material, inputs.sections)
    if bars is None:
        if stinger.material.start_tick is not None and stinger.material.end_tick is not None:
            return stinger.material.start_tick, stinger.material.end_tick
        return None
    try:
        return inputs.timeline.bar_range_ticks(bars[0], bars[1])
    except ValueError:
        return None


def _transition_source(inputs: PlaybackInputs, transition_id: str) -> str | None:
    transition = next((item for item in inputs.score.transitions if item.id == transition_id), None)
    if transition is None:
        return None
    return transition.from_state_id
