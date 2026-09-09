"""Deterministic arrangement context, instrumentation preconditions, and bounded projection.

Validates source/protected authorization and explicit before inventory, assigns
request-local logical source-note references (including ID-less events and ties),
and builds a bounded provider projection. The complete selected-note reference
table is never truncated; advisory analysis/examples shrink first. Over-limit
selections raise CompositionArrangementError (422) before any provider call.

Analysis and harmony summaries are advisory only — never persisted and never used
as playable content. The source composition is deep-copied and left immutable.
"""

from __future__ import annotations

import copy
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from app.arrangement_schemas import (
    ARRANGEMENT_DEFAULT_CONTEXT_BUDGET_CHARS,
    ARRANGEMENT_MAX_DRAFT_SOURCE_NOTE_REFS,
    ARRANGEMENT_MAX_REASON_CHARS,
    ArrangementDensityMetrics,
    ArrangementPartRequirement,
    CompositionArrangementError,
    CompositionArrangementPreviewRequest,
    EDIT_FINGERPRINT_LOG_PREFIX_LEN,
)
from app.composition_schemas import (
    CompositionV2,
    CompositionV2HarmonyItem,
    CompositionV2Track,
    midi_pitch_number,
)
from app.services.composition_analysis import analyze_composition, build_llm_analysis_context
from app.services.composition_edit_fingerprint import (
    composition_edit_fingerprint,
    edit_fingerprint_log_prefix,
)
from app.services.composition_logical_notes import (
    CollapsedLogicalNote,
    clip_occupancy,
    collapse_track_tie_chains,
)
from app.services.composition_timeline import CompiledTimeline, compile_timeline
from app.services.instrument_catalog import (
    ArrangementInstrumentCatalog,
    InstrumentCatalogError,
    InstrumentProfile,
    get_catalog,
    resolve_profile,
    resolve_profile_for_track,
)
from app.services.instrument_identity import (
    normalize_instrument_identity,
    normalize_label_text,
    normalize_role,
)


logger = logging.getLogger(__name__)

ARRANGEMENT_CONTEXT_VERSION = "composition.arrangement.context.v1"
# Hard cap on selected logical notes (same bound as draft source_note_refs).
ARRANGEMENT_MAX_SELECTED_LOGICAL_NOTES = ARRANGEMENT_MAX_DRAFT_SOURCE_NOTE_REFS
# Reserved for operation/inventory/target metadata that must coexist with the note table.
_CONTEXT_FIXED_OVERHEAD_CHARS = 2_000
_MAX_HARMONY_SUMMARY_ITEMS = 24
_MAX_SECTION_SUMMARY_ITEMS = 16
_MAX_REGISTER_EXAMPLE_NOTES = 8
_MAX_ADVISORY_ANALYSIS_CHARS = 8_192
_MELODY_LEAD_ROLES = frozenset({"melody", "lead"})


@dataclass(frozen=True)
class ArrangementSourceNoteRef:
    """One request-local logical source note (tie chains collapsed)."""

    ref: str
    track_id: str
    start_tick: int
    duration_ticks: int
    pitch: str
    midi_number: int
    velocity: int
    staff: str | None
    voice: int | None
    articulations: tuple[str, ...]
    tie_group_id: str | None
    member_count: int
    source_event_ids: tuple[str, ...]
    is_drum: bool


@dataclass(frozen=True)
class ArrangementTrackRoleView:
    """Authored vs inferred roles kept separate; declared is authoritative."""

    track_id: str
    instrument: str
    instrument_id: str | None
    authored_role: str
    inferred_role: str | None
    effective_role: str
    is_drum: bool
    midi_program: int
    event_count: int
    logical_note_count: int
    pitch_min: int | None
    pitch_max: int | None


@dataclass(frozen=True)
class ArrangementMelodyLeadIdentity:
    """Effective melody/lead identity for preservation (declared authoritative)."""

    track_ids: tuple[str, ...]
    source: Literal["declared", "protected", "inferred", "none", "ambiguous"]
    authored_roles: tuple[str, ...]


@dataclass(frozen=True)
class ArrangementTargetRangeSummary:
    part_id: str
    instrument_id: str
    role: str | None
    range_policy: str
    playable_low: int | None
    playable_high: int | None
    preferred_low: int | None
    preferred_high: int | None
    profile_fingerprint: str


@dataclass(frozen=True)
class ArrangementActualInventoryItem:
    track_id: str
    instrument: str
    instrument_id: str | None
    role: str
    midi_program: int | None
    event_count: int
    part_id: str | None


