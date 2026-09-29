"""Deterministic continuation fallbacks and local-bar placement.

Loop reuse, motif repeat, and fake accompaniment run to completion before a
model task is scheduled. This module does not import playback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.adaptive_runtime_continuation_settings import (
    load_adaptive_runtime_continuation_settings,
)
from app.composition_plan_schemas import CompositionPlan
from app.composition_schemas import (
    COMPOSITION_SCHEMA_VERSION_V2,
    CompositionV2,
    CompositionV2NoteEvent,
    CompositionV2Section,
    CompositionV2Track,
)
from app.services.adaptive_runtime_continuation import (
    FALLBACK_ACCOMPANIMENT,
    FALLBACK_MOTIF,
    FALLBACK_REUSE_LOOP,
    WARNING_INVALID,
    WARNING_METER,
    WARNING_TRUNCATED,
)
from app.services.composition_motif_transform import (
    MotifDestinationSpec,
    RelativeMotifNote,
    transform_repeat,
)
from app.services.composition_validator import validate_composition_integrity
from app.services.fake_symbolic_composer import generate_fake_symbolic_composition

logger = logging.getLogger(__name__)


class ContinuationPlacementError(Exception):
    """Validation failed. The caller walks to the next fallback kind."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PlacedEvents:
    events: tuple[CompositionV2NoteEvent, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class FallbackRealization:
    fallback_kind: str
    source: str
    events: tuple[CompositionV2NoteEvent, ...]
    warnings: tuple[str, ...]


def place_local_events(
    composition: CompositionV2,
    *,
    deadline_tick: int,
    ticks_per_bar: int,
    generate_bars: int,
    prefix_end_tick: int = 0,
    meter_changed: bool = False,
) -> PlacedEvents:
    """Validate a local piece at tick 0, then shift the first kept bar onto the deadline.

    Events whose start is still inside a prefix span are dropped before the shift.
    """
    logger.debug(
        "Placing local continuation bars",
        extra={
            "deadline_tick": deadline_tick,
            "generate_bars": generate_bars,
            "bar_count": composition.bar_count,
            "prefix_clipped": prefix_end_tick > 0,
        },
    )
    if ticks_per_bar < 1 or generate_bars < 1 or deadline_tick < 0:
        raise ContinuationPlacementError(WARNING_INVALID)
    integrity = validate_composition_integrity(composition, profile="generation")
    if not integrity.ok:
        logger.debug(
            "Local continuation failed integrity",
            extra={"code": WARNING_INVALID, "error_count": len(integrity.errors)},
        )
        raise ContinuationPlacementError(WARNING_INVALID)
    if prefix_end_tick <= 0 and composition.bar_count != generate_bars:
        logger.debug(
            "Local continuation bar count does not match the target",
            extra={
                "code": WARNING_INVALID,
                "bar_count": composition.bar_count,
                "generate_bars": generate_bars,
            },
        )
        raise ContinuationPlacementError(WARNING_INVALID)

    warnings: list[str] = []
    if meter_changed:
        warnings.append(WARNING_METER)
    kept: list[CompositionV2NoteEvent] = []
    for track in composition.tracks:
        for event in track.events:
            if event.start_tick < prefix_end_tick:
                continue
            kept.append(event)
    if not kept:
        return PlacedEvents((), tuple(warnings))

    earliest = min(event.start_tick for event in kept)
    local_bar_start = (earliest // ticks_per_bar) * ticks_per_bar
    shift = deadline_tick - local_bar_start
    target_end = deadline_tick + generate_bars * ticks_per_bar
    placed: list[CompositionV2NoteEvent] = []
    for event in kept:
        new_start = event.start_tick + shift
        if new_start < deadline_tick or new_start >= target_end:
            continue
        placed.append(event.model_copy(update={"start_tick": new_start}))
    cap = load_adaptive_runtime_continuation_settings().max_buffer_events
    if len(placed) > cap:
        placed = placed[:cap]
        warnings.append(WARNING_TRUNCATED)
        logger.debug(
            "Continuation buffer truncated",
            extra={"code": WARNING_TRUNCATED, "event_count": cap},
        )
    return PlacedEvents(tuple(placed), tuple(warnings))


def realize_fallback(
    order: tuple[str, ...] | list[str],
    *,
    loop_enabled: bool,
    loop_start_bar: int | None,
    loop_end_bar: int | None,
    plan: CompositionPlan | None,
    seed: int,
    relative_notes: tuple[RelativeMotifNote, ...] | list[RelativeMotifNote] | None,
    anchor_midi: int,
    destination_track: CompositionV2Track | None,
    composition: CompositionV2 | None,
    job_id: str,
    deadline_tick: int,
    ticks_per_bar: int,
    generate_bars: int,
    target_span_ticks: int,
    meter_changed: bool = False,
) -> FallbackRealization:
    """Walk the fallback order. Exhaustion returns an empty loop buffer."""
    logger.debug(
        "Realizing continuation fallback",
        extra={"fallback_kind": order[0] if order else FALLBACK_REUSE_LOOP, "seed": seed},
    )
    for kind in order:
        try:
            realized = _realize_kind(
                kind,
                loop_enabled=loop_enabled,
                loop_start_bar=loop_start_bar,
                loop_end_bar=loop_end_bar,
                plan=plan,
                seed=seed,
                relative_notes=relative_notes,
                anchor_midi=anchor_midi,
                destination_track=destination_track,
                composition=composition,
                job_id=job_id,
                deadline_tick=deadline_tick,
                ticks_per_bar=ticks_per_bar,
                generate_bars=generate_bars,
                target_span_ticks=target_span_ticks,
                meter_changed=meter_changed,
            )
        except ContinuationPlacementError as exc:
            logger.debug(
                "Continuation fallback kind failed",
                extra={"fallback_kind": kind, "code": exc.code},
            )
            continue
        except Exception:
            logger.debug(
                "Continuation fallback kind failed",
                extra={"fallback_kind": kind, "code": WARNING_INVALID},
            )
            continue
        if realized is None:
            logger.debug(
                "Continuation fallback kind skipped",
                extra={"fallback_kind": kind},
            )
            continue
        logger.debug(
            "Continuation fallback realized",
            extra={
                "fallback_kind": realized.fallback_kind,
                "event_count": len(realized.events),
                "source": realized.source,
            },
        )
        return realized
    logger.debug(
        "Continuation fallback exhausted",
        extra={"fallback_kind": FALLBACK_REUSE_LOOP, "code": WARNING_INVALID},
    )
    return FallbackRealization(
        fallback_kind=FALLBACK_REUSE_LOOP,
        source="fallback",
        events=(),
        warnings=(WARNING_INVALID,),
    )


def _realize_kind(
    kind: str,
    *,
    loop_enabled: bool,
    loop_start_bar: int | None,
    loop_end_bar: int | None,
    plan: CompositionPlan | None,
    seed: int,
    relative_notes: tuple[RelativeMotifNote, ...] | list[RelativeMotifNote] | None,
    anchor_midi: int,
    destination_track: CompositionV2Track | None,
    composition: CompositionV2 | None,
    job_id: str,
    deadline_tick: int,
    ticks_per_bar: int,
    generate_bars: int,
    target_span_ticks: int,
    meter_changed: bool,
) -> FallbackRealization | None:
    if kind == FALLBACK_REUSE_LOOP:
        if not _loop_ready(loop_enabled, loop_start_bar, loop_end_bar):
            return None
        return FallbackRealization(
            fallback_kind=FALLBACK_REUSE_LOOP,
            source="fallback",
            events=(),
            warnings=(),
        )
    if kind == FALLBACK_MOTIF:
        return _motif_variation(
            relative_notes=relative_notes,
            anchor_midi=anchor_midi,
            destination_track=destination_track,
            composition=composition,
            job_id=job_id,
            deadline_tick=deadline_tick,
            ticks_per_bar=ticks_per_bar,
            generate_bars=generate_bars,
            target_span_ticks=target_span_ticks,
            meter_changed=meter_changed,
        )
    if kind == FALLBACK_ACCOMPANIMENT:
        return _accompaniment(
            plan=plan,
            seed=seed,
            deadline_tick=deadline_tick,
            ticks_per_bar=ticks_per_bar,
            generate_bars=generate_bars,
            meter_changed=meter_changed,
        )
    return None


def _loop_ready(
    loop_enabled: bool,
    loop_start_bar: int | None,
    loop_end_bar: int | None,
) -> bool:
    if not loop_enabled or loop_start_bar is None or loop_end_bar is None:
        return False
    return loop_end_bar >= loop_start_bar


def _motif_variation(
    *,
    relative_notes: tuple[RelativeMotifNote, ...] | list[RelativeMotifNote] | None,
    anchor_midi: int,
    destination_track: CompositionV2Track | None,
    composition: CompositionV2 | None,
    job_id: str,
    deadline_tick: int,
    ticks_per_bar: int,
    generate_bars: int,
    target_span_ticks: int,
    meter_changed: bool,
) -> FallbackRealization | None:
    if not relative_notes or destination_track is None or composition is None:
        return None
    track_copy = destination_track.model_copy(update={"events": []})
    destination = MotifDestinationSpec(
        track=track_copy,
        start_tick=0,
        duration_limit=target_span_ticks,
        anchor_midi=anchor_midi,
        existing_event_ids=frozenset(),
        allow_overlap=True,
    )
    transformed = transform_repeat(
        tuple(relative_notes),
        composition=composition,
        destination=destination,
        id_seed=f"arc-{job_id}",
    )
    local = _local_composition(
        composition,
        track=track_copy.model_copy(update={"events": list(transformed.events)}),
        generate_bars=generate_bars,
        ticks_per_bar=ticks_per_bar,
    )
    placed = place_local_events(
        local,
        deadline_tick=deadline_tick,
        ticks_per_bar=ticks_per_bar,
        generate_bars=generate_bars,
        prefix_end_tick=0,
        meter_changed=meter_changed,
    )
    return FallbackRealization(
        fallback_kind=FALLBACK_MOTIF,
        source="fallback",
        events=placed.events,
        warnings=placed.warnings,
    )


def _accompaniment(
    *,
    plan: CompositionPlan | None,
    seed: int,
    deadline_tick: int,
    ticks_per_bar: int,
    generate_bars: int,
    meter_changed: bool,
) -> FallbackRealization | None:
    if plan is None:
        return None
    music, _report = generate_fake_symbolic_composition(
        plan,
        seed=seed,
        prefix_composition=None,
    )
    placed = place_local_events(
        music,
        deadline_tick=deadline_tick,
        ticks_per_bar=ticks_per_bar,
        generate_bars=generate_bars,
        prefix_end_tick=0,
        meter_changed=meter_changed,
    )
    return FallbackRealization(
        fallback_kind=FALLBACK_ACCOMPANIMENT,
        source="fallback",
        events=placed.events,
        warnings=placed.warnings,
    )


def _local_composition(
    source: CompositionV2,
    *,
    track: CompositionV2Track,
    generate_bars: int,
    ticks_per_bar: int,
) -> CompositionV2:
    duration = generate_bars * ticks_per_bar
    section = CompositionV2Section(
        type="unsectioned",
        start_bar=1,
        bar_count=generate_bars,
        start_tick=0,
        duration_ticks=duration,
    )
    return CompositionV2(
        schema_version=COMPOSITION_SCHEMA_VERSION_V2,
        tempo=source.tempo,
        key=source.key,
        time_signature=source.time_signature,
        ticks_per_quarter=source.ticks_per_quarter,
        duration_ticks=duration,
        bar_count=generate_bars,
        sections=[section],
        tracks=[track],
        harmony=[],
        markers=[],
        key_changes=[],
        motifs=[],
    )
