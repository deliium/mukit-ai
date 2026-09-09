"""Deterministic region selection and replace_region patch application."""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from pydantic import ValidationError

from ..composition_schemas import (
    CompositionV1,
    CompositionV2,
    CompositionV2MotifDefinition,
    reconcile_motifs_for_removed_event_ids,
)
from ..schemas import (
    Composition,
    CompositionEditSelection,
    CompositionRegionReplacementPatch,
    CompositionTrack,
    CompositionV2NoteEvent,
    LLMMusicHarmonyItem,
    NoteEvent,
)
from .composition_timing import bar_duration_ticks, bar_to_start_tick
from .composition_timeline import compile_timeline
from .composition_validator import CompositionValidationResult, validate_composition_integrity


logger = logging.getLogger(__name__)

CompositionLike = CompositionV1 | CompositionV2


class CompositionRegionPatchError(ValueError):
    """Raised when a region selection or replace_region patch cannot be applied safely."""

    def __init__(self, message: str, *, code: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.context = context or {}


@dataclass(frozen=True)
class RegionTickBounds:
    start_bar: int
    end_bar: int
    start_tick: int
    end_tick: int
    bar_ticks: int


@dataclass
class RegionSelectionSummary:
    bounds: RegionTickBounds
    target_track_ids: list[str]
    in_region_event_counts: dict[str, int] = field(default_factory=dict)
    outside_region_event_counts: dict[str, int] = field(default_factory=dict)
    total_in_region_events: int = 0
    total_outside_region_events: int = 0


@dataclass
class RegionPatchApplicationResult:
    composition: CompositionLike
    patch: CompositionRegionReplacementPatch
    summary: RegionSelectionSummary
    replaced_event_count: int
    preserved_event_count: int
    added_track_count: int
    warnings: list[str] = field(default_factory=list)


def selection_tick_bounds(
    composition: CompositionLike,
    selection: CompositionEditSelection | CompositionRegionReplacementPatch,
) -> RegionTickBounds:
    """Convert an inclusive bar selection into exclusive-end tick boundaries."""
    logger.debug(
        "Deriving region tick bounds",
        extra={
            "start_bar": selection.start_bar,
            "end_bar": selection.end_bar,
            "bar_count": composition.bar_count,
            "schema_version": composition.schema_version,
        },
    )
    if selection.start_bar < 1 or selection.end_bar < selection.start_bar:
        logger.warning(
            "Rejected invalid region bar range",
            extra={"start_bar": selection.start_bar, "end_bar": selection.end_bar, "code": "invalid_bar_range"},
        )
        raise CompositionRegionPatchError(
            "Selection bar range is invalid",
            code="invalid_bar_range",
            context={"start_bar": selection.start_bar, "end_bar": selection.end_bar},
        )
    if selection.end_bar > composition.bar_count:
        logger.warning(
            "Rejected out-of-range region bar selection",
            extra={
                "start_bar": selection.start_bar,
                "end_bar": selection.end_bar,
                "bar_count": composition.bar_count,
                "code": "bar_range_out_of_bounds",
            },
        )
        raise CompositionRegionPatchError(
            "Selection end_bar exceeds composition bar_count",
            code="bar_range_out_of_bounds",
            context={
                "start_bar": selection.start_bar,
                "end_bar": selection.end_bar,
                "bar_count": composition.bar_count,
            },
        )

    try:
        timeline = compile_timeline(composition)
        start_tick, end_tick = timeline.bar_range_ticks(selection.start_bar, selection.end_bar)
        bar_ticks = timeline.bar_end_tick(selection.start_bar) - timeline.bar_start_tick(selection.start_bar)
    except Exception:
        # Constant-meter fallback for V1-shaped documents without change arrays.
        bar_ticks = bar_duration_ticks(composition.time_signature, composition.ticks_per_quarter)
        start_tick = bar_to_start_tick(selection.start_bar, composition.time_signature, composition.ticks_per_quarter)
        end_tick = bar_to_start_tick(selection.end_bar + 1, composition.time_signature, composition.ticks_per_quarter)
    if end_tick > composition.duration_ticks:
        end_tick = composition.duration_ticks

    bounds = RegionTickBounds(
        start_bar=selection.start_bar,
        end_bar=selection.end_bar,
        start_tick=start_tick,
        end_tick=end_tick,
        bar_ticks=bar_ticks,
    )
    logger.debug(
        "Derived region tick bounds",
        extra={
            "start_bar": bounds.start_bar,
            "end_bar": bounds.end_bar,
            "start_tick": bounds.start_tick,
            "end_tick": bounds.end_tick,
            "bar_ticks": bounds.bar_ticks,
        },
    )
    return bounds


def resolve_target_track_ids(
    composition: CompositionLike,
    track_ids: list[str] | None,
) -> list[str]:
    known = [track.id for track in composition.tracks]
    if track_ids is None:
        logger.debug("Resolving target tracks to all composition tracks", extra={"track_count": len(known)})
        return list(known)

    unknown = sorted(set(track_ids) - set(known))
    if unknown:
        logger.warning(
            "Rejected unknown target track ids",
            extra={"unknown_track_ids": unknown, "code": "unknown_track_ids"},
        )
        raise CompositionRegionPatchError(
            "Selection track_ids must exist in the composition",
            code="unknown_track_ids",
            context={"unknown_track_ids": unknown},
        )
    logger.debug("Resolved target track ids", extra={"target_track_count": len(track_ids)})
    return list(track_ids)


def event_in_region(event: NoteEvent | CompositionV2NoteEvent | dict[str, Any], bounds: RegionTickBounds) -> bool:
    start_tick = int(event["start_tick"] if isinstance(event, dict) else event.start_tick)
    duration_ticks = int(event["duration_ticks"] if isinstance(event, dict) else event.duration_ticks)
    end_tick = start_tick + duration_ticks
    # Inclusive start, exclusive end; overlapping notes count as in-region.
    return start_tick < bounds.end_tick and end_tick > bounds.start_tick


def event_fully_within_region(
    event: NoteEvent | CompositionV2NoteEvent | dict[str, Any],
    bounds: RegionTickBounds,
) -> bool:
    start_tick = int(event["start_tick"] if isinstance(event, dict) else event.start_tick)
    duration_ticks = int(event["duration_ticks"] if isinstance(event, dict) else event.duration_ticks)
    end_tick = start_tick + duration_ticks
    return start_tick >= bounds.start_tick and end_tick <= bounds.end_tick


def reject_boundary_crossing_content(
    composition: CompositionLike,
    bounds: RegionTickBounds,
    target_track_ids: Iterable[str],
) -> None:
    """Reject edits that would silently truncate crossing notes or split tie chains."""
    target_set = set(target_track_ids)
    for track in composition.tracks:
        if track.id not in target_set:
            continue
        for event in track.events:
            if event_in_region(event, bounds) and not event_fully_within_region(event, bounds):
                logger.warning(
                    "Rejected boundary-crossing note in region selection",
                    extra={
                        "track_id": track.id,
                        "start_tick": event.start_tick,
                        "duration_ticks": event.duration_ticks,
                        "region_start_tick": bounds.start_tick,
                        "region_end_tick": bounds.end_tick,
                        "code": "boundary_crossing_note",
                    },
                )
                raise CompositionRegionPatchError(
                    "Selected region overlaps a note that continues outside the selection; "
                    "narrow the selection or edit the full note span",
                    code="boundary_crossing_note",
                    context={
                        "track_id": track.id,
                        "start_tick": event.start_tick,
                        "duration_ticks": event.duration_ticks,
                        "region_start_tick": bounds.start_tick,
                        "region_end_tick": bounds.end_tick,
                    },
                )

        groups: dict[str, list[Any]] = {}
        for event in track.events:
            tie = getattr(event, "tie", None)
            if tie is None:
                continue
            groups.setdefault(tie.group_id, []).append(event)
        for group_id, members in groups.items():
            any_in = any(event_in_region(event, bounds) for event in members)
            any_out = any(not event_in_region(event, bounds) for event in members)
            if any_in and any_out:
                logger.warning(
                    "Rejected boundary-crossing tie chain in region selection",
                    extra={
                        "track_id": track.id,
                        "group_id": group_id,
                        "member_count": len(members),
                        "code": "boundary_crossing_tie_chain",
                    },
                )
                raise CompositionRegionPatchError(
                    "Selected region intersects a tie chain that continues outside the selection; "
                    "include the full tie chain or clear ties before editing",
                    code="boundary_crossing_tie_chain",
                    context={
                        "track_id": track.id,
                        "group_id": group_id,
                        "member_count": len(members),
                    },
                )


def summarize_region_selection(
    composition: CompositionLike,
    selection: CompositionEditSelection,
) -> RegionSelectionSummary:
    bounds = selection_tick_bounds(composition, selection)
    target_track_ids = resolve_target_track_ids(composition, selection.track_ids)
    target_set = set(target_track_ids)

    in_region_event_counts: dict[str, int] = {}
    outside_region_event_counts: dict[str, int] = {}
    total_in = 0
    total_out = 0

    for track in composition.tracks:
        in_count = 0
        out_count = 0
        for event in track.events:
            if track.id in target_set and event_in_region(event, bounds):
                in_count += 1
            else:
                out_count += 1
        in_region_event_counts[track.id] = in_count
        outside_region_event_counts[track.id] = out_count
        total_in += in_count
        total_out += out_count

    summary = RegionSelectionSummary(
        bounds=bounds,
        target_track_ids=target_track_ids,
        in_region_event_counts=in_region_event_counts,
        outside_region_event_counts=outside_region_event_counts,
        total_in_region_events=total_in,
        total_outside_region_events=total_out,
    )
    logger.debug(
        "Summarized region selection",
        extra={
            "start_bar": bounds.start_bar,
            "end_bar": bounds.end_bar,
            "target_track_count": len(target_track_ids),
            "in_region_event_count": total_in,
            "outside_region_event_count": total_out,
        },
    )
    return summary


def _event_dict(event: NoteEvent | CompositionV2NoteEvent | dict[str, Any]) -> dict[str, Any]:
    if hasattr(event, "model_dump"):
        data = event.model_dump(mode="json")
    else:
        data = copy.deepcopy(event)
    # Patch replacement notes may still be V1-shaped; fill V2 defaults when absent.
    data.setdefault("articulations", [])
    data.setdefault("tie", None)
    return data


def _event_semantic_tuple(event: NoteEvent | CompositionV2NoteEvent | dict[str, Any]) -> tuple[Any, ...]:
    data = _event_dict(event)
    return (
        data.get("type", "note"),
        data.get("pitch"),
        data.get("start_tick"),
        data.get("duration_ticks"),
        data.get("velocity"),
        data.get("id"),
        data.get("staff"),
        data.get("voice"),
        tuple(data.get("articulations") or ()),
        (
            (data["tie"].get("group_id"), data["tie"].get("type"))
            if isinstance(data.get("tie"), dict)
            else None
        ),
    )


def canonical_json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def preserved_region_events(
    composition: CompositionLike,
    bounds: RegionTickBounds,
    target_track_ids: Iterable[str],
) -> dict[str, list[dict[str, Any]]]:
    target_set = set(target_track_ids)
    preserved: dict[str, list[dict[str, Any]]] = {}
    for track in composition.tracks:
        events: list[dict[str, Any]] = []
        for event in track.events:
            if track.id in target_set and event_in_region(event, bounds):
                continue
            events.append(_event_dict(event))
        preserved[track.id] = events
    return preserved


def compare_preserved_regions(
    original: CompositionLike,
    updated: CompositionLike,
    bounds: RegionTickBounds,
    target_track_ids: Iterable[str],
) -> dict[str, Any]:
    """Compare outside-scope events. Prefer byte-for-byte canonical JSON when possible."""
    original_preserved = preserved_region_events(original, bounds, target_track_ids)
    updated_preserved = preserved_region_events(updated, bounds, target_track_ids)

    original_track_ids = [track.id for track in original.tracks]
    updated_track_ids = [track.id for track in updated.tracks]
    added_track_ids = sorted(set(updated_track_ids) - set(original_track_ids))
    removed_track_ids = sorted(set(original_track_ids) - set(updated_track_ids))

    mismatched_tracks: list[str] = []
    byte_equal_tracks: list[str] = []
    semantic_equal_tracks: list[str] = []

    for track_id in original_track_ids:
        left = original_preserved.get(track_id, [])
        right = updated_preserved.get(track_id, [])
        left_json = canonical_json_dumps(left)
        right_json = canonical_json_dumps(right)
        if left_json == right_json:
            byte_equal_tracks.append(track_id)
            continue
        left_sem = sorted(_event_semantic_tuple(event) for event in left)
        right_sem = sorted(_event_semantic_tuple(event) for event in right)
        if left_sem == right_sem:
            semantic_equal_tracks.append(track_id)
        else:
            mismatched_tracks.append(track_id)

    metadata_fields = (
        "tempo",
        "key",
        "time_signature",
        "ticks_per_quarter",
        "duration_ticks",
        "bar_count",
        "schema_version",
    )
    metadata_mismatches = [
        field_name
        for field_name in metadata_fields
        if getattr(original, field_name) != getattr(updated, field_name)
    ]
    for field_name in (
        "tempo_changes",
        "time_signature_changes",
        "key_changes",
        "markers",
    ):
        if hasattr(original, field_name) or hasattr(updated, field_name):
            left = getattr(original, field_name, [])
            right = getattr(updated, field_name, [])
            left_dump = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in left
            ]
            right_dump = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in right
            ]
            if canonical_json_dumps(left_dump) != canonical_json_dumps(right_dump):
                metadata_mismatches.append(field_name)

    sections_equal = canonical_json_dumps(
        [section.model_dump(mode="json") for section in original.sections]
    ) == canonical_json_dumps([section.model_dump(mode="json") for section in updated.sections])

    # Track-level expression metadata outside replaced event lists must be preserved.
    track_metadata_mismatches: list[str] = []
    original_by_id = {track.id: track for track in original.tracks}
    updated_by_id = {track.id: track for track in updated.tracks}
    for track_id in original_track_ids:
        left_track = original_by_id.get(track_id)
        right_track = updated_by_id.get(track_id)
        if left_track is None or right_track is None:
            continue
        left_meta = left_track.model_dump(mode="json")
        right_meta = right_track.model_dump(mode="json")
        left_meta.pop("events", None)
        right_meta.pop("events", None)
        if canonical_json_dumps(left_meta) != canonical_json_dumps(right_meta):
            track_metadata_mismatches.append(track_id)

    result = {
        "ok": (
            not mismatched_tracks
            and not removed_track_ids
            and not metadata_mismatches
            and not track_metadata_mismatches
            and sections_equal
        ),
        "mismatched_tracks": mismatched_tracks,
        "byte_equal_tracks": byte_equal_tracks,
        "semantic_equal_tracks": semantic_equal_tracks,
        "added_track_ids": added_track_ids,
        "removed_track_ids": removed_track_ids,
        "metadata_mismatches": metadata_mismatches,
        "track_metadata_mismatches": track_metadata_mismatches,
        "sections_equal": sections_equal,
    }
    logger.debug(
        "Compared preserved regions",
        extra={
            "ok": result["ok"],
            "mismatched_track_count": len(mismatched_tracks),
            "byte_equal_track_count": len(byte_equal_tracks),
            "semantic_equal_track_count": len(semantic_equal_tracks),
            "added_track_count": len(added_track_ids),
            "removed_track_count": len(removed_track_ids),
            "metadata_mismatch_count": len(metadata_mismatches),
            "sections_equal": sections_equal,
        },
    )
    return result


