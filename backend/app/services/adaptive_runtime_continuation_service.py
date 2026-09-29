"""Adaptive runtime continuation session.

Reads the playback snapshot, the score, and the composition. Never commands
playback and never writes the score or ``projects.composition_json``. The arm
path realizes a fallback and returns. The symbolic model runs only inside a
coroutine the caller schedules.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import secrets
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from app.adaptive_runtime_continuation_schemas import (
    AdaptiveRuntimeBufferHarmonyV1,
    AdaptiveRuntimeBufferV1,
    AdaptiveRuntimeContinuationStartRequest,
    AdaptiveRuntimeContinuationTelemetryV1,
    AdaptiveRuntimeContinuationV1,
    continuation_not_running_error,
)
from app.adaptive_runtime_continuation_settings import (
    load_adaptive_runtime_continuation_settings,
)
from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_ERROR_CODES,
    AdaptiveScoreError,
    AdaptiveTransitionScheduleWarningV1,
)
from app.composition_plan_schemas import CompositionPlan, PlanDensity
from app.composition_schemas import (
    CompositionV2,
    CompositionV2Section,
    midi_pitch_number,
)
from app.services.adaptive_playback_runtime import (
    AdaptivePlaybackRegistry,
    get_default_playback_registry,
)
from app.services.adaptive_runtime_continuation import (
    WARNING_INVALID,
    WARNING_LATE,
    WARNING_MESSAGES,
    WARNING_METER,
    WARNING_MODEL_FAILED,
    WARNING_NOT_RUNNING,
    WARNING_STALE,
    WARNING_UNBOUNDED,
    continuation_audible,
    continuation_seed,
    count_pitch_class_repetitions,
    density_band_for_intensity,
    empty_runtime_context,
    harmony_summary,
    job_identity_matches,
    merge_warning_codes,
    pipeline_id_for_mode,
    plan_runtime_window,
    prefix_digest,
    prefix_window,
    result_applicable,
    update_runtime_context,
)
from app.services.adaptive_runtime_continuation_fallback import (
    ContinuationPlacementError,
    place_local_events,
    realize_fallback,
)
from app.services.adaptive_runtime_continuation_runtime import (
    AdaptiveRuntimeContinuationRegistry,
    ContinuationJob,
    HeldContinuation,
    get_default_continuation_registry,
)
from app.services.adaptive_score_store import get_score
from app.services.composition_motif_transform import extract_relative_motif
from app.services.composition_planner import ComposerFormPlan, ComposerFormSection
from app.services.composition_timeline import compile_timeline
from app.services.composition_timing import bar_duration_ticks
from app.services.project_store import ProjectNotFoundError, get_project
from app.services.symbolic_composition_generate import (
    SymbolicCompositionGenerateError,
    generate_symbolic_composition,
)

logger = logging.getLogger(__name__)

Scheduler = Callable[[Coroutine[Any, Any, None]], Any]
Clock = Callable[[], int]
Model = Callable[..., CompositionV2]

_model_override: Model | None = None


def install_continuation_model(model: Model | None) -> None:
    """Test seam. ``None`` restores ``generate_symbolic_composition``."""
    global _model_override
    _model_override = model


def reset_continuation_model() -> None:
    install_continuation_model(None)


def start_adaptive_runtime_continuation(
    project_id: str,
    score_id: str,
    request: AdaptiveRuntimeContinuationStartRequest,
    *,
    db_path: Path | None = None,
    registry: AdaptiveRuntimeContinuationRegistry | None = None,
    playback_registry: AdaptivePlaybackRegistry | None = None,
    now_ms: Clock | None = None,
) -> AdaptiveRuntimeContinuationV1:
    """Open a session. Does not schedule a model."""
    slots = registry or get_default_continuation_registry()
    loaded = _load(project_id, score_id, db_path=db_path, playback_registry=playback_registry)
    if loaded.score_revision != request.expected_document_revision:
        raise AdaptiveScoreError(
            "adaptive_score_conflict",
            ADAPTIVE_SCORE_ERROR_CODES["adaptive_score_conflict"],
            http_status=409,
        )
    previous = slots.get(project_id, score_id)
    if previous is not None:
        _cancel_job(previous.job)
        slots.clear(project_id, score_id)
    if loaded.playback is None or loaded.playback.transport != "playing":
        snapshot = _idle_snapshot(
            mode=request.mode,
            document_revision=loaded.score_revision,
            warnings=[WARNING_NOT_RUNNING],
            state_id="idle",
        )
        _log("start", project_id, score_id, snapshot)
        return snapshot
    held = _held_from_playback(
        project_id,
        loaded,
        mode=request.mode,
        now_ms=now_ms or _monotonic_ms,
        schedule_model=False,
        scheduler=None,
    )
    slots.put(project_id, score_id, held)
    _log("start", project_id, score_id, held.snapshot)
    return held.snapshot.model_copy(deep=True)


def get_adaptive_runtime_continuation(
    project_id: str,
    score_id: str,
    *,
    registry: AdaptiveRuntimeContinuationRegistry | None = None,
) -> AdaptiveRuntimeContinuationV1 | None:
    slots = registry or get_default_continuation_registry()
    held = slots.get(project_id, score_id)
    if held is None:
        return None
    return held.snapshot.model_copy(deep=True)


def get_adaptive_runtime_buffer(
    project_id: str,
    score_id: str,
    *,
    registry: AdaptiveRuntimeContinuationRegistry | None = None,
) -> AdaptiveRuntimeBufferV1 | None:
    slots = registry or get_default_continuation_registry()
    held = slots.get(project_id, score_id)
    if held is None or held.buffer is None:
        return None
    return held.buffer.model_copy(deep=True)


def maintain_adaptive_runtime_continuation(
    project_id: str,
    score_id: str,
    *,
    scheduler: Scheduler,
    db_path: Path | None = None,
    registry: AdaptiveRuntimeContinuationRegistry | None = None,
    playback_registry: AdaptivePlaybackRegistry | None = None,
    now_ms: Clock | None = None,
) -> AdaptiveRuntimeContinuationV1:
    """Publish a fallback and schedule the model unless the active job still matches."""
    slots = registry or get_default_continuation_registry()
    held = slots.get(project_id, score_id)
    if held is None:
        raise continuation_not_running_error()
    clock = now_ms or _monotonic_ms
    loaded = _load(project_id, score_id, db_path=db_path, playback_registry=playback_registry)
    if loaded.playback is None or loaded.playback.transport != "playing":
        snapshot = _replace_snapshot(
            held,
            warnings=merge_warning_codes(
                [item.code for item in held.snapshot.warnings],
                [WARNING_NOT_RUNNING],
            ),
            applicable=False,
            job_status="idle",
        )
        _log("maintain", project_id, score_id, snapshot)
        return snapshot
    view = _prepare(held.mode, loaded, previous=held.snapshot.context)
    job = held.job
    if job is not None and job_identity_matches(
        anchor_bar=view.window.anchor_bar,
        mode=held.mode,
        runtime_state_id=loaded.playback.runtime_state_id,
        document_revision=loaded.playback.document_revision,
        prefix_digest_value=view.digest,
        armed_anchor_bar=job.anchor_bar,
        armed_mode=job.mode,
        armed_state_id=job.runtime_state_id,
        armed_revision=job.document_revision,
        armed_prefix_digest=job.prefix_digest,
        job_status=held.snapshot.job_status,
    ):
        _log("maintain", project_id, score_id, held.snapshot)
        return held.snapshot.model_copy(deep=True)
    _cancel_job(job)
    refreshed = _arm(
        held,
        loaded,
        view,
        now_ms=clock,
    )
    slots.put(project_id, score_id, refreshed)
    if (
        refreshed.job is not None
        and view.window.job_status == "pending"
        and refreshed.job.plan is not None
    ):
        _schedule(refreshed.job, scheduler, clock)
    _log("maintain", project_id, score_id, refreshed.snapshot)
    return refreshed.snapshot.model_copy(deep=True)


def stop_adaptive_runtime_continuation(
    project_id: str,
    score_id: str,
    *,
    registry: AdaptiveRuntimeContinuationRegistry | None = None,
) -> None:
    slots = registry or get_default_continuation_registry()
    held = slots.clear(project_id, score_id)
    if held is None:
        return
    _cancel_job(held.job)
    _log("stop", project_id, score_id, held.snapshot)


class _Loaded:
    def __init__(self, playback, score, composition, timeline, score_revision: int) -> None:
        self.playback = playback
        self.score = score
        self.composition = composition
        self.timeline = timeline
        self.score_revision = score_revision


class _View:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def _load(
    project_id: str,
    score_id: str,
    *,
    db_path: Path | None,
    playback_registry: AdaptivePlaybackRegistry | None,
) -> _Loaded:
    try:
        record = get_score(project_id, score_id, db_path=db_path)
    except AdaptiveScoreError:
        raise
    try:
        project = get_project(project_id, db_path=db_path)
    except ProjectNotFoundError as exc:
        raise AdaptiveScoreError(
            "project_not_found",
            "Project id was not found",
            http_status=404,
            details={"project_id": project_id},
        ) from exc
    if not project.composition_json:
        raise AdaptiveScoreError(
            "composition_unavailable",
            ADAPTIVE_SCORE_ERROR_CODES["composition_unavailable"],
            http_status=422,
        )
    composition = CompositionV2.model_validate(json.loads(project.composition_json))
    timeline = compile_timeline(composition)
    slots = playback_registry or get_default_playback_registry()
    playback_held = slots.get(project_id, score_id)
    playback = None
    if playback_held is not None and playback_held.clock.snapshot is not None:
        playback = playback_held.clock.snapshot
    logger.debug(
        "Continuation inputs read",
        extra={
            "project_id": project_id,
            "score_id": score_id,
            "bar_count": composition.bar_count,
            "playing": playback is not None and playback.transport == "playing",
        },
    )
    return _Loaded(playback, record.score, composition, timeline, record.document_revision)


def _prepare(mode: str, loaded: _Loaded, *, previous) -> _View:
    playback = loaded.playback
    composition = loaded.composition
    timeline = loaded.timeline
    state = next(
        (item for item in loaded.score.states if item.id == playback.runtime_state_id),
        None,
    )
    span_start, span_end, unbounded = (None, None, False)
    if state is not None:
        span_start, span_end, unbounded = _material_bars(state.material, composition)
    clip_start = span_start if mode == "state_specific" else None
    clip_end = span_end if mode == "state_specific" else None
    recent_start, recent_end = prefix_window(
        anchor_bar=playback.bar,
        bar_count=composition.bar_count,
        span_start=clip_start,
        span_end=clip_end,
    )
    extra: list[str] = []
    if unbounded or (mode == "state_specific" and (span_start is None or span_end is None)):
        if state is not None and state.material.kind in {"motif", "asset"}:
            extra.append(WARNING_UNBOUNDED)
        elif unbounded:
            extra.append(WARNING_UNBOUNDED)
    prefix_start_tick = 0
    prefix_end_tick = 0
    if recent_start is not None and recent_end is not None:
        prefix_start_tick, prefix_end_tick = timeline.bar_range_ticks(recent_start, recent_end)
    tail, chord_count = harmony_summary(
        [
            (item.start_tick, item.duration_ticks, item.chord)
            for item in composition.harmony
        ],
        prefix_start_tick=prefix_start_tick,
        prefix_end_tick=prefix_end_tick,
    )
    classes = _bar_pitch_classes(composition, timeline, recent_start, recent_end)
    repetition = count_pitch_class_repetitions(classes)
    themes = _theme_ids(composition, prefix_start_tick, prefix_end_tick)
    context = update_runtime_context(
        previous,
        intensity=playback.intensity,
        theme_ids=themes,
        harmony_tail=tail,
        harmony_chord_count=chord_count,
        recent_start_bar=recent_start,
        recent_end_bar=recent_end,
        repetition_count=repetition,
    )
    loop = playback.instructions.loop
    target_guess = playback.bar + load_adaptive_runtime_continuation_settings().play_bars
    deadline = 0
    if 1 <= target_guess <= composition.bar_count:
        deadline = timeline.bar_start_tick(target_guess)
    window = plan_runtime_window(
        bar=playback.bar,
        bar_count=composition.bar_count,
        loop_start_bar=loop.start_bar,
        loop_end_bar=loop.end_bar,
        loop_enabled=loop.enabled,
        intensity=playback.intensity,
        state_id=playback.runtime_state_id,
        context=context,
        deadline_tick=deadline,
        extra_warnings=extra,
    )
    digest = prefix_digest(_prefix_rows(composition, prefix_start_tick, prefix_end_tick))
    notes, anchor_midi, track = _relative_cell(composition, context.theme_ids)
    generate_bars = load_adaptive_runtime_continuation_settings().generate_bars
    meter_changed = False
    ticks_per_bar = bar_duration_ticks(composition.time_signature, composition.ticks_per_quarter)
    if window.target_start_bar is not None and window.target_end_bar is not None:
        target_start = timeline.bar_start_tick(window.target_start_bar)
        target_end = timeline.bar_end_tick(window.target_end_bar)
        meter = timeline.active_time_signature(target_start)
        ticks_per_bar = bar_duration_ticks(meter, composition.ticks_per_quarter)
        meter_changed = any(target_start < tick < target_end for tick, _sig in timeline.time_signature_changes)
        if window.deadline_tick != target_start:
            window = plan_runtime_window(
                bar=playback.bar,
                bar_count=composition.bar_count,
                loop_start_bar=loop.start_bar,
                loop_end_bar=loop.end_bar,
                loop_enabled=loop.enabled,
                intensity=playback.intensity,
                state_id=playback.runtime_state_id,
                context=context,
                deadline_tick=target_start,
                extra_warnings=extra,
            )
    plan = None
    if window.target_start_bar is not None:
        plan = _symbolic_plan(composition, generate_bars, mode, playback.intensity)
    return _View(
        window=window,
        context=context,
        digest=digest,
        tail=context.harmony_tail,
        notes=notes,
        anchor_midi=anchor_midi,
        track=track,
        ticks_per_bar=ticks_per_bar,
        generate_bars=generate_bars,
        meter_changed=meter_changed,
        plan=plan,
        prefix_composition=_prefix_piece(
            composition, recent_start, recent_end, prefix_start_tick, prefix_end_tick
        ),
        loop_enabled=loop.enabled,
        loop_start=loop.start_bar,
        loop_end=loop.end_bar,
    )


def _arm(
    held: HeldContinuation,
    loaded: _Loaded,
    view: _View,
    *,
    now_ms: Clock,
) -> HeldContinuation:
    playback = loaded.playback
    window = view.window
    realized = None
    warnings = list(window.warnings)
    if view.meter_changed:
        warnings = merge_warning_codes(warnings, [WARNING_METER])
    events: tuple = ()
    if window.target_start_bar is not None and view.plan is not None:
        seed = continuation_seed(window.anchor_bar, held.mode, playback.runtime_state_id)
        realized = realize_fallback(
            window.fallback_order,
            loop_enabled=view.loop_enabled,
            loop_start_bar=view.loop_start,
            loop_end_bar=view.loop_end,
            plan=view.plan,
            seed=seed,
            relative_notes=view.notes,
            anchor_midi=view.anchor_midi,
            destination_track=view.track,
            composition=loaded.composition,
            job_id="pending",
            deadline_tick=window.deadline_tick,
            ticks_per_bar=view.ticks_per_bar,
            generate_bars=view.generate_bars,
            target_span_ticks=view.generate_bars * view.ticks_per_bar,
            meter_changed=view.meter_changed,
        )
        events = realized.events
        warnings = merge_warning_codes(warnings, list(realized.warnings))
        kind = realized.fallback_kind
    else:
        seed = 0
        kind = window.fallback_kind
    job_id = secrets.token_hex(4)
    job_token = f"arcj_{job_id}"
    if realized is not None:
        realized = realize_fallback(
            (kind,),
            loop_enabled=view.loop_enabled,
            loop_start_bar=view.loop_start,
            loop_end_bar=view.loop_end,
            plan=view.plan,
            seed=seed,
            relative_notes=view.notes,
            anchor_midi=view.anchor_midi,
            destination_track=view.track,
            composition=loaded.composition,
            job_id=job_token,
            deadline_tick=window.deadline_tick,
            ticks_per_bar=view.ticks_per_bar,
            generate_bars=view.generate_bars,
            target_span_ticks=view.generate_bars * view.ticks_per_bar,
            meter_changed=view.meter_changed,
        )
        events = realized.events
        kind = realized.fallback_kind
        warnings = merge_warning_codes(list(window.warnings), list(realized.warnings))
        if view.meter_changed:
            warnings = merge_warning_codes(warnings, [WARNING_METER])
    audible = continuation_audible(
        fallback_kind=kind,
        loop_enabled=view.loop_enabled,
        loop_start_bar=view.loop_start,
        loop_end_bar=view.loop_end,
        target_start_bar=window.target_start_bar,
    )
    prior = held.snapshot.telemetry
    telemetry = prior.model_copy(
        update={
            "arm_count": prior.arm_count + 1,
            "fallback_count": prior.fallback_count + (1 if window.target_start_bar is not None else 0),
        }
    )
    status = "pending" if window.target_start_bar is not None else "idle"
    snapshot = _snapshot(
        continuation_id=held.continuation_id,
        job_id=job_token if window.target_start_bar is not None else None,
        mode=held.mode,
        window=window,
        fallback_kind=kind,
        source="fallback" if window.target_start_bar is not None else "none",
        job_status=status if window.target_start_bar is not None else "idle",
        applicable=window.target_start_bar is not None,
        audible=audible,
        state_id=playback.runtime_state_id,
        intensity=playback.intensity,
        context=view.context,
        warnings=warnings,
        telemetry=telemetry,
        document_revision=held.session_revision,
    )
    buffer = None
    job = None
    if window.target_start_bar is not None:
        buffer = _buffer(
            continuation_id=held.continuation_id,
            job_id=job_token,
            source="fallback",
            fallback_kind=kind,
            events=events,
            harmony=_harmonic_labels(held.mode, view.tail, window.deadline_tick, view.ticks_per_bar, view.generate_bars),
        )
        job = ContinuationJob(
            job_id=job_token,
            anchor_bar=window.anchor_bar,
            mode=held.mode,
            runtime_state_id=playback.runtime_state_id,
            document_revision=playback.document_revision,
            prefix_digest=view.digest,
            harmony_tail=view.tail,
            intensity=playback.intensity,
            target_start_bar=window.target_start_bar,
            deadline_tick=window.deadline_tick,
            armed_monotonic_ms=now_ms(),
            fallback_kind=kind,
            pipeline_id=pipeline_id_for_mode(held.mode),
            seed=seed,
            ticks_per_bar=view.ticks_per_bar,
            generate_bars=view.generate_bars,
            meter_changed=view.meter_changed,
            plan=view.plan,
            prefix_composition=view.prefix_composition,
        )
    held.snapshot = snapshot
    held.buffer = buffer
    held.job = job
    held.active_job_id = None if job is None else job.job_id
    logger.debug(
        "Continuation armed",
        extra={
            "anchor_bar": snapshot.anchor_bar,
            "target_start_bar": snapshot.target_start_bar,
            "deadline_tick": snapshot.deadline_tick,
            "repetition_count": view.context.repetition_count,
            "theme_count": len(view.context.theme_ids),
            "harmony_chord_count": view.context.harmony_chord_count,
            "fallback_kind": snapshot.fallback_kind,
            "pipeline_id": pipeline_id_for_mode(held.mode),
        },
    )
    return held


def _schedule(job: ContinuationJob, scheduler: Scheduler, now_ms: Clock) -> None:
    coroutine = _model_coroutine(job, now_ms)
    job.coroutine = coroutine
    job.handle = scheduler(coroutine)


def _model_coroutine(job: ContinuationJob, now_ms: Clock):
    async def _run() -> None:
        model = _model_override or _default_model
        try:
            music = await asyncio.to_thread(
                model,
                job.plan,
                prefix_composition=job.prefix_composition,
                seed=job.seed,
            )
        except SymbolicCompositionGenerateError:
            _mark_failed(job.job_id, WARNING_MODEL_FAILED)
            return
        except Exception:
            logger.error(
                "Adaptive runtime continuation failed",
                extra={"code": WARNING_MODEL_FAILED},
            )
            _mark_failed(job.job_id, WARNING_MODEL_FAILED)
            return
        _apply_model(job, music, now_ms)

    return _run()


def _apply_model(job: ContinuationJob, music: CompositionV2, now_ms: Clock) -> None:
    located = _locate_active_job(job.job_id)
    if located is None:
        return
    project_id, score_id, held = located
    if held.active_job_id != job.job_id:
        logger.debug(
            "Continuation result ignored for an older job",
            extra={"code": WARNING_STALE, "job_id": job.job_id},
        )
        return
    try:
        loaded = _load(project_id, score_id, db_path=None, playback_registry=None)
    except AdaptiveScoreError as exc:
        logger.error("Adaptive runtime continuation failed", extra={"code": exc.code})
        _mark_failed(job.job_id, WARNING_MODEL_FAILED)
        return
    playback = loaded.playback
    if playback is None:
        _discard(held, WARNING_STALE, late=True)
        _log("discard", project_id, score_id, held.snapshot)
        return
    view = _prepare(held.mode, loaded, previous=held.snapshot.context)
    settings = load_adaptive_runtime_continuation_settings()
    elapsed = now_ms() - job.armed_monotonic_ms
    musical = result_applicable(
        playback_bar=playback.bar,
        position_tick=playback.position_tick,
        target_start_bar=job.target_start_bar,
        deadline_tick=job.deadline_tick,
        armed_state_id=job.runtime_state_id,
        playback_state_id=playback.runtime_state_id,
        armed_revision=job.document_revision,
        playback_revision=playback.document_revision,
        armed_intensity=job.intensity,
        playback_intensity=playback.intensity,
        armed_harmony_tail=job.harmony_tail,
        playback_harmony_tail=view.tail,
        armed_prefix_digest=job.prefix_digest,
        playback_prefix_digest=view.digest,
    )
    if elapsed >= settings.deadline_ms or musical.warning_code == WARNING_LATE:
        _discard(held, WARNING_LATE, late=True)
        _log("discard", project_id, score_id, held.snapshot)
        return
    if not musical.applicable:
        _discard(held, musical.warning_code or WARNING_STALE, late=True)
        _log("discard", project_id, score_id, held.snapshot)
        return
    prefix_end = 0
    if music.bar_count > job.generate_bars:
        prefix_end = (music.bar_count - job.generate_bars) * job.ticks_per_bar
    try:
        placed = place_local_events(
            music,
            deadline_tick=job.deadline_tick,
            ticks_per_bar=job.ticks_per_bar,
            generate_bars=job.generate_bars,
            prefix_end_tick=prefix_end,
            meter_changed=job.meter_changed,
        )
    except ContinuationPlacementError:
        _discard(held, WARNING_INVALID, late=False)
        _log("discard", project_id, score_id, held.snapshot)
        return
    if held.active_job_id != job.job_id:
        return
    audible = continuation_audible(
        fallback_kind=held.snapshot.fallback_kind,
        loop_enabled=playback.instructions.loop.enabled,
        loop_start_bar=playback.instructions.loop.start_bar,
        loop_end_bar=playback.instructions.loop.end_bar,
        target_start_bar=job.target_start_bar,
    )
    warnings = merge_warning_codes(
        [item.code for item in held.snapshot.warnings],
        list(placed.warnings),
    )
    telemetry = held.snapshot.telemetry.model_copy(
        update={"model_apply_count": held.snapshot.telemetry.model_apply_count + 1}
    )
    held.snapshot = held.snapshot.model_copy(
        update={
            "source": "model",
            "job_status": "applied",
            "applicable": True,
            "audible": audible,
            "warnings": _warning_models(warnings),
            "telemetry": telemetry,
        }
    )
    held.buffer = _buffer(
        continuation_id=held.continuation_id,
        job_id=job.job_id,
        source="model",
        fallback_kind=held.snapshot.fallback_kind,
        events=placed.events,
        harmony=_harmonic_labels(
            held.mode,
            job.harmony_tail,
            job.deadline_tick,
            job.ticks_per_bar,
            job.generate_bars,
        ),
    )
    _log("apply", project_id, score_id, held.snapshot)


def _locate_active_job(job_id: str) -> tuple[str, str, HeldContinuation] | None:
    registry = get_default_continuation_registry()
    for (project_id, score_id), held in list(registry._slots.items()):
        if held.active_job_id == job_id:
            return project_id, score_id, held
    return None


def _mark_failed(job_id: str, code: str) -> None:
    registry = get_default_continuation_registry()
    for (_project_id, score_id), held in list(registry._slots.items()):
        if held.active_job_id != job_id:
            continue
        project_id = _project_id
        _discard(held, code, late=False, failed=code == WARNING_MODEL_FAILED)
        logger.error("Adaptive runtime continuation failed", extra={"code": code})
        _log("discard", project_id, score_id, held.snapshot)
        return


def _discard(held: HeldContinuation, code: str, *, late: bool, failed: bool = False) -> None:
    prior = held.snapshot.telemetry
    telemetry = prior.model_copy(
        update={
            "late_discard_count": prior.late_discard_count + (1 if late else 0),
            "failure_count": prior.failure_count + (0 if late else 1),
        }
    )
    warnings = merge_warning_codes([item.code for item in held.snapshot.warnings], [code])
    held.snapshot = held.snapshot.model_copy(
        update={
            "source": "fallback",
            "job_status": "failed" if failed else "discarded",
            "applicable": False,
            "warnings": _warning_models(warnings),
            "telemetry": telemetry,
        }
    )


def _replace_snapshot(held: HeldContinuation, *, warnings: list[str], applicable: bool, job_status: str):
    held.snapshot = held.snapshot.model_copy(
        update={
            "warnings": _warning_models(warnings),
            "applicable": applicable,
            "job_status": job_status,
        }
    )
    return held.snapshot.model_copy(deep=True)


def _held_from_playback(project_id: str, loaded: _Loaded, *, mode: str, now_ms: Clock, schedule_model: bool, scheduler):
    del project_id, now_ms, schedule_model, scheduler
    playback = loaded.playback
    view = _prepare(mode, loaded, previous=empty_runtime_context())
    snapshot = _snapshot(
        continuation_id="arcn_" + secrets.token_hex(4),
        job_id=None,
        mode=mode,
        window=view.window,
        fallback_kind=view.window.fallback_kind,
        source="none",
        job_status="idle",
        applicable=False,
        audible=False,
        state_id=playback.runtime_state_id,
        intensity=playback.intensity,
        context=view.context,
        warnings=list(view.window.warnings),
        telemetry=AdaptiveRuntimeContinuationTelemetryV1(),
        document_revision=loaded.score_revision,
    )
    return HeldContinuation(
        continuation_id=snapshot.continuation_id,
        mode=mode,
        snapshot=snapshot,
        session_revision=loaded.score_revision,
    )


def _idle_snapshot(*, mode: str, document_revision: int, warnings: list[str], state_id: str):
    context = empty_runtime_context()
    return AdaptiveRuntimeContinuationV1(
        continuation_id="arcn_" + secrets.token_hex(4),
        job_id=None,
        mode=mode,
        anchor_bar=1,
        reserved_start_bar=1,
        reserved_end_bar=1,
        target_start_bar=None,
        target_end_bar=None,
        deadline_tick=0,
        fallback_kind="reuse_loop",
        source="none",
        job_status="idle",
        applicable=False,
        audible=False,
        runtime_state_id=state_id,
        intensity=0,
        context=context,
        warnings=_warning_models(warnings),
        telemetry=AdaptiveRuntimeContinuationTelemetryV1(),
        document_revision=document_revision,
    )


def _snapshot(**kwargs: Any) -> AdaptiveRuntimeContinuationV1:
    window = kwargs["window"]
    return AdaptiveRuntimeContinuationV1(
        continuation_id=kwargs["continuation_id"],
        job_id=kwargs["job_id"],
        mode=kwargs["mode"],
        anchor_bar=window.anchor_bar,
        reserved_start_bar=window.reserved_start_bar,
        reserved_end_bar=window.reserved_end_bar,
        target_start_bar=window.target_start_bar,
        target_end_bar=window.target_end_bar,
        deadline_tick=window.deadline_tick,
        fallback_kind=kwargs["fallback_kind"],
        source=kwargs["source"],
        job_status=kwargs["job_status"],
        applicable=kwargs["applicable"],
        audible=kwargs["audible"],
        runtime_state_id=kwargs["state_id"],
        intensity=kwargs["intensity"],
        context=kwargs["context"],
        warnings=_warning_models(kwargs["warnings"]),
        telemetry=kwargs["telemetry"],
        document_revision=kwargs["document_revision"],
    )


def _buffer(**kwargs: Any) -> AdaptiveRuntimeBufferV1:
    return AdaptiveRuntimeBufferV1(
        continuation_id=kwargs["continuation_id"],
        job_id=kwargs["job_id"],
        source=kwargs["source"],
        fallback_kind=kwargs["fallback_kind"],
        events=list(kwargs["events"]),
        harmony=list(kwargs["harmony"]),
    )


def _harmonic_labels(mode: str, tail: str | None, deadline_tick: int, ticks_per_bar: int, generate_bars: int):
    if mode != "harmonic_continuation" or not tail:
        return []
    labels = []
    for index in range(min(8, generate_bars)):
        labels.append(
            AdaptiveRuntimeBufferHarmonyV1(
                start_tick=deadline_tick + index * ticks_per_bar,
                duration_ticks=ticks_per_bar,
                chord=tail,
            )
        )
    return labels


def _warning_models(codes: list[str]) -> list[AdaptiveTransitionScheduleWarningV1]:
    return [
        AdaptiveTransitionScheduleWarningV1(
            code=code,
            message=WARNING_MESSAGES.get(code, "")[:200],
        )
        for code in merge_warning_codes(codes)
    ]


def _material_bars(material, composition: CompositionV2):
    if material.kind in {"motif", "asset"}:
        return None, None, True
    if material.kind == "section":
        section = next(
            (item for item in composition.sections if item.id == material.section_id),
            None,
        )
        if section is None:
            return None, None, True
        return section.start_bar, section.start_bar + section.bar_count - 1, False
    if material.kind in {"bar_range", "track_range", "revision_region"}:
        if material.start_bar is not None and material.end_bar is not None:
            return material.start_bar, material.end_bar, False
        return None, None, True
    return None, None, False


def _bar_pitch_classes(composition, timeline, start_bar, end_bar):
    if start_bar is None or end_bar is None:
        return []
    rows = []
    for bar in range(start_bar, end_bar + 1):
        bar_start, bar_end = timeline.bar_range_ticks(bar, bar)
        pitches = []
        for track in composition.tracks:
            for event in track.events:
                if bar_start <= event.start_tick < bar_end:
                    pitches.append((event.start_tick, midi_pitch_number(event.pitch) % 12))
        pitches.sort()
        rows.append(tuple(pitch for _onset, pitch in pitches))
    return rows


def _prefix_rows(composition, start_tick: int, end_tick: int):
    rows = []
    for track in composition.tracks:
        for event in track.events:
            if start_tick <= event.start_tick < end_tick:
                rows.append(
                    (event.start_tick, midi_pitch_number(event.pitch), event.duration_ticks)
                )
    return rows


def _theme_ids(composition, start_tick: int, end_tick: int) -> list[str]:
    found = []
    indexed = {
        event.id: event
        for track in composition.tracks
        for event in track.events
        if event.id
    }
    for motif in composition.motifs:
        for occurrence in motif.occurrences:
            for event_id in occurrence.event_ids:
                event = indexed.get(event_id)
                if event is None:
                    continue
                if start_tick <= event.start_tick < end_tick:
                    found.append(motif.id)
                    break
            else:
                continue
            break
    return found


def _relative_cell(composition, theme_ids: list[str]):
    by_id = {motif.id: motif for motif in composition.motifs}
    for theme_id in theme_ids:
        motif = by_id.get(theme_id)
        if motif is None:
            continue
        occurrence = next(
            (item for item in motif.occurrences if item.relationship == "original"),
            motif.occurrences[0],
        )
        track = next((item for item in composition.tracks if item.id == occurrence.track_id), None)
        if track is None:
            continue
        try:
            extracted = extract_relative_motif(
                composition,
                track_id=occurrence.track_id,
                event_ids=occurrence.event_ids,
            )
        except Exception:
            logger.debug("Continuation motif cell skipped", extra={"code": WARNING_INVALID})
            continue
        return extracted.notes, extracted.anchor_midi, track
    return None, 60, None


def _prefix_piece(composition, start_bar, end_bar, start_tick: int, end_tick: int):
    if start_bar is None or end_bar is None or end_tick <= start_tick:
        return None
    bar_count = end_bar - start_bar + 1
    duration = end_tick - start_tick
    tracks = []
    for track in composition.tracks:
        kept = []
        for event in track.events:
            if start_tick <= event.start_tick < end_tick:
                kept.append(event.model_copy(update={"start_tick": event.start_tick - start_tick}))
        tracks.append(track.model_copy(update={"events": kept}))
    if not tracks:
        return None
    try:
        return CompositionV2(
            tempo=composition.tempo,
            key=composition.key,
            time_signature=composition.time_signature,
            ticks_per_quarter=composition.ticks_per_quarter,
            duration_ticks=duration,
            bar_count=bar_count,
            sections=[
                CompositionV2Section(
                    type="verse",
                    start_bar=1,
                    bar_count=bar_count,
                    start_tick=0,
                    duration_ticks=duration,
                )
            ],
            tracks=tracks,
        )
    except Exception:
        logger.debug("Continuation prefix slice skipped", extra={"code": WARNING_INVALID})
        return None


def _symbolic_plan(composition, generate_bars: int, mode: str, intensity: float) -> CompositionPlan:
    band = density_band_for_intensity(intensity) if mode == "intensity_adaptation" else "moderate"
    labels: list[str] = []
    seen: set[str] = set()
    for track in composition.tracks:
        label = track.instrument.strip()
        key = label.casefold()
        if label and key not in seen:
            seen.add(key)
            labels.append(label)
    if not labels:
        labels = ["piano"]
    return CompositionPlan(
        form=ComposerFormPlan(
            tempo=composition.tempo,
            key=composition.key,
            time_signature=composition.time_signature,
            bar_count=generate_bars,
            sections=[ComposerFormSection(type="verse", start_bar=1, bar_count=generate_bars)],
            instrumentation=labels[:6],
        ),
        density=PlanDensity(global_band=band),  # type: ignore[arg-type]
    )


def _default_model(plan, *, prefix_composition, seed):
    result = generate_symbolic_composition(
        plan,
        prefix_composition=prefix_composition,
        seed=seed,
    )
    return result.composition


def _cancel_job(job: ContinuationJob | None) -> None:
    if job is None:
        return
    handle = job.handle
    coroutine = job.coroutine
    if handle is not None and handle is not coroutine and hasattr(handle, "cancel"):
        handle.cancel()
        return
    if inspect.iscoroutine(coroutine):
        coroutine.close()


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def _log(action: str, project_id: str, score_id: str, snapshot: AdaptiveRuntimeContinuationV1) -> None:
    logger.info(
        "Adaptive runtime continuation",
        extra={
            "adaptive_runtime_continuation": True,
            "action": action,
            "project_id": project_id,
            "score_id": score_id,
            "continuation_id": snapshot.continuation_id,
            "job_id": snapshot.job_id,
            "mode": snapshot.mode,
            "anchor_bar": snapshot.anchor_bar,
            "target_start_bar": snapshot.target_start_bar,
            "fallback_kind": snapshot.fallback_kind,
            "source": snapshot.source,
            "job_status": snapshot.job_status,
            "audible": snapshot.audible,
            "warning_codes": [item.code for item in snapshot.warnings],
            "arm_count": snapshot.telemetry.arm_count,
            "model_apply_count": snapshot.telemetry.model_apply_count,
            "late_discard_count": snapshot.telemetry.late_discard_count,
            "failure_count": snapshot.telemetry.failure_count,
            "fallback_count": snapshot.telemetry.fallback_count,
        },
    )
    logger.debug(
        "Continuation counters",
        extra={
            "action": action,
            "deadline_tick": snapshot.deadline_tick,
            "repetition_count": snapshot.context.repetition_count,
            "theme_count": len(snapshot.context.theme_ids),
            "harmony_chord_count": snapshot.context.harmony_chord_count,
        },
    )