@dataclass
class ArrangementSourceContext:
    """Structured bounded context for one arrangement preview request."""

    version: str
    composition: CompositionV2
    timeline: CompiledTimeline
    edit_source_fingerprint: str
    operation: str
    source_track_ids: tuple[str, ...]
    protected_track_ids: tuple[str, ...]
    source_notes: tuple[ArrangementSourceNoteRef, ...]
    note_by_ref: Mapping[str, ArrangementSourceNoteRef]
    track_roles: tuple[ArrangementTrackRoleView, ...]
    actual_before_inventory: tuple[ArrangementActualInventoryItem, ...]
    before_part_count: int
    after_part_count: int
    instrument_counts: Mapping[str, int]
    authored_role_counts: Mapping[str, int]
    inferred_role_counts: Mapping[str, int]
    melody_lead: ArrangementMelodyLeadIdentity
    density: ArrangementDensityMetrics
    register_summary: dict[str, Any]
    rhythm_summary: dict[str, Any]
    section_summary: tuple[dict[str, Any], ...]
    harmony_summary: tuple[dict[str, Any], ...]
    target_ranges: tuple[ArrangementTargetRangeSummary, ...]
    catalog_version: str
    range_policy_version: str
    catalog_fingerprint: str
    advisory_analysis: str | None
    truncated: bool
    truncation_reasons: tuple[str, ...] = field(default_factory=tuple)
    warning_codes: tuple[str, ...] = field(default_factory=tuple)
    context_char_count: int = 0
    analysis_advisory: bool = True
    prompt_include_track_summaries: bool = True
    prompt_include_target_ranges: bool = True


def build_arrangement_source_context(
    request: CompositionArrangementPreviewRequest,
    *,
    catalog: ArrangementInstrumentCatalog | None = None,
) -> ArrangementSourceContext:
    """Build immutable, bounded arrangement context or raise before provider use."""
    # Deep copy so callers cannot observe mutation of the request composition.
    composition = CompositionV2.model_validate(
        copy.deepcopy(request.composition.model_dump(mode="json"))
    )
    try:
        loaded_catalog = catalog or get_catalog()
    except InstrumentCatalogError as exc:
        raise CompositionArrangementError(
            "arrangement_catalog_unavailable",
            http_status=503,
            details={"validation_code": getattr(exc, "code", "catalog_invalid_schema")},
        ) from exc

    timeline = compile_timeline(composition)
    edit_fp = composition_edit_fingerprint(composition)

    source_ids = tuple(request.source_track_ids)
    protected_ids = tuple(request.protected_track_ids)
    track_by_id = {track.id: track for track in composition.tracks}

    _validate_track_authorization(track_by_id, source_ids, protected_ids)
    part_to_track = _validate_before_inventory(
        composition=composition,
        track_by_id=track_by_id,
        source_ids=source_ids,
        before_parts=request.instrumentation.before,
        catalog=loaded_catalog,
    )

    source_tracks = [track_by_id[track_id] for track_id in source_ids]
    source_notes, note_by_ref = _assign_source_note_refs(source_tracks)
    if len(source_notes) > ARRANGEMENT_MAX_SELECTED_LOGICAL_NOTES:
        logger.info(
            "Rejected over-limit arrangement selection",
            extra={
                "error_code": "arrangement_request_too_large",
                "logical_note_count": len(source_notes),
                "max_logical_notes": ARRANGEMENT_MAX_SELECTED_LOGICAL_NOTES,
                "operation": request.operation,
            },
        )
        raise CompositionArrangementError(
            "arrangement_request_too_large",
            http_status=422,
            details={
                "logical_note_count": len(source_notes),
                "max_logical_notes": ARRANGEMENT_MAX_SELECTED_LOGICAL_NOTES,
                "reason": "selected_logical_notes",
            },
        )

    warning_codes: list[str] = []
    truncation_reasons: list[str] = []

    track_roles, role_warnings = _build_track_role_views(
        composition=composition,
        source_ids=source_ids,
        protected_ids=protected_ids,
        catalog=loaded_catalog,
    )
    warning_codes.extend(role_warnings)

    melody_lead = _derive_melody_lead_identity(
        track_roles=track_roles,
        source_ids=source_ids,
        protected_ids=protected_ids,
    )
    if melody_lead.source == "ambiguous":
        warning_codes.append("ambiguous_role")

    actual_before = _actual_inventory(
        composition=composition,
        source_ids=source_ids,
        part_to_track=part_to_track,
        catalog=loaded_catalog,
    )
    instrument_counts = dict(
        Counter(
            item.instrument_id or normalize_instrument_identity(item.instrument) or item.instrument
            for item in actual_before
        )
    )
    authored_role_counts = dict(Counter(view.authored_role for view in track_roles if view.track_id in source_ids))
    inferred_role_counts = dict(
        Counter(view.inferred_role for view in track_roles if view.inferred_role and view.track_id in source_ids)
    )

    density = _compute_density_metrics(source_tracks, composition.duration_ticks)
    register_summary = _register_summary(source_tracks, source_notes)
    rhythm_summary = _rhythm_summary(source_notes, composition)
    section_summary, section_truncated = _section_summary(composition)
    if section_truncated:
        truncation_reasons.append("sections")

    harmony_summary, harmony_truncated = _harmony_summary(composition.harmony)
    if harmony_truncated:
        truncation_reasons.append("harmony")
    if not composition.harmony:
        warning_codes.append("empty_harmony_context")

    target_ranges = _target_range_summaries(request.instrumentation.after, loaded_catalog)

    budget = int(request.options.context_budget_chars or ARRANGEMENT_DEFAULT_CONTEXT_BUDGET_CHARS)
    note_table_chars = _measure_note_table_chars(source_notes)
    # Hard reject only when the non-truncatable note table itself cannot fit.
    # Fixed operation metadata is reserved separately and advisory content shrinks first.
    if note_table_chars > budget:
        logger.info(
            "Rejected arrangement context exceeding budget",
            extra={
                "error_code": "arrangement_request_too_large",
                "note_table_chars": note_table_chars,
                "context_budget_chars": budget,
                "logical_note_count": len(source_notes),
                "operation": request.operation,
            },
        )
        raise CompositionArrangementError(
            "arrangement_request_too_large",
            http_status=422,
            details={
                "note_table_chars": note_table_chars,
                "context_budget_chars": budget,
                "logical_note_count": len(source_notes),
                "reason": "selected_note_table_exceeds_budget",
            },
        )

    remaining_for_advisory = max(
        0,
        budget - note_table_chars - _CONTEXT_FIXED_OVERHEAD_CHARS,
    )
    advisory, advisory_warnings, advisory_truncated = _advisory_analysis_projection(
        composition,
        budget_chars=min(remaining_for_advisory, _MAX_ADVISORY_ANALYSIS_CHARS)
        if remaining_for_advisory > 0
        else 0,
    )
    warning_codes.extend(advisory_warnings)
    if advisory_truncated:
        truncation_reasons.append("advisory_analysis")
    if remaining_for_advisory < 400:
        advisory = None
        if "advisory_analysis" not in truncation_reasons:
            truncation_reasons.append("advisory_analysis")

    # Truncation order: shrink advisory/examples first; note table never truncated.
    register_summary, register_truncated = _maybe_truncate_register_examples(
        register_summary, remaining_for_advisory
    )
    if register_truncated:
        truncation_reasons.append("register_examples")

    truncated = bool(truncation_reasons) or ("context_truncated" in warning_codes)
    if truncated and "context_truncated" not in warning_codes:
        warning_codes.append("context_truncated")

    context = ArrangementSourceContext(
        version=ARRANGEMENT_CONTEXT_VERSION,
        composition=composition,
        timeline=timeline,
        edit_source_fingerprint=edit_fp,
        operation=request.operation,
        source_track_ids=source_ids,
        protected_track_ids=protected_ids,
        source_notes=source_notes,
        note_by_ref=note_by_ref,
        track_roles=track_roles,
        actual_before_inventory=actual_before,
        before_part_count=request.instrumentation.before_part_count,
        after_part_count=request.instrumentation.after_part_count,
        instrument_counts=instrument_counts,
        authored_role_counts=authored_role_counts,
        inferred_role_counts=inferred_role_counts,
        melody_lead=melody_lead,
        density=density,
        register_summary=register_summary,
        rhythm_summary=rhythm_summary,
        section_summary=section_summary,
        harmony_summary=harmony_summary,
        target_ranges=target_ranges,
        catalog_version=loaded_catalog.catalog_version,
        range_policy_version=loaded_catalog.range_policy_version,
        catalog_fingerprint=loaded_catalog.fingerprint,
        advisory_analysis=advisory,
        truncated=truncated,
        truncation_reasons=tuple(dict.fromkeys(truncation_reasons)),
        warning_codes=tuple(dict.fromkeys(warning_codes)),
        analysis_advisory=True,
    )
    _fit_context_to_budget(context, budget=budget)

    event_count = sum(len(track.events) for track in source_tracks)
    logger.info(
        "Built composition arrangement source context",
        extra={
            "operation": request.operation,
            "selected_track_count": len(source_ids),
            "protected_track_count": len(protected_ids),
            "before_part_count": request.instrumentation.before_part_count,
            "after_part_count": request.instrumentation.after_part_count,
            "event_count": event_count,
            "logical_note_count": len(source_notes),
            "context_char_count": context.context_char_count,
            "context_budget_chars": budget,
            "truncated": context.truncated,
            "fingerprint_prefix": edit_fingerprint_log_prefix(edit_fp),
            "prefix_len": EDIT_FINGERPRINT_LOG_PREFIX_LEN,
        },
    )
    logger.debug(
        "Arrangement context diagnostics",
        extra={
            "normalized_identities": sorted(instrument_counts.keys()),
            "authored_role_counts": dict(authored_role_counts),
            "inferred_role_counts": dict(inferred_role_counts),
            "metric_names": sorted(context.density.model_dump().keys()),
            "melody_lead_source": melody_lead.source,
            "truncation_reasons": list(context.truncation_reasons),
            "warning_code_count": len(context.warning_codes),
        },
    )
    return context