def _assert_unaffected_motif_definitions_preserved(
    original_motifs: list[CompositionV2MotifDefinition],
    reconciled_motifs: list[CompositionV2MotifDefinition],
    removed_event_ids: set[str],
) -> None:
    """Ensure motif definitions with no removed references remain byte-for-byte."""
    if not removed_event_ids:
        if canonical_json_dumps([item.model_dump(mode="json") for item in original_motifs]) != (
            canonical_json_dumps([item.model_dump(mode="json") for item in reconciled_motifs])
        ):
            raise CompositionRegionPatchError(
                "Region patch unexpectedly mutated motif metadata",
                code="preserved_region_mutated",
                context={"field": "motifs"},
            )
        return

    reconciled_by_id = {item.id: item for item in reconciled_motifs}
    for motif in original_motifs:
        affected = any(
            removed_event_ids.intersection(occurrence.event_ids) for occurrence in motif.occurrences
        )
        if affected:
            continue
        reconciled = reconciled_by_id.get(motif.id)
        if reconciled is None:
            raise CompositionRegionPatchError(
                "Region patch removed an unaffected motif definition",
                code="preserved_region_mutated",
                context={"motif_id": motif.id},
            )
        original_json = canonical_json_dumps(motif.model_dump(mode="json"))
        reconciled_json = canonical_json_dumps(reconciled.model_dump(mode="json"))
        if original_json != reconciled_json:
            raise CompositionRegionPatchError(
                "Region patch mutated an unaffected motif definition",
                code="preserved_region_mutated",
                context={"motif_id": motif.id},
            )


