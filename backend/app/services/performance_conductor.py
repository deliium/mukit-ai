"""Pure deterministic conductor: Composition + Plan → realization.

No FastAPI, SQLite, or LLM imports. Rubato/microtiming → tick_delta only.
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any

from app.composition_schemas import (
    CompositionV2,
    CompositionV2Track,
    articulation_gate_ticks,
    articulation_velocity,
)
from app.performance_conductor_constants import (
    DURATION_FLOOR_TICKS,
    ENGINE_VERSION,
    MICROTIMING_MAX_ABS_TICKS,
    RUBATO_TEMPO_FACTOR_MAX,
    RUBATO_TEMPO_FACTOR_MIN,
    TRACK_GAIN_MAX,
    TRACK_GAIN_MIN,
    VELOCITY_MAX,
    VELOCITY_MIN,
)
from app.performance_schemas import (
    PerformancePlanV1,
    PerformanceRealizationV1,
    RealizationMetricsV1,
    RealizationNoteV1,
    RealizationSustainSpanV1,
    RealizationTrackGainV1,
)
from app.services.composition_logical_notes import collapse_track_tie_chains
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.performance_identity import identity_digest

logger = logging.getLogger(__name__)


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _clamp_float(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _bar_start_ticks(composition: CompositionV2) -> list[int]:
    """Approximate bar starts from duration / bar_count when boundaries absent."""
    bar_count = max(1, int(composition.bar_count or 1))
    duration = max(1, int(composition.duration_ticks or 1))
    bar_len = max(1, duration // bar_count)
    return [i * bar_len for i in range(bar_count)]


def _section_phase(composition: CompositionV2, start_tick: int) -> float:
    for section in composition.sections or []:
        start = int(getattr(section, "start_tick", 0) or 0)
        dur = int(getattr(section, "duration_ticks", 0) or 0)
        if dur > 0 and start <= start_tick < start + dur:
            return (start_tick - start) / float(dur)
    total = max(1, int(composition.duration_ticks or 1))
    return (start_tick % total) / float(total)


def _bar_phase(composition: CompositionV2, start_tick: int) -> float:
    bars = _bar_start_ticks(composition)
    bar_len = max(1, bars[1] - bars[0] if len(bars) > 1 else int(composition.ticks_per_quarter) * 4)
    return (start_tick % bar_len) / float(bar_len)


def _is_downbeat(composition: CompositionV2, start_tick: int) -> bool:
    tpq = max(1, int(composition.ticks_per_quarter or 480))
    bar_len = tpq * 4
    return (start_tick % bar_len) == 0


def _track_role(track: CompositionV2Track) -> str:
    role = getattr(track, "role", None)
    if isinstance(role, str) and role.strip():
        return role.strip().lower()
    return "other"


def _tick_delta_cap(duration_ticks: int) -> int:
    return min(MICROTIMING_MAX_ABS_TICKS, max(0, int(duration_ticks) - 1))


def _apply_duration_floor(duration_ticks: int, duration_delta: int) -> int:
    floor = DURATION_FLOOR_TICKS - int(duration_ticks)
    return max(floor, duration_delta)


def realize_performance(
    composition: CompositionV2,
    plan: PerformancePlanV1,
    *,
    plan_revision: int = 1,
) -> PerformanceRealizationV1:
    """Deterministic realize. Ignores capture ``note_performances`` in ship-1."""
    before_identity = identity_digest(composition)
    fingerprint = composition_snapshot_fingerprint(composition)
    dims = plan.dimensions
    rng = random.Random(int(plan.seed))

    notes: list[RealizationNoteV1] = []
    abs_tick = 0.0
    abs_vel = 0.0
    abs_dur = 0.0
    note_count = 0
    tie_fanout_count = 0

    for track in composition.tracks:
        logical_notes = collapse_track_tie_chains(track)
        event_by_id = {event.id: event for event in track.events}

        for logical in logical_notes:
            phase = (
                _section_phase(composition, logical.start_tick)
                if dims.tempo_rubato.phrase_anchor == "section"
                else _bar_phase(composition, logical.start_tick)
            )
            # Rubato: local tempo factor in [0.85, 1.15] → tick offset (not Transport.bpm).
            rubato_wave = math.sin(
                2.0 * math.pi * (dims.tempo_rubato.rate * phase + (plan.seed % 97) / 97.0)
            )
            tempo_factor = 1.0 + dims.tempo_rubato.depth * 0.15 * rubato_wave
            tempo_factor = _clamp_float(
                tempo_factor, RUBATO_TEMPO_FACTOR_MIN, RUBATO_TEMPO_FACTOR_MAX
            )
            rubato_delta = int(round((tempo_factor - 1.0) * logical.duration_ticks * 0.25))

            # Microtiming: swing on offbeats + seeded humanize.
            tpq = max(1, int(composition.ticks_per_quarter or 480))
            eighth = tpq // 2
            in_swing_slot = eighth > 0 and ((logical.start_tick // eighth) % 2 == 1)
            swing_delta = int(round(dims.microtiming.swing * 0.15 * tpq)) if in_swing_slot else 0
            humanize_amp = dims.microtiming.humanize * MICROTIMING_MAX_ABS_TICKS
            humanize_delta = int(round(rng.uniform(-humanize_amp, humanize_amp))) if humanize_amp else 0

            # Phrasing breath gap near phrase ends.
            breath = 0
            if dims.phrasing.phrase_arc > 0 and phase > 0.85:
                breath = int(round(dims.phrasing.breath_gap_ticks * dims.phrasing.phrase_arc))

            tick_delta = rubato_delta + swing_delta + humanize_delta + breath
            cap = _tick_delta_cap(logical.duration_ticks)
            tick_delta = _clamp_int(tick_delta, -cap, cap)

            # Dynamics + accent offsets (applied per source event below).
            arc = math.sin(math.pi * phase)
            dyn = dims.dynamics.curve_strength * dims.dynamics.contrast * 24.0 * (arc - 0.5) * 2.0
            if _is_downbeat(composition, logical.start_tick):
                accent_boost = dims.accent.downbeat * 16.0
            else:
                accent_boost = dims.accent.offbeat * 8.0

            # Articulation bias relative to written mechanical gate (logical span).
            mechanical_gate = articulation_gate_ticks(
                logical.duration_ticks, logical.articulations
            )
            mechanical_duration_delta = mechanical_gate - logical.duration_ticks
            legato = dims.articulation.legato_bias * 0.12 * logical.duration_ticks
            staccato = dims.articulation.staccato_bias * -0.12 * logical.duration_ticks
            duration_delta = int(round(mechanical_duration_delta + legato + staccato))
            duration_delta = _apply_duration_floor(logical.duration_ticks, duration_delta)
            max_extend = max(0, int(logical.duration_ticks * 0.25))
            duration_delta = _clamp_int(
                duration_delta,
                DURATION_FLOOR_TICKS - logical.duration_ticks,
                max_extend,
            )

            source_ids = logical.source_event_ids or ()
            if not source_ids and logical.tie_group_id is None:
                for event in track.events:
                    if (
                        event.start_tick == logical.start_tick
                        and event.pitch == logical.pitch
                        and event.id
                        not in {n.event_id for n in notes if n.track_id == track.id}
                    ):
                        source_ids = (event.id,)
                        break

            fanout = 0
            for event_id in source_ids:
                event = event_by_id.get(event_id)
                if event is None:
                    continue
                # Shared tick_delta across tie members; velocity from each event.
                mechanical_velocity = articulation_velocity(
                    event.velocity, event.articulations
                )
                performed_velocity = int(round(mechanical_velocity + dyn + accent_boost))
                performed_velocity = _clamp_int(
                    performed_velocity,
                    max(VELOCITY_MIN, dims.dynamics.velocity_floor),
                    min(VELOCITY_MAX, dims.dynamics.velocity_ceiling),
                )
                member_mech_gate = articulation_gate_ticks(
                    event.duration_ticks, event.articulations
                )
                member_mech_dur = member_mech_gate - event.duration_ticks
                # Tie head carries conductor gate bias; continuations keep mechanical gate.
                raw_dur = duration_delta if fanout == 0 else member_mech_dur
                member_dur_delta = _apply_duration_floor(
                    event.duration_ticks,
                    _clamp_int(
                        raw_dur,
                        DURATION_FLOOR_TICKS - event.duration_ticks,
                        max(0, int(event.duration_ticks * 0.25)),
                    ),
                )
                member_cap = _tick_delta_cap(event.duration_ticks)
                member_tick = _clamp_int(tick_delta, -member_cap, member_cap)
                notes.append(
                    RealizationNoteV1(
                        event_id=event.id,
                        track_id=track.id,
                        tick_delta=member_tick,
                        duration_delta=member_dur_delta,
                        velocity=performed_velocity,
                    )
                )
                abs_tick += abs(member_tick)
                abs_vel += abs(performed_velocity - mechanical_velocity)
                abs_dur += abs(member_dur_delta - member_mech_dur)
                note_count += 1
                fanout += 1
            if fanout > 1:
                tie_fanout_count += 1

    # Pedaling → realization sustain_spans only (never write Composition sustain_pedals).
    sustain_spans: list[RealizationSustainSpanV1] = []
    style = dims.pedaling.style
    depth = dims.pedaling.depth
    if style != "none" and depth > 0:
        for track in composition.tracks:
            if style == "literal":
                for pedal in getattr(track, "sustain_pedals", None) or []:
                    start = int(pedal.start_tick)
                    end = start + int(pedal.duration_ticks)
                    sustain_spans.append(
                        RealizationSustainSpanV1(
                            track_id=track.id, start_tick=start, end_tick=end
                        )
                    )
            elif style in ("harmonic", "dry"):
                # Phrase-length spans from sections; dry shortens.
                shrink = 0.55 if style == "dry" else 1.0
                for section in composition.sections or []:
                    start = int(getattr(section, "start_tick", 0) or 0)
                    dur = int(getattr(section, "duration_ticks", 0) or 0)
                    if dur <= 0:
                        continue
                    span_dur = int(dur * depth * shrink)
                    if span_dur <= 0:
                        continue
                    sustain_spans.append(
                        RealizationSustainSpanV1(
                            track_id=track.id,
                            start_tick=start,
                            end_tick=start + span_dur,
                        )
                    )

    # Orchestral balance → session track gains.
    track_gains: list[RealizationTrackGainV1] = []
    role_gains = dims.orchestral_balance.role_gains
    explicit_track = dims.orchestral_balance.track_gains
    for track in composition.tracks:
        if track.id in explicit_track:
            raw = float(explicit_track[track.id])
        else:
            role = _track_role(track)
            raw = float(role_gains.get(role, 1.0))
        # Map role gain [0.25, 1.5] into session [0, 1] with 1.0 → ~0.85 center.
        gain = _clamp_float(raw / 1.5, TRACK_GAIN_MIN, TRACK_GAIN_MAX)
        track_gains.append(RealizationTrackGainV1(track_id=track.id, gain=gain))

    metrics = RealizationMetricsV1(
        mean_abs_tick_delta=(abs_tick / note_count) if note_count else 0.0,
        mean_abs_velocity_delta=(abs_vel / note_count) if note_count else 0.0,
        mean_abs_duration_delta=(abs_dur / note_count) if note_count else 0.0,
        sustain_span_count=len(sustain_spans),
        note_count=note_count,
        capture_performance_ignored=True,
    )

    realization = PerformanceRealizationV1(
        plan_id=plan.id or "ephemeral",
        plan_revision=max(1, int(plan_revision)),
        engine_version=ENGINE_VERSION,
        source_composition_fingerprint=fingerprint,
        notes=notes,
        sustain_spans=sustain_spans,
        track_gains=track_gains,
        metrics=metrics,
    )

    after_identity = identity_digest(composition)
    if after_identity != before_identity:
        logger.error(
            "Conductor identity digest changed unexpectedly",
            extra={"plan_id": plan.id, "code": "identity_mismatch"},
        )
        raise RuntimeError("realize_performance must not mutate composition identity")

    logger.debug(
        "Realized performance",
        extra={
            "plan_id": plan.id,
            "preset_id": plan.preset_id,
            "engine_version": ENGINE_VERSION,
            "note_count": note_count,
            "tie_fanout_groups": tie_fanout_count,
            "mean_abs_tick_delta": round(metrics.mean_abs_tick_delta, 4),
            "mean_abs_velocity_delta": round(metrics.mean_abs_velocity_delta, 4),
            "sustain_span_count": metrics.sustain_span_count,
            "capture_performance_ignored": True,
        },
    )
    return realization


def mechanical_metrics(composition: CompositionV2) -> RealizationMetricsV1:
    """Near-zero mechanical baseline metrics (canonical schedule)."""
    count = sum(len(track.events) for track in composition.tracks)
    return RealizationMetricsV1(
        mean_abs_tick_delta=0.0,
        mean_abs_velocity_delta=0.0,
        mean_abs_duration_delta=0.0,
        sustain_span_count=0,
        note_count=count,
        capture_performance_ignored=True,
    )


def sample_deltas(
    realization: PerformanceRealizationV1,
    *,
    max_sample: int = 8,
) -> list[dict[str, Any]]:
    """Capped sample for compare responses — never dump full note arrays at INFO."""
    sample: list[dict[str, Any]] = []
    for note in realization.notes[: max(0, max_sample)]:
        if note.tick_delta == 0 and note.duration_delta == 0:
            continue
        sample.append(
            {
                "event_id": note.event_id,
                "track_id": note.track_id,
                "tick_delta": note.tick_delta,
                "duration_delta": note.duration_delta,
                "velocity": note.velocity,
            }
        )
        if len(sample) >= max_sample:
            break
    return sample