def lookup_source_note(
    context: ArrangementSourceContext,
    ref: str,
) -> ArrangementSourceNoteRef:
    """Resolve a request-local source-note reference."""
    note = context.note_by_ref.get(ref)
    if note is None:
        raise CompositionArrangementError(
            "arrangement_invalid_source",
            "Unknown arrangement source-note reference",
            details={"ref_present": bool(ref)},
        )
    return note


def arrangement_context_prompt_payload(context: ArrangementSourceContext) -> dict[str, Any]:
    """Serialize bounded context for prompts.

    The selected-note reference table is always complete. Advisory fields may be
    truncated or omitted. Harmony is labeled advisory and is never playable content.
    """
    return {
        "version": context.version,
        "analysis_advisory": True,
        "operation": context.operation,
        "edit_source_fingerprint_prefix": edit_fingerprint_log_prefix(context.edit_source_fingerprint),
        "catalog_version": context.catalog_version,
        "range_policy_version": context.range_policy_version,
        "source_track_ids": list(context.source_track_ids),
        "protected_track_ids": list(context.protected_track_ids),
        "before_part_count": context.before_part_count,
        "after_part_count": context.after_part_count,
        "instrument_counts": dict(context.instrument_counts),
        "authored_role_counts": dict(context.authored_role_counts),
        "inferred_role_counts": dict(context.inferred_role_counts),
        "melody_lead": {
            "track_ids": list(context.melody_lead.track_ids),
            "source": context.melody_lead.source,
            "authored_roles": list(context.melody_lead.authored_roles),
        },
        "tracks": [
            {
                "track_id": view.track_id,
                "instrument": view.instrument,
                "instrument_id": view.instrument_id,
                "authored_role": view.authored_role,
                "inferred_role": view.inferred_role,
                "effective_role": view.effective_role,
                "is_drum": view.is_drum,
                "pitch_min": view.pitch_min,
                "pitch_max": view.pitch_max,
                "logical_note_count": view.logical_note_count,
            }
            for view in context.track_roles
            if view.track_id in set(context.source_track_ids) | set(context.protected_track_ids)
        ]
        if context.prompt_include_track_summaries
        else [],
        "density": context.density.model_dump(mode="json"),
        "register": context.register_summary,
        "rhythm": context.rhythm_summary,
        "sections": list(context.section_summary),
        "harmony_advisory": list(context.harmony_summary),
        "target_ranges": [
            {
                "part_id": item.part_id,
                "instrument_id": item.instrument_id,
                "role": item.role,
                "range_policy": item.range_policy,
                "playable_low": item.playable_low,
                "playable_high": item.playable_high,
                "preferred_low": item.preferred_low,
                "preferred_high": item.preferred_high,
            }
            for item in context.target_ranges
        ]
        if context.prompt_include_target_ranges
        else [],
        # NON-truncatable complete compact selected-note reference table.
        "selected_note_refs": [_compact_source_note_ref(note) for note in context.source_notes],
        "advisory_analysis": context.advisory_analysis,
        "truncated": context.truncated,
        "truncation_reasons": list(context.truncation_reasons),
        "warning_codes": list(context.warning_codes),
    }