def _validate_patch_boundaries(
    composition: CompositionLike,
    patch: CompositionRegionReplacementPatch,
    selection: CompositionEditSelection | None,
) -> RegionTickBounds:
    bounds = selection_tick_bounds(composition, patch)
    if selection is not None:
        if patch.start_bar != selection.start_bar or patch.end_bar != selection.end_bar:
            logger.warning(
                "Rejected patch with boundary mismatch",
                extra={
                    "patch_start_bar": patch.start_bar,
                    "patch_end_bar": patch.end_bar,
                    "selection_start_bar": selection.start_bar,
                    "selection_end_bar": selection.end_bar,
                    "code": "patch_boundary_mismatch",
                },
            )
            raise CompositionRegionPatchError(
                "Patch bar boundaries must match the requested selection",
                code="patch_boundary_mismatch",
                context={
                    "patch_start_bar": patch.start_bar,
                    "patch_end_bar": patch.end_bar,
                    "selection_start_bar": selection.start_bar,
                    "selection_end_bar": selection.end_bar,
                },
            )
        if selection.track_ids is not None and patch.target_track_ids is not None:
            if sorted(selection.track_ids) != sorted(patch.target_track_ids):
                logger.warning(
                    "Rejected patch with target track mismatch",
                    extra={
                        "selection_track_count": len(selection.track_ids),
                        "patch_track_count": len(patch.target_track_ids),
                        "code": "patch_target_mismatch",
                    },
                )
                raise CompositionRegionPatchError(
                    "Patch target_track_ids must match the requested selection",
                    code="patch_target_mismatch",
                    context={
                        "selection_track_ids": selection.track_ids,
                        "patch_target_track_ids": patch.target_track_ids,
                    },
                )
    return bounds


