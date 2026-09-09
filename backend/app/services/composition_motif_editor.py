"""Atomic motif apply orchestration with span-level replacement and reconciliation."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from app.composition_schemas import (
    CompositionV2,
    CompositionV2MotifDefinition,
    CompositionV2MotifOccurrence,
    CompositionV2NoteEvent,
    CompositionV2Section,
    CompositionV2Track,
    reconcile_motifs_for_removed_event_ids,
)
from app.llm_settings import LLMProviderSettings, LLMSettings
from app.motif_schemas import (
    MotifApplyDiagnostics,
    MotifApplyError,
    MotifApplyOperation,
    MotifApplyOperationResult,
    MotifApplyRequest,
    MotifDestinationSelector,
    build_transform_provenance,
)
from app.services.composition_motif_similarity import CREATIVE_OPERATIONS
from app.services.composition_motif_transform import (
    MotifTransformError,
    MotifTransformResult,
    RelativeMotifNote,
    build_motif_destination,
    extract_relative_motif,
    transform_augmentation,
    transform_diminution,
    transform_inversion,
    transform_repeat,
    transform_sequence,
    transform_transpose,
)
from app.services.composition_region_patch import (
    CompositionRegionPatchError,
    RegionTickBounds,
    canonical_json_dumps,
    compare_preserved_regions,
    event_fully_within_region,
    event_in_region,
    reject_boundary_crossing_content,
)
from app.services.composition_timeline import compile_timeline
from app.services.composition_validator import validate_composition_integrity
from app.services.llm_music_generator import (
    NoLLMProviderConfiguredError,
    UnsupportedLLMProviderError,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MotifApplyOutcome:
    composition: CompositionV2
    result: MotifApplyOperationResult
    warnings: tuple[str, ...]
    provider: LLMProviderSettings | None


async def apply_motif_operation(request: MotifApplyRequest, settings: LLMSettings) -> MotifApplyOutcome:
    """Resolve source, transform, replace destination span, reconcile, and append occurrence."""
    from app.services.llm_motif_editor import draft_creative_motif_transform

    operation = request.operation
    composition = request.composition
    logger.info(
        "Motif apply orchestration started",
        extra={
            "motif_id": request.source.motif_id,
            "source_occurrence_id": request.source.occurrence_id,
            "destination_track_id": request.destination.track_id,
            "destination_start_bar": request.destination.start_bar,
            "operation": operation,
            "provider": request.selection.provider,
            "model": request.selection.model,
        },
    )

    motif, source_occurrence = _resolve_source(composition, request.source.motif_id, request.source.occurrence_id)
    destination_track, section = _resolve_destination_track(composition, request.destination)
    destination_start_tick = _resolve_destination_start_tick(composition, request.destination, section)

    extracted = extract_relative_motif(
        composition,
        track_id=source_occurrence.track_id,
        event_ids=source_occurrence.event_ids,
    )
    expected_span = _expected_span_ticks(extracted.notes)
    span_bounds = _span_bounds(destination_start_tick, expected_span)
    logger.debug(
        "Resolved motif destination span",
        extra={
            "start_tick": span_bounds.start_tick,
            "end_tick": span_bounds.end_tick,
            "expected_span_ticks": expected_span,
            "source_occurrence_id": source_occurrence.id,
        },
    )

    replaced_event_ids = _collect_fully_contained_event_ids(destination_track, span_bounds)
    _reject_destination_boundary_crossing(destination_track, span_bounds)
    _reject_self_overlap(composition, source_occurrence, destination_track.id, span_bounds)
    _protect_original_source(
        composition,
        motif,
        source_occurrence,
        destination_track.id,
        span_bounds,
    )

    id_seed = _build_id_seed(request, destination_start_tick)
    destination_spec = build_motif_destination(
        composition,
        track_id=destination_track.id,
        start_tick=destination_start_tick,
        anchor_midi=extracted.anchor_midi,
        allow_overlap=True,
        exclude_event_ids=tuple(replaced_event_ids),
    )

    provider: LLMProviderSettings | None = None
    extra_warnings: tuple[str, ...] = ()
    try:
        if operation in CREATIVE_OPERATIONS:
            provider = _select_creative_provider(request, settings)
            draft = await draft_creative_motif_transform(
                request=request,
                provider=provider,
                source_notes=extracted.notes,
                composition=composition,
                destination=destination_spec,
                id_seed=id_seed,
                settings=settings,
            )
            transform_result = draft.transform_result
            extra_warnings = draft.warnings
            provider = draft.provider
        else:
            transform_result = _run_mechanical_transform(
                request,
                extracted.notes,
                composition=composition,
                destination=destination_spec,
                id_seed=id_seed,
            )
    except MotifTransformError as exc:
        raise _map_transform_error(exc) from exc
    # Let InvalidLLMOutputError / LLMGenerationError propagate to the router (502).

    if transform_result.span_ticks != expected_span:
        logger.debug(
            "Motif transform span adjusted",
            extra={
                "expected_span_ticks": expected_span,
                "actual_span_ticks": transform_result.span_ticks,
            },
        )

    try:
        updated_composition, replaced_count, reconcile_warnings = _apply_destination_replacement(
            composition,
            track_id=destination_track.id,
            span_bounds=_span_bounds(destination_start_tick, transform_result.span_ticks),
            new_events=list(transform_result.events),
        )
        new_occurrence = _build_new_occurrence(
            id_seed=id_seed,
            operation=operation,
            source_occurrence=source_occurrence,
            destination_track_id=destination_track.id,
            transform_result=transform_result,
            request=request,
        )
        updated_motifs = _append_occurrence(updated_composition.motifs, motif.id, new_occurrence)
        updated_composition = updated_composition.model_copy(update={"motifs": updated_motifs})
        _verify_apply_result(
            original=composition,
            updated=updated_composition,
            span_bounds=_span_bounds(destination_start_tick, transform_result.span_ticks),
            destination_track_id=destination_track.id,
            source_occurrence=source_occurrence,
            replaced_event_ids=replaced_event_ids,
            transform_result=transform_result,
        )
    except MotifApplyError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Motif apply orchestration failed",
            extra={"error_type": type(exc).__name__, "code": "motif_internal_error"},
        )
        raise MotifApplyError(
            "motif_internal_error",
            "Unexpected motif apply failure",
            http_status=500,
        ) from exc

    created_event_ids = [event_id for event in transform_result.events if (event_id := event.id)]
    components = transform_result.verification.components
    strength = request.variation_strength if operation in CREATIVE_OPERATIONS else None
    transform = build_transform_provenance(
        operation,
        source_occurrence_id=source_occurrence.id,
        parameters=request.parameters,
        variation_strength=strength,
    )
    diagnostics = MotifApplyDiagnostics(
        source_event_count=len(source_occurrence.event_ids),
        created_event_count=len(created_event_ids),
        replaced_event_count=replaced_count,
        destination_span_ticks=transform_result.span_ticks,
        identity_threshold=components.threshold,
        exact_transform_verified=components.exact_transform_verified,
        warning_codes=list(transform_result.warning_codes),
    )
    result = MotifApplyOperationResult(
        motif_id=motif.id,
        source_occurrence_id=source_occurrence.id,
        destination_section_id=section.id,
        destination_track_id=destination_track.id,
        destination_start_bar=request.destination.start_bar,
        destination_start_tick=destination_start_tick,
        created_event_ids=created_event_ids,
        new_occurrence_id=new_occurrence.id,
        relationship=operation,
        identity_score=components.combined_score,
        provider=provider.provider if provider else None,
        model=(request.selection.model or provider.model) if provider else None,
        transform=transform,
        diagnostics=diagnostics,
    )
    warnings = (*extra_warnings, *transform_result.warning_codes, *reconcile_warnings)
    # Preserve order while dropping exact duplicates.
    deduped: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        if warning in seen:
            continue
        seen.add(warning)
        deduped.append(warning)
    logger.info(
        "Motif apply orchestration completed",
        extra={
            "motif_id": motif.id,
            "source_occurrence_id": source_occurrence.id,
            "new_occurrence_id": new_occurrence.id,
            "operation": operation,
            "created_event_count": len(created_event_ids),
            "replaced_event_count": replaced_count,
            "reconciled_warning_count": len(reconcile_warnings),
            "identity_score": components.combined_score,
            "provider": result.provider,
            "model": result.model,
        },
    )
    return MotifApplyOutcome(
        composition=updated_composition,
        result=result,
        warnings=tuple(deduped),
        provider=provider,
    )


def _select_creative_provider(request: MotifApplyRequest, settings: LLMSettings) -> LLMProviderSettings:
    if not settings.providers:
        logger.warning(
            "Creative motif apply rejected: no configured providers",
            extra={"operation": request.operation, "code": "motif_creative_provider_required"},
        )
        raise NoLLMProviderConfiguredError("No LLM providers are configured")

    requested_provider = request.selection.provider or settings.default_provider
    requested_model = request.selection.model
    for provider in settings.providers:
        if provider.provider == requested_provider:
            if requested_model and requested_model != provider.model:
                return LLMProviderSettings(
                    provider=provider.provider,
                    model=requested_model,
                    api_key=provider.api_key,
                    base_url=provider.base_url,
                    is_default=provider.is_default,
                )
            return provider

    logger.warning(
        "Creative motif apply rejected: unsupported provider",
        extra={"operation": request.operation, "provider": requested_provider},
    )
    raise UnsupportedLLMProviderError(f"Unsupported or unavailable LLM provider: {requested_provider}")


def _resolve_source(
    composition: CompositionV2,
    motif_id: str,
    occurrence_id: str,
) -> tuple[CompositionV2MotifDefinition, CompositionV2MotifOccurrence]:
    motif = next((item for item in composition.motifs if item.id == motif_id), None)
    if motif is None:
        logger.warning(
            "Motif source unresolved",
            extra={"code": "motif_source_unresolved", "motif_id": motif_id},
        )
        raise MotifApplyError(
            "motif_source_unresolved",
            "Motif definition not found in composition",
            details={"motif_id": motif_id},
        )
    occurrence = next((item for item in motif.occurrences if item.id == occurrence_id), None)
    if occurrence is None:
        logger.warning(
            "Motif source occurrence unresolved",
            extra={
                "code": "motif_source_unresolved",
                "motif_id": motif_id,
                "occurrence_id": occurrence_id,
            },
        )
        raise MotifApplyError(
            "motif_source_unresolved",
            "Source occurrence not found in motif definition",
            details={"motif_id": motif_id, "occurrence_id": occurrence_id},
        )
    return motif, occurrence


def _resolve_destination_track(
    composition: CompositionV2,
    destination: MotifDestinationSelector,
) -> tuple[CompositionV2Track, CompositionV2Section]:
    section = _resolve_section(composition, destination)
    track = next((item for item in composition.tracks if item.id == destination.track_id), None)
    if track is None:
        raise MotifApplyError(
            "motif_destination_unresolved",
            "Destination track not found",
            details={"track_id": destination.track_id},
        )
    if track.is_drum or track.role in {"drums", "percussion"}:
        raise MotifApplyError(
            "motif_destination_invalid_track",
            "Destination track must be pitched and non-percussion",
            details={"track_id": track.id, "role": track.role},
        )
    return track, section


def _resolve_section(
    composition: CompositionV2,
    destination: MotifDestinationSelector,
) -> CompositionV2Section:
    if destination.section_id is not None:
        section = next(
            (item for item in composition.sections if item.id == destination.section_id),
            None,
        )
        if section is None:
            raise MotifApplyError(
                "motif_destination_unresolved",
                "Destination section_id not found",
                details={"section_id": destination.section_id},
            )
        section_end_bar = section.start_bar + section.bar_count - 1
        if not (section.start_bar <= destination.start_bar <= section_end_bar):
            raise MotifApplyError(
                "motif_destination_out_of_bounds",
                "destination.start_bar is outside the selected section",
                details={
                    "section_id": destination.section_id,
                    "start_bar": destination.start_bar,
                    "section_start_bar": section.start_bar,
                    "section_end_bar": section_end_bar,
                },
            )
        return section

    for section in composition.sections:
        section_end_bar = section.start_bar + section.bar_count - 1
        if section.start_bar <= destination.start_bar <= section_end_bar:
            return section

    raise MotifApplyError(
        "motif_destination_out_of_bounds",
        "destination.start_bar does not fall within any section",
        details={"start_bar": destination.start_bar},
    )


def _resolve_destination_start_tick(
    composition: CompositionV2,
    destination: MotifDestinationSelector,
    section: CompositionV2Section,
) -> int:
    timeline = compile_timeline(composition)
    bar_start = timeline.bar_start_tick(destination.start_bar)
    bar_end = timeline.bar_end_tick(destination.start_bar)
    if destination.start_tick is None:
        resolved = bar_start
    else:
        resolved = destination.start_tick
        if resolved < bar_start or resolved >= bar_end:
            raise MotifApplyError(
                "motif_destination_out_of_bounds",
                "destination.start_tick must align with destination.start_bar",
                details={
                    "start_bar": destination.start_bar,
                    "start_tick": resolved,
                    "bar_start_tick": bar_start,
                    "bar_end_tick_exclusive": bar_end,
                },
            )

    section_end_tick = section.start_tick + section.duration_ticks
    if resolved < section.start_tick or resolved >= section_end_tick:
        raise MotifApplyError(
            "motif_destination_out_of_bounds",
            "Destination start tick is outside the resolved section",
            details={
                "start_tick": resolved,
                "section_start_tick": section.start_tick,
                "section_end_tick_exclusive": section_end_tick,
            },
        )
    if resolved < 0 or resolved >= composition.duration_ticks:
        raise MotifApplyError(
            "motif_destination_out_of_bounds",
            "Destination start tick exceeds composition duration",
            details={"start_tick": resolved, "duration_ticks": composition.duration_ticks},
        )
    return resolved


def _expected_span_ticks(notes: tuple[RelativeMotifNote, ...]) -> int:
    return max(note.relative_start_tick + note.duration_ticks for note in notes)


def _span_bounds(start_tick: int, span_ticks: int) -> RegionTickBounds:
    return RegionTickBounds(
        start_bar=0,
        end_bar=0,
        start_tick=start_tick,
        end_tick=start_tick + span_ticks,
        bar_ticks=span_ticks,
    )


def _collect_fully_contained_event_ids(
    track: CompositionV2Track,
    bounds: RegionTickBounds,
) -> set[str]:
    contained: set[str] = set()
    for event in track.events:
        if event_in_region(event, bounds) and event_fully_within_region(event, bounds):
            if event.id:
                contained.add(event.id)
    logger.debug(
        "Collected fully contained destination events",
        extra={
            "track_id": track.id,
            "contained_event_count": len(contained),
            "start_tick": bounds.start_tick,
            "end_tick": bounds.end_tick,
        },
    )
    return contained


def _reject_destination_boundary_crossing(
    track: CompositionV2Track,
    bounds: RegionTickBounds,
) -> None:
    class _SingleTrackComposition:
        schema_version = "composition.v2"
        tracks = [track]

    try:
        reject_boundary_crossing_content(_SingleTrackComposition(), bounds, [track.id])
    except CompositionRegionPatchError as exc:
        if exc.code == "boundary_crossing_note":
            logger.warning(
                "Rejected motif destination with boundary-crossing note",
                extra={"code": "motif_boundary_crossing_note", "track_id": track.id},
            )
            raise MotifApplyError(
                "motif_boundary_crossing_note",
                "Destination span intersects a note that continues outside the span",
                details={"track_id": track.id},
            ) from exc
        if exc.code == "boundary_crossing_tie_chain":
            logger.warning(
                "Rejected motif destination with boundary-crossing tie chain",
                extra={"code": "motif_boundary_crossing_tie_chain", "track_id": track.id},
            )
            raise MotifApplyError(
                "motif_boundary_crossing_tie_chain",
                "Destination span intersects a tie chain that continues outside the span",
                details={"track_id": track.id},
            ) from exc
        raise


def _occurrence_tick_span(
    composition: CompositionV2,
    occurrence: CompositionV2MotifOccurrence,
) -> tuple[int, int]:
    track = next(item for item in composition.tracks if item.id == occurrence.track_id)
    indexed = {event.id: event for event in track.events if event.id}
    starts: list[int] = []
    ends: list[int] = []
    for event_id in occurrence.event_ids:
        event = indexed.get(event_id)
        if event is None:
            continue
        starts.append(event.start_tick)
        ends.append(event.start_tick + event.duration_ticks)
    if not starts:
        return 0, 0
    return min(starts), max(ends)


def _spans_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a < end_b and start_b < end_a


def _reject_self_overlap(
    composition: CompositionV2,
    source_occurrence: CompositionV2MotifOccurrence,
    destination_track_id: str,
    span_bounds: RegionTickBounds,
) -> None:
    if source_occurrence.track_id != destination_track_id:
        return
    source_start, source_end = _occurrence_tick_span(composition, source_occurrence)
    if _spans_overlap(source_start, source_end, span_bounds.start_tick, span_bounds.end_tick):
        logger.warning(
            "Rejected motif self-overlap",
            extra={
                "code": "motif_overlap_rejected",
                "source_occurrence_id": source_occurrence.id,
                "destination_track_id": destination_track_id,
            },
        )
        raise MotifApplyError(
            "motif_overlap_rejected",
            "Destination overlaps the source occurrence span",
            details={
                "source_occurrence_id": source_occurrence.id,
                "destination_start_tick": span_bounds.start_tick,
                "destination_end_tick": span_bounds.end_tick,
            },
        )


def _protect_original_source(
    composition: CompositionV2,
    motif: CompositionV2MotifDefinition,
    source_occurrence: CompositionV2MotifOccurrence,
    destination_track_id: str,
    span_bounds: RegionTickBounds,
) -> None:
    original = next((item for item in motif.occurrences if item.relationship == "original"), None)
    if original is None or original.id == source_occurrence.id:
        return
    if original.track_id != destination_track_id:
        return
    original_start, original_end = _occurrence_tick_span(composition, original)
    if _spans_overlap(original_start, original_end, span_bounds.start_tick, span_bounds.end_tick):
        logger.warning(
            "Rejected motif apply that would invalidate original source",
            extra={
                "code": "motif_source_protection",
                "motif_id": motif.id,
                "original_occurrence_id": original.id,
            },
        )
        raise MotifApplyError(
            "motif_source_protection",
            "Destination would replace events from the original motif occurrence",
            details={
                "motif_id": motif.id,
                "original_occurrence_id": original.id,
            },
        )


def _run_mechanical_transform(
    request: MotifApplyRequest,
    source,
    *,
    composition: CompositionV2,
    destination,
    id_seed: str,
) -> MotifTransformResult:
    operation: MotifApplyOperation = request.operation
    params = request.parameters
    if operation == "repeat":
        return transform_repeat(source, composition=composition, destination=destination, id_seed=id_seed)
    if operation == "transpose":
        assert params.transpose_semitones is not None
        return transform_transpose(
            source,
            semitones=params.transpose_semitones,
            composition=composition,
            destination=destination,
            id_seed=id_seed,
        )
    if operation == "inversion":
        return transform_inversion(
            source,
            axis_pitch=params.inversion_axis_pitch,
            composition=composition,
            destination=destination,
            id_seed=id_seed,
        )
    if operation == "augmentation":
        assert params.time_scale_numerator is not None and params.time_scale_denominator is not None
        return transform_augmentation(
            source,
            numerator=params.time_scale_numerator,
            denominator=params.time_scale_denominator,
            composition=composition,
            destination=destination,
            id_seed=id_seed,
        )
    if operation == "diminution":
        assert params.time_scale_numerator is not None and params.time_scale_denominator is not None
        return transform_diminution(
            source,
            numerator=params.time_scale_numerator,
            denominator=params.time_scale_denominator,
            composition=composition,
            destination=destination,
            id_seed=id_seed,
        )
    if operation == "sequence":
        assert (
            params.sequence_steps is not None
            and params.sequence_interval_semitones is not None
            and params.sequence_step_ticks is not None
        )
        return transform_sequence(
            source,
            steps=params.sequence_steps,
            interval_semitones=params.sequence_interval_semitones,
            step_ticks=params.sequence_step_ticks,
            composition=composition,
            destination=destination,
            id_seed=id_seed,
        )
    raise MotifApplyError(
        "motif_invalid_operation",
        f"Mechanical handler missing for operation {operation}",
        details={"operation": operation},
    )


def _apply_destination_replacement(
    composition: CompositionV2,
    *,
    track_id: str,
    span_bounds: RegionTickBounds,
    new_events: list[CompositionV2NoteEvent],
) -> tuple[CompositionV2, int, tuple[str, ...]]:
    updated_tracks: list[CompositionV2Track] = []
    replaced_event_ids: list[str] = []
    replaced_count = 0

    for track in composition.tracks:
        if track.id != track_id:
            updated_tracks.append(track)
            continue

        kept_events: list[CompositionV2NoteEvent] = []
        for event in track.events:
            if event_in_region(event, span_bounds):
                if not event_fully_within_region(event, span_bounds):
                    logger.warning(
                        "Rejected partial event overlap during motif replacement",
                        extra={
                            "code": "motif_boundary_crossing_note",
                            "track_id": track.id,
                            "event_id": event.id,
                        },
                    )
                    raise MotifApplyError(
                        "motif_boundary_crossing_note",
                        "Destination span intersects a note that continues outside the span",
                        details={"track_id": track.id, "event_id": event.id},
                    )
                replaced_count += 1
                if event.id:
                    replaced_event_ids.append(event.id)
                continue
            kept_events.append(event)

        merged = kept_events + new_events
        merged.sort(key=lambda item: (item.start_tick, item.duration_ticks, item.id or ""))
        updated_tracks.append(track.model_copy(update={"events": merged}))

    reconcile = reconcile_motifs_for_removed_event_ids(composition.motifs, set(replaced_event_ids))
    for warning in reconcile.warnings:
        logger.warning(
            "Motif reference reconciled after destination replacement",
            extra={
                "code": warning.code,
                "motif_id": warning.motif_id,
                "occurrence_id": warning.occurrence_id,
            },
        )
    logger.info(
        "Motif destination replacement applied",
        extra={
            "replaced_event_count": replaced_count,
            "reconciled_warning_count": len(reconcile.warnings),
            "motif_count_after": len(reconcile.motifs),
        },
    )
    updated = composition.model_copy(update={"tracks": updated_tracks, "motifs": reconcile.motifs})
    warnings = tuple(item.code for item in reconcile.warnings)
    return updated, replaced_count, warnings


def _build_new_occurrence(
    *,
    id_seed: str,
    operation: MotifApplyOperation,
    source_occurrence: CompositionV2MotifOccurrence,
    destination_track_id: str,
    transform_result: MotifTransformResult,
    request: MotifApplyRequest,
) -> CompositionV2MotifOccurrence:
    new_occurrence_id = _allocate_occurrence_id(id_seed)
    transform = build_transform_provenance(
        operation,
        source_occurrence_id=source_occurrence.id,
        parameters=request.parameters,
        variation_strength=request.variation_strength if request.operation in CREATIVE_OPERATIONS else None,
    )
    return CompositionV2MotifOccurrence(
        id=new_occurrence_id,
        track_id=destination_track_id,
        event_ids=[event_id for event in transform_result.events if (event_id := event.id)],
        relationship=operation,  # type: ignore[arg-type]
        transform=transform,
    )


def _append_occurrence(
    motifs: list[CompositionV2MotifDefinition],
    motif_id: str,
    occurrence: CompositionV2MotifOccurrence,
) -> list[CompositionV2MotifDefinition]:
    updated: list[CompositionV2MotifDefinition] = []
    for motif in motifs:
        if motif.id != motif_id:
            updated.append(motif)
            continue
        updated.append(motif.model_copy(update={"occurrences": [*motif.occurrences, occurrence]}))
    return updated


def _verify_apply_result(
    *,
    original: CompositionV2,
    updated: CompositionV2,
    span_bounds: RegionTickBounds,
    destination_track_id: str,
    source_occurrence: CompositionV2MotifOccurrence,
    replaced_event_ids: set[str],
    transform_result: MotifTransformResult,
) -> None:
    CompositionV2.model_validate(updated.model_dump(mode="python"))
    integrity = validate_composition_integrity(updated, profile="canonical")
    if not integrity.ok:
        logger.error(
            "Motif apply failed canonical integrity validation",
            extra={
                "code": "motif_internal_error",
                "error_count": len(integrity.errors),
            },
        )
        raise MotifApplyError(
            "motif_internal_error",
            "Applied composition failed integrity validation",
            details={"error_codes": [item.code for item in integrity.errors[:8]]},
            http_status=500,
        )

    comparison = compare_preserved_regions(
        original,
        updated,
        span_bounds,
        [destination_track_id],
    )
    if not comparison["ok"]:
        logger.error(
            "Motif apply mutated preserved destination metadata",
            extra={
                "code": "motif_internal_error",
                "mismatched_track_count": len(comparison["mismatched_tracks"]),
            },
        )
        raise MotifApplyError(
            "motif_internal_error",
            "Motif apply mutated events or metadata outside the destination span",
            http_status=500,
        )

    if source_occurrence.track_id == destination_track_id:
        source_track = next(item for item in original.tracks if item.id == source_occurrence.track_id)
        updated_track = next(item for item in updated.tracks if item.id == source_occurrence.track_id)
        for event_id in source_occurrence.event_ids:
            if event_id in replaced_event_ids:
                continue
            original_event = next(item for item in source_track.events if item.id == event_id)
            updated_event = next(item for item in updated_track.events if item.id == event_id)
            if canonical_json_dumps(original_event.model_dump(mode="json")) != canonical_json_dumps(
                updated_event.model_dump(mode="json")
            ):
                logger.error(
                    "Motif apply mutated source occurrence events",
                    extra={"code": "motif_internal_error", "event_id": event_id},
                )
                raise MotifApplyError(
                    "motif_internal_error",
                    "Source occurrence events were mutated outside the destination span",
                    http_status=500,
                )

    if not transform_result.verification.passed:
        logger.error(
            "Motif apply identity verification failed",
            extra={
                "code": "motif_identity_failed",
                "exact_transform_verified": transform_result.verification.components.exact_transform_verified,
                "identity_score": transform_result.verification.components.combined_score,
                "identity_threshold": transform_result.verification.components.threshold,
            },
        )
        raise MotifApplyError(
            "motif_identity_failed",
            "Transformed material failed identity verification",
        )

    _verify_no_symbolic_motif_placeholders(updated)


def _verify_no_symbolic_motif_placeholders(composition: CompositionV2) -> None:
    for motif in composition.motifs:
        dumped = motif.model_dump(mode="json")
        forbidden = ("pitch", "notes", "events", "cells")
        for key in forbidden:
            if key in dumped:
                logger.error(
                    "Symbolic motif placeholder detected",
                    extra={"code": "motif_internal_error", "motif_id": motif.id, "field": key},
                )
                raise MotifApplyError(
                    "motif_internal_error",
                    "Motif metadata must not contain symbolic note payloads",
                    http_status=500,
                )
        for occurrence in motif.occurrences:
            occ_dumped = occurrence.model_dump(mode="json")
            for key in forbidden:
                if key in occ_dumped:
                    logger.error(
                        "Symbolic motif occurrence placeholder detected",
                        extra={
                            "code": "motif_internal_error",
                            "motif_id": motif.id,
                            "occurrence_id": occurrence.id,
                            "field": key,
                        },
                    )
                    raise MotifApplyError(
                        "motif_internal_error",
                        "Motif occurrence metadata must not contain symbolic note payloads",
                        http_status=500,
                    )


def _build_id_seed(request: MotifApplyRequest, destination_start_tick: int) -> str:
    return "|".join(
        [
            request.source.motif_id,
            request.source.occurrence_id,
            request.destination.track_id,
            str(destination_start_tick),
            request.operation,
        ]
    )


def _allocate_occurrence_id(id_seed: str) -> str:
    digest = hashlib.sha256(f"occ|{id_seed}".encode("utf-8")).hexdigest()[:12]
    return f"occ-{digest}"


def _map_transform_error(exc: MotifTransformError) -> MotifApplyError:
    code = exc.code
    allowed = {
        "motif_source_unresolved",
        "motif_source_event_count",
        "motif_source_event_order",
        "motif_source_incomplete_tie",
        "motif_source_invalid_track",
        "motif_destination_unresolved",
        "motif_destination_out_of_bounds",
        "motif_invalid_parameters",
        "motif_overlap_rejected",
        "motif_identity_failed",
        "motif_pitch_out_of_range",
        "motif_proposal_mismatch",
        "motif_transform_empty",
        "motif_source_empty",
    }
    if code not in allowed:
        code = "motif_internal_error"  # type: ignore[assignment]
    logger.warning(
        "Motif transform rejected",
        extra={"code": exc.code, "mapped_code": code},
    )
    return MotifApplyError(
        code,  # type: ignore[arg-type]
        str(exc),
        details={key: value for key, value in exc.context.items() if isinstance(value, (str, int, float, bool))},
    )


__all__ = ["MotifApplyOutcome", "apply_motif_operation"]