def _compact_source_note_ref(note: ArrangementSourceNoteRef) -> dict[str, Any]:
    """Serialize one source-note ref, omitting null/empty optional fields."""
    payload: dict[str, Any] = {
        "ref": note.ref,
        "track_id": note.track_id,
        "start_tick": note.start_tick,
        "duration_ticks": note.duration_ticks,
        "pitch": note.pitch,
        "velocity": note.velocity,
        "is_drum": note.is_drum,
    }
    if note.staff is not None:
        payload["staff"] = note.staff
    if note.voice is not None:
        payload["voice"] = note.voice
    if note.articulations:
        payload["articulations"] = list(note.articulations)
    if note.tie_group_id is not None:
        payload["tie_group_id"] = note.tie_group_id
        payload["member_count"] = note.member_count
    return payload


def _validate_track_authorization(
    track_by_id: Mapping[str, CompositionV2Track],
    source_ids: Sequence[str],
    protected_ids: Sequence[str],
) -> None:
    missing_source = [track_id for track_id in source_ids if track_id not in track_by_id]
    missing_protected = [track_id for track_id in protected_ids if track_id not in track_by_id]
    if missing_source or missing_protected:
        raise CompositionArrangementError(
            "arrangement_invalid_source",
            http_status=422,
            details={
                "missing_source_count": len(missing_source),
                "missing_protected_count": len(missing_protected),
            },
        )


def _validate_before_inventory(
    *,
    composition: CompositionV2,
    track_by_id: Mapping[str, CompositionV2Track],
    source_ids: Sequence[str],
    before_parts: Sequence[ArrangementPartRequirement],
    catalog: ArrangementInstrumentCatalog,
) -> dict[str, str]:
    """Match explicit before parts to source tracks. Returns part_id → track_id."""
    source_set = set(source_ids)
    if len(before_parts) != len(source_ids):
        raise CompositionArrangementError(
            "arrangement_inventory_mismatch",
            http_status=422,
            details={
                "before_part_count": len(before_parts),
                "source_track_count": len(source_ids),
                "reason": "before_part_count_mismatch",
            },
        )

    mapped: dict[str, str] = {}
    claimed_tracks: set[str] = set()
    explicit = any(part.source_track_ids for part in before_parts)

    if explicit:
        for part in before_parts:
            if len(part.source_track_ids) != 1:
                raise CompositionArrangementError(
                    "arrangement_inventory_mismatch",
                    http_status=422,
                    details={
                        "part_id": part.part_id,
                        "reason": "before_part_requires_exactly_one_source_track",
                    },
                )
            track_id = part.source_track_ids[0]
            if track_id not in source_set:
                raise CompositionArrangementError(
                    "arrangement_inventory_mismatch",
                    http_status=422,
                    details={
                        "part_id": part.part_id,
                        "reason": "before_source_not_in_selection",
                    },
                )
            if track_id in claimed_tracks:
                raise CompositionArrangementError(
                    "arrangement_inventory_mismatch",
                    http_status=422,
                    details={
                        "part_id": part.part_id,
                        "reason": "duplicate_before_source_mapping",
                    },
                )
            track = track_by_id[track_id]
            _assert_part_matches_track(part, track, catalog)
            mapped[part.part_id] = track_id
            claimed_tracks.add(track_id)
        if claimed_tracks != source_set:
            raise CompositionArrangementError(
                "arrangement_inventory_mismatch",
                http_status=422,
                details={"reason": "before_mapping_incomplete"},
            )
        return mapped

    # Greedy match by instrument identity + optional role (deterministic track order).
    remaining = list(source_ids)
    for part in before_parts:
        match_id: str | None = None
        for track_id in remaining:
            track = track_by_id[track_id]
            if _part_matches_track(part, track, catalog):
                match_id = track_id
                break
        if match_id is None:
            raise CompositionArrangementError(
                "arrangement_inventory_mismatch",
                http_status=422,
                details={
                    "part_id": part.part_id,
                    "reason": "no_matching_source_track",
                },
            )
        mapped[part.part_id] = match_id
        remaining.remove(match_id)
    return mapped