def _validate_replacement_events(
    replace_tracks: list[Any],
    bounds: RegionTickBounds,
    target_track_ids: list[str],
) -> int:
    target_set = set(target_track_ids)
    replaced_count = 0
    for track_replacement in replace_tracks:
        track_id = track_replacement.track_id
        if track_id not in target_set:
            logger.warning(
                "Rejected replacement for non-target track",
                extra={"track_id": track_id, "code": "non_target_track_replacement"},
            )
            raise CompositionRegionPatchError(
                "replace_tracks may only include target tracks",
                code="non_target_track_replacement",
                context={"track_id": track_id, "target_track_ids": target_track_ids},
            )
        for event in track_replacement.events:
            if not event_fully_within_region(event, bounds):
                logger.warning(
                    "Rejected out-of-region replacement event",
                    extra={
                        "track_id": track_id,
                        "start_tick": event.start_tick,
                        "duration_ticks": event.duration_ticks,
                        "region_start_tick": bounds.start_tick,
                        "region_end_tick": bounds.end_tick,
                        "code": "replacement_event_out_of_region",
                    },
                )
                raise CompositionRegionPatchError(
                    "Replacement events must start and end within the selected region",
                    code="replacement_event_out_of_region",
                    context={
                        "track_id": track_id,
                        "start_tick": event.start_tick,
                        "duration_ticks": event.duration_ticks,
                        "region_start_tick": bounds.start_tick,
                        "region_end_tick": bounds.end_tick,
                    },
                )
            replaced_count += 1
    return replaced_count


