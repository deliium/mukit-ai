"""Structured theme planning helpers and thematic recurrence realization."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Sequence

from pydantic import ValidationError

from app.composition_schemas import (
    MOTIF_MAX_EVENT_REFS,
    MOTIF_MIN_EVENT_REFS,
    CompositionV2,
    CompositionV2MotifDefinition,
    CompositionV2MotifOccurrence,
    CompositionV2MotifTransformProvenance,
    CompositionV2NoteEvent,
    CompositionV2Section,
    CompositionV2Track,
    midi_pitch_number,
)
from app.schemas import GenerationValidationIssue
from app.services.composition_motif_similarity import (
    CREATIVE_OPERATIONS,
    MECHANICAL_OPERATIONS,
    verify_motif_identity,
)
from app.services.composition_motif_transform import (
    MotifDestinationSpec,
    MotifTransformError,
    RelativeMotifNote,
    transform_augmentation,
    transform_diminution,
    transform_inversion,
    transform_repeat,
    transform_sequence,
    transform_transpose,
)
from app.services.composition_planner import (
    THEME_MAX_DEPLOYMENTS,
    ComposerDraftNote,
    ComposerFormPlan,
    ComposerThemeDeployment,
    ComposerThemeParameters,
    ComposerThemePlan,
    ComposerThemeRelativeNote,
    ComposerThemeSeedSpec,
    ComposerTrackDraft,
    ValidationDiagnostic,
    summarize_theme_plan,
)
from app.services.composition_timing import bar_duration_ticks


logger = logging.getLogger(__name__)

THEME_SOURCE_EMPTY = "theme_source_empty"
THEME_TARGET_MISSING = "theme_target_missing"
THEME_TRANSFORM_MISMATCH = "theme_transform_mismatch"
THEME_IDENTITY_BELOW_THRESHOLD = "theme_identity_below_threshold"
THEME_TARGET_OUT_OF_BOUNDS = "theme_target_out_of_bounds"
THEME_PLAN_TRUNCATED = "theme_plan_truncated"

THEME_DIAGNOSTIC_CODES = frozenset(
    {
        THEME_SOURCE_EMPTY,
        THEME_TARGET_MISSING,
        THEME_TRANSFORM_MISMATCH,
        THEME_IDENTITY_BELOW_THRESHOLD,
        THEME_TARGET_OUT_OF_BOUNDS,
        THEME_PLAN_TRUNCATED,
    }
)


@dataclass(frozen=True)
class ThemeRecurrenceOutcome:
    deployment_id: str
    operation: str
    identity_score: float | None
    status: str  # realized | verified | skipped | failed
    diagnostic_code: str | None = None


@dataclass(frozen=True)
class ThemeRealizationResult:
    melody_draft: ComposerTrackDraft
    theme_plan: ComposerThemePlan
    motifs: tuple[CompositionV2MotifDefinition, ...]
    outcomes: tuple[ThemeRecurrenceOutcome, ...]
    diagnostics: tuple[ValidationDiagnostic, ...]


def validate_theme_plan_against_form(
    plan: ComposerThemePlan,
    form: ComposerFormPlan,
) -> tuple[ComposerThemePlan, list[ValidationDiagnostic]]:
    """Clamp and validate a theme plan against form bounds; may disable unsuitable plans."""
    diagnostics: list[ValidationDiagnostic] = []
    section_count = len(form.sections)

    if not plan.enabled:
        logger.info(
            "Theme plan disabled",
            extra={"reason": plan.no_theme_reason, "section_count": section_count},
        )
        return plan, diagnostics

    if section_count < 2:
        disabled = ComposerThemePlan(
            enabled=False,
            no_theme_reason="single_section_form",
        )
        diagnostics.append(
            ValidationDiagnostic(
                code=THEME_TARGET_MISSING,
                message="Theme recurrence requires multiple sections",
                severity="warning",
                context={"section_count": section_count},
            )
        )
        logger.info(
            "Disabled theme plan for unsuitable form",
            extra={"reason": "single_section_form", "section_count": section_count},
        )
        return disabled, diagnostics

    if plan.seed is None:
        diagnostics.append(
            ValidationDiagnostic(
                code=THEME_SOURCE_EMPTY,
                message="Enabled theme plan is missing a seed specification",
                severity="error",
                context={"deployment_count": len(plan.deployments)},
            )
        )
        disabled = empty_theme_plan(reason="missing_seed")
        logger.warning(
            "Disabled theme plan with missing seed",
            extra={"code": THEME_SOURCE_EMPTY, "deployment_count": len(plan.deployments)},
        )
        return disabled, diagnostics

    seed = plan.seed
    truncated = plan.truncated
    deployments = list(plan.deployments)

    if len(deployments) > THEME_MAX_DEPLOYMENTS:
        deployments = deployments[:THEME_MAX_DEPLOYMENTS]
        truncated = True

    if seed.section_index >= section_count:
        diagnostics.append(
            ValidationDiagnostic(
                code=THEME_SOURCE_EMPTY,
                message="Theme seed section index is out of bounds",
                severity="error",
                context={"section_index": seed.section_index, "section_count": section_count},
            )
        )
        return (
            ComposerThemePlan(enabled=False, no_theme_reason="seed_section_out_of_bounds"),
            diagnostics,
        )

    seed_section = form.sections[seed.section_index]
    if seed.start_bar_offset + seed.bar_span > seed_section.bar_count:
        diagnostics.append(
            ValidationDiagnostic(
                code=THEME_SOURCE_EMPTY,
                message="Theme seed bar span exceeds seed section",
                severity="error",
                context={
                    "start_bar_offset": seed.start_bar_offset,
                    "bar_span": seed.bar_span,
                    "section_bar_count": seed_section.bar_count,
                },
            )
        )
        return (
            ComposerThemePlan(enabled=False, no_theme_reason="seed_span_out_of_bounds"),
            diagnostics,
        )

    valid_deployments: list[ComposerThemeDeployment] = []
    for deployment in deployments:
        if deployment.target_section_index >= section_count:
            diagnostics.append(
                ValidationDiagnostic(
                    code=THEME_TARGET_MISSING,
                    message="Theme deployment target section missing",
                    severity="error",
                    context={
                        "deployment_id": deployment.id,
                        "target_section_index": deployment.target_section_index,
                    },
                )
            )
            continue
        if deployment.target_section_index == seed.section_index and deployment.start_bar_offset == seed.start_bar_offset:
            diagnostics.append(
                ValidationDiagnostic(
                    code=THEME_TARGET_MISSING,
                    message="Theme deployment must target a later or distinct placement",
                    severity="warning",
                    context={"deployment_id": deployment.id},
                )
            )
            continue
        target_section = form.sections[deployment.target_section_index]
        if deployment.start_bar_offset >= target_section.bar_count:
            diagnostics.append(
                ValidationDiagnostic(
                    code=THEME_TARGET_OUT_OF_BOUNDS,
                    message="Theme deployment start exceeds target section",
                    severity="error",
                    context={
                        "deployment_id": deployment.id,
                        "start_bar_offset": deployment.start_bar_offset,
                        "section_bar_count": target_section.bar_count,
                    },
                )
            )
            continue
        valid_deployments.append(deployment)

    if truncated:
        diagnostics.append(
            ValidationDiagnostic(
                code=THEME_PLAN_TRUNCATED,
                message="Theme plan deployments were truncated to bound",
                severity="warning",
                context={"max_deployments": THEME_MAX_DEPLOYMENTS},
            )
        )

    if not valid_deployments:
        logger.info(
            "Disabled theme plan; no valid deployments",
            extra={"section_count": section_count},
        )
        return (
            ComposerThemePlan(
                enabled=False,
                truncated=truncated,
                no_theme_reason="no_valid_deployments",
            ),
            diagnostics,
        )

    # Prefer at least one later section when available.
    later = [d for d in valid_deployments if d.target_section_index > seed.section_index]
    if not later and section_count > 1:
        # Keep cross-track / same-section later offsets if any survived.
        later = valid_deployments

    corrected = plan.model_copy(
        update={
            "deployments": later,
            "truncated": truncated,
            "seed": seed,
        }
    )
    logger.info(
        "Validated theme plan",
        extra={
            **summarize_theme_plan(corrected),
            "diagnostic_count": len(diagnostics),
        },
    )
    logger.debug(
        "Theme plan validation details",
        extra={
            "seed_section_index": seed.section_index,
            "deployment_ids": [item.id for item in corrected.deployments],
            "truncated": truncated,
        },
    )
    return corrected, diagnostics


def empty_theme_plan(*, reason: str = "unsuitable_request") -> ComposerThemePlan:
    return ComposerThemePlan(enabled=False, no_theme_reason=reason)


def default_theme_plan_for_form(form: ComposerFormPlan) -> ComposerThemePlan:
    """Deterministic fallback when the provider returns an empty/disabled multi-section plan."""
    if len(form.sections) < 2:
        return empty_theme_plan(reason="single_section_form")
    seed = ComposerThemeSeedSpec(section_index=0, track_role="melody", start_bar_offset=0, bar_span=1)
    target_index = min(len(form.sections) - 1, max(1, len(form.sections) // 2))
    if target_index == 0:
        target_index = 1
    deployment = ComposerThemeDeployment(
        id="dep-transpose-1",
        target_section_index=target_index,
        operation="transpose",
        parameters=ComposerThemeParameters(transpose_semitones=5),
    )
    plan = ComposerThemePlan(
        enabled=True,
        motif_id="motif-a",
        motif_label="Motif A",
        seed=seed,
        deployments=[deployment],
    )
    corrected, _ = validate_theme_plan_against_form(plan, form)
    return corrected


def section_tick_bounds(
    form: ComposerFormPlan,
    section_index: int,
    *,
    ticks_per_quarter: int,
    start_bar_offset: int = 0,
    bar_span: int | None = None,
) -> tuple[int, int]:
    section = form.sections[section_index]
    bar_ticks = bar_duration_ticks(form.time_signature, ticks_per_quarter)
    absolute_start_bar = section.start_bar + start_bar_offset
    span = bar_span if bar_span is not None else max(1, section.bar_count - start_bar_offset)
    start_tick = (absolute_start_bar - 1) * bar_ticks
    end_tick = start_tick + span * bar_ticks
    return start_tick, end_tick


def assign_draft_event_ids(
    draft: ComposerTrackDraft,
    *,
    id_prefix: str,
    used_ids: set[str] | None = None,
) -> ComposerTrackDraft:
    """Ensure every draft note has a stable unique id before motif assembly."""
    occupied = set(used_ids or set())
    events: list[ComposerDraftNote] = []
    for index, event in enumerate(draft.events):
        event_id = event.id
        if not event_id or event_id in occupied:
            digest = hashlib.sha256(f"{id_prefix}|{index}|{event.pitch}|{event.start_tick}".encode()).hexdigest()[
                :12
            ]
            candidate = f"{id_prefix}-{digest}"
            suffix = 2
            while candidate in occupied:
                candidate = f"{id_prefix}-{digest}-{suffix}"
                suffix += 1
            event_id = candidate
        occupied.add(event_id)
        events.append(event.model_copy(update={"id": event_id}))
    return draft.model_copy(update={"events": events})


def extract_seed_relative_cell(
    draft: ComposerTrackDraft,
    *,
    form: ComposerFormPlan,
    seed: ComposerThemeSeedSpec,
    ticks_per_quarter: int,
) -> tuple[list[ComposerThemeRelativeNote], list[str]]:
    start_tick, end_tick = section_tick_bounds(
        form,
        seed.section_index,
        ticks_per_quarter=ticks_per_quarter,
        start_bar_offset=seed.start_bar_offset,
        bar_span=seed.bar_span,
    )
    seed_events = [
        event
        for event in draft.events
        if event.start_tick >= start_tick and event.start_tick < end_tick
    ]
    seed_events = sorted(seed_events, key=lambda item: (item.start_tick, item.duration_ticks, item.id or ""))
    if len(seed_events) < MOTIF_MIN_EVENT_REFS:
        raise MotifTransformError(
            "Theme seed has too few notes",
            code=THEME_SOURCE_EMPTY,
            context={"event_count": len(seed_events)},
        )
    if len(seed_events) > MOTIF_MAX_EVENT_REFS:
        seed_events = seed_events[:MOTIF_MAX_EVENT_REFS]

    anchor_midi = midi_pitch_number(seed_events[0].pitch)
    anchor_start = seed_events[0].start_tick
    cells: list[ComposerThemeRelativeNote] = []
    event_ids: list[str] = []
    for event in seed_events:
        if not event.id:
            raise MotifTransformError(
                "Theme seed events require ids",
                code=THEME_SOURCE_EMPTY,
            )
        cells.append(
            ComposerThemeRelativeNote(
                relative_start_tick=event.start_tick - anchor_start,
                duration_ticks=event.duration_ticks,
                pitch_semitone_offset=midi_pitch_number(event.pitch) - anchor_midi,
                velocity=event.velocity,
            )
        )
        event_ids.append(event.id)
    return cells, event_ids


def relative_notes_from_theme_cell(
    cell: Sequence[ComposerThemeRelativeNote],
) -> tuple[RelativeMotifNote, ...]:
    return tuple(
        RelativeMotifNote(
            relative_start_tick=note.relative_start_tick,
            duration_ticks=note.duration_ticks,
            pitch_semitone_offset=note.pitch_semitone_offset,
            velocity=note.velocity,
            staff="treble",
            voice=None,
            articulations=(),
        )
        for note in cell
    )


def theme_plan_prompt_projection(plan: ComposerThemePlan | None) -> dict[str, Any]:
    """Compact theme context for bass/accompaniment — includes relative cell when frozen."""
    if plan is None or not plan.enabled or plan.seed is None:
        return {"enabled": False}
    seed = plan.seed
    return {
        "enabled": True,
        "motif_id": plan.motif_id,
        "motif_label": plan.motif_label,
        "seed_section_index": seed.section_index,
        "seed_bar_span": seed.bar_span,
        "relative_cell": [note.model_dump() for note in seed.relative_cell[:THEME_MAX_DEPLOYMENTS * 8]],
        "deployments": [
            {
                "id": item.id,
                "target_section_index": item.target_section_index,
                "operation": item.operation,
                "start_bar_offset": item.start_bar_offset,
                "variation_strength": item.variation_strength,
            }
            for item in plan.deployments
        ],
        "prior_section_handoff": seed.prior_section_handoff,
    }


def _provisional_composition_from_melody(
    draft: ComposerTrackDraft,
    *,
    form: ComposerFormPlan,
    duration_ticks: int,
    ticks_per_quarter: int,
) -> CompositionV2:
    bar_ticks = bar_duration_ticks(form.time_signature, ticks_per_quarter)
    sections = [
        CompositionV2Section(
            id=f"section-{index}",
            type=section.type,
            start_bar=section.start_bar,
            bar_count=section.bar_count,
            start_tick=(section.start_bar - 1) * bar_ticks,
            duration_ticks=section.bar_count * bar_ticks,
        )
        for index, section in enumerate(form.sections, start=1)
    ]
    events = []
    for event in draft.events:
        if event.start_tick >= duration_ticks:
            continue
        max_dur = duration_ticks - event.start_tick
        duration = min(event.duration_ticks, max_dur)
        if duration <= 0:
            continue
        events.append(
            CompositionV2NoteEvent(
                pitch=event.pitch,
                start_tick=event.start_tick,
                duration_ticks=duration,
                velocity=event.velocity,
                staff=event.staff,
                id=event.id,
                articulations=[],
                tie=None,
            )
        )
    track = CompositionV2Track(
        id=draft.id or "melody-1",
        name=draft.name or "Melody",
        instrument=draft.instrument,
        role=draft.role,
        midi_program=draft.midi_program or 0,
        channel=draft.channel or 1,
        is_drum=False,
        volume=draft.volume,
        pan=draft.pan,
        expression=127,
        staff=draft.staff or "treble",
        events=events,
        dynamic_marks=[],
        sustain_pedals=[],
        automation=[],
    )
    return CompositionV2(
        tempo=form.tempo,
        key=form.key,
        time_signature=form.time_signature,
        ticks_per_quarter=ticks_per_quarter,
        duration_ticks=duration_ticks,
        bar_count=form.bar_count,
        sections=sections,
        tracks=[track],
        harmony=[],
        motifs=[],
    )


def _apply_mechanical_transform(
    source: Sequence[RelativeMotifNote],
    *,
    deployment: ComposerThemeDeployment,
    composition: CompositionV2,
    destination: MotifDestinationSpec,
) -> Any:
    op = deployment.operation
    params = deployment.parameters
    id_seed = f"theme-{deployment.id}"
    if op == "repeat":
        return transform_repeat(source, composition=composition, destination=destination, id_seed=id_seed)
    if op == "transpose":
        return transform_transpose(
            source,
            semitones=int(params.transpose_semitones or 0),
            composition=composition,
            destination=destination,
            id_seed=id_seed,
        )
    if op == "inversion":
        return transform_inversion(
            source,
            axis_pitch=params.inversion_axis_pitch,
            composition=composition,
            destination=destination,
            id_seed=id_seed,
        )
    if op == "augmentation":
        return transform_augmentation(
            source,
            numerator=int(params.time_scale_numerator or 2),
            denominator=int(params.time_scale_denominator or 1),
            composition=composition,
            destination=destination,
            id_seed=id_seed,
        )
    if op == "diminution":
        return transform_diminution(
            source,
            numerator=int(params.time_scale_numerator or 1),
            denominator=int(params.time_scale_denominator or 2),
            composition=composition,
            destination=destination,
            id_seed=id_seed,
        )
    if op == "sequence":
        return transform_sequence(
            source,
            steps=int(params.sequence_steps or 1),
            interval_semitones=int(params.sequence_interval_semitones or 0),
            step_ticks=int(params.sequence_step_ticks or composition.ticks_per_quarter),
            composition=composition,
            destination=destination,
            id_seed=id_seed,
        )
    raise MotifTransformError(f"Unsupported mechanical theme op: {op}", code=THEME_TRANSFORM_MISMATCH)


def _events_overlapping_span(
    events: Sequence[ComposerDraftNote],
    start_tick: int,
    end_tick: int,
) -> list[ComposerDraftNote]:
    return [
        event
        for event in events
        if event.start_tick < end_tick and (event.start_tick + event.duration_ticks) > start_tick
    ]


def realize_theme_plan(
    *,
    theme_plan: ComposerThemePlan,
    melody_draft: ComposerTrackDraft,
    form: ComposerFormPlan,
    ticks_per_quarter: int = 480,
) -> ThemeRealizationResult:
    """Realize mechanical deployments and verify creative ones before assembly."""
    diagnostics: list[ValidationDiagnostic] = []
    outcomes: list[ThemeRecurrenceOutcome] = []

    if not theme_plan.enabled or theme_plan.seed is None:
        logger.info("Theme realization skipped (disabled plan)")
        return ThemeRealizationResult(
            melody_draft=melody_draft,
            theme_plan=theme_plan,
            motifs=(),
            outcomes=(),
            diagnostics=(),
        )

    bar_ticks = bar_duration_ticks(form.time_signature, ticks_per_quarter)
    duration_ticks = form.bar_count * bar_ticks
    draft = assign_draft_event_ids(melody_draft, id_prefix="gen-mel")
    # Drop notes that start past the end; leave slight duration overflows for assemble clamping.
    draft = draft.model_copy(
        update={
            "events": [event for event in draft.events if event.start_tick < duration_ticks]
        }
    )

    try:
        if theme_plan.seed.relative_cell:
            cells = list(theme_plan.seed.relative_cell)
            seed_event_ids = list(theme_plan.seed.seed_event_ids)
            if not seed_event_ids:
                _, seed_event_ids = extract_seed_relative_cell(
                    draft,
                    form=form,
                    seed=theme_plan.seed,
                    ticks_per_quarter=ticks_per_quarter,
                )
        else:
            cells, seed_event_ids = extract_seed_relative_cell(
                draft,
                form=form,
                seed=theme_plan.seed,
                ticks_per_quarter=ticks_per_quarter,
            )
    except MotifTransformError as exc:
        code = getattr(exc, "code", THEME_SOURCE_EMPTY)
        diagnostics.append(
            ValidationDiagnostic(
                code=code if code in THEME_DIAGNOSTIC_CODES else THEME_SOURCE_EMPTY,
                message=str(exc)[:200],
                severity="error",
                context={"stage": "realize_themes", **(exc.context or {})},
            )
        )
        logger.warning(
            "Theme realization failed at seed extraction",
            extra={"code": code},
        )
        return ThemeRealizationResult(
            melody_draft=draft,
            theme_plan=theme_plan,
            motifs=(),
            outcomes=(),
            diagnostics=tuple(diagnostics),
        )

    frozen_seed = theme_plan.seed.model_copy(
        update={"relative_cell": cells, "seed_event_ids": seed_event_ids}
    )
    frozen_plan = theme_plan.model_copy(update={"seed": frozen_seed})
    source_notes = relative_notes_from_theme_cell(cells)
    composition = _provisional_composition_from_melody(
        draft,
        form=form,
        duration_ticks=duration_ticks,
        ticks_per_quarter=ticks_per_quarter,
    )
    track_id = composition.tracks[0].id
    working_events = list(draft.events)
    occurrence_defs: list[CompositionV2MotifOccurrence] = [
        CompositionV2MotifOccurrence(
            id=f"{frozen_plan.motif_id}-original",
            track_id=track_id,
            event_ids=seed_event_ids,
            relationship="original",
        )
    ]

    for deployment in frozen_plan.deployments:
        target_start, _ = section_tick_bounds(
            form,
            deployment.target_section_index,
            ticks_per_quarter=ticks_per_quarter,
            start_bar_offset=deployment.start_bar_offset,
            bar_span=1,
        )
        try:
            if deployment.operation in MECHANICAL_OPERATIONS:
                # Rebuild provisional track from working events for destination overlap checks.
                composition = _provisional_composition_from_melody(
                    draft.model_copy(update={"events": working_events}),
                    form=form,
                    duration_ticks=duration_ticks,
                    ticks_per_quarter=ticks_per_quarter,
                )
                seed_anchor = midi_pitch_number(
                    next(event.pitch for event in working_events if event.id == seed_event_ids[0])
                )
                destination = MotifDestinationSpec(
                    track=composition.tracks[0],
                    start_tick=target_start,
                    duration_limit=duration_ticks,
                    anchor_midi=seed_anchor,
                    existing_event_ids=frozenset(seed_event_ids),
                    allow_overlap=True,
                )
                transform_result = _apply_mechanical_transform(
                    source_notes,
                    deployment=deployment,
                    composition=composition,
                    destination=destination,
                )
                span_end = target_start + transform_result.span_ticks
                kept = [
                    event
                    for event in working_events
                    if not (
                        event.id not in seed_event_ids
                        and event.start_tick < span_end
                        and (event.start_tick + event.duration_ticks) > target_start
                    )
                ]
                new_draft_notes = [
                    ComposerDraftNote(
                        pitch=event.pitch,
                        start_tick=event.start_tick,
                        duration_ticks=event.duration_ticks,
                        velocity=event.velocity,
                        staff=event.staff,
                        id=event.id,
                    )
                    for event in transform_result.events
                ]
                working_events = sorted(
                    [*kept, *new_draft_notes],
                    key=lambda item: (item.start_tick, item.duration_ticks, item.id or ""),
                )
                created_ids = [event.id for event in transform_result.events if event.id]
                if len(created_ids) < MOTIF_MIN_EVENT_REFS:
                    raise MotifTransformError(
                        "Mechanical theme realization produced too few events",
                        code=THEME_TRANSFORM_MISMATCH,
                    )
                transform_prov = CompositionV2MotifTransformProvenance(
                    operation=deployment.operation,  # type: ignore[arg-type]
                    transpose_semitones=deployment.parameters.transpose_semitones,
                    inversion_axis_pitch=deployment.parameters.inversion_axis_pitch,
                    time_scale_numerator=deployment.parameters.time_scale_numerator,
                    time_scale_denominator=deployment.parameters.time_scale_denominator,
                    sequence_steps=deployment.parameters.sequence_steps,
                    sequence_interval_semitones=deployment.parameters.sequence_interval_semitones,
                    sequence_step_ticks=deployment.parameters.sequence_step_ticks,
                )
                occurrence_defs.append(
                    CompositionV2MotifOccurrence(
                        id=f"{frozen_plan.motif_id}-{deployment.id}",
                        track_id=track_id,
                        event_ids=created_ids[:MOTIF_MAX_EVENT_REFS],
                        relationship=deployment.operation,  # type: ignore[arg-type]
                        transform=transform_prov,
                    )
                )
                outcomes.append(
                    ThemeRecurrenceOutcome(
                        deployment_id=deployment.id,
                        operation=deployment.operation,
                        identity_score=transform_result.verification.components.combined_score,
                        status="realized",
                    )
                )
            elif deployment.operation in CREATIVE_OPERATIONS:
                # Approximate target span from seed length.
                seed_span = max(
                    (note.relative_start_tick + note.duration_ticks for note in cells),
                    default=bar_ticks,
                )
                target_end = target_start + seed_span
                target_events = _events_overlapping_span(working_events, target_start, target_end)
                target_events = sorted(
                    target_events,
                    key=lambda item: (item.start_tick, item.duration_ticks, item.id or ""),
                )
                if len(target_events) < MOTIF_MIN_EVENT_REFS:
                    raise MotifTransformError(
                        "Creative theme target region is empty",
                        code=THEME_TARGET_MISSING,
                        context={"deployment_id": deployment.id},
                    )
                # Build relative cells vs seed anchor for identity check.
                target_rel: list[RelativeMotifNote] = []
                anchor_midi = midi_pitch_number(
                    next(event.pitch for event in working_events if event.id == seed_event_ids[0])
                )
                anchor_start = target_events[0].start_tick
                for event in target_events[:MOTIF_MAX_EVENT_REFS]:
                    target_rel.append(
                        RelativeMotifNote(
                            relative_start_tick=event.start_tick - anchor_start,
                            duration_ticks=event.duration_ticks,
                            pitch_semitone_offset=midi_pitch_number(event.pitch) - anchor_midi,
                            velocity=event.velocity,
                            staff=event.staff,
                            voice=None,
                            articulations=(),
                        )
                    )
                verification = verify_motif_identity(
                    source_notes,
                    target_rel,
                    operation=deployment.operation,  # type: ignore[arg-type]
                    variation_strength=deployment.variation_strength,
                )
                if not verification.passed:
                    raise MotifTransformError(
                        "Creative theme identity below threshold",
                        code=THEME_IDENTITY_BELOW_THRESHOLD,
                        context={
                            "deployment_id": deployment.id,
                            "score": verification.components.combined_score,
                            "threshold": verification.components.threshold,
                        },
                    )
                created_ids = [event.id for event in target_events if event.id][:MOTIF_MAX_EVENT_REFS]
                occurrence_defs.append(
                    CompositionV2MotifOccurrence(
                        id=f"{frozen_plan.motif_id}-{deployment.id}",
                        track_id=track_id,
                        event_ids=created_ids,
                        relationship=deployment.operation,  # type: ignore[arg-type]
                        transform=CompositionV2MotifTransformProvenance(
                            operation=deployment.operation,  # type: ignore[arg-type]
                            variation_strength=deployment.variation_strength,
                        ),
                    )
                )
                outcomes.append(
                    ThemeRecurrenceOutcome(
                        deployment_id=deployment.id,
                        operation=deployment.operation,
                        identity_score=verification.components.combined_score,
                        status="verified",
                    )
                )
            else:
                raise MotifTransformError(
                    f"Unknown theme operation {deployment.operation}",
                    code=THEME_TRANSFORM_MISMATCH,
                )
        except (MotifTransformError, ValidationError, ValueError, StopIteration) as exc:
            code = getattr(exc, "code", THEME_TRANSFORM_MISMATCH)
            if code not in THEME_DIAGNOSTIC_CODES:
                if "out of bounds" in str(exc).lower():
                    code = THEME_TARGET_OUT_OF_BOUNDS
                elif "identity" in str(exc).lower():
                    code = THEME_IDENTITY_BELOW_THRESHOLD
                else:
                    code = THEME_TRANSFORM_MISMATCH
            context = getattr(exc, "context", None) or {"deployment_id": deployment.id}
            diagnostics.append(
                ValidationDiagnostic(
                    code=code,
                    message=str(exc)[:200],
                    severity="error",
                    context={"stage": "realize_themes", **context},
                )
            )
            outcomes.append(
                ThemeRecurrenceOutcome(
                    deployment_id=deployment.id,
                    operation=deployment.operation,
                    identity_score=None,
                    status="failed",
                    diagnostic_code=code,
                )
            )
            logger.warning(
                "Theme deployment failed",
                extra={"code": code, "deployment_id": deployment.id, "operation": deployment.operation},
            )

    updated_draft = draft.model_copy(update={"events": working_events, "id": track_id})
    motifs: tuple[CompositionV2MotifDefinition, ...] = ()
    if not any(item.severity == "error" for item in diagnostics) and len(occurrence_defs) >= 2:
        motifs = (
            CompositionV2MotifDefinition(
                id=frozen_plan.motif_id,
                label=frozen_plan.motif_label,
                occurrences=occurrence_defs,
            ),
        )

    realized = sum(1 for item in outcomes if item.status in {"realized", "verified"})
    logger.info(
        "Theme realization completed",
        extra={
            "realized_count": realized,
            "failed_count": sum(1 for item in outcomes if item.status == "failed"),
            "motif_count": len(motifs),
            "scores": [item.identity_score for item in outcomes if item.identity_score is not None],
        },
    )
    logger.debug(
        "Theme realization deployment summary",
        extra={
            "outcomes": [
                {
                    "deployment_id": item.deployment_id,
                    "operation": item.operation,
                    "status": item.status,
                    "identity_score": item.identity_score,
                    "diagnostic_code": item.diagnostic_code,
                }
                for item in outcomes
            ]
        },
    )
    return ThemeRealizationResult(
        melody_draft=updated_draft,
        theme_plan=frozen_plan,
        motifs=motifs,
        outcomes=tuple(outcomes),
        diagnostics=tuple(diagnostics),
    )


def thematic_issues_from_outcomes(
    outcomes: Sequence[ThemeRecurrenceOutcome],
) -> list[GenerationValidationIssue]:
    issues: list[GenerationValidationIssue] = []
    for item in outcomes:
        if item.status == "failed" and item.diagnostic_code:
            issues.append(
                GenerationValidationIssue(
                    code=item.diagnostic_code,
                    message=f"Thematic deployment {item.deployment_id} failed ({item.operation})",
                    context={
                        "deployment_id": item.deployment_id,
                        "operation": item.operation,
                        "identity_score": item.identity_score,
                    },
                )
            )
    return issues


def thematic_report_payload(
    outcomes: Sequence[ThemeRecurrenceOutcome],
) -> list[dict[str, Any]]:
    return [
        {
            "deployment_id": item.deployment_id,
            "operation": item.operation,
            "identity_score": item.identity_score,
            "status": item.status,
            "diagnostic_code": item.diagnostic_code,
        }
        for item in outcomes
    ]


__all__ = [
    "THEME_DIAGNOSTIC_CODES",
    "THEME_IDENTITY_BELOW_THRESHOLD",
    "THEME_PLAN_TRUNCATED",
    "THEME_SOURCE_EMPTY",
    "THEME_TARGET_MISSING",
    "THEME_TARGET_OUT_OF_BOUNDS",
    "THEME_TRANSFORM_MISMATCH",
    "ThemeRealizationResult",
    "ThemeRecurrenceOutcome",
    "assign_draft_event_ids",
    "default_theme_plan_for_form",
    "empty_theme_plan",
    "extract_seed_relative_cell",
    "realize_theme_plan",
    "relative_notes_from_theme_cell",
    "section_tick_bounds",
    "thematic_issues_from_outcomes",
    "thematic_report_payload",
    "theme_plan_prompt_projection",
    "validate_theme_plan_against_form",
]