def _assert_part_matches_track(
    part: ArrangementPartRequirement,
    track: CompositionV2Track,
    catalog: ArrangementInstrumentCatalog,
) -> None:
    if not _part_matches_track(part, track, catalog):
        raise CompositionArrangementError(
            "arrangement_inventory_mismatch",
            http_status=422,
            details={
                "part_id": part.part_id,
                "reason": "instrument_or_role_mismatch",
            },
        )


def _part_matches_track(
    part: ArrangementPartRequirement,
    track: CompositionV2Track,
    catalog: ArrangementInstrumentCatalog,
) -> bool:
    part_profile = resolve_profile(instrument_id=part.instrument_id, catalog=catalog)
    track_profile = resolve_profile_for_track(track, catalog=catalog)
    if part_profile is not None and track_profile is not None:
        if part_profile.instrument_id != track_profile.instrument_id:
            return False
    else:
        part_identity = normalize_instrument_identity(part.instrument_id)
        track_identity = normalize_instrument_identity(track.instrument)
        if part_identity and track_identity and part_identity != track_identity:
            return False
        if part_profile is not None and track_identity and part_profile.compatibility_identity != track_identity:
            return False
    if part.role is not None:
        authored = normalize_role(track.role) if track.role else ""
        if authored != part.role:
            return False
    return True


def _assign_source_note_refs(
    source_tracks: Sequence[CompositionV2Track],
) -> tuple[tuple[ArrangementSourceNoteRef, ...], dict[str, ArrangementSourceNoteRef]]:
    """Assign deterministic sn##### refs to every logical note on selected tracks."""
    refs: list[ArrangementSourceNoteRef] = []
    ordinal = 1
    for track in source_tracks:
        logical_notes = collapse_track_tie_chains(track)
        for note in logical_notes:
            ref = f"sn{ordinal:05d}"
            ordinal += 1
            item = ArrangementSourceNoteRef(
                ref=ref,
                track_id=track.id,
                start_tick=note.start_tick,
                duration_ticks=note.duration_ticks,
                pitch=note.pitch,
                midi_number=note.midi_number,
                velocity=note.velocity,
                staff=note.staff,
                voice=note.voice,
                articulations=note.articulations,
                tie_group_id=note.tie_group_id,
                member_count=note.member_count,
                source_event_ids=note.source_event_ids,
                is_drum=bool(track.is_drum),
            )
            refs.append(item)
    by_ref = {item.ref: item for item in refs}
    return tuple(refs), by_ref


def _build_track_role_views(
    *,
    composition: CompositionV2,
    source_ids: Sequence[str],
    protected_ids: Sequence[str],
    catalog: ArrangementInstrumentCatalog,
) -> tuple[tuple[ArrangementTrackRoleView, ...], list[str]]:
    """Build authored/inferred/effective role views. Declared roles win."""
    warnings: list[str] = []
    relevant = set(source_ids) | set(protected_ids)
    views: list[ArrangementTrackRoleView] = []

    # Lightweight inferred roles: instrument priors only (advisory; never override declared).
    for track in composition.tracks:
        if track.id not in relevant:
            continue
        logical = collapse_track_tie_chains(track)
        midi_values = [note.midi_number for note in logical] if not track.is_drum else []
        profile = resolve_profile_for_track(track, catalog=catalog)
        authored = normalize_role(track.role) if track.role else "other"
        inferred = _infer_role_advisory(track, logical, profile)
        # Declared roles are authoritative for effective_role.
        effective = authored
        if inferred and inferred != authored and authored in {"other", "pad", "rhythm"}:
            # Soft ambiguity signal when authored is generic and inference differs.
            warnings.append("ambiguous_role")
        views.append(
            ArrangementTrackRoleView(
                track_id=track.id,
                instrument=track.instrument,
                instrument_id=profile.instrument_id if profile else None,
                authored_role=authored,
                inferred_role=inferred,
                effective_role=effective,
                is_drum=bool(track.is_drum),
                midi_program=int(track.midi_program),
                event_count=len(track.events),
                logical_note_count=len(logical),
                pitch_min=min(midi_values) if midi_values else None,
                pitch_max=max(midi_values) if midi_values else None,
            )
        )
    return tuple(views), list(dict.fromkeys(warnings))


