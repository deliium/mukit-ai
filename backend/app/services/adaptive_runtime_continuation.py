"""Pure window, memory, and applicability for adaptive runtime continuation.

This module does not read a playback clock, SQLite, FastAPI, the symbolic
composer, or the composition validator. Tick and bar integers arrive already
resolved.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from app.adaptive_runtime_continuation_schemas import AdaptiveRuntimeContextV1
from app.adaptive_runtime_continuation_settings import (
    load_adaptive_runtime_continuation_settings,
)

logger = logging.getLogger(__name__)

FALLBACK_REUSE_LOOP = "reuse_loop"
FALLBACK_MOTIF = "motif_variation"
FALLBACK_ACCOMPANIMENT = "accompaniment"

WARNING_WINDOW_PAST_END = "continuation_window_past_end"
WARNING_OUTSIDE_LOOP = "continuation_outside_loop"
WARNING_LATE = "continuation_late"
WARNING_STALE = "continuation_stale"
WARNING_INVALID = "continuation_invalid"
WARNING_MODEL_FAILED = "continuation_model_failed"
WARNING_TRUNCATED = "continuation_truncated"
WARNING_UNBOUNDED = "continuation_material_unbounded"
WARNING_METER = "continuation_meter_assumed_constant"
WARNING_NOT_RUNNING = "playback_not_running"
WARNING_VIRTUAL_TIMELINE = "continuation_virtual_timeline"
WARNING_GUARD_DUPLICATE = "continuation_guard_duplicate"

WARNING_MESSAGES: dict[str, str] = {
    WARNING_NOT_RUNNING: "Adaptive playback is not running.",
    WARNING_WINDOW_PAST_END: "The target window starts after the last bar.",
    WARNING_LATE: "The symbolic result arrived after the deadline.",
    WARNING_STALE: "The symbolic result no longer matches playback.",
    WARNING_INVALID: "The continuation material failed validation.",
    WARNING_MODEL_FAILED: "The symbolic continuation model failed.",
    WARNING_TRUNCATED: "The continuation buffer dropped events above the cap.",
    WARNING_OUTSIDE_LOOP: "The target window sits outside the published loop.",
    WARNING_UNBOUNDED: "The state material has no bar span.",
    WARNING_METER: "The target window keeps the meter at the deadline tick.",
    WARNING_VIRTUAL_TIMELINE: "Generating past the stored composition bar count.",
    WARNING_GUARD_DUPLICATE: "Model buffer matched the last applied digest.",
}


@dataclass(frozen=True)
class RuntimeWindowPlan:
    """Reserved and target bars for one anchor. Not a playable score."""

    anchor_bar: int
    reserved_start_bar: int
    reserved_end_bar: int
    target_start_bar: int | None
    target_end_bar: int | None
    deadline_tick: int
    fallback_order: tuple[str, str, str]
    fallback_kind: str
    job_status: str
    applicable: bool
    audible: bool
    warnings: tuple[str, ...]
    intensity: float
    runtime_state_id: str
    repetition_count: int


@dataclass(frozen=True)
class Applicability:
    """Whether a finished model result may replace the fallback buffer."""

    applicable: bool
    warning_code: str | None


def empty_runtime_context() -> AdaptiveRuntimeContextV1:
    return AdaptiveRuntimeContextV1()


def continuation_seed(anchor_bar: int, mode: str, state_id: str) -> int:
    """Deterministic fake/model seed. Does not call ``random``."""
    mode_code = sum(ord(char) for char in mode)
    state_code = sum(ord(char) for char in state_id)
    return (anchor_bar * 10007 + mode_code * 97 + state_code) & 0x7FFFFFFF


def density_band_for_intensity(intensity: float) -> str:
    if intensity < 0.34:
        return "sparse"
    if intensity < 0.67:
        return "moderate"
    return "dense"


def pipeline_id_for_mode(mode: str) -> str:
    if mode == "variation":
        return "symbolic_variation"
    return "symbolic_continuation"


def fallback_order(repetition_count: int) -> tuple[str, str, str]:
    if repetition_count >= 3:
        return (FALLBACK_MOTIF, FALLBACK_ACCOMPANIMENT, FALLBACK_REUSE_LOOP)
    return (FALLBACK_REUSE_LOOP, FALLBACK_MOTIF, FALLBACK_ACCOMPANIMENT)


def prefix_window(
    *,
    anchor_bar: int,
    bar_count: int,
    prefix_bars: int | None = None,
    span_start: int | None = None,
    span_end: int | None = None,
) -> tuple[int | None, int | None]:
    """Recent bars ending at the anchor, clipped to the composition or a material span."""
    if bar_count < 1 or anchor_bar < 1:
        return None, None
    length = prefix_bars
    if length is None:
        length = load_adaptive_runtime_continuation_settings().prefix_bars
    end = min(anchor_bar, bar_count)
    start = max(1, end - length + 1)
    if span_start is not None:
        start = max(start, span_start)
    if span_end is not None:
        end = min(end, span_end)
    if end < start:
        return None, None
    return start, end


def count_pitch_class_repetitions(bars: list[tuple[int, ...]]) -> int:
    """How many earlier pairs match the last two bars. Clamped to 0..8."""
    if len(bars) < 2:
        return 0
    target = (bars[-2], bars[-1])
    count = 0
    for index in range(len(bars) - 2):
        if (bars[index], bars[index + 1]) == target:
            count += 1
    return min(8, count)


def harmony_summary(
    spans: list[tuple[int, int, str]],
    *,
    prefix_start_tick: int,
    prefix_end_tick: int,
) -> tuple[str | None, int]:
    """Return the chord active at the prefix end and the overlapping chord count.

    ``spans`` are ``(start_tick, duration_ticks, chord)``. The chord string is
    not logged.
    """
    overlapping: list[str] = []
    tail: str | None = None
    end_tick = prefix_end_tick
    for start, duration, chord in spans:
        stop = start + duration
        overlaps_prefix = start < prefix_end_tick and stop > prefix_start_tick
        if overlaps_prefix:
            overlapping.append(chord)
        overlaps_end = start < end_tick and stop > end_tick - 1 if end_tick > 0 else False
        if overlaps_end:
            tail = chord
    count = min(32, len(overlapping))
    if tail is not None:
        cleaned = tail.strip()
        tail = cleaned[:32] if cleaned else None
    return tail, count


def prefix_digest(events: list[tuple[int, int, int]]) -> str:
    """First 16 hex characters of sha256 over onset, MIDI pitch, and duration.

    Callers must not log the return value.
    """
    payload = "|".join(f"{onset},{pitch},{duration}" for onset, pitch, duration in events)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def merge_warning_codes(*groups: list[str] | tuple[str, ...], limit: int = 8) -> list[str]:
    """First-seen warning codes, capped."""
    merged: list[str] = []
    for group in groups:
        for code in group:
            if code in merged:
                continue
            if len(merged) >= limit:
                return merged
            merged.append(code)
    return merged


def update_runtime_context(
    previous: AdaptiveRuntimeContextV1 | None,
    *,
    intensity: float,
    theme_ids: list[str],
    harmony_tail: str | None,
    harmony_chord_count: int,
    recent_start_bar: int | None,
    recent_end_bar: int | None,
    repetition_count: int,
) -> AdaptiveRuntimeContextV1:
    """Append intensity and replace the musical memory fields for this prefix."""
    prior = previous.energy if previous is not None else []
    energy = [*prior, max(0.0, min(1.0, float(intensity)))]
    if len(energy) > 8:
        energy = energy[-8:]
    themes = sorted({item.strip() for item in theme_ids if item.strip()})[:16]
    tail = None if harmony_tail is None else harmony_tail.strip()[:32] or None
    logger.debug(
        "Updated adaptive runtime memory",
        extra={
            "theme_count": len(themes),
            "harmony_chord_count": min(32, max(0, harmony_chord_count)),
            "repetition_count": min(8, max(0, repetition_count)),
            "energy_count": len(energy),
            "recent_start_bar": recent_start_bar,
            "recent_end_bar": recent_end_bar,
        },
    )
    return AdaptiveRuntimeContextV1(
        theme_ids=themes,
        harmony_tail=tail,
        harmony_chord_count=min(32, max(0, harmony_chord_count)),
        recent_start_bar=recent_start_bar,
        recent_end_bar=recent_end_bar,
        repetition_count=min(8, max(0, repetition_count)),
        energy=energy,
    )


def virtual_bar_deadline_tick(
    *,
    target_start_bar: int,
    bar_count: int,
    last_compiled_bar_end_tick: int,
    ticks_per_bar_assumed: int,
) -> int:
    """Constant-tempo deadline for a target that starts past stored ``bar_count``.

    Does not call ``bar_start_tick``. ``last_compiled_bar_end_tick`` is the end
    of bar ``bar_count`` (start of the first virtual bar).
    """
    if target_start_bar <= bar_count or bar_count < 1 or ticks_per_bar_assumed < 1:
        return max(0, last_compiled_bar_end_tick)
    offset = target_start_bar - bar_count - 1
    return max(0, last_compiled_bar_end_tick + offset * ticks_per_bar_assumed)


def plan_runtime_window(
    *,
    bar: int,
    bar_count: int,
    loop_start_bar: int | None,
    loop_end_bar: int | None,
    loop_enabled: bool,
    intensity: float,
    state_id: str,
    context: AdaptiveRuntimeContextV1,
    deadline_tick: int,
    play_bars: int | None = None,
    generate_bars: int | None = None,
    extra_warnings: list[str] | None = None,
    continuous: bool = False,
    virtual_bar: int | None = None,
    playback_at_end: bool = False,
    loop_wrapping_at_end: bool = False,
    last_compiled_bar_end_tick: int | None = None,
    ticks_per_bar_assumed: int | None = None,
) -> RuntimeWindowPlan:
    """Map playback bar ``N`` to reserved / target windows.

    When ``continuous`` is false, past-end targets stay idle with
    ``continuation_window_past_end``. When continuous and the legacy planner
    would idle — or playback is at end / looping at end — the anchor becomes
    ``virtual_bar`` (default ``bar_count + 1``) and the target may extend past
    stored ``bar_count``. ``playback.bar`` is never required to exceed
    ``bar_count``.
    """
    settings = load_adaptive_runtime_continuation_settings()
    reserved_length = play_bars if play_bars is not None else settings.play_bars
    target_length = generate_bars if generate_bars is not None else settings.generate_bars
    anchor = max(1, bar)
    order = fallback_order(context.repetition_count)
    warnings = list(extra_warnings or [])

    if bar_count < 1:
        if continuous:
            return plan_continuous_window(
                bar_count=1,
                virtual_bar=max(1, virtual_bar or 1),
                intensity=intensity,
                state_id=state_id,
                context=context,
                play_bars=reserved_length,
                generate_bars=target_length,
                last_compiled_bar_end_tick=max(0, last_compiled_bar_end_tick or 0),
                ticks_per_bar_assumed=max(1, ticks_per_bar_assumed or 1),
                extra_warnings=warnings,
            )
        plan = RuntimeWindowPlan(
            anchor_bar=anchor,
            reserved_start_bar=1,
            reserved_end_bar=1,
            target_start_bar=None,
            target_end_bar=None,
            deadline_tick=max(0, deadline_tick),
            fallback_order=order,
            fallback_kind=FALLBACK_REUSE_LOOP,
            job_status="idle",
            applicable=False,
            audible=False,
            warnings=tuple(
                merge_warning_codes(warnings, [WARNING_WINDOW_PAST_END])
            ),
            intensity=intensity,
            runtime_state_id=state_id,
            repetition_count=context.repetition_count,
        )
        _log_window(plan, len(context.theme_ids), continuous=False, virtual_bar=None)
        return plan

    reserved_start = min(anchor, bar_count)
    reserved_end = min(bar_count, reserved_start + reserved_length - 1)
    target_start = anchor + reserved_length
    legacy_idle = target_start > bar_count
    use_virtual = continuous and (
        legacy_idle or playback_at_end or loop_wrapping_at_end
    )
    if use_virtual:
        resolved_virtual = virtual_bar if virtual_bar is not None else bar_count + 1
        end_tick = (
            last_compiled_bar_end_tick
            if last_compiled_bar_end_tick is not None
            else max(0, deadline_tick)
        )
        tpb = ticks_per_bar_assumed if ticks_per_bar_assumed is not None else 1
        return plan_continuous_window(
            bar_count=bar_count,
            virtual_bar=max(1, resolved_virtual),
            intensity=intensity,
            state_id=state_id,
            context=context,
            play_bars=reserved_length,
            generate_bars=target_length,
            last_compiled_bar_end_tick=max(0, end_tick),
            ticks_per_bar_assumed=max(1, tpb),
            extra_warnings=warnings,
        )

    if legacy_idle:
        plan = RuntimeWindowPlan(
            anchor_bar=anchor,
            reserved_start_bar=reserved_start,
            reserved_end_bar=max(reserved_start, reserved_end),
            target_start_bar=None,
            target_end_bar=None,
            deadline_tick=max(0, deadline_tick),
            fallback_order=order,
            fallback_kind=FALLBACK_REUSE_LOOP,
            job_status="idle",
            applicable=False,
            audible=False,
            warnings=tuple(
                merge_warning_codes(warnings, [WARNING_WINDOW_PAST_END])
            ),
            intensity=intensity,
            runtime_state_id=state_id,
            repetition_count=context.repetition_count,
        )
        _log_window(plan, len(context.theme_ids), continuous=False, virtual_bar=None)
        return plan

    target_end = min(bar_count, target_start + target_length - 1)
    outside = _target_outside_loop(
        loop_enabled=loop_enabled,
        loop_start_bar=loop_start_bar,
        loop_end_bar=loop_end_bar,
        target_start_bar=target_start,
    )
    if outside:
        warnings = merge_warning_codes(warnings, [WARNING_OUTSIDE_LOOP])
    kind = order[0]
    audible = _audible(
        fallback_kind=kind,
        outside_loop=outside,
        target_start_bar=target_start,
    )
    plan = RuntimeWindowPlan(
        anchor_bar=anchor,
        reserved_start_bar=reserved_start,
        reserved_end_bar=reserved_end,
        target_start_bar=target_start,
        target_end_bar=target_end,
        deadline_tick=max(0, deadline_tick),
        fallback_order=order,
        fallback_kind=kind,
        job_status="pending",
        applicable=True,
        audible=audible,
        warnings=tuple(warnings),
        intensity=intensity,
        runtime_state_id=state_id,
        repetition_count=context.repetition_count,
    )
    _log_window(plan, len(context.theme_ids), continuous=False, virtual_bar=None)
    return plan


def plan_continuous_window(
    *,
    bar_count: int,
    virtual_bar: int,
    intensity: float,
    state_id: str,
    context: AdaptiveRuntimeContextV1,
    last_compiled_bar_end_tick: int,
    ticks_per_bar_assumed: int,
    play_bars: int | None = None,
    generate_bars: int | None = None,
    extra_warnings: list[str] | None = None,
) -> RuntimeWindowPlan:
    """Non-idle virtual timeline past stored ``bar_count``. Pure integers only."""
    settings = load_adaptive_runtime_continuation_settings()
    reserved_length = play_bars if play_bars is not None else settings.play_bars
    target_length = generate_bars if generate_bars is not None else settings.generate_bars
    anchor = max(1, virtual_bar)
    order = fallback_order(context.repetition_count)
    warnings = merge_warning_codes(
        list(extra_warnings or []),
        [WARNING_VIRTUAL_TIMELINE, WARNING_METER],
    )
    reserved_start = anchor
    reserved_end = reserved_start + reserved_length - 1
    target_start = anchor + reserved_length
    target_end = target_start + target_length - 1
    deadline = virtual_bar_deadline_tick(
        target_start_bar=target_start,
        bar_count=max(1, bar_count),
        last_compiled_bar_end_tick=last_compiled_bar_end_tick,
        ticks_per_bar_assumed=ticks_per_bar_assumed,
    )
    kind = order[0]
    # Virtual targets are outside the authored loop by definition; continuous
    # still schedules accompaniment / motif buffers as audible.
    audible = kind != FALLBACK_REUSE_LOOP
    plan = RuntimeWindowPlan(
        anchor_bar=anchor,
        reserved_start_bar=reserved_start,
        reserved_end_bar=reserved_end,
        target_start_bar=target_start,
        target_end_bar=target_end,
        deadline_tick=deadline,
        fallback_order=order,
        fallback_kind=kind,
        job_status="pending",
        applicable=True,
        audible=audible,
        warnings=tuple(warnings),
        intensity=intensity,
        runtime_state_id=state_id,
        repetition_count=context.repetition_count,
    )
    _log_window(plan, len(context.theme_ids), continuous=True, virtual_bar=anchor)
    return plan


def result_applicable(
    *,
    playback_bar: int,
    position_tick: int,
    target_start_bar: int | None,
    deadline_tick: int,
    armed_state_id: str,
    playback_state_id: str,
    armed_revision: int,
    playback_revision: int,
    armed_intensity: float,
    playback_intensity: float,
    armed_harmony_tail: str | None,
    playback_harmony_tail: str | None,
    armed_prefix_digest: str,
    playback_prefix_digest: str,
    intensity_epsilon: float | None = None,
) -> Applicability:
    """Musical applicability. Wall-clock lateness is checked by the service."""
    epsilon = intensity_epsilon
    if epsilon is None:
        epsilon = load_adaptive_runtime_continuation_settings().intensity_epsilon
    if target_start_bar is None:
        return Applicability(False, WARNING_WINDOW_PAST_END)
    if playback_bar >= target_start_bar or position_tick >= deadline_tick:
        logger.debug(
            "Continuation result missed the tick deadline",
            extra={
                "anchor_bar": playback_bar,
                "target_start_bar": target_start_bar,
                "fallback_kind": None,
                "repetition_count": None,
                "theme_count": None,
                "code": WARNING_LATE,
            },
        )
        return Applicability(False, WARNING_LATE)
    identity_changed = (
        playback_state_id != armed_state_id
        or playback_revision != armed_revision
        or abs(playback_intensity - armed_intensity) >= epsilon
        or playback_harmony_tail != armed_harmony_tail
        or playback_prefix_digest != armed_prefix_digest
    )
    if identity_changed:
        logger.debug(
            "Continuation result is stale",
            extra={
                "anchor_bar": playback_bar,
                "target_start_bar": target_start_bar,
                "code": WARNING_STALE,
            },
        )
        return Applicability(False, WARNING_STALE)
    return Applicability(True, None)


def job_identity_matches(
    *,
    anchor_bar: int,
    mode: str,
    runtime_state_id: str,
    document_revision: int,
    prefix_digest_value: str,
    armed_anchor_bar: int,
    armed_mode: str,
    armed_state_id: str,
    armed_revision: int,
    armed_prefix_digest: str,
    job_status: str,
) -> bool:
    """Keep the active job while the musical identity key matches."""
    if job_status not in {"pending", "applied"}:
        return False
    return (
        anchor_bar == armed_anchor_bar
        and mode == armed_mode
        and runtime_state_id == armed_state_id
        and document_revision == armed_revision
        and prefix_digest_value == armed_prefix_digest
    )


def _target_outside_loop(
    *,
    loop_enabled: bool,
    loop_start_bar: int | None,
    loop_end_bar: int | None,
    target_start_bar: int | None,
) -> bool:
    if not loop_enabled or target_start_bar is None:
        return False
    if loop_start_bar is None or loop_end_bar is None:
        return False
    return target_start_bar < loop_start_bar or target_start_bar > loop_end_bar


def continuation_audible(
    *,
    fallback_kind: str,
    loop_enabled: bool,
    loop_start_bar: int | None,
    loop_end_bar: int | None,
    target_start_bar: int | None,
) -> bool:
    """Loop reuse and a target outside the published loop stay silent."""
    outside = _target_outside_loop(
        loop_enabled=loop_enabled,
        loop_start_bar=loop_start_bar,
        loop_end_bar=loop_end_bar,
        target_start_bar=target_start_bar,
    )
    return _audible(
        fallback_kind=fallback_kind,
        outside_loop=outside,
        target_start_bar=target_start_bar,
    )


def _audible(*, fallback_kind: str, outside_loop: bool, target_start_bar: int | None) -> bool:
    if fallback_kind == FALLBACK_REUSE_LOOP or target_start_bar is None or outside_loop:
        return False
    return True


def _log_window(
    plan: RuntimeWindowPlan,
    theme_count: int,
    *,
    continuous: bool = False,
    virtual_bar: int | None = None,
) -> None:
    logger.debug(
        "Planned adaptive runtime window",
        extra={
            "anchor_bar": plan.anchor_bar,
            "target_start_bar": plan.target_start_bar,
            "fallback_kind": plan.fallback_kind,
            "repetition_count": plan.repetition_count,
            "theme_count": theme_count,
            "continuous": continuous,
            "virtual_bar": virtual_bar,
            "warning_codes": list(plan.warnings),
        },
    )