def _validate_added_tracks(
    composition: CompositionLike,
    added_tracks: list[CompositionTrack],
    *,
    allow_added_tracks: bool,
) -> None:
    if not added_tracks:
        return
    if not allow_added_tracks:
        logger.warning(
            "Rejected unexpected added tracks",
            extra={"added_track_count": len(added_tracks), "code": "added_tracks_not_allowed"},
        )
        raise CompositionRegionPatchError(
            "added_tracks are not allowed for this edit request",
            code="added_tracks_not_allowed",
            context={"added_track_count": len(added_tracks)},
        )

    existing_ids = {track.id for track in composition.tracks}
    existing_channels = {track.channel for track in composition.tracks if not track.is_drum}
    for track in added_tracks:
        if track.id in existing_ids:
            logger.warning(
                "Rejected added track with colliding id",
                extra={"track_id": track.id, "code": "added_track_id_collision"},
            )
            raise CompositionRegionPatchError(
                "added_tracks must use unique track IDs",
                code="added_track_id_collision",
                context={"track_id": track.id},
            )
        if not track.is_drum and track.channel in existing_channels:
            logger.warning(
                "Rejected added track with colliding channel",
                extra={"track_id": track.id, "channel": track.channel, "code": "added_track_channel_collision"},
            )
            raise CompositionRegionPatchError(
                "added_tracks must use unique non-drum MIDI channels",
                code="added_track_channel_collision",
                context={"track_id": track.id, "channel": track.channel},
            )
        existing_ids.add(track.id)
        if not track.is_drum:
            existing_channels.add(track.channel)