def _infer_role_advisory(
    track: CompositionV2Track,
    logical: Sequence[CollapsedLogicalNote],
    profile: InstrumentProfile | None,
) -> str | None:
    if track.is_drum:
        return "drums"
    if not logical:
        return None
    authored = normalize_role(track.role) if track.role else None
    # Prefer declaring authored when already specific — inference remains separate.
    mean_midi = sum(note.midi_number for note in logical) / float(len(logical))
    max_sim = _track_max_simultaneity(logical)
    if mean_midi < 48 and max_sim <= 2:
        return "bass"
    if max_sim <= 1 and mean_midi >= 60:
        return "melody"
    if max_sim >= 3:
        return "harmony"
    if profile and profile.suggested_roles:
        return profile.suggested_roles[0]
    return authored


def _derive_melody_lead_identity(
    *,
    track_roles: Sequence[ArrangementTrackRoleView],
    source_ids: Sequence[str],
    protected_ids: Sequence[str],
) -> ArrangementMelodyLeadIdentity:
    by_id = {view.track_id: view for view in track_roles}
    protected_melody = [
        track_id
        for track_id in protected_ids
        if by_id.get(track_id) and by_id[track_id].authored_role in _MELODY_LEAD_ROLES
    ]
    if protected_melody:
        return ArrangementMelodyLeadIdentity(
            track_ids=tuple(protected_melody),
            source="protected",
            authored_roles=tuple(by_id[track_id].authored_role for track_id in protected_melody),
        )

    declared = [
        track_id
        for track_id in source_ids
        if by_id.get(track_id) and by_id[track_id].authored_role in _MELODY_LEAD_ROLES
    ]
    if declared:
        return ArrangementMelodyLeadIdentity(
            track_ids=tuple(declared),
            source="declared",
            authored_roles=tuple(by_id[track_id].authored_role for track_id in declared),
        )

    inferred = [
        track_id
        for track_id in source_ids
        if by_id.get(track_id) and by_id[track_id].inferred_role in _MELODY_LEAD_ROLES
    ]
    if len(inferred) == 1:
        return ArrangementMelodyLeadIdentity(
            track_ids=tuple(inferred),
            source="inferred",
            authored_roles=(by_id[inferred[0]].authored_role,),
        )
    if len(inferred) > 1:
        return ArrangementMelodyLeadIdentity(
            track_ids=tuple(inferred),
            source="ambiguous",
            authored_roles=tuple(by_id[track_id].authored_role for track_id in inferred),
        )
    return ArrangementMelodyLeadIdentity(track_ids=(), source="none", authored_roles=())


def _actual_inventory(
    *,
    composition: CompositionV2,
    source_ids: Sequence[str],
    part_to_track: Mapping[str, str],
    catalog: ArrangementInstrumentCatalog,
) -> tuple[ArrangementActualInventoryItem, ...]:
    track_to_part = {track_id: part_id for part_id, track_id in part_to_track.items()}
    items: list[ArrangementActualInventoryItem] = []
    for track in composition.tracks:
        if track.id not in source_ids:
            continue
        profile = resolve_profile_for_track(track, catalog=catalog)
        items.append(
            ArrangementActualInventoryItem(
                track_id=track.id,
                instrument=track.instrument,
                instrument_id=profile.instrument_id if profile else None,
                role=normalize_role(track.role) if track.role else "other",
                midi_program=int(track.midi_program),
                event_count=len(track.events),
                part_id=track_to_part.get(track.id),
            )
        )
    return tuple(items)


def _compute_density_metrics(
    source_tracks: Sequence[CompositionV2Track],
    duration_ticks: int,
) -> ArrangementDensityMetrics:
    logical_by_track: list[list[CollapsedLogicalNote]] = [
        collapse_track_tie_chains(track) for track in source_tracks
    ]
    all_notes = [note for notes in logical_by_track for note in notes]
    event_count = sum(len(track.events) for track in source_tracks)
    attack_count = len(all_notes)
    active_track_count = sum(1 for notes in logical_by_track if notes)
    end = max(1, duration_ticks)
    union = _union_occupancy(all_notes, 0, end)
    max_sim = _max_simultaneity(all_notes, 0, end)
    return ArrangementDensityMetrics(
        event_count=event_count,
        attack_count=attack_count,
        active_track_count=active_track_count,
        union_occupancy_ticks=union,
        max_simultaneity=max_sim,
    )


def _register_summary(
    source_tracks: Sequence[CompositionV2Track],
    source_notes: Sequence[ArrangementSourceNoteRef],
) -> dict[str, Any]:
    pitched = [note for note in source_notes if not note.is_drum]
    per_track: list[dict[str, Any]] = []
    for track in source_tracks:
        track_notes = [note for note in pitched if note.track_id == track.id]
        if not track_notes:
            per_track.append({"track_id": track.id, "pitch_min": None, "pitch_max": None})
            continue
        per_track.append(
            {
                "track_id": track.id,
                "pitch_min": min(note.midi_number for note in track_notes),
                "pitch_max": max(note.midi_number for note in track_notes),
            }
        )
    return {
        "global_pitch_min": min((n.midi_number for n in pitched), default=None),
        "global_pitch_max": max((n.midi_number for n in pitched), default=None),
        "tracks": per_track,
        "example_note_count": min(_MAX_REGISTER_EXAMPLE_NOTES, len(pitched)),
    }


