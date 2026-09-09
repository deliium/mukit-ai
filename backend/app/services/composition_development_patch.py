"""Deterministic append/variation realization for composition development drafts.

Providers supply relative drafts only. This module owns absolute ticks, IDs,
section/timeline extension, motif reconciliation, and exact preservation gates.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from app.composition_development_schemas import (
    CompositionDevelopmentDraft,
    CompositionDevelopmentError,
    CompositionDevelopmentPreviewRequest,
    DevelopmentHarmonyChangeSummary,
    DevelopmentMotifChangeSummary,
    DevelopmentPreservationAssertion,
    DevelopmentResolvedRange,
    DevelopmentSectionChangeSummary,
    DevelopmentTrackChangeSummary,
)
from app.composition_schemas import (
    CompositionV2,
    CompositionV2HarmonyItem,
    CompositionV2MotifDefinition,
    CompositionV2MotifOccurrence,
    CompositionV2NoteEvent,
    CompositionV2Section,
    CompositionV2Track,
    bar_duration_ticks,
    reconcile_motifs_for_removed_event_ids,
)
from app.services.composition_development_context import (
    DevelopmentSourceContext,
    build_development_source_context,
)
from app.services.composition_development_identity import evaluate_development_identity
from app.services.composition_region_patch import (
    CompositionRegionPatchError,
    RegionTickBounds,
    reject_boundary_crossing_content,
)
from app.services.composition_timeline import compile_timeline
from app.services.composition_validator import validate_composition_integrity


logger = logging.getLogger(__name__)

DEVELOPMENT_PATCH_VERSION = "composition.development.patch.v1"


@dataclass
class DevelopmentRealizationResult:
    composition: CompositionV2
    source_range: DevelopmentResolvedRange
    output_range: DevelopmentResolvedRange
    section_changes: list[DevelopmentSectionChangeSummary] = field(default_factory=list)
    harmony_changes: list[DevelopmentHarmonyChangeSummary] = field(default_factory=list)
    motif_changes: list[DevelopmentMotifChangeSummary] = field(default_factory=list)
    track_changes: list[DevelopmentTrackChangeSummary] = field(default_factory=list)
    preservation: list[DevelopmentPreservationAssertion] = field(default_factory=list)
    identity_diagnostics: list[Any] = field(default_factory=list)
    warning_codes: list[str] = field(default_factory=list)
    created_event_count: int = 0
    id_collision_count: int = 0


def _assert(
    kind: str,
    satisfied: bool,
    detail: str,
    *,
    required: bool = True,
    track_id: str | None = None,
) -> DevelopmentPreservationAssertion:
    return DevelopmentPreservationAssertion(
        kind=kind,  # type: ignore[arg-type]
        satisfied=satisfied,
        required=required,
        detail=detail,
        track_id=track_id,
    )


def _collect_used_ids(composition: CompositionV2) -> set[str]:
    used: set[str] = set()
    for track in composition.tracks:
        used.add(track.id)
        for event in track.events:
            if event.id:
                used.add(event.id)
    for section in composition.sections:
        if section.id:
            used.add(section.id)
    for motif in composition.motifs:
        used.add(motif.id)
        for occurrence in motif.occurrences:
            used.add(occurrence.id)
    return used


def _allocate_id(prefix: str, parts: list[Any], used: set[str]) -> tuple[str, bool]:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:10]
    base = f"{prefix}-{digest}"
    candidate = base
    collided = False
    suffix = 2
    while candidate in used:
        collided = True
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate, collided


def _compute_append_duration_ticks(
    composition: CompositionV2,
    *,
    output_bars: int,
    draft: CompositionDevelopmentDraft,
) -> tuple[int, list[int]]:
    """Extend from the active ending meter for ``output_bars`` complete bars.

    Returns ``(new_duration_ticks, bar_boundaries)`` where boundaries length is
    ``bar_count + output_bars + 1`` and the final value equals new duration.
    """
    timeline = compile_timeline(composition)
    boundaries = list(timeline.bar_boundaries)
    active_meter = timeline.active_time_signature(max(0, composition.duration_ticks - 1))
    meter_by_relative_tick = {
        change.relative_tick: change.time_signature
        for change in draft.timeline_changes
        if change.time_signature is not None
    }

    for bar_index in range(output_bars):
        start = boundaries[-1]
        relative_start = start - composition.duration_ticks
        if relative_start in meter_by_relative_tick:
            active_meter = meter_by_relative_tick[relative_start]
        bar_ticks = bar_duration_ticks(active_meter, composition.ticks_per_quarter)
        boundaries.append(start + bar_ticks)

    new_duration = boundaries[-1]
    if len(boundaries) != composition.bar_count + output_bars + 1:
        raise CompositionDevelopmentError(
            "development_draft_invalid",
            "Append bar boundary extension produced unexpected length",
            details={
                "expected_boundary_count": composition.bar_count + output_bars + 1,
                "actual_boundary_count": len(boundaries),
            },
        )
    return new_duration, boundaries


def _reject_seam_crossing_for_append(composition: CompositionV2) -> None:
    """Reject notes/ties that straddle the immutable append seam."""
    seam = composition.duration_ticks
    bounds = RegionTickBounds(
        start_bar=composition.bar_count,
        end_bar=composition.bar_count,
        start_tick=seam,
        end_tick=seam,
        bar_ticks=0,
    )
    # Reuse region helper semantics by checking events that start before seam and end after.
    for track in composition.tracks:
        for event in track.events:
            end = event.start_tick + event.duration_ticks
            if event.start_tick < seam < end:
                raise CompositionDevelopmentError(
                    "development_seam_crossing",
                    "Source note crosses the immutable append seam",
                    details={
                        "track_id": track.id,
                        "start_tick": event.start_tick,
                        "duration_ticks": event.duration_ticks,
                        "seam_tick": seam,
                    },
                )
        groups: dict[str, list[CompositionV2NoteEvent]] = {}
        for event in track.events:
            if event.tie is None:
                continue
            groups.setdefault(event.tie.group_id, []).append(event)
        for group_id, members in groups.items():
            starts = [event.start_tick for event in members]
            ends = [event.start_tick + event.duration_ticks for event in members]
            if min(starts) < seam < max(ends):
                raise CompositionDevelopmentError(
                    "development_seam_crossing",
                    "Source tie chain crosses the immutable append seam",
                    details={"track_id": track.id, "group_id": group_id, "seam_tick": seam},
                )
    # Silence unused local for type checkers when bar_ticks unused.
    _ = bounds


def _validate_draft_against_scope(
    *,
    composition: CompositionV2,
    draft: CompositionDevelopmentDraft,
    output_start_tick: int,
    output_duration_ticks: int,
    allow_modulation: bool,
) -> None:
    source_ids = [track.id for track in composition.tracks]
    draft_ids = [track.track_id for track in draft.tracks]
    if draft_ids != source_ids:
        raise CompositionDevelopmentError(
            "development_track_topology",
            "Draft track ids/order must match the source composition exactly",
            details={"source_track_count": len(source_ids), "draft_track_count": len(draft_ids)},
        )

    for track in draft.tracks:
        for event in track.events:
            end = event.relative_start_tick + event.duration_ticks
            if end > output_duration_ticks:
                raise CompositionDevelopmentError(
                    "development_draft_invalid",
                    "Draft event overflows the generated output scope",
                    details={
                        "track_id": track.track_id,
                        "relative_start_tick": event.relative_start_tick,
                        "duration_ticks": event.duration_ticks,
                        "output_duration_ticks": output_duration_ticks,
                    },
                )

    for item in draft.harmony:
        end = item.relative_start_tick + item.duration_ticks
        if end > output_duration_ticks:
            raise CompositionDevelopmentError(
                "development_draft_invalid",
                "Draft harmony overflows the generated output scope",
                details={
                    "relative_start_tick": item.relative_start_tick,
                    "duration_ticks": item.duration_ticks,
                    "output_duration_ticks": output_duration_ticks,
                },
            )

    if not allow_modulation:
        for change in draft.timeline_changes:
            if change.key is not None:
                raise CompositionDevelopmentError(
                    "development_draft_invalid",
                    "Key modulation is not allowed unless allow_modulation is true",
                    details={"relative_tick": change.relative_tick},
                )


def _documents_equal_prefix(source: dict[str, Any], result: dict[str, Any], *, seam_tick: int) -> bool:
    """Exact structural equality for all fields that must remain an append prefix."""
    for key in (
        "schema_version",
        "tempo",
        "key",
        "time_signature",
        "ticks_per_quarter",
    ):
        if source.get(key) != result.get(key):
            return False

    # Existing sections unchanged (result may append more).
    source_sections = source.get("sections") or []
    result_sections = result.get("sections") or []
    if result_sections[: len(source_sections)] != source_sections:
        return False

    for key in ("tempo_changes", "time_signature_changes", "key_changes", "markers"):
        source_items = source.get(key) or []
        result_items = result.get(key) or []
        if result_items[: len(source_items)] != source_items:
            return False

    source_harmony = source.get("harmony") or []
    result_harmony = result.get("harmony") or []
    if result_harmony[: len(source_harmony)] != source_harmony:
        return False

    source_tracks = source.get("tracks") or []
    result_tracks = result.get("tracks") or []
    if len(source_tracks) != len(result_tracks):
        return False
    for source_track, result_track in zip(source_tracks, result_tracks, strict=True):
        for meta in (
            "id",
            "name",
            "instrument",
            "role",
            "midi_program",
            "channel",
            "is_drum",
            "volume",
            "pan",
            "expression",
            "staff",
            "dynamic_marks",
            "sustain_pedals",
            "automation",
        ):
            if source_track.get(meta) != result_track.get(meta):
                return False
        source_events = source_track.get("events") or []
        result_events = result_track.get("events") or []
        if result_events[: len(source_events)] != source_events:
            return False
        for event in result_events[len(source_events) :]:
            if int(event.get("start_tick", -1)) < seam_tick:
                return False
    return True


def _outside_range_equal(
    source: CompositionV2,
    result: CompositionV2,
    *,
    start_tick: int,
    end_tick: int,
) -> bool:
    source_dump = source.model_dump(mode="json")
    result_dump = result.model_dump(mode="json")
    for key in (
        "schema_version",
        "tempo",
        "key",
        "time_signature",
        "ticks_per_quarter",
        "duration_ticks",
        "bar_count",
        "sections",
        "tempo_changes",
        "time_signature_changes",
        "key_changes",
        "markers",
    ):
        if source_dump.get(key) != result_dump.get(key):
            return False

    def _outside_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept = []
        for event in events:
            event_start = int(event["start_tick"])
            event_end = event_start + int(event["duration_ticks"])
            if event_end <= start_tick or event_start >= end_tick:
                kept.append(event)
        return kept

    def _outside_harmony(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept = []
        for item in items:
            item_start = int(item["start_tick"])
            item_end = item_start + int(item["duration_ticks"])
            if item_end <= start_tick or item_start >= end_tick:
                kept.append(item)
        return kept

    if _outside_harmony(source_dump.get("harmony") or []) != _outside_harmony(result_dump.get("harmony") or []):
        return False

    for source_track, result_track in zip(source_dump["tracks"], result_dump["tracks"], strict=True):
        for meta in (
            "id",
            "name",
            "instrument",
            "role",
            "midi_program",
            "channel",
            "is_drum",
            "volume",
            "pan",
            "expression",
            "staff",
            "dynamic_marks",
            "sustain_pedals",
            "automation",
        ):
            if source_track.get(meta) != result_track.get(meta):
                return False
        if _outside_events(source_track.get("events") or []) != _outside_events(result_track.get("events") or []):
            return False
    return True


def realize_development_draft(
    request: CompositionDevelopmentPreviewRequest,
    draft: CompositionDevelopmentDraft,
    *,
    context: DevelopmentSourceContext | None = None,
    candidate_ordinal: int = 1,
) -> DevelopmentRealizationResult:
    """Realize one relative draft into a strict CompositionV2 with preservation gates."""
    source = request.composition
    source_snapshot = source.model_dump(mode="json")
    ctx = context or build_development_source_context(request)
    scope = ctx.scope

    identity = evaluate_development_identity(
        context=ctx,
        draft=draft,
        strength=request.variation_strength,
        development_intent=request.development_intent,
    )
    if not identity.passed:
        raise CompositionDevelopmentError(
            "development_identity_failed",
            "Candidate failed required identity or seam continuity checks",
            details={
                "error_codes": list(identity.error_codes),
                "warning_codes": list(identity.warning_codes),
                "diagnostic_codes": [item.code for item in identity.diagnostics],
            },
        )

    used_ids = _collect_used_ids(source)
    id_collision_count = 0
    created_event_count = 0
    track_changes: list[DevelopmentTrackChangeSummary] = []
    section_changes: list[DevelopmentSectionChangeSummary] = []
    harmony_changes: list[DevelopmentHarmonyChangeSummary] = []
    motif_changes: list[DevelopmentMotifChangeSummary] = []
    preservation: list[DevelopmentPreservationAssertion] = []
    warning_codes = list(identity.warning_codes)

    if request.operation in {"continue", "add_section"}:
        _reject_seam_crossing_for_append(source)
        new_duration, boundaries = _compute_append_duration_ticks(
            source,
            output_bars=scope.output_bars,
            draft=draft,
        )
        output_start_tick = source.duration_ticks
        output_end_tick = new_duration
        output_duration = new_duration - source.duration_ticks
        _validate_draft_against_scope(
            composition=source,
            draft=draft,
            output_start_tick=output_start_tick,
            output_duration_ticks=output_duration,
            allow_modulation=request.allow_modulation,
        )

        new_tracks: list[CompositionV2Track] = []
        draft_by_id = {track.track_id: track for track in draft.tracks}
        draft_id_map: dict[str, str] = {}

        for track in source.tracks:
            draft_track = draft_by_id[track.id]
            before_count = len(track.events)
            new_events = list(track.events)
            for index, note in enumerate(draft_track.events):
                event_id, collided = _allocate_id(
                    "dev",
                    [candidate_ordinal, track.id, note.relative_start_tick, note.pitch, index],
                    used_ids,
                )
                id_collision_count += int(collided)
                if note.draft_event_id:
                    draft_id_map[note.draft_event_id] = event_id
                new_events.append(
                    CompositionV2NoteEvent(
                        id=event_id,
                        pitch=note.pitch,
                        start_tick=output_start_tick + note.relative_start_tick,
                        duration_ticks=note.duration_ticks,
                        velocity=note.velocity,
                        staff=note.staff,
                        voice=note.voice,
                        articulations=list(note.articulations),
                    )
                )
                created_event_count += 1
            new_tracks.append(track.model_copy(update={"events": new_events}))
            track_changes.append(
                DevelopmentTrackChangeSummary(
                    track_id=track.id,
                    role=track.role,
                    events_before=before_count,
                    events_after=len(new_events),
                    events_created=len(new_events) - before_count,
                    events_removed=0,
                )
            )

        new_sections = list(source.sections)
        section_type = request.target_section_type or (
            scope.source_section.section_type if scope.source_section else "verse"
        )
        section_id, collided = _allocate_id(
            "sec",
            [candidate_ordinal, section_type, scope.output_start_bar, scope.output_bars],
            used_ids,
        )
        id_collision_count += int(collided)
        new_section = CompositionV2Section(
            id=section_id,
            type=section_type,
            label=request.target_section_label or draft.section_label,
            start_bar=scope.output_start_bar,
            bar_count=scope.output_bars,
            start_tick=output_start_tick,
            duration_ticks=output_duration,
        )
        new_sections.append(new_section)
        section_changes.append(
            DevelopmentSectionChangeSummary(
                action="appended",
                section_id=section_id,
                section_type=section_type,
                start_bar=scope.output_start_bar,
                bar_count=scope.output_bars,
            )
        )

        new_harmony = list(source.harmony)
        for item in draft.harmony:
            new_harmony.append(
                CompositionV2HarmonyItem(
                    start_tick=output_start_tick + item.relative_start_tick,
                    duration_ticks=item.duration_ticks,
                    chord=item.chord,
                )
            )
            harmony_changes.append(
                DevelopmentHarmonyChangeSummary(
                    action="appended",
                    start_tick=output_start_tick + item.relative_start_tick,
                    duration_ticks=item.duration_ticks,
                    chord=item.chord,
                )
            )

        # Timeline / marker appends at absolute ticks.
        tempo_payload = [item.model_dump(mode="json") for item in source.tempo_changes]
        meter_payload = [item.model_dump(mode="json") for item in source.time_signature_changes]
        key_payload = [item.model_dump(mode="json") for item in source.key_changes]
        marker_payload = [item.model_dump(mode="json") for item in source.markers]
        for change in draft.timeline_changes:
            abs_tick = output_start_tick + change.relative_tick
            if abs_tick < output_start_tick or abs_tick >= output_end_tick:
                raise CompositionDevelopmentError(
                    "development_draft_invalid",
                    "Timeline changes must land inside the generated span",
                    details={"absolute_tick": abs_tick},
                )
            if abs_tick not in boundaries:
                raise CompositionDevelopmentError(
                    "development_draft_invalid",
                    "Timeline changes must land on generated bar boundaries",
                    details={"absolute_tick": abs_tick},
                )
            if change.bpm is not None:
                tempo_payload.append({"tick": abs_tick, "bpm": change.bpm})
            if change.time_signature is not None:
                meter_payload.append({"tick": abs_tick, "time_signature": change.time_signature})
            if change.key is not None:
                key_payload.append({"tick": abs_tick, "key": change.key})
        for marker in draft.markers:
            marker_payload.append(
                {
                    "tick": output_start_tick + marker.relative_tick,
                    "kind": marker.kind,
                    "label": marker.label,
                }
            )

        motifs = list(source.motifs)
        for occurrence in draft.motif_occurrences:
            mapped_ids = []
            for draft_event_id in occurrence.draft_event_ids:
                mapped = draft_id_map.get(draft_event_id)
                if mapped is None:
                    raise CompositionDevelopmentError(
                        "development_draft_invalid",
                        "Motif occurrence references unknown draft_event_id",
                        details={"draft_event_id": draft_event_id},
                    )
                mapped_ids.append(mapped)
            occ_id, collided = _allocate_id(
                "occ",
                [candidate_ordinal, occurrence.motif_id, mapped_ids[0]],
                used_ids,
            )
            id_collision_count += int(collided)
            found = False
            updated_motifs: list[CompositionV2MotifDefinition] = []
            for motif in motifs:
                if motif.id != occurrence.motif_id:
                    updated_motifs.append(motif)
                    continue
                found = True
                new_occ = CompositionV2MotifOccurrence(
                    id=occ_id,
                    track_id=occurrence.track_id,
                    event_ids=mapped_ids,
                    relationship=occurrence.relationship or "repeat",  # type: ignore[arg-type]
                )
                updated_motifs.append(
                    motif.model_copy(update={"occurrences": list(motif.occurrences) + [new_occ]})
                )
                motif_changes.append(
                    DevelopmentMotifChangeSummary(
                        action="appended_occurrence",
                        motif_id=motif.id,
                        occurrence_count_delta=1,
                    )
                )
            if not found:
                raise CompositionDevelopmentError(
                    "development_draft_invalid",
                    "Motif occurrence references unknown motif_id",
                    details={"motif_id": occurrence.motif_id},
                )
            motifs = updated_motifs

        candidate_payload = {
            **source_snapshot,
            "bar_count": source.bar_count + scope.output_bars,
            "duration_ticks": new_duration,
            "sections": [section.model_dump(mode="json") for section in new_sections],
            "tracks": [track.model_dump(mode="json") for track in new_tracks],
            "harmony": [item.model_dump(mode="json") for item in new_harmony],
            "tempo_changes": tempo_payload,
            "time_signature_changes": meter_payload,
            "key_changes": key_payload,
            "markers": marker_payload,
            "motifs": [motif.model_dump(mode="json") for motif in motifs],
        }
        result = CompositionV2.model_validate(candidate_payload)
        integrity = validate_composition_integrity(result, profile="canonical")
        if not integrity.ok:
            raise CompositionDevelopmentError(
                "development_draft_invalid",
                "Realized candidate failed composition integrity validation",
                details={"error_count": len(integrity.errors), "error_codes": integrity.error_codes()[:16]},
            )

        prefix_ok = _documents_equal_prefix(
            source_snapshot,
            result.model_dump(mode="json"),
            seam_tick=output_start_tick,
        )
        preservation.extend(
            [
                _assert("append_prefix", prefix_ok, "Exact source prefix preserved through append seam"),
                _assert(
                    "track_topology",
                    [track.id for track in result.tracks] == [track.id for track in source.tracks],
                    "Track order/ids unchanged",
                ),
                _assert(
                    "instrumentation",
                    all(
                        (left.instrument, left.midi_program, left.channel, left.role)
                        == (right.instrument, right.midi_program, right.channel, right.role)
                        for left, right in zip(source.tracks, result.tracks, strict=True)
                    ),
                    "Instrumentation unchanged",
                ),
                _assert(
                    "structural_ids",
                    all(section.id == source.sections[index].id for index, section in enumerate(result.sections[: len(source.sections)])),
                    "Existing section ids unchanged",
                ),
            ]
        )
        if not prefix_ok:
            raise CompositionDevelopmentError(
                "development_preservation_failed",
                "Append realization mutated immutable source prefix",
                details={"seam_tick": output_start_tick},
            )

        output_range = DevelopmentResolvedRange(
            start_bar=scope.output_start_bar,
            end_bar=scope.output_end_bar,
            start_tick=output_start_tick,
            end_tick=output_end_tick,
        )
        source_range = DevelopmentResolvedRange(
            start_bar=scope.source_start_bar,
            end_bar=scope.source_end_bar,
            start_tick=scope.source_start_tick,
            end_tick=scope.source_end_tick,
        )

    else:
        # vary_section
        start_tick = scope.source_start_tick
        end_tick = scope.source_end_tick
        output_duration = end_tick - start_tick
        bounds = RegionTickBounds(
            start_bar=scope.source_start_bar,
            end_bar=scope.source_end_bar,
            start_tick=start_tick,
            end_tick=end_tick,
            bar_ticks=max(1, end_tick - start_tick),
        )
        try:
            reject_boundary_crossing_content(source, bounds, [track.id for track in source.tracks])
        except CompositionRegionPatchError as exc:
            raise CompositionDevelopmentError(
                "development_seam_crossing",
                str(exc),
                details={"region_code": exc.code},
            ) from exc

        _validate_draft_against_scope(
            composition=source,
            draft=draft,
            output_start_tick=start_tick,
            output_duration_ticks=output_duration,
            allow_modulation=request.allow_modulation,
        )

        draft_by_id = {track.track_id: track for track in draft.tracks}
        draft_id_map: dict[str, str] = {}
        removed_event_ids: set[str] = set()
        new_tracks = []
        for track in source.tracks:
            draft_track = draft_by_id[track.id]
            before_count = len(track.events)
            kept = []
            removed = 0
            for event in track.events:
                event_end = event.start_tick + event.duration_ticks
                if event.start_tick >= start_tick and event_end <= end_tick:
                    if event.id:
                        removed_event_ids.add(event.id)
                    removed += 1
                    continue
                kept.append(event)
            created = 0
            for index, note in enumerate(draft_track.events):
                event_id, collided = _allocate_id(
                    "dev",
                    [candidate_ordinal, "vary", track.id, note.relative_start_tick, note.pitch, index],
                    used_ids,
                )
                id_collision_count += int(collided)
                if note.draft_event_id:
                    draft_id_map[note.draft_event_id] = event_id
                kept.append(
                    CompositionV2NoteEvent(
                        id=event_id,
                        pitch=note.pitch,
                        start_tick=start_tick + note.relative_start_tick,
                        duration_ticks=note.duration_ticks,
                        velocity=note.velocity,
                        staff=note.staff,
                        voice=note.voice,
                        articulations=list(note.articulations),
                    )
                )
                created += 1
                created_event_count += 1
            kept.sort(key=lambda event: (event.start_tick, event.pitch, event.duration_ticks))
            new_tracks.append(track.model_copy(update={"events": kept}))
            track_changes.append(
                DevelopmentTrackChangeSummary(
                    track_id=track.id,
                    role=track.role,
                    events_before=before_count,
                    events_after=len(kept),
                    events_created=created,
                    events_removed=removed,
                )
            )

        new_harmony = []
        for item in source.harmony:
            item_end = item.start_tick + item.duration_ticks
            if item.start_tick >= start_tick and item_end <= end_tick:
                harmony_changes.append(
                    DevelopmentHarmonyChangeSummary(
                        action="removed",
                        start_tick=item.start_tick,
                        duration_ticks=item.duration_ticks,
                        chord=item.chord,
                    )
                )
                continue
            if item.start_tick < end_tick and item_end > start_tick:
                raise CompositionDevelopmentError(
                    "development_draft_invalid",
                    "Variation cannot split existing harmony spans; select a clean range",
                    details={"start_tick": item.start_tick, "duration_ticks": item.duration_ticks},
                )
            new_harmony.append(item)
        for item in draft.harmony:
            new_harmony.append(
                CompositionV2HarmonyItem(
                    start_tick=start_tick + item.relative_start_tick,
                    duration_ticks=item.duration_ticks,
                    chord=item.chord,
                )
            )
            harmony_changes.append(
                DevelopmentHarmonyChangeSummary(
                    action="replaced",
                    start_tick=start_tick + item.relative_start_tick,
                    duration_ticks=item.duration_ticks,
                    chord=item.chord,
                )
            )
        new_harmony.sort(key=lambda item: item.start_tick)

        reconcile = reconcile_motifs_for_removed_event_ids(source.motifs, removed_event_ids)
        motifs = list(reconcile.motifs)
        for warning in reconcile.warnings:
            code = getattr(warning, "code", None) or str(warning)
            warning_codes.append(str(code))

        for section in source.sections:
            section_changes.append(
                DevelopmentSectionChangeSummary(
                    action="replaced_content"
                    if section.start_bar <= scope.source_end_bar
                    and (section.start_bar + section.bar_count - 1) >= scope.source_start_bar
                    else "unchanged",
                    section_id=section.id,
                    section_type=section.type,
                    start_bar=section.start_bar,
                    bar_count=section.bar_count,
                )
            )

        candidate_payload = {
            **source_snapshot,
            "tracks": [track.model_dump(mode="json") for track in new_tracks],
            "harmony": [item.model_dump(mode="json") for item in new_harmony],
            "motifs": [motif.model_dump(mode="json") for motif in motifs],
        }
        result = CompositionV2.model_validate(candidate_payload)
        integrity = validate_composition_integrity(result, profile="canonical")
        if not integrity.ok:
            raise CompositionDevelopmentError(
                "development_draft_invalid",
                "Realized variation candidate failed composition integrity validation",
                details={"error_count": len(integrity.errors), "error_codes": integrity.error_codes()[:16]},
            )

        outside_ok = _outside_range_equal(source, result, start_tick=start_tick, end_tick=end_tick)
        geometry_ok = [section.model_dump(mode="json") for section in source.sections] == [
            section.model_dump(mode="json") for section in result.sections
        ]
        preservation.extend(
            [
                _assert("outside_range", outside_ok, "Content outside variation range preserved exactly"),
                _assert("section_geometry", geometry_ok, "Section geometry unchanged under variation"),
                _assert(
                    "track_topology",
                    [track.id for track in result.tracks] == [track.id for track in source.tracks],
                    "Track order/ids unchanged",
                ),
            ]
        )
        if not outside_ok or not geometry_ok:
            raise CompositionDevelopmentError(
                "development_preservation_failed",
                "Variation realization mutated unauthorized material",
                details={"start_tick": start_tick, "end_tick": end_tick},
            )

        output_range = DevelopmentResolvedRange(
            start_bar=scope.source_start_bar,
            end_bar=scope.source_end_bar,
            start_tick=start_tick,
            end_tick=end_tick,
        )
        source_range = output_range

    # Ensure source immutability
    if source.model_dump(mode="json") != source_snapshot:
        raise CompositionDevelopmentError(
            "development_internal_error",
            "Source composition was mutated during realization",
            http_status=500,
        )

    if any(not item.satisfied and item.required for item in preservation):
        raise CompositionDevelopmentError(
            "development_preservation_failed",
            "One or more required preservation assertions failed",
            details={"failed_kinds": [item.kind for item in preservation if not item.satisfied]},
        )

    logger.info(
        "Realized composition development draft",
        extra={
            "operation": request.operation,
            "old_bar_count": source.bar_count,
            "new_bar_count": result.bar_count,
            "old_duration_ticks": source.duration_ticks,
            "new_duration_ticks": result.duration_ticks,
            "changed_track_count": sum(1 for item in track_changes if item.events_created or item.events_removed),
            "created_event_count": created_event_count,
            "preservation_ok": all(item.satisfied for item in preservation if item.required),
            "id_collision_count": id_collision_count,
            "diagnostic_codes": [item.code for item in identity.diagnostics][:16],
        },
    )
    logger.debug(
        "Development realization per-track counts",
        extra={
            "track_change_count": len(track_changes),
            "id_collision_count": id_collision_count,
            "created_event_count": created_event_count,
        },
    )

    return DevelopmentRealizationResult(
        composition=result,
        source_range=source_range,
        output_range=output_range,
        section_changes=section_changes,
        harmony_changes=harmony_changes,
        motif_changes=motif_changes,
        track_changes=track_changes,
        preservation=preservation,
        identity_diagnostics=list(identity.diagnostics),
        warning_codes=list(dict.fromkeys(warning_codes)),
        created_event_count=created_event_count,
        id_collision_count=id_collision_count,
    )