def _apply_harmony_patch(
    original_harmony: list[LLMMusicHarmonyItem],
    harmony_patch: list[LLMMusicHarmonyItem] | None,
    bounds: RegionTickBounds,
    *,
    allow_harmony_changes: bool,
) -> list[dict[str, Any]]:
    if harmony_patch is None:
        return [item.model_dump(mode="json") for item in original_harmony]
    if not allow_harmony_changes:
        logger.warning(
            "Rejected unexpected harmony patch",
            extra={"harmony_patch_count": len(harmony_patch), "code": "harmony_patch_not_allowed"},
        )
        raise CompositionRegionPatchError(
            "harmony_patch is not allowed for this edit request",
            code="harmony_patch_not_allowed",
            context={"harmony_patch_count": len(harmony_patch)},
        )

    preserved = [
        item.model_dump(mode="json")
        for item in original_harmony
        if item.bar < bounds.start_bar or item.bar > bounds.end_bar
    ]
    for item in harmony_patch:
        if item.bar < bounds.start_bar or item.bar > bounds.end_bar:
            logger.warning(
                "Rejected harmony patch item outside selection",
                extra={"bar": item.bar, "code": "harmony_patch_out_of_region"},
            )
            raise CompositionRegionPatchError(
                "harmony_patch items must fall within the selected bars",
                code="harmony_patch_out_of_region",
                context={"bar": item.bar, "start_bar": bounds.start_bar, "end_bar": bounds.end_bar},
            )
        preserved.append(item.model_dump(mode="json"))
    preserved.sort(key=lambda item: int(item["bar"]))
    return preserved