def _rhythm_summary(
    source_notes: Sequence[ArrangementSourceNoteRef],
    composition: CompositionV2,
) -> dict[str, Any]:
    tpq = max(1, composition.ticks_per_quarter)
    onsets = sorted({note.start_tick for note in source_notes})
    bar_count = max(1, composition.bar_count)
    return {
        "attack_count": len(source_notes),
        "distinct_onset_count": len(onsets),
        "attacks_per_bar_approx": round(len(source_notes) / float(bar_count), 4),
        "onset_grid_quarter_buckets": len({onset // tpq for onset in onsets}),
    }


def _section_summary(
    composition: CompositionV2,
) -> tuple[tuple[dict[str, Any], ...], bool]:
    truncated = len(composition.sections) > _MAX_SECTION_SUMMARY_ITEMS
    selected = composition.sections[:_MAX_SECTION_SUMMARY_ITEMS]
    payload = tuple(
        {
            "section_id": section.id,
            "type": section.type,
            "start_bar": section.start_bar,
            "bar_count": section.bar_count,
            "start_tick": section.start_tick,
            "duration_ticks": section.duration_ticks,
        }
        for section in selected
    )
    return payload, truncated


def _harmony_summary(
    harmony: Sequence[CompositionV2HarmonyItem],
) -> tuple[tuple[dict[str, Any], ...], bool]:
    """Advisory harmony only — never playable content."""
    truncated = len(harmony) > _MAX_HARMONY_SUMMARY_ITEMS
    selected = harmony[:_MAX_HARMONY_SUMMARY_ITEMS]
    payload = tuple(
        {
            "relative_start_tick": item.start_tick,
            "duration_ticks": item.duration_ticks,
            "chord": item.chord,
            "advisory": True,
        }
        for item in selected
    )
    return payload, truncated


def _target_range_summaries(
    after_parts: Sequence[ArrangementPartRequirement],
    catalog: ArrangementInstrumentCatalog,
) -> tuple[ArrangementTargetRangeSummary, ...]:
    summaries: list[ArrangementTargetRangeSummary] = []
    for part in after_parts:
        profile = resolve_profile(instrument_id=part.instrument_id, catalog=catalog)
        if profile is None:
            profile = resolve_profile(alias=part.instrument_id, catalog=catalog)
        if profile is None:
            raise CompositionArrangementError(
                "arrangement_inventory_mismatch",
                http_status=422,
                details={
                    "part_id": part.part_id,
                    "reason": "unknown_after_instrument",
                    "instrument_id": part.instrument_id[:80],
                },
            )
        summaries.append(
            ArrangementTargetRangeSummary(
                part_id=part.part_id,
                instrument_id=profile.instrument_id,
                role=part.role,
                range_policy=profile.range_policy,
                playable_low=profile.playable_low,
                playable_high=profile.playable_high,
                preferred_low=profile.preferred_low,
                preferred_high=profile.preferred_high,
                profile_fingerprint=profile.fingerprint,
            )
        )
    return tuple(summaries)


def _advisory_analysis_projection(
    composition: CompositionV2,
    *,
    budget_chars: int,
) -> tuple[str | None, list[str], bool]:
    warnings: list[str] = []
    try:
        report = analyze_composition(composition)
        text = build_llm_analysis_context(
            report,
            max_chars=min(budget_chars, _MAX_ADVISORY_ANALYSIS_CHARS),
            purpose="arrangement",
        )
        truncated = "truncated" in text.lower() or "[analysis_context_truncated]" in text
        if truncated:
            warnings.append("context_truncated")
        return text, warnings, truncated
    except Exception as exc:  # noqa: BLE001 — advisory only; never fail arrangement on analysis
        logger.info(
            "Advisory arrangement analysis projection skipped",
            extra={"error_type": type(exc).__name__},
        )
        return None, [], False


def _fit_context_to_budget(context: ArrangementSourceContext, *, budget: int) -> None:
    """Shrink advisory fields until the prompt payload fits; never drop note refs.

    Truncation order: advisory analysis → register examples → harmony → sections →
    rhythm examples → compact target ranges. Rejects only when the irreducible
    note table plus minimal required metadata still exceeds the budget.
    """
    payload = arrangement_context_prompt_payload(context)
    context.context_char_count = len(_canonical_json(payload))
    if context.context_char_count <= budget:
        return

    def _mark(*reasons: str) -> None:
        context.truncated = True
        reason_list = list(context.truncation_reasons)
        for reason in reasons:
            if reason not in reason_list:
                reason_list.append(reason)
        context.truncation_reasons = tuple(reason_list)
        codes = list(context.warning_codes)
        if "context_truncated" not in codes:
            codes.append("context_truncated")
        context.warning_codes = tuple(codes)

    shrink_steps: list[tuple[str, Any]] = [
        (
            "advisory_analysis",
            lambda: setattr(context, "advisory_analysis", None),
        ),
        (
            "register_examples",
            lambda: setattr(
                context,
                "register_summary",
                {
                    **dict(context.register_summary),
                    "example_notes": [],
                    "example_note_count": 0,
                },
            ),
        ),
        (
            "harmony",
            lambda: setattr(context, "harmony_summary", ()),
        ),
        (
            "sections",
            lambda: setattr(context, "section_summary", ()),
        ),
        (
            "rhythm",
            lambda: setattr(
                context,
                "rhythm_summary",
                {
                    "attack_count": context.rhythm_summary.get("attack_count", 0),
                    "unique_onset_count": context.rhythm_summary.get("unique_onset_count", 0),
                    "onset_ticks": [],
                },
            ),
        ),
        (
            "target_range_preferred",
            lambda: setattr(
                context,
                "target_ranges",
                tuple(
                    ArrangementTargetRangeSummary(
                        part_id=item.part_id,
                        instrument_id=item.instrument_id,
                        role=item.role,
                        range_policy=item.range_policy,
                        playable_low=item.playable_low,
                        playable_high=item.playable_high,
                        preferred_low=None,
                        preferred_high=None,
                        profile_fingerprint=item.profile_fingerprint,
                    )
                    for item in context.target_ranges
                ),
            ),
        ),
        (
            "track_summaries",
            lambda: setattr(context, "prompt_include_track_summaries", False),
        ),
        (
            "target_ranges",
            lambda: setattr(context, "prompt_include_target_ranges", False),
        ),
    ]

    for reason, action in shrink_steps:
        if context.context_char_count <= budget:
            break
        action()
        _mark(reason)
        payload = arrangement_context_prompt_payload(context)
        context.context_char_count = len(_canonical_json(payload))

    if context.context_char_count > budget:
        context.register_summary = {
            "pitch_min": context.register_summary.get("pitch_min"),
            "pitch_max": context.register_summary.get("pitch_max"),
            "example_notes": [],
            "example_note_count": 0,
        }
        context.rhythm_summary = {
            "attack_count": context.rhythm_summary.get("attack_count", 0),
            "unique_onset_count": 0,
            "onset_ticks": [],
        }
        context.prompt_include_track_summaries = False
        context.prompt_include_target_ranges = False
        _mark("register_examples", "rhythm", "track_summaries", "target_ranges")
        payload = arrangement_context_prompt_payload(context)
        context.context_char_count = len(_canonical_json(payload))

    if context.context_char_count > budget:
        raise CompositionArrangementError(
            "arrangement_request_too_large",
            http_status=422,
            details={
                "context_char_count": context.context_char_count,
                "context_budget_chars": budget,
                "logical_note_count": len(context.source_notes),
                "reason": "context_exceeds_budget_after_advisory_truncation",
            },
        )


def _maybe_truncate_register_examples(
    register_summary: dict[str, Any],
    remaining_budget: int,
) -> tuple[dict[str, Any], bool]:
    if remaining_budget >= 200:
        return register_summary, False
    trimmed = dict(register_summary)
    trimmed["example_note_count"] = 0
    return trimmed, True


def _measure_note_table_chars(notes: Sequence[ArrangementSourceNoteRef]) -> int:
    table = [_compact_source_note_ref(note) for note in notes]
    return len(_canonical_json({"selected_note_refs": table}))


def _estimate_summary_chars(
    register_summary: dict[str, Any],
    rhythm_summary: dict[str, Any],
    section_summary: Sequence[dict[str, Any]],
    harmony_summary: Sequence[dict[str, Any]],
    target_ranges: Sequence[ArrangementTargetRangeSummary],
) -> int:
    payload = {
        "register": register_summary,
        "rhythm": rhythm_summary,
        "sections": list(section_summary),
        "harmony_advisory": list(harmony_summary),
        "target_ranges": [
            {
                "part_id": item.part_id,
                "instrument_id": item.instrument_id,
                "role": item.role,
                "range_policy": item.range_policy,
                "playable_low": item.playable_low,
                "playable_high": item.playable_high,
                "preferred_low": item.preferred_low,
                "preferred_high": item.preferred_high,
            }
            for item in target_ranges
        ],
    }
    return len(_canonical_json(payload))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _track_max_simultaneity(notes: Sequence[CollapsedLogicalNote]) -> int:
    if not notes:
        return 0
    events: list[tuple[int, int]] = []
    for note in notes:
        events.append((note.start_tick, 1))
        events.append((note.end_tick, -1))
    events.sort(key=lambda item: (item[0], item[1]))
    current = 0
    peak = 0
    for _, delta in events:
        current += delta
        if current > peak:
            peak = current
    return peak


def _union_occupancy(notes: Sequence[CollapsedLogicalNote], start: int, end: int) -> int:
    events: list[tuple[int, int]] = []
    for note in notes:
        left = max(note.start_tick, start)
        right = min(note.end_tick, end)
        if right <= left:
            continue
        events.append((left, 1))
        events.append((right, -1))
    if not events:
        return 0
    events.sort(key=lambda item: (item[0], item[1]))
    active = 0
    cursor = start
    union = 0
    for tick, delta in events:
        if active > 0 and tick > cursor:
            union += min(end, tick) - max(start, cursor)
        cursor = tick
        active += delta
        if cursor >= end:
            break
    return union


def _max_simultaneity(notes: Sequence[CollapsedLogicalNote], start: int, end: int) -> int:
    events: list[tuple[int, int]] = []
    for note in notes:
        left = max(note.start_tick, start)
        right = min(note.end_tick, end)
        if right <= left:
            continue
        events.append((left, 1))
        events.append((right, -1))
    if not events:
        return 0
    events.sort(key=lambda item: (item[0], item[1]))
    current = 0
    peak = 0
    for tick, delta in events:
        if tick >= end:
            break
        current += delta
        if current > peak:
            peak = current
    return peak
