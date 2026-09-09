"""Operation-specific arrangement topology realization.

Providers return relative drafts with source-note references. This module owns
exact note copying, generated-note materialization, track topology, deterministic
IDs, GM channel allocation, catalog programs, motif reconciliation, and
canonical ordering. Source input is never mutated.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.arrangement_schemas import (
    ArrangementDraftNote,
    ArrangementDraftPart,
    ArrangementEventCounts,
    ArrangementSourceTargetMapping,
    ArrangementTargetProfileFingerprint,
    ArrangementTopologyManifest,
    ArrangementTrackInventoryItem,
    CompositionArrangementDraft,
    CompositionArrangementError,
    CompositionArrangementPreviewRequest,
)
from app.composition_schemas import (
    CompositionV2,
    CompositionV2MotifDefinition,
    CompositionV2MotifOccurrence,
    CompositionV2NoteEvent,
    CompositionV2NoteTie,
    CompositionV2Track,
    midi_pitch_number,
    reconcile_motifs_for_removed_event_ids,
)
from app.services.composition_arrangement_context import (
    ArrangementSourceContext,
    ArrangementSourceNoteRef,
    build_arrangement_source_context,
    lookup_source_note,
)
from app.services.composition_import import midi_number_to_pitch
from app.services.composition_midi import (
    CompositionMidiError,
    assert_shared_channel_control_compatible,
    assert_shared_channel_program_compatible,
    tracks_share_channel_controllers,
)
from app.services.composition_region_patch import (
    allocate_deterministic_id,
    collect_used_composition_ids,
    group_events_by_tie,
    sort_note_events_deterministically,
)
from app.services.composition_validator import validate_composition_integrity
from app.services.instrument_catalog import (
    ArrangementInstrumentCatalog,
    InstrumentProfile,
    get_catalog,
    resolve_profile,
)
from app.services.instrument_identity import normalize_role


logger = logging.getLogger(__name__)

ARRANGEMENT_PATCH_VERSION = "composition.arrangement.patch.v1"

# GM pitched channels excluding reserved drum channel 10.
_PITCHED_CHANNELS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16)
_DRUM_CHANNEL = 10
_MELODY_LEAD_ROLES = frozenset({"melody", "lead"})
_OPERATIONS_CONSUMING_SELECTED = frozenset(
    {
        "orchestrate_selected_tracks",
        "piano_to_ensemble",
        "simplify_arrangement",
        "remove_accompaniment",
    }
)
_RETAIN_SOURCE_OPERATIONS = frozenset(
    {
        "add_accompaniment",
        "increase_texture_density",
        "create_countermelody",
        "double_melody",
        "change_instrumentation",
        "decrease_texture_density",
    }
)


@dataclass
class ArrangementChannelAllocationSummary:
    pitched_unique_count: int = 0
    shared_channel_count: int = 0
    drum_track_count: int = 0
    exhausted: bool = False


@dataclass
class ArrangementRealizationResult:
    composition: CompositionV2
    manifest: ArrangementTopologyManifest
    event_counts: ArrangementEventCounts
    before_inventory: list[ArrangementTrackInventoryItem]
    after_inventory: list[ArrangementTrackInventoryItem]
    warning_codes: list[str] = field(default_factory=list)
    target_profile_fingerprints: list[ArrangementTargetProfileFingerprint] = field(
        default_factory=list
    )
    motif_reconciliation_count: int = 0
    id_collision_count: int = 0
    channel_allocation: ArrangementChannelAllocationSummary = field(
        default_factory=ArrangementChannelAllocationSummary
    )
    codes: list[str] = field(default_factory=list)


@dataclass
class _TargetBuild:
    part_id: str
    action: str
    profile: InstrumentProfile
    role: str
    source_track_ids: list[str]
    events: list[CompositionV2NoteEvent] = field(default_factory=list)
    retain_track_id: str | None = None
    relationship: str = "redistributed"


@dataclass
class _RealizationState:
    used_ids: set[str]
    id_collision_count: int = 0
    copied: int = 0
    moved: int = 0
    generated: int = 0
    removed: int = 0
    octave_adjusted: int = 0
    unchanged: int = 0
    # old_event_id → (new_event_id, target_track_id) for primary remaps (not doubles)
    primary_event_map: dict[str, tuple[str, str]] = field(default_factory=dict)
    removed_event_ids: set[str] = field(default_factory=set)
    claimed_source_refs: set[str] = field(default_factory=set)
    warning_codes: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)


def realize_arrangement_draft(
    request: CompositionArrangementPreviewRequest,
    draft: CompositionArrangementDraft,
    *,
    context: ArrangementSourceContext | None = None,
    catalog: ArrangementInstrumentCatalog | None = None,
    candidate_ordinal: int = 1,
) -> ArrangementRealizationResult:
    """Realize one arrangement draft into a strict CompositionV2 candidate."""
    source_snapshot = request.composition.model_dump(mode="json")
    loaded_catalog = catalog or get_catalog()
    ctx = context or build_arrangement_source_context(request, catalog=loaded_catalog)
    # Work from the context's immutable deep copy — never mutate request.composition.
    composition = CompositionV2.model_validate(
        copy.deepcopy(ctx.composition.model_dump(mode="json"))
    )

    state = _RealizationState(used_ids=collect_used_composition_ids(composition))
    after_by_id = {part.part_id: part for part in request.instrumentation.after}
    before_by_id = {part.part_id: part for part in request.instrumentation.before}
    source_set = set(request.source_track_ids)
    protected_set = set(request.protected_track_ids)
    track_by_id = {track.id: track for track in composition.tracks}
    melody_protected = _melody_protected_track_ids(ctx, request)

    _validate_draft_shape(
        request=request,
        draft=draft,
        after_by_id=after_by_id,
        before_by_id=before_by_id,
        source_set=source_set,
        protected_set=protected_set,
    )

    removed_track_ids: set[str] = set()
    retained_track_ids: set[str] = set()
    reinstrumented_track_ids: set[str] = set()
    split_track_ids: set[str] = set()
    merged_track_ids: set[str] = set()
    mappings: list[ArrangementSourceTargetMapping] = []
    targets: list[_TargetBuild] = []
    profile_fps: dict[str, str] = {}

    # Sources used by redistribute/split/merge/remove are consumed (not silently kept).
    consumed_source_ids: set[str] = set()
    for draft_part in draft.parts:
        if draft_part.action in {"redistribute", "split", "merge", "remove"}:
            consumed_source_ids.update(draft_part.source_track_ids)

    # Process remove actions first.
    for draft_part in draft.parts:
        if draft_part.action != "remove":
            continue
        for track_id in _resolve_remove_track_ids(
            draft_part,
            before_by_id=before_by_id,
            source_set=source_set,
            protected_set=protected_set,
            track_by_id=track_by_id,
        ):
            if track_id in removed_track_ids:
                continue
            removed_track_ids.add(track_id)
            track = track_by_id[track_id]
            for event in track.events:
                if event.id:
                    state.removed_event_ids.add(event.id)
                state.removed += 1
            mappings.append(
                ArrangementSourceTargetMapping(
                    source_track_id=track_id,
                    target_track_id=track_id,
                    relationship="removed",
                )
            )

    # Realize after parts from non-remove draft actions.
    draft_after_parts = [part for part in draft.parts if part.action != "remove"]
    draft_by_after = {part.part_id: part for part in draft_after_parts}
    for after_part in request.instrumentation.after:
        draft_part = draft_by_after.get(after_part.part_id)
        if draft_part is None:
            if request.allow_unlisted_after:
                state.warning_codes.append("unlisted_after_allowed")
                continue
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                "Draft is missing a required after part",
                details={"part_id": after_part.part_id, "reason": "missing_after_part"},
            )
        profile = _resolve_after_profile(after_part.instrument_id, loaded_catalog)
        profile_fps[profile.instrument_id] = profile.fingerprint
        role = after_part.role or (
            normalize_role(track_by_id[draft_part.source_track_ids[0]].role)
            if len(draft_part.source_track_ids) == 1 and draft_part.source_track_ids[0] in track_by_id
            else "other"
        )
        target = _realize_target_part(
            request=request,
            draft_part=draft_part,
            after_part_id=after_part.part_id,
            profile=profile,
            role=role,
            ctx=ctx,
            composition=composition,
            track_by_id=track_by_id,
            state=state,
            candidate_ordinal=candidate_ordinal,
            melody_protected=melody_protected,
            removed_track_ids=removed_track_ids,
        )
        targets.append(target)
        if draft_part.action == "reinstrument" and target.retain_track_id:
            reinstrumented_track_ids.add(target.retain_track_id)
            retained_track_ids.add(target.retain_track_id)
        elif draft_part.action == "retain" and target.retain_track_id:
            retained_track_ids.add(target.retain_track_id)
        elif draft_part.action == "split":
            split_track_ids.update(target.source_track_ids)
        elif draft_part.action == "merge":
            merged_track_ids.update(target.source_track_ids)

        for source_track_id in target.source_track_ids:
            mappings.append(
                ArrangementSourceTargetMapping(
                    source_track_id=source_track_id,
                    target_track_id=target.retain_track_id or after_part.part_id,
                    relationship=target.relationship,  # type: ignore[arg-type]
                )
            )

    # Consume selected source tracks when redistributed/removed or operation replaces them.
    for track_id in request.source_track_ids:
        if track_id in retained_track_ids or track_id in reinstrumented_track_ids:
            continue
        if track_id in removed_track_ids:
            continue
        if track_id in protected_set:
            continue
        should_consume = (
            track_id in consumed_source_ids
            or request.operation in _OPERATIONS_CONSUMING_SELECTED
        )
        if not should_consume:
            continue
        removed_track_ids.add(track_id)
        track = track_by_id[track_id]
        for event in track.events:
            if event.id and event.id not in state.primary_event_map:
                if event.id not in state.removed_event_ids:
                    state.removed_event_ids.add(event.id)
        if not any(m.source_track_id == track_id for m in mappings):
            mappings.append(
                ArrangementSourceTargetMapping(
                    source_track_id=track_id,
                    target_track_id=track_id,
                    relationship="removed",
                )
            )

    # Unselected / protected tracks remain byte-exact unless reinstrumented.
    result_tracks: list[CompositionV2Track] = []
    realized_retain_ids = {t.retain_track_id for t in targets if t.retain_track_id}
    for track in composition.tracks:
        if track.id in removed_track_ids and track.id not in realized_retain_ids:
            continue
        if track.id in source_set and track.id not in realized_retain_ids:
            if track.id in removed_track_ids or track.id in consumed_source_ids:
                continue
            if request.operation in _OPERATIONS_CONSUMING_SELECTED:
                continue
            if request.operation in _RETAIN_SOURCE_OPERATIONS:
                # Keep selected source exact when not consumed by redistribute/remove.
                result_tracks.append(track.model_copy(deep=True))
                state.unchanged += len(track.events)
                retained_track_ids.add(track.id)
                continue
            continue
        if track.id in realized_retain_ids:
            continue
        # Unselected or protected: preserve exactly.
        result_tracks.append(track.model_copy(deep=True))
        if track.id not in source_set:
            state.unchanged += len(track.events)

    # Materialize target tracks (new or reinstrumented).
    part_track_ids: dict[str, str] = {}
    added_track_ids: list[str] = []
    for target in targets:
        track, is_new = _materialize_target_track(
            target=target,
            composition=composition,
            state=state,
            candidate_ordinal=candidate_ordinal,
        )
        part_track_ids[target.part_id] = track.id
        # Update mappings that used part_id as placeholder target.
        for index, mapping in enumerate(mappings):
            if mapping.target_track_id == target.part_id:
                mappings[index] = mapping.model_copy(update={"target_track_id": track.id})
        # Update primary event map track ids that used temporary part id.
        for event_id, (new_id, mapped_track) in list(state.primary_event_map.items()):
            if mapped_track == target.part_id:
                state.primary_event_map[event_id] = (new_id, track.id)
        if is_new:
            added_track_ids.append(track.id)
        # Replace existing retain slot or append.
        if target.retain_track_id:
            replaced = False
            for index, existing in enumerate(result_tracks):
                if existing.id == target.retain_track_id:
                    result_tracks[index] = track
                    replaced = True
                    break
            if not replaced:
                result_tracks.append(track)
        else:
            result_tracks.append(track)

    # Allocate channels for changed/created targets; preserved tracks keep channels.
    channel_summary = _allocate_channels(
        result_tracks,
        changed_track_ids=set(added_track_ids) | reinstrumented_track_ids,
        state=state,
    )

    # Sort events per track; order tracks: original relative order for retained,
    # then after-part order for newly added.
    result_tracks = _order_tracks(
        result_tracks,
        original_ids=[track.id for track in composition.tracks],
        after_part_track_ids=[part_track_ids[p.part_id] for p in request.instrumentation.after if p.part_id in part_track_ids],
    )
    for index, track in enumerate(result_tracks):
        sorted_events = sort_note_events_deterministically(track.events)
        result_tracks[index] = track.model_copy(update={"events": sorted_events})

    motifs, motif_count, motif_warnings = _reconcile_motifs(
        composition.motifs,
        state=state,
        result_tracks=result_tracks,
    )
    state.warning_codes.extend(motif_warnings)

    candidate_payload = {
        **composition.model_dump(mode="json"),
        "tracks": [track.model_dump(mode="json") for track in result_tracks],
        "motifs": [motif.model_dump(mode="json") for motif in motifs],
    }
    result = CompositionV2.model_validate(candidate_payload)

    # Shared-channel program/CC validation for the realized topology.
    try:
        assert_shared_channel_program_compatible(result.tracks)
        assert_shared_channel_control_compatible(result.tracks)
    except CompositionMidiError as exc:
        raise CompositionArrangementError(
            "arrangement_draft_invalid",
            "Realized arrangement has unsafe MIDI channel allocation",
            details={"reason": "shared_channel_conflict", "error_type": type(exc).__name__},
        ) from exc

    integrity = validate_composition_integrity(result, profile="canonical")
    # Practical role/instrument pitch ranges are Task 5 arrangement validation.
    # Keep structural bounds/schema/motif integrity failures here.
    structural_errors = [
        error
        for error in integrity.errors
        if not (
            error.code == "event_out_of_range"
            and isinstance(error.context, dict)
            and "allowed_low" in error.context
        )
    ]
    if structural_errors:
        raise CompositionArrangementError(
            "arrangement_draft_invalid",
            "Realized arrangement failed composition integrity validation",
            details={
                "error_count": len(structural_errors),
                "error_codes": [error.code for error in structural_errors][:16],
            },
        )

    # Every accepted target must have explicit events[].
    after_ids = set(part_track_ids.values())
    for track in result.tracks:
        if track.id in after_ids and not track.events:
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                "Arrangement target tracks must contain explicit events",
                details={"reason": "empty_target_events"},
            )

    if request.composition.model_dump(mode="json") != source_snapshot:
        raise CompositionArrangementError(
            "arrangement_internal_error",
            "Source composition was mutated during arrangement realization",
            http_status=500,
        )

    before_inventory = [
        ArrangementTrackInventoryItem(
            track_id=item.track_id,
            instrument=item.instrument,
            role=item.role,
            midi_program=item.midi_program,
            event_count=item.event_count,
            part_id=item.part_id,
        )
        for item in ctx.actual_before_inventory
    ]
    after_inventory = [
        ArrangementTrackInventoryItem(
            track_id=track.id,
            instrument=track.instrument,
            role=track.role,
            midi_program=track.midi_program,
            event_count=len(track.events),
            part_id=next((pid for pid, tid in part_track_ids.items() if tid == track.id), None),
        )
        for track in result.tracks
    ]

    original_order = [track.id for track in composition.tracks]
    new_order = [track.id for track in result.tracks]
    reordered = [
        track_id
        for track_id in new_order
        if track_id in set(original_order) and original_order.index(track_id) != new_order.index(track_id)
    ] if set(original_order) & set(new_order) else []

    manifest = ArrangementTopologyManifest(
        retained_track_ids=sorted(retained_track_ids & set(new_order)),
        removed_track_ids=sorted(removed_track_ids - set(new_order)),
        added_track_ids=list(added_track_ids),
        reordered_track_ids=reordered,
        reinstrumented_track_ids=sorted(reinstrumented_track_ids),
        split_track_ids=sorted(split_track_ids),
        merged_track_ids=sorted(merged_track_ids),
        source_to_target=_dedupe_mappings(mappings),
    )
    event_counts = ArrangementEventCounts(
        copied=state.copied,
        moved=state.moved,
        generated=state.generated,
        removed=state.removed,
        octave_adjusted=state.octave_adjusted,
        unchanged=state.unchanged,
    )
    warning_codes = list(dict.fromkeys(state.warning_codes))
    if state.octave_adjusted:
        warning_codes.append("octave_adjustment_applied")
    if any(code == "motif_occurrence_pruned" for code in warning_codes):
        pass
    codes = list(dict.fromkeys(state.codes + ["arrangement_realized"]))

    logger.info(
        "Realized composition arrangement draft",
        extra={
            "operation": request.operation,
            "added_track_count": len(added_track_ids),
            "removed_track_count": len(manifest.removed_track_ids),
            "reinstrumented_track_count": len(reinstrumented_track_ids),
            "copied_event_count": state.copied,
            "generated_event_count": state.generated,
            "removed_event_count": state.removed,
            "octave_adjusted_event_count": state.octave_adjusted,
            "channel_pitched_unique": channel_summary.pitched_unique_count,
            "channel_shared": channel_summary.shared_channel_count,
            "motif_reconciliation_count": motif_count,
            "codes": codes[:16],
        },
    )
    logger.debug(
        "Arrangement realization diagnostics",
        extra={
            "per_track_event_counts": {track.id: len(track.events) for track in result.tracks},
            "id_collision_count": state.id_collision_count,
            "claimed_source_ref_count": len(state.claimed_source_refs),
            "primary_event_map_count": len(state.primary_event_map),
        },
    )

    return ArrangementRealizationResult(
        composition=result,
        manifest=manifest,
        event_counts=event_counts,
        before_inventory=before_inventory,
        after_inventory=after_inventory,
        warning_codes=warning_codes,
        target_profile_fingerprints=[
            ArrangementTargetProfileFingerprint(
                instrument_id=instrument_id,
                profile_fingerprint=fingerprint,
            )
            for instrument_id, fingerprint in sorted(profile_fps.items())
        ],
        motif_reconciliation_count=motif_count,
        id_collision_count=state.id_collision_count,
        channel_allocation=channel_summary,
        codes=codes,
    )


def _validate_draft_shape(
    *,
    request: CompositionArrangementPreviewRequest,
    draft: CompositionArrangementDraft,
    after_by_id: Mapping[str, Any],
    before_by_id: Mapping[str, Any],
    source_set: set[str],
    protected_set: set[str],
) -> None:
    for part in draft.parts:
        if part.action == "remove":
            if part.part_id not in before_by_id and not part.source_track_ids:
                raise CompositionArrangementError(
                    "arrangement_draft_invalid",
                    "Remove draft part must reference a before part or source tracks",
                    details={"part_id": part.part_id, "reason": "remove_target_unresolved"},
                )
            continue
        if part.part_id not in after_by_id:
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                "Draft part_id must match an after instrumentation part",
                details={"part_id": part.part_id, "reason": "unknown_after_part"},
            )
        for track_id in part.source_track_ids:
            if track_id not in source_set:
                raise CompositionArrangementError(
                    "arrangement_draft_invalid",
                    "Draft source_track_ids must be selected source tracks",
                    details={"reason": "source_track_not_selected"},
                )
            if track_id in protected_set and part.action not in {"retain", "reinstrument"}:
                raise CompositionArrangementError(
                    "arrangement_draft_invalid",
                    "Protected tracks cannot be redistributed or removed",
                    details={"reason": "protected_track_mutation"},
                )
        if part.action == "reinstrument" and request.operation == "change_instrumentation":
            if part.notes or (part.source_note_refs and False):
                # reinstrument may omit refs (copy all events from single source).
                pass
        if part.action in {"add", "double", "create"} and not part.notes and not part.source_note_refs:
            if part.action in {"add", "double"}:
                raise CompositionArrangementError(
                    "arrangement_draft_invalid",
                    "Add/double draft parts require notes or source_note_refs",
                    details={"part_id": part.part_id, "reason": "empty_generated_part"},
                )


def _resolve_remove_track_ids(
    draft_part: ArrangementDraftPart,
    *,
    before_by_id: Mapping[str, Any],
    source_set: set[str],
    protected_set: set[str],
    track_by_id: Mapping[str, CompositionV2Track],
) -> list[str]:
    ids: list[str] = []
    if draft_part.source_track_ids:
        ids.extend(draft_part.source_track_ids)
    elif draft_part.part_id in before_by_id:
        before = before_by_id[draft_part.part_id]
        if before.source_track_ids:
            ids.extend(before.source_track_ids)
    for track_id in ids:
        if track_id not in source_set:
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                "Cannot remove a track outside the source selection",
                details={"reason": "remove_not_selected"},
            )
        if track_id in protected_set:
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                "Cannot remove a protected track",
                details={"reason": "remove_protected"},
            )
        track = track_by_id.get(track_id)
        if track is None:
            raise CompositionArrangementError(
                "arrangement_invalid_source",
                details={"reason": "remove_unknown_track"},
            )
        role = normalize_role(track.role) if track.role else "other"
        if role in _MELODY_LEAD_ROLES:
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                "Cannot remove melody/lead tracks",
                details={"reason": "remove_melody"},
            )
    return list(dict.fromkeys(ids))


def _resolve_after_profile(
    instrument_id: str,
    catalog: ArrangementInstrumentCatalog,
) -> InstrumentProfile:
    profile = resolve_profile(instrument_id=instrument_id, catalog=catalog)
    if profile is None:
        profile = resolve_profile(alias=instrument_id, catalog=catalog)
    if profile is None:
        raise CompositionArrangementError(
            "arrangement_inventory_mismatch",
            details={"reason": "unknown_after_instrument", "instrument_id": instrument_id[:80]},
        )
    return profile


def _melody_protected_track_ids(
    ctx: ArrangementSourceContext,
    request: CompositionArrangementPreviewRequest,
) -> set[str]:
    protected = set(request.protected_track_ids)
    if request.preserve_melody:
        protected.update(ctx.melody_lead.track_ids)
        for view in ctx.track_roles:
            if view.authored_role in _MELODY_LEAD_ROLES:
                protected.add(view.track_id)
    return protected


def _realize_target_part(
    *,
    request: CompositionArrangementPreviewRequest,
    draft_part: ArrangementDraftPart,
    after_part_id: str,
    profile: InstrumentProfile,
    role: str,
    ctx: ArrangementSourceContext,
    composition: CompositionV2,
    track_by_id: Mapping[str, CompositionV2Track],
    state: _RealizationState,
    candidate_ordinal: int,
    melody_protected: set[str],
    removed_track_ids: set[str],
) -> _TargetBuild:
    action = draft_part.action
    source_ids = list(draft_part.source_track_ids)
    relationship = {
        "retain": "retained",
        "reinstrument": "reinstrumented",
        "add": "redistributed",
        "split": "split",
        "merge": "merged",
        "redistribute": "redistributed",
        "double": "doubled",
    }.get(action, "redistributed")

    retain_track_id: str | None = None
    if action in {"retain", "reinstrument"}:
        if len(source_ids) != 1:
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                f"{action} requires exactly one source_track_id",
                details={"part_id": after_part_id, "reason": "retain_source_arity"},
            )
        retain_track_id = source_ids[0]
        if retain_track_id in removed_track_ids:
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                "Cannot retain a removed track",
                details={"reason": "retain_removed"},
            )

    events: list[CompositionV2NoteEvent] = []
    if action == "retain":
        source_track = track_by_id[retain_track_id]  # type: ignore[index]
        events = [event.model_copy(deep=True) for event in source_track.events]
        state.unchanged += len(events)
    elif action == "reinstrument":
        source_track = track_by_id[retain_track_id]  # type: ignore[index]
        # Exact event copy (same ids) — metadata-only reinstrumentation.
        events = [event.model_copy(deep=True) for event in source_track.events]
        state.unchanged += len(events)
        state.codes.append("reinstrumented")
    else:
        # Copy referenced source notes and/or materialize generated notes.
        is_double = action == "double"
        is_move = action in {"redistribute", "split", "merge"} and not is_double
        events.extend(
            _copy_source_note_refs(
                draft_part.source_note_refs,
                ctx=ctx,
                composition=composition,
                track_by_id=track_by_id,
                state=state,
                candidate_ordinal=candidate_ordinal,
                target_key=after_part_id,
                profile=profile,
                request=request,
                melody_protected=melody_protected,
                as_primary=is_move and not is_double,
                is_double=is_double,
            )
        )
        for index, note in enumerate(draft_part.notes):
            events.append(
                _materialize_generated_note(
                    note,
                    index=index,
                    state=state,
                    candidate_ordinal=candidate_ordinal,
                    target_key=after_part_id,
                    profile=profile,
                    request=request,
                    melody_protected=melody_protected,
                    source_track_ids=source_ids,
                )
            )

    if action in {"redistribute", "split", "merge", "add", "double"} and not events:
        raise CompositionArrangementError(
            "arrangement_draft_invalid",
            "Target part produced no explicit events",
            details={"part_id": after_part_id, "reason": "empty_target_events"},
        )

    return _TargetBuild(
        part_id=after_part_id,
        action=action,
        profile=profile,
        role=role,
        source_track_ids=source_ids,
        events=events,
        retain_track_id=retain_track_id,
        relationship=relationship,
    )


def _copy_source_note_refs(
    refs: Sequence[str],
    *,
    ctx: ArrangementSourceContext,
    composition: CompositionV2,
    track_by_id: Mapping[str, CompositionV2Track],
    state: _RealizationState,
    candidate_ordinal: int,
    target_key: str,
    profile: InstrumentProfile,
    request: CompositionArrangementPreviewRequest,
    melody_protected: set[str],
    as_primary: bool,
    is_double: bool,
) -> list[CompositionV2NoteEvent]:
    events: list[CompositionV2NoteEvent] = []
    for ref in refs:
        if ref in state.claimed_source_refs and as_primary:
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                "Source note reference claimed by multiple primary targets",
                details={"reason": "duplicate_primary_ref"},
            )
        note = lookup_source_note(ctx, ref)
        source_track = track_by_id.get(note.track_id)
        if source_track is None:
            raise CompositionArrangementError(
                "arrangement_invalid_source",
                details={"reason": "missing_source_track_for_ref"},
            )
        members = _source_events_for_note(note, source_track)
        if not members:
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                "Referenced source material could not be resolved",
                details={"reason": "missing_referenced_material"},
            )
        if as_primary:
            state.claimed_source_refs.add(ref)

        adjusted_pitch = note.pitch
        octave_shifted = False
        if not profile.is_drum and profile.range_policy == "absolute":
            adjusted_pitch, octave_shifted = _maybe_octave_fold(
                pitch=note.pitch,
                midi_number=note.midi_number,
                profile=profile,
                request=request,
                source_track_id=note.track_id,
                melody_protected=melody_protected,
            )
        # Exact copy preserves timing/velocity/articulation/staff/voice/ties.
        # Octave fold only rewrites pitch (pitch class preserved).
        tie_group_map: dict[str, str] = {}
        for member_index, member in enumerate(members):
            new_id, collided = allocate_deterministic_id(
                "arr-ev",
                [
                    candidate_ordinal,
                    target_key,
                    ref,
                    member_index,
                    member.start_tick,
                    member.pitch if not octave_shifted else adjusted_pitch,
                ],
                state.used_ids,
            )
            state.id_collision_count += int(collided)
            tie = None
            if member.tie is not None:
                old_group = member.tie.group_id
                if old_group not in tie_group_map:
                    new_group, group_collided = allocate_deterministic_id(
                        "arr-tie",
                        [candidate_ordinal, target_key, ref, old_group],
                        state.used_ids,
                    )
                    state.id_collision_count += int(group_collided)
                    tie_group_map[old_group] = new_group
                tie = CompositionV2NoteTie(group_id=tie_group_map[old_group], type=member.tie.type)
            pitch = adjusted_pitch if octave_shifted else member.pitch
            # For multi-member ties with octave fold, shift every member by same PC offset.
            if octave_shifted and member.pitch != note.pitch:
                delta = midi_pitch_number(adjusted_pitch) - note.midi_number
                pitch = midi_number_to_pitch(
                    midi_pitch_number(member.pitch) + delta,
                    key=composition.key,
                )
            events.append(
                CompositionV2NoteEvent(
                    id=new_id,
                    pitch=pitch,
                    start_tick=member.start_tick,
                    duration_ticks=member.duration_ticks,
                    velocity=member.velocity,
                    staff=member.staff,
                    voice=member.voice,
                    articulations=list(member.articulations),
                    tie=tie,
                )
            )
            if as_primary and member.id:
                state.primary_event_map[member.id] = (new_id, target_key)
                state.removed_event_ids.add(member.id)
            if is_double:
                state.copied += 1
            elif as_primary:
                state.moved += 1
            else:
                state.copied += 1
            if octave_shifted:
                state.octave_adjusted += 1
    return events


def _source_events_for_note(
    note: ArrangementSourceNoteRef,
    track: CompositionV2Track,
) -> list[CompositionV2NoteEvent]:
    """Resolve exact source events for a logical note (including full tie chains)."""
    if note.source_event_ids:
        by_id = {event.id: event for event in track.events if event.id}
        members = [by_id[event_id] for event_id in note.source_event_ids if event_id in by_id]
        if len(members) == len(note.source_event_ids):
            if note.tie_group_id:
                # Ensure complete tie chain, not a truncated subset.
                groups = group_events_by_tie(track.events)
                full = groups.get(note.tie_group_id, [])
                if full and len(full) != len(members):
                    raise CompositionArrangementError(
                        "arrangement_draft_invalid",
                        "Refusing to truncate a tie chain during arrangement copy",
                        details={"reason": "tie_truncation"},
                    )
            return sorted(members, key=lambda item: (item.start_tick, item.duration_ticks))
    if note.tie_group_id:
        groups = group_events_by_tie(track.events)
        members = groups.get(note.tie_group_id, [])
        return sorted(members, key=lambda item: (item.start_tick, item.duration_ticks))
    # ID-less standalone: match by semantic identity.
    matches = [
        event
        for event in track.events
        if event.tie is None
        and event.pitch == note.pitch
        and event.start_tick == note.start_tick
        and event.duration_ticks == note.duration_ticks
        and event.velocity == note.velocity
    ]
    return matches[:1]


def _materialize_generated_note(
    note: ArrangementDraftNote,
    *,
    index: int,
    state: _RealizationState,
    candidate_ordinal: int,
    target_key: str,
    profile: InstrumentProfile,
    request: CompositionArrangementPreviewRequest,
    melody_protected: set[str],
    source_track_ids: Sequence[str],
) -> CompositionV2NoteEvent:
    pitch = note.pitch
    midi = midi_pitch_number(pitch)
    octave_shifted = False
    # Generated notes are never protected melody source material.
    if not profile.is_drum and profile.range_policy == "absolute":
        pitch, octave_shifted = _maybe_octave_fold(
            pitch=pitch,
            midi_number=midi,
            profile=profile,
            request=request,
            source_track_id=None,
            melody_protected=melody_protected,
            force_unprotected=True,
        )
    event_id, collided = allocate_deterministic_id(
        "arr-ev",
        [
            candidate_ordinal,
            target_key,
            "gen",
            index,
            note.relative_start_tick,
            pitch,
            note.duration_ticks,
        ],
        state.used_ids,
    )
    state.id_collision_count += int(collided)
    tie = None
    if note.tie is not None:
        group_id, group_collided = allocate_deterministic_id(
            "arr-tie",
            [candidate_ordinal, target_key, "gen", note.tie.group_id],
            state.used_ids,
        )
        state.id_collision_count += int(group_collided)
        tie = CompositionV2NoteTie(group_id=group_id, type=note.tie.type)
    state.generated += 1
    if octave_shifted:
        state.octave_adjusted += 1
    return CompositionV2NoteEvent(
        id=event_id,
        pitch=pitch,
        start_tick=note.relative_start_tick,
        duration_ticks=note.duration_ticks,
        velocity=note.velocity,
        staff=note.staff,
        voice=note.voice,
        articulations=list(note.articulations),
        tie=tie,
    )


def _maybe_octave_fold(
    *,
    pitch: str,
    midi_number: int,
    profile: InstrumentProfile,
    request: CompositionArrangementPreviewRequest,
    source_track_id: str | None,
    melody_protected: set[str],
    force_unprotected: bool = False,
) -> tuple[str, bool]:
    low = profile.playable_low
    high = profile.playable_high
    if low is None or high is None:
        return pitch, False
    if low <= midi_number <= high:
        return pitch, False
    protected = (source_track_id in melody_protected) if source_track_id else False
    if protected and not force_unprotected:
        # Never clamp/fold protected melody — Task 5 validates hard failure.
        return pitch, False
    if request.range_adjustment != "octave_shift_unprotected":
        return pitch, False
    # Fold by octaves toward the playable window, preserving pitch class.
    shifted = midi_number
    while shifted < low:
        shifted += 12
    while shifted > high:
        shifted -= 12
    if shifted < low or shifted > high:
        # Cannot fold into range without changing pitch class.
        return pitch, False
    if shifted == midi_number:
        return pitch, False
    return midi_number_to_pitch(shifted, key=request.composition.key), True


def _materialize_target_track(
    *,
    target: _TargetBuild,
    composition: CompositionV2,
    state: _RealizationState,
    candidate_ordinal: int,
) -> tuple[CompositionV2Track, bool]:
    profile = target.profile
    if target.retain_track_id:
        source = next(track for track in composition.tracks if track.id == target.retain_track_id)
        track = source.model_copy(
            update={
                "instrument": profile.display_name[:80],
                "role": target.role,
                "midi_program": profile.midi_program,
                "is_drum": profile.is_drum,
                "channel": _DRUM_CHANNEL if profile.is_drum else source.channel,
                "events": target.events,
                # Do not accept provider channels/programs — catalog wins for reinstrument.
            }
        )
        return track, False

    track_id, collided = allocate_deterministic_id(
        "arr-tr",
        [candidate_ordinal, target.part_id, profile.instrument_id, target.role],
        state.used_ids,
    )
    state.id_collision_count += int(collided)
    track = CompositionV2Track(
        id=track_id,
        name=f"{profile.display_name} ({target.role})"[:120],
        instrument=profile.display_name[:80],
        role=target.role,
        midi_program=profile.midi_program,
        channel=_DRUM_CHANNEL if profile.is_drum else 1,  # placeholder; allocated later
        is_drum=profile.is_drum,
        events=target.events,
    )
    return track, True


def _allocate_channels(
    tracks: list[CompositionV2Track],
    *,
    changed_track_ids: set[str],
    state: _RealizationState,
) -> ArrangementChannelAllocationSummary:
    """Allocate GM channels for changed tracks; share only when program/CC compatible."""
    # Preserve channels on unchanged tracks; recompute only for changed set.
    occupied: dict[int, list[CompositionV2Track]] = {}
    for track in tracks:
        if track.id in changed_track_ids:
            continue
        occupied.setdefault(int(track.channel), []).append(track)

    shared = 0
    for index, track in enumerate(tracks):
        if track.id not in changed_track_ids:
            continue
        if track.is_drum:
            tracks[index] = track.model_copy(update={"channel": _DRUM_CHANNEL})
            occupied.setdefault(_DRUM_CHANNEL, []).append(tracks[index])
            continue
        assigned: int | None = None
        # Prefer sharing with an existing compatible same-program channel.
        for channel, group in occupied.items():
            if channel == _DRUM_CHANNEL:
                continue
            reference = group[0]
            if int(reference.midi_program) != int(track.midi_program):
                continue
            if any(item.is_drum for item in group):
                continue
            if tracks_share_channel_controllers(reference, track):
                assigned = channel
                shared += 1
                break
        if assigned is None:
            used_pitched = {ch for ch in occupied if ch != _DRUM_CHANNEL}
            for channel in _PITCHED_CHANNELS:
                if channel not in used_pitched:
                    assigned = channel
                    break
        if assigned is None:
            state.codes.append("channel_exhausted")
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                "More than 15 independently programmed pitched channels are required",
                details={
                    "reason": "channel_exhausted",
                    "max_pitched_channels": len(_PITCHED_CHANNELS),
                    "changed_track_count": len(changed_track_ids),
                },
            )
        tracks[index] = track.model_copy(update={"channel": assigned})
        occupied.setdefault(assigned, []).append(tracks[index])

    pitched_unique = len({ch for ch in occupied if ch != _DRUM_CHANNEL})
    drum_count = len(occupied.get(_DRUM_CHANNEL, []))
    summary = ArrangementChannelAllocationSummary(
        pitched_unique_count=pitched_unique,
        shared_channel_count=shared,
        drum_track_count=drum_count,
        exhausted=False,
    )
    state.codes.append("channel_allocated")
    return summary


def _order_tracks(
    tracks: list[CompositionV2Track],
    *,
    original_ids: Sequence[str],
    after_part_track_ids: Sequence[str],
) -> list[CompositionV2Track]:
    by_id = {track.id: track for track in tracks}
    ordered: list[CompositionV2Track] = []
    seen: set[str] = set()
    for track_id in original_ids:
        track = by_id.get(track_id)
        if track is None or track_id in seen:
            continue
        ordered.append(track)
        seen.add(track_id)
    for track_id in after_part_track_ids:
        track = by_id.get(track_id)
        if track is None or track_id in seen:
            continue
        ordered.append(track)
        seen.add(track_id)
    for track in tracks:
        if track.id in seen:
            continue
        ordered.append(track)
        seen.add(track.id)
    return ordered


def _reconcile_motifs(
    motifs: Sequence[CompositionV2MotifDefinition],
    *,
    state: _RealizationState,
    result_tracks: Sequence[CompositionV2Track],
) -> tuple[list[CompositionV2MotifDefinition], int, list[str]]:
    if not motifs:
        return [], 0, []

    result_event_ids = {
        event.id
        for track in result_tracks
        for event in track.events
        if event.id
    }
    warnings: list[str] = []
    remapped: list[CompositionV2MotifDefinition] = []
    reconciliation_count = 0

    for motif in motifs:
        surviving: list[CompositionV2MotifOccurrence] = []
        original_invalidated = False
        for occurrence in motif.occurrences:
            event_ids = list(occurrence.event_ids)
            targets: set[str] = set()
            new_ids: list[str] = []
            missing = 0
            for event_id in event_ids:
                if event_id in state.primary_event_map:
                    new_id, track_id = state.primary_event_map[event_id]
                    new_ids.append(new_id)
                    targets.add(track_id)
                elif event_id in state.removed_event_ids:
                    missing += 1
                elif event_id in result_event_ids:
                    # Unmoved event still present on original track.
                    new_ids.append(event_id)
                    targets.add(occurrence.track_id)
                else:
                    missing += 1

            if missing and not new_ids:
                # Explicitly removed — prune occurrence.
                warnings.append("motif_occurrence_pruned")
                reconciliation_count += 1
                if occurrence.relationship == "original":
                    original_invalidated = True
                continue
            if missing and new_ids:
                # Partial removal without full remap — prune rather than invent.
                warnings.append("motif_occurrence_pruned")
                reconciliation_count += 1
                if occurrence.relationship == "original":
                    original_invalidated = True
                continue
            if len(targets) > 1:
                raise CompositionArrangementError(
                    "arrangement_draft_invalid",
                    "Motif occurrence events cannot split across arrangement targets",
                    details={
                        "reason": "motif_occurrence_split",
                        "motif_id": motif.id,
                        "target_count": len(targets),
                    },
                )
            target_track = next(iter(targets)) if targets else occurrence.track_id
            if new_ids != event_ids or target_track != occurrence.track_id:
                reconciliation_count += 1
                surviving.append(
                    occurrence.model_copy(
                        update={"track_id": target_track, "event_ids": new_ids}
                    )
                )
            else:
                surviving.append(occurrence)

        if original_invalidated or not surviving:
            warnings.append("motif_occurrence_pruned")
            continue
        if not any(item.relationship == "original" for item in surviving):
            warnings.append("motif_occurrence_pruned")
            continue
        remapped.append(motif.model_copy(update={"occurrences": surviving}))

    # Also run standard prune for any leftover removed ids not handled above.
    pruned = reconcile_motifs_for_removed_event_ids(
        remapped,
        state.removed_event_ids - set(state.primary_event_map.keys()),
    )
    for warning in pruned.warnings:
        code = getattr(warning, "code", None) or "motif_occurrence_pruned"
        warnings.append(str(code))
        reconciliation_count += 1
    return list(pruned.motifs), reconciliation_count, list(dict.fromkeys(warnings))


def _dedupe_mappings(
    mappings: Sequence[ArrangementSourceTargetMapping],
) -> list[ArrangementSourceTargetMapping]:
    seen: set[tuple[str, str, str]] = set()
    result: list[ArrangementSourceTargetMapping] = []
    for mapping in mappings:
        key = (mapping.source_track_id, mapping.target_track_id, mapping.relationship)
        if key in seen:
            continue
        seen.add(key)
        result.append(mapping)
    return result