def apply_region_replacement_patch(
    composition: CompositionLike,
    patch: CompositionRegionReplacementPatch,
    *,
    selection: CompositionEditSelection | None = None,
    allow_added_tracks: bool = False,
    allow_harmony_changes: bool = False,
    validate_integrity: bool = True,
) -> RegionPatchApplicationResult:
    """Apply a replace_region patch immutably and validate preservation guarantees."""
    logger.info(
        "Applying composition region replacement patch",
        extra={
            "schema_version": composition.schema_version,
            "start_bar": patch.start_bar,
            "end_bar": patch.end_bar,
            "target_track_count": len(patch.target_track_ids or []),
            "added_track_count": len(patch.added_tracks),
            "replace_track_count": len(patch.replace_tracks),
        },
    )

    bounds = _validate_patch_boundaries(composition, patch, selection)
    requested_targets = (
        selection.track_ids
        if selection is not None and selection.track_ids is not None
        else patch.target_track_ids
    )
    target_track_ids = resolve_target_track_ids(composition, requested_targets)
    target_set = set(target_track_ids)
    removed_event_ids: set[str] = set()
    if isinstance(composition, CompositionV2):
        for track in composition.tracks:
            if track.id not in target_set:
                continue
            for event in track.events:
                if event_in_region(event, bounds) and event.id:
                    removed_event_ids.add(event.id)
        logger.debug(
            "Collected motif reconciliation candidates for region patch",
            extra={
                "removed_event_id_count": len(removed_event_ids),
                "motif_count": len(composition.motifs),
            },
        )
    reject_boundary_crossing_content(composition, bounds, target_track_ids)
    replaced_event_count = _validate_replacement_events(patch.replace_tracks, bounds, target_track_ids)
    _validate_added_tracks(composition, patch.added_tracks, allow_added_tracks=allow_added_tracks)

    replacements = {item.track_id: item.events for item in patch.replace_tracks}
    original_data = composition.model_dump(mode="json")
    updated_tracks: list[dict[str, Any]] = []
    preserved_event_count = 0

    for track_data in original_data["tracks"]:
        track_id = track_data["id"]
        original_events = track_data.get("events", [])
        if track_id not in set(target_track_ids):
            preserved_event_count += len(original_events)
            updated_tracks.append(copy.deepcopy(track_data))
            continue

        preserved_events = [
            copy.deepcopy(event)
            for event in original_events
            if not event_in_region(event, bounds)
        ]
        preserved_event_count += len(preserved_events)
        replacement_events = [_event_dict(event) for event in replacements.get(track_id, [])]
        new_events = preserved_events + replacement_events
        new_events.sort(key=lambda event: (int(event["start_tick"]), str(event.get("pitch", ""))))
        updated_track = copy.deepcopy(track_data)
        updated_track["events"] = new_events
        updated_tracks.append(updated_track)

    for added in patch.added_tracks:
        updated_tracks.append(added.model_dump(mode="json"))

    updated_data = copy.deepcopy(original_data)
    updated_data["tracks"] = updated_tracks
    updated_data["harmony"] = _apply_harmony_patch(
        composition.harmony,
        patch.harmony_patch,
        bounds,
        allow_harmony_changes=allow_harmony_changes,
    )

    motif_reconcile_warnings: list[str] = []
    if isinstance(composition, CompositionV2) and composition.motifs:
        reconcile = reconcile_motifs_for_removed_event_ids(composition.motifs, removed_event_ids)
        _assert_unaffected_motif_definitions_preserved(
            composition.motifs,
            reconcile.motifs,
            removed_event_ids,
        )
        for warning in reconcile.warnings:
            logger.warning(
                "Motif reference reconciled after region patch",
                extra={
                    "code": warning.code,
                    "motif_id": warning.motif_id,
                    "occurrence_id": warning.occurrence_id,
                },
            )
        motif_reconcile_warnings = [item.code for item in reconcile.warnings]
        updated_data["motifs"] = [item.model_dump(mode="json") for item in reconcile.motifs]
        logger.info(
            "Region patch motif reconciliation completed",
            extra={
                "motif_count_before": len(composition.motifs),
                "motif_count_after": len(reconcile.motifs),
                "pruned_warning_count": len(motif_reconcile_warnings),
            },
        )

    try:
        if (
            isinstance(composition, CompositionV2)
            or original_data.get("schema_version") == "composition.v2"
        ):
            for track in updated_tracks:
                track.setdefault("expression", 127)
                track.setdefault("dynamic_marks", [])
                track.setdefault("sustain_pedals", [])
                track.setdefault("automation", [])
            updated_data.setdefault("tempo_changes", [])
            updated_data.setdefault("time_signature_changes", [])
            updated_data.setdefault("key_changes", [])
            updated_data.setdefault("markers", [])
            updated_composition: CompositionLike = CompositionV2.model_validate(updated_data)
        else:
            updated_composition = Composition.model_validate(updated_data)
    except ValidationError as exc:
        logger.warning(
            "Rejected patch that produced invalid composition",
            extra={
                "error_count": exc.error_count(),
                "code": "invalid_resulting_composition",
                "schema_version": original_data.get("schema_version"),
            },
        )
        raise CompositionRegionPatchError(
            "Applied patch produced an invalid composition document",
            code="invalid_resulting_composition",
            context={"error_count": exc.error_count()},
        ) from exc

    comparison = compare_preserved_regions(composition, updated_composition, bounds, target_track_ids)
    if not comparison["ok"]:
        logger.warning(
            "Rejected patch that mutated preserved regions",
            extra={
                "code": "preserved_region_mutated",
                "mismatched_track_count": len(comparison["mismatched_tracks"]),
                "removed_track_count": len(comparison["removed_track_ids"]),
                "metadata_mismatch_count": len(comparison["metadata_mismatches"]),
                "sections_equal": comparison["sections_equal"],
            },
        )
        raise CompositionRegionPatchError(
            "Patch mutated notes or metadata outside the selected region",
            code="preserved_region_mutated",
            context=comparison,
        )

    if not allow_added_tracks and comparison["added_track_ids"]:
        logger.warning(
            "Rejected unexpected added tracks after application",
            extra={"added_track_ids": comparison["added_track_ids"], "code": "added_tracks_not_allowed"},
        )
        raise CompositionRegionPatchError(
            "Patch unexpectedly added tracks",
            code="added_tracks_not_allowed",
            context={"added_track_ids": comparison["added_track_ids"]},
        )

    integrity_warnings: list[str] = []
    if validate_integrity:
        integrity: CompositionValidationResult = validate_composition_integrity(
            updated_composition,
            profile="canonical",
        )
        if not integrity.ok:
            logger.warning(
                "Rejected patch failing composition integrity checks",
                extra={
                    "code": "integrity_validation_failed",
                    "error_count": len(integrity.errors),
                    "warning_count": len(integrity.warnings),
                },
            )
            raise CompositionRegionPatchError(
                "Applied composition failed integrity validation",
                code="integrity_validation_failed",
                context={
                    "error_codes": [item.code for item in integrity.errors],
                    "warning_codes": [item.code for item in integrity.warnings],
                },
            )
        integrity_warnings = [item.message for item in integrity.warnings]

    summary = RegionSelectionSummary(
        bounds=bounds,
        target_track_ids=target_track_ids,
        total_in_region_events=replaced_event_count,
        total_outside_region_events=preserved_event_count,
    )
    warnings = list(patch.warnings) + motif_reconcile_warnings + integrity_warnings
    logger.info(
        "Applied composition region replacement patch",
        extra={
            "schema_version": updated_composition.schema_version,
            "start_bar": bounds.start_bar,
            "end_bar": bounds.end_bar,
            "target_track_count": len(target_track_ids),
            "added_track_count": len(patch.added_tracks),
            "replaced_event_count": replaced_event_count,
            "preserved_event_count": preserved_event_count,
            "warning_count": len(warnings),
        },
    )
    return RegionPatchApplicationResult(
        composition=updated_composition,
        patch=patch,
        summary=summary,
        replaced_event_count=replaced_event_count,
        preserved_event_count=preserved_event_count,
        added_track_count=len(patch.added_tracks),
        warnings=warnings,
    )
