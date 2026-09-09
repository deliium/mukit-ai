"""Arrangement preservation, range, harmony, density, duplicate, and postcondition validation.

Deterministic checks over a realized arrangement candidate. Returns bounded
findings/assertions and measured before/after metrics for the UI. Never mutates
the source composition or realization result.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Sequence

from app.arrangement_schemas import (
    ARRANGEMENT_MAX_ASSERTIONS,
    ARRANGEMENT_MAX_DUPLICATE_FINDINGS,
    ARRANGEMENT_MAX_RANGE_FINDINGS,
    ARRANGEMENT_MAX_REASON_CHARS,
    ARRANGEMENT_MAX_WARNINGS,
    AUTHORIZED_DOUBLING_POLICIES,
    ArrangementDensityMetrics,
    ArrangementDensitySummary,
    ArrangementDuplicateFinding,
    ArrangementHarmonyCompatibilitySummary,
    ArrangementPreservationAssertion,
    ArrangementRangeFinding,
    ArrangementRejectedStage,
    ArrangementTopologyManifest,
    CompositionArrangementPreviewRequest,
)
from app.composition_schemas import (
    CompositionV2,
    CompositionV2NoteEvent,
    CompositionV2Track,
    midi_pitch_number,
)
from app.services.composition_arrangement_context import (
    ArrangementSourceContext,
    build_arrangement_source_context,
)
from app.services.composition_arrangement_patch import ArrangementRealizationResult
from app.services.composition_harmony_compatibility import analyze_harmony_compatibility
from app.services.composition_logical_notes import (
    CollapsedLogicalNote,
    collapse_track_tie_chains,
)
from app.services.composition_validator import validate_composition_integrity
from app.services.instrument_catalog import (
    ArrangementInstrumentCatalog,
    find_baseline_mismatches,
    get_catalog,
    resolve_profile,
    resolve_profile_for_track,
)
from app.services.instrument_identity import (
    classify_content_relationship,
    event_content_fingerprint,
    normalize_instrument_identity,
    normalize_role,
)


logger = logging.getLogger(__name__)

ARRANGEMENT_VALIDATION_VERSION = "composition.arrangement.validation.v1"

_MELODY_LEAD_ROLES = frozenset({"melody", "lead"})
_DENSITY_OPERATIONS = frozenset(
    {
        "simplify_arrangement",
        "increase_texture_density",
        "decrease_texture_density",
    }
)
_GLOBAL_METADATA_FIELDS = (
    "tempo",
    "key",
    "time_signature",
    "ticks_per_quarter",
    "bar_count",
    "duration_ticks",
    "sections",
    "tempo_changes",
    "time_signature_changes",
    "key_changes",
    "markers",
)

# Contour / clone similarity thresholds (onset + interval normalized).
_CLONE_CONTOUR_MIN = 0.85
_CLONE_MIN_NOTES = 3


@dataclass
class ArrangementValidationResult:
    """Bounded arrangement candidate validation outcome."""

    ok: bool
    status: Literal["accepted", "rejected"]
    assertions: list[ArrangementPreservationAssertion] = field(default_factory=list)
    range_findings: list[ArrangementRangeFinding] = field(default_factory=list)
    duplicate_findings: list[ArrangementDuplicateFinding] = field(default_factory=list)
    density: ArrangementDensitySummary | None = None
    harmony_compatibility: ArrangementHarmonyCompatibilitySummary | None = None
    warning_codes: list[str] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    rejection_stage: ArrangementRejectedStage | None = None
    rejection_codes: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)

    @property
    def required_assertion_failures(self) -> list[ArrangementPreservationAssertion]:
        return [item for item in self.assertions if item.required and not item.satisfied]


def validate_arrangement_candidate(
    request: CompositionArrangementPreviewRequest,
    realization: ArrangementRealizationResult,
    *,
    context: ArrangementSourceContext | None = None,
    catalog: ArrangementInstrumentCatalog | None = None,
) -> ArrangementValidationResult:
    """Validate a realized arrangement candidate against request invariants."""
    loaded_catalog = catalog or get_catalog()
    ctx = context or build_arrangement_source_context(request, catalog=loaded_catalog)
    source = ctx.composition
    candidate = realization.composition
    manifest = realization.manifest

    assertions: list[ArrangementPreservationAssertion] = []
    range_findings: list[ArrangementRangeFinding] = []
    duplicate_findings: list[ArrangementDuplicateFinding] = []
    warning_codes: list[str] = []
    error_codes: list[str] = []
    rejection_stage: ArrangementRejectedStage | None = None
    rejection_codes: list[str] = []
    rejection_reasons: list[str] = []

    source_by_id = {track.id: track for track in source.tracks}
    candidate_by_id = {track.id: track for track in candidate.tracks}
    source_set = set(request.source_track_ids)
    protected_set = set(request.protected_track_ids)
    unselected_ids = {track.id for track in source.tracks} - source_set

    changed_track_ids = _changed_target_track_ids(realization)
    doubling_track_ids = _declared_doubling_track_ids(request, realization)
    doubling_source_ids = _declared_doubling_source_ids(request)

    def _fail(
        stage: ArrangementRejectedStage,
        code: str,
        reason: str,
        *,
        warning: str | None = None,
    ) -> None:
        nonlocal rejection_stage
        if rejection_stage is None:
            rejection_stage = stage
        if code not in rejection_codes:
            rejection_codes.append(code)
        if reason not in rejection_reasons and len(rejection_reasons) < 8:
            rejection_reasons.append(reason[:ARRANGEMENT_MAX_REASON_CHARS])
        if code not in error_codes:
            error_codes.append(code)
        if warning and warning not in warning_codes:
            warning_codes.append(warning)

    def _assert(
        kind: str,
        satisfied: bool,
        detail: str,
        *,
        required: bool = True,
        track_id: str | None = None,
        stage: ArrangementRejectedStage | None = None,
        code: str | None = None,
        warning: str | None = None,
    ) -> None:
        if len(assertions) >= ARRANGEMENT_MAX_ASSERTIONS:
            return
        assertions.append(
            ArrangementPreservationAssertion(
                kind=kind,  # type: ignore[arg-type]
                satisfied=satisfied,
                required=required,
                detail=detail[:ARRANGEMENT_MAX_REASON_CHARS],
                track_id=track_id,
            )
        )
        if required and not satisfied and stage is not None and code is not None:
            _fail(stage, code, detail, warning=warning)

    # --- 12. Canonical V2 validity (scoped practical ranges) -----------------
    integrity = validate_composition_integrity(
        candidate,
        profile="canonical",
        practical_range_track_ids=changed_track_ids,
    )
    structural_errors = [
        item
        for item in integrity.errors
        if not (
            item.code == "event_out_of_range"
            and isinstance(item.context, dict)
            and "allowed_low" in item.context
        )
    ]
    canonical_ok = not structural_errors
    _assert(
        "motif_integrity",
        not any(item.code.startswith("motif_") for item in structural_errors),
        (
            "Motif references remain valid after arrangement"
            if not any(item.code.startswith("motif_") for item in structural_errors)
            else "Motif integrity failed after arrangement"
        ),
        stage="preservation",
        code="arrangement_preservation_failed",
        warning="candidate_failed_preservation",
    )
    if not canonical_ok:
        _fail(
            "preservation",
            "arrangement_preservation_failed",
            "Candidate failed canonical composition integrity",
            warning="candidate_failed_validation",
        )

    # --- Global metadata + protected/unselected preservation ----------------
    global_ok = _global_metadata_equal(source, candidate)

    protected_ok = True
    for track_id in sorted(protected_set):
        source_track = source_by_id.get(track_id)
        candidate_track = candidate_by_id.get(track_id)
        if source_track is None or candidate_track is None:
            protected_ok = False
            break
        if not _tracks_byte_equal(source_track, candidate_track):
            # change_instrumentation may reinstrument selected; protected must stay exact.
            protected_ok = False
            break
    _assert(
        "protected_tracks",
        protected_ok,
        (
            "Protected tracks remain byte-equivalent"
            if protected_ok
            else "Protected track content or topology changed"
        ),
        stage="preservation",
        code="arrangement_preservation_failed",
        warning="candidate_failed_preservation",
    )

    unselected_ok = True
    for track_id in sorted(unselected_ids):
        source_track = source_by_id.get(track_id)
        candidate_track = candidate_by_id.get(track_id)
        if source_track is None:
            continue
        if candidate_track is None or not _tracks_byte_equal(source_track, candidate_track):
            unselected_ok = False
            break
    _assert(
        "unselected_tracks",
        unselected_ok,
        (
            "Unselected tracks remain byte-equivalent"
            if unselected_ok
            else "Unselected track was modified or removed"
        ),
        stage="preservation",
        code="arrangement_preservation_failed",
        warning="candidate_failed_preservation",
    )

    # --- Melody multiset preservation ----------------------------------------
    melody_track_ids = set(ctx.melody_lead.track_ids)
    if not melody_track_ids and request.preserve_melody:
        melody_track_ids = {
            track.id
            for track in source.tracks
            if normalize_role(track.role) in _MELODY_LEAD_ROLES and track.id in source_set
        }
    source_melody = _semantic_multiset(
        _logical_notes_for_tracks(source, melody_track_ids)
    )
    # Primary representation excludes declared doubling targets.
    primary_candidate_tracks = {
        track.id for track in candidate.tracks if track.id not in doubling_track_ids
    }
    candidate_melody = _semantic_multiset(
        _logical_notes_for_tracks(candidate, primary_candidate_tracks)
    )
    # Melody notes must appear as a multiset subset of primary material when
    # preserve_melody is required; equality when redistribution consumes sources.
    if request.preserve_melody and source_melody:
        # For operations that keep source melody tracks, compare equality on
        # the melody multiset extracted from candidate melody/lead roles plus
        # redistributed material that matches source melody fingerprints.
        matching = Counter(
            {key: min(source_melody[key], candidate_melody[key]) for key in source_melody}
        )
        melody_ok = matching == source_melody
        detail = (
            "Semantic melody multiset preserved across tracks"
            if melody_ok
            else "Melody notes were dropped, altered, or only satisfied by doubles"
        )
    else:
        melody_ok = True
        detail = "Melody preservation not required for this request"
    _assert(
        "melody_preservation",
        melody_ok,
        detail,
        required=request.preserve_melody,
        stage="preservation",
        code="arrangement_preservation_failed",
        warning="candidate_failed_preservation",
    )

    # --- Harmony metadata exactness + generated-note compatibility -----------
    harmony_exact = _harmony_metadata_equal(source, candidate)
    _assert(
        "harmony_preservation",
        harmony_exact if request.preserve_harmony else True,
        (
            "Harmony metadata unchanged"
            if harmony_exact
            else "Harmony or related tonal metadata changed"
        ),
        required=request.preserve_harmony,
        stage="harmony",
        code="arrangement_preservation_failed",
        warning="candidate_failed_preservation",
    )

    harmony_summary, harmony_warnings = _check_harmony_compatibility(
        source=source,
        candidate=candidate,
        preserve_harmony=request.preserve_harmony,
        changed_track_ids=changed_track_ids,
    )
    for code in harmony_warnings:
        if code not in warning_codes:
            warning_codes.append(code)
    if harmony_summary.failed and request.preserve_harmony:
        _fail(
            "harmony",
            "arrangement_preservation_failed",
            harmony_summary.detail or "Generated notes fail harmony compatibility",
            warning="candidate_failed_preservation",
        )

    # --- Topology authorization vs manifest ----------------------------------
    topology_ok, topology_detail = _validate_topology_manifest(
        source=source,
        candidate=candidate,
        manifest=manifest,
        source_set=source_set,
        protected_set=protected_set,
        operation=request.operation,
    )
    if not global_ok:
        topology_ok = False
        topology_detail = "Top-level form/timeline metadata changed"
    _assert(
        "topology_authorization",
        topology_ok,
        topology_detail,
        stage="preservation",
        code="arrangement_preservation_failed",
        warning="candidate_failed_preservation",
    )

    # --- After instrumentation / counts / roles ------------------------------
    after_ok, after_detail, after_warning = _validate_after_instrumentation(
        request=request,
        realization=realization,
        catalog=loaded_catalog,
    )
    if after_warning and after_warning not in warning_codes:
        warning_codes.append(after_warning)
    _assert(
        "instrumentation_after",
        after_ok,
        after_detail,
        stage="identity",
        code="arrangement_inventory_mismatch",
        warning="candidate_failed_validation",
    )

    # --- Source-note cardinality ---------------------------------------------
    cardinality_ok, cardinality_detail = _validate_source_note_cardinality(
        request=request,
        source=source,
        candidate=candidate,
        realization=realization,
        doubling_track_ids=doubling_track_ids,
    )
    _assert(
        "source_note_cardinality",
        cardinality_ok,
        cardinality_detail,
        stage="preservation",
        code="arrangement_preservation_failed",
        warning="candidate_failed_preservation",
    )

    # --- Density direction + simplification subset ---------------------------
    density_summary = _measure_density_summary(
        request=request,
        source=source,
        candidate=candidate,
        changed_track_ids=changed_track_ids,
    )
    density_ok, density_detail, density_warning = _validate_density_direction(
        operation=request.operation,
        density=density_summary,
        source=source,
        candidate=candidate,
        source_set=source_set,
    )
    if density_warning and density_warning not in warning_codes:
        warning_codes.append(density_warning)
    density_required = request.operation in _DENSITY_OPERATIONS
    _assert(
        "density_direction",
        density_ok if density_required else True,
        density_detail,
        required=density_required,
        stage="density",
        code="arrangement_preservation_failed",
        warning="candidate_failed_density",
    )

    # --- Audible effect ------------------------------------------------------
    audible_ok, audible_detail = _validate_audible_effect(
        operation=request.operation,
        source=source,
        candidate=candidate,
        realization=realization,
    )
    _assert(
        "audible_effect",
        audible_ok,
        audible_detail,
        stage="preservation",
        code="arrangement_preservation_failed",
        warning="candidate_failed_preservation",
    )

    # --- Declared doubling + duplicates --------------------------------------
    declared_ok, declared_detail, declared_warning = _validate_declared_doubling(
        request=request,
        realization=realization,
        source=source,
        candidate=candidate,
        doubling_track_ids=doubling_track_ids,
        doubling_source_ids=doubling_source_ids,
    )
    if declared_warning and declared_warning not in warning_codes:
        warning_codes.append(declared_warning)
    _assert(
        "declared_doubling",
        declared_ok,
        declared_detail,
        required=request.operation == "double_melody"
        or any(
            part.doubling_policy in AUTHORIZED_DOUBLING_POLICIES
            for part in request.instrumentation.after
        ),
        stage="duplicate",
        code="arrangement_preservation_failed",
        warning="candidate_failed_duplicate",
    )

    dup_findings, dup_error = _find_duplicate_clones(
        source=source,
        candidate=candidate,
        request=request,
        doubling_track_ids=doubling_track_ids,
        doubling_source_ids=doubling_source_ids,
        catalog=loaded_catalog,
    )
    duplicate_findings.extend(dup_findings[:ARRANGEMENT_MAX_DUPLICATE_FINDINGS])
    if dup_error:
        _fail(
            "duplicate",
            "arrangement_preservation_failed",
            dup_error,
            warning="candidate_failed_duplicate",
        )

    # --- Catalog absolute / preferred ranges ---------------------------------
    range_ok, range_items, range_warnings = _validate_catalog_ranges(
        source=source,
        candidate=candidate,
        changed_track_ids=changed_track_ids,
        catalog=loaded_catalog,
    )
    range_findings.extend(range_items[:ARRANGEMENT_MAX_RANGE_FINDINGS])
    for code in range_warnings:
        if code not in warning_codes:
            warning_codes.append(code)
    _assert(
        "range_policy",
        range_ok,
        (
            "Changed target material respects absolute playable ranges"
            if range_ok
            else "Changed target material violates absolute playable range"
        ),
        stage="range",
        code="arrangement_range_failed",
        warning="candidate_failed_range",
    )

    # Baseline instrument mismatches for untouched tracks (info only).
    for mismatch in find_baseline_mismatches(
        [track for track in candidate.tracks if track.id not in changed_track_ids],
        catalog=loaded_catalog,
    ):
        if "baseline_instrument_mismatch" not in warning_codes:
            warning_codes.append("baseline_instrument_mismatch")
        if len(range_findings) < ARRANGEMENT_MAX_RANGE_FINDINGS:
            range_findings.append(
                ArrangementRangeFinding(
                    severity="info",
                    code="baseline_range_retained",
                    track_id=mismatch.track_id,
                    instrument_id=mismatch.resolved_instrument_id,
                    detail=mismatch.message[:ARRANGEMENT_MAX_REASON_CHARS],
                )
            )

    # Carry forward realization warnings that are arrangement-relevant.
    for code in realization.warning_codes:
        if code in {
            "motif_occurrence_pruned",
            "octave_adjustment_applied",
            "declared_doubling_applied",
            "unlisted_after_allowed",
        } and code not in warning_codes:
            warning_codes.append(code)

    warning_codes = warning_codes[:ARRANGEMENT_MAX_WARNINGS]
    ok = rejection_stage is None and not any(
        item.required and not item.satisfied for item in assertions
    )
    status: Literal["accepted", "rejected"] = "accepted" if ok else "rejected"

    logger.info(
        "Arrangement candidate validation complete",
        extra={
            "version": ARRANGEMENT_VALIDATION_VERSION,
            "operation": request.operation,
            "candidate_status": status,
            "assertion_total": len(assertions),
            "assertion_satisfied": sum(1 for item in assertions if item.satisfied),
            "assertion_failed": sum(1 for item in assertions if not item.satisfied),
            "density_delta_events": (
                None
                if density_summary is None
                else density_summary.after.event_count - density_summary.before.event_count
            ),
            "density_delta_attacks": (
                None
                if density_summary is None
                else density_summary.after.attack_count - density_summary.before.attack_count
            ),
            "range_codes": sorted({item.code for item in range_findings})[:16],
            "duplicate_codes": sorted({item.code for item in duplicate_findings})[:16],
            "compatibility_failed": bool(harmony_summary and harmony_summary.failed),
            "rejection_stage": rejection_stage,
            "error_codes": error_codes[:16],
            "warning_codes": warning_codes[:16],
        },
    )
    logger.debug(
        "Arrangement validation aggregate metrics",
        extra={
            "before_event_count": None if density_summary is None else density_summary.before.event_count,
            "after_event_count": None if density_summary is None else density_summary.after.event_count,
            "before_attack_count": None if density_summary is None else density_summary.before.attack_count,
            "after_attack_count": None if density_summary is None else density_summary.after.attack_count,
            "before_max_simultaneity": (
                None if density_summary is None else density_summary.before.max_simultaneity
            ),
            "after_max_simultaneity": (
                None if density_summary is None else density_summary.after.max_simultaneity
            ),
            "instrument_role_pairs": _normalized_instrument_role_pairs(candidate)[:32],
            "changed_track_count": len(changed_track_ids),
            "doubling_track_count": len(doubling_track_ids),
        },
    )

    return ArrangementValidationResult(
        ok=ok,
        status=status,
        assertions=assertions,
        range_findings=range_findings,
        duplicate_findings=duplicate_findings,
        density=density_summary,
        harmony_compatibility=harmony_summary,
        warning_codes=warning_codes,
        error_codes=error_codes,
        rejection_stage=rejection_stage,
        rejection_codes=rejection_codes,
        rejection_reasons=rejection_reasons,
    )


# --- Helpers -----------------------------------------------------------------


def _changed_target_track_ids(realization: ArrangementRealizationResult) -> set[str]:
    return set(realization.manifest.added_track_ids) | set(
        realization.manifest.reinstrumented_track_ids
    )


def _declared_doubling_track_ids(
    request: CompositionArrangementPreviewRequest,
    realization: ArrangementRealizationResult,
) -> set[str]:
    doubling_parts = {
        part.part_id
        for part in request.instrumentation.after
        if part.doubling_policy in AUTHORIZED_DOUBLING_POLICIES
    }
    ids: set[str] = set()
    for item in realization.after_inventory:
        if item.part_id and item.part_id in doubling_parts:
            ids.add(item.track_id)
    for mapping in realization.manifest.source_to_target:
        if mapping.relationship == "doubled":
            ids.add(mapping.target_track_id)
    return ids


def _declared_doubling_source_ids(request: CompositionArrangementPreviewRequest) -> set[str]:
    ids: set[str] = set()
    for part in request.instrumentation.after:
        if part.doubling_policy in AUTHORIZED_DOUBLING_POLICIES:
            ids.update(part.source_track_ids)
    return ids


def _global_metadata_equal(source: CompositionV2, candidate: CompositionV2) -> bool:
    source_dump = source.model_dump(mode="json")
    candidate_dump = candidate.model_dump(mode="json")
    for field_name in _GLOBAL_METADATA_FIELDS:
        if source_dump.get(field_name) != candidate_dump.get(field_name):
            return False
    return True


def _harmony_metadata_equal(source: CompositionV2, candidate: CompositionV2) -> bool:
    return (
        source.harmony == candidate.harmony
        and source.key == candidate.key
        and source.key_changes == candidate.key_changes
    )


def _tracks_byte_equal(left: CompositionV2Track, right: CompositionV2Track) -> bool:
    """Compare tracks ignoring authored event array order (canonical sort)."""
    left_dump = left.model_dump(mode="json")
    right_dump = right.model_dump(mode="json")
    left_events = left_dump.pop("events", [])
    right_events = right_dump.pop("events", [])
    if left_dump != right_dump:
        return False
    return _sorted_event_payloads(left_events) == _sorted_event_payloads(right_events)


def _sorted_event_payloads(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda item: (
            item.get("start_tick", 0),
            item.get("pitch") or "",
            item.get("duration_ticks", 0),
            item.get("id") or "",
        ),
    )


def _track_events_byte_equal(left: CompositionV2Track, right: CompositionV2Track) -> bool:
    left_events = [event.model_dump(mode="json") for event in left.events]
    right_events = [event.model_dump(mode="json") for event in right.events]
    return _sorted_event_payloads(left_events) == _sorted_event_payloads(right_events)


def _logical_notes_for_tracks(
    composition: CompositionV2,
    track_ids: Iterable[str],
) -> list[CollapsedLogicalNote]:
    wanted = set(track_ids)
    notes: list[CollapsedLogicalNote] = []
    for track in composition.tracks:
        if track.id not in wanted or track.is_drum:
            continue
        notes.extend(collapse_track_tie_chains(track))
    return notes


def _semantic_key(note: CollapsedLogicalNote) -> tuple[Any, ...]:
    return (
        note.pitch,
        note.start_tick,
        note.duration_ticks,
        note.velocity,
        note.articulations,
        note.staff,
        note.voice,
        note.tie_group_id,
    )


def _semantic_multiset(notes: Sequence[CollapsedLogicalNote]) -> Counter[tuple[Any, ...]]:
    return Counter(_semantic_key(note) for note in notes)


def _event_fingerprint_multiset(tracks: Sequence[CompositionV2Track]) -> Counter[tuple[Any, ...]]:
    counter: Counter[tuple[Any, ...]] = Counter()
    for track in tracks:
        for event in track.events:
            counter[event_content_fingerprint(event)] += 1
    return counter


def _validate_topology_manifest(
    *,
    source: CompositionV2,
    candidate: CompositionV2,
    manifest: ArrangementTopologyManifest,
    source_set: set[str],
    protected_set: set[str],
    operation: str,
) -> tuple[bool, str]:
    source_ids = {track.id for track in source.tracks}
    candidate_ids = {track.id for track in candidate.tracks}

    retained = set(manifest.retained_track_ids)
    removed = set(manifest.removed_track_ids)
    added = set(manifest.added_track_ids)
    reinstrumented = set(manifest.reinstrumented_track_ids)
    split = set(manifest.split_track_ids)
    merged = set(manifest.merged_track_ids)

    # Contradictions inside the manifest.
    if retained & removed:
        return False, "Manifest lists the same track as retained and removed"
    if added & removed:
        return False, "Manifest lists the same track as added and removed"
    if added & source_ids and operation != "change_instrumentation":
        # Added IDs should be new unless reinstrument keeps id.
        unexpected = (added & source_ids) - reinstrumented
        if unexpected:
            return False, "Manifest added_track_ids overlaps unexplained source track ids"

    for track_id in protected_set:
        if track_id in removed:
            return False, "Protected track appears in removed_track_ids"
        if track_id not in candidate_ids:
            return False, "Protected track missing from candidate"

    # Every candidate track explained.
    explained_candidate = retained | added | reinstrumented
    # Retained may omit some unchanged unselected; treat presence in source+candidate
    # with identical content as explained.
    for track_id in candidate_ids:
        if track_id in explained_candidate:
            continue
        if track_id in source_ids and track_id in candidate_ids:
            continue
        return False, "Candidate track is not explained by the topology manifest"

    # Every removed source track must be absent (unless reinstrumented same id).
    for track_id in removed:
        if track_id in candidate_ids and track_id not in reinstrumented:
            return False, "Removed track still present in candidate"

    # Mapping consistency.
    for mapping in manifest.source_to_target:
        if mapping.relationship == "removed":
            if mapping.source_track_id not in removed and mapping.source_track_id in candidate_ids:
                return False, "Source-to-target removed mapping contradicts track presence"
            continue
        if mapping.target_track_id not in candidate_ids:
            return False, "Source-to-target mapping references missing target track"

    del split, merged, source_set, operation
    return True, "Topology manifest authorizes every base and candidate track"


def _validate_after_instrumentation(
    *,
    request: CompositionArrangementPreviewRequest,
    realization: ArrangementRealizationResult,
    catalog: ArrangementInstrumentCatalog,
) -> tuple[bool, str, str | None]:
    after_parts = request.instrumentation.after
    inventory = realization.after_inventory
    by_part = {item.part_id: item for item in inventory if item.part_id}
    warning: str | None = None

    missing_parts = [part.part_id for part in after_parts if part.part_id not in by_part]
    if missing_parts and not request.allow_unlisted_after:
        return False, "Requested after part missing from candidate inventory", None
    if missing_parts and request.allow_unlisted_after:
        warning = "unlisted_after_allowed"

    candidate_tracks = {track.id: track for track in realization.composition.tracks}
    for part in after_parts:
        item = by_part.get(part.part_id)
        if item is None:
            continue
        track = candidate_tracks.get(item.track_id)
        if track is None:
            return False, "After inventory references a missing track", None
        profile = resolve_profile(instrument_id=part.instrument_id, catalog=catalog)
        if profile is None:
            return False, "Unknown target instrument in after requirements", None
        track_profile = resolve_profile_for_track(track, catalog=catalog)
        if track_profile is None or track_profile.instrument_id != profile.instrument_id:
            # Compare by identity / program as a soft fallback.
            if int(track.midi_program) != int(profile.midi_program):
                return False, "After track instrument/program does not match requirement", None
        if part.role is not None and normalize_role(track.role) != part.role:
            return False, "After track role does not match requirement", None

    # Aggregate counts derived from after list multiplicity.
    required_instruments = Counter(part.instrument_id for part in after_parts)
    actual_instruments: Counter[str] = Counter()
    for part in after_parts:
        item = by_part.get(part.part_id)
        if item is None:
            continue
        track = candidate_tracks.get(item.track_id)
        if track is None:
            continue
        profile = resolve_profile_for_track(track, catalog=catalog)
        if profile is not None:
            actual_instruments[profile.instrument_id] += 1
        else:
            resolved = resolve_profile(instrument_id=part.instrument_id, catalog=catalog)
            if resolved is not None:
                actual_instruments[resolved.instrument_id] += 1

    if not request.allow_unlisted_after:
        for instrument_id, count in required_instruments.items():
            if actual_instruments.get(instrument_id, 0) < count:
                return False, "After instrument counts do not match requirements", None

    required_roles = Counter(part.role for part in after_parts if part.role is not None)
    actual_roles = Counter(
        normalize_role(track.role) for track in realization.composition.tracks if track.role
    )
    for role, count in required_roles.items():
        if actual_roles.get(role, 0) < count:
            return False, "After role counts do not match requirements", None

    return True, "After instrumentation inventory satisfied", warning


def _validate_source_note_cardinality(
    *,
    request: CompositionArrangementPreviewRequest,
    source: CompositionV2,
    candidate: CompositionV2,
    realization: ArrangementRealizationResult,
    doubling_track_ids: set[str],
) -> tuple[bool, str]:
    operation = request.operation
    source_tracks = [track for track in source.tracks if track.id in set(request.source_track_ids)]
    source_fp = _event_fingerprint_multiset(source_tracks)

    if operation in {"remove_accompaniment", "simplify_arrangement", "decrease_texture_density"}:
        # Selected material may shrink; no new fingerprints vs source selected set.
        candidate_selected = [
            track
            for track in candidate.tracks
            if track.id in set(request.source_track_ids)
            or track.id in set(realization.manifest.added_track_ids)
            or any(
                mapping.target_track_id == track.id
                for mapping in realization.manifest.source_to_target
            )
        ]
        candidate_fp = _event_fingerprint_multiset(candidate_selected)
        # New material forbidden for simplify; decrease may remove only.
        if operation == "simplify_arrangement":
            extras = candidate_fp - source_fp
            if sum(extras.values()) > 0:
                return False, "Simplification introduced new musical material"
        return True, "Source-note cardinality compatible with density/removal operation"

    if operation in {"add_accompaniment", "increase_texture_density", "create_countermelody", "double_melody"}:
        # Source notes must still be present somewhere in the candidate (doubles excluded
        # from primary coverage for melody, but additive ops keep source tracks).
        primary_tracks = [
            track for track in candidate.tracks if track.id not in doubling_track_ids
        ]
        primary_fp = _event_fingerprint_multiset(primary_tracks)
        missing = source_fp - primary_fp
        if sum(missing.values()) > 0:
            return False, "Source notes were lost during additive arrangement"
        return True, "Source-note cardinality preserved for additive operation"

    # Redistribution / orchestration / piano_to_ensemble / change_instrumentation.
    primary_tracks = [track for track in candidate.tracks if track.id not in doubling_track_ids]
    primary_fp = _event_fingerprint_multiset(primary_tracks)
    if operation == "change_instrumentation":
        # Events stay exact on reinstrumented tracks.
        for track_id in request.source_track_ids:
            source_track = next((t for t in source.tracks if t.id == track_id), None)
            candidate_track = next((t for t in candidate.tracks if t.id == track_id), None)
            if source_track is None or candidate_track is None:
                return False, "change_instrumentation lost a selected track"
            if not _track_events_byte_equal(source_track, candidate_track):
                return False, "change_instrumentation altered selected track events"
        return True, "Source-note cardinality preserved for reinstrumentation"

    # Redistributed material: every source note fingerprint appears exactly once
    # in primary candidate material (doubles excluded). Generated notes may add extras.
    missing = source_fp - primary_fp
    if sum(missing.values()) > 0:
        return False, "Selected source notes missing after redistribution"
    # Exact clones of the full source multiset on a single target are duplicate-checked
    # separately; cardinality only requires coverage.
    return True, "Selected source notes retained with expected cardinality"


def _compute_density_metrics(
    tracks: Sequence[CompositionV2Track],
    duration_ticks: int,
) -> ArrangementDensityMetrics:
    logical_by_track = [collapse_track_tie_chains(track) for track in tracks if not track.is_drum]
    all_notes = [note for notes in logical_by_track for note in notes]
    event_count = sum(len(track.events) for track in tracks)
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


def _measure_density_summary(
    *,
    request: CompositionArrangementPreviewRequest,
    source: CompositionV2,
    candidate: CompositionV2,
    changed_track_ids: set[str],
) -> ArrangementDensitySummary:
    source_ids = set(request.source_track_ids)
    before_tracks = [track for track in source.tracks if track.id in source_ids]
    # After metrics over selected remaining tracks plus changed/created targets.
    unselected = {track.id for track in source.tracks} - source_ids
    after_tracks = [
        track
        for track in candidate.tracks
        if track.id not in unselected or track.id in changed_track_ids
    ]
    if not after_tracks:
        after_tracks = list(candidate.tracks)
    return ArrangementDensitySummary(
        before=_compute_density_metrics(before_tracks, source.duration_ticks),
        after=_compute_density_metrics(after_tracks, candidate.duration_ticks),
    )


def _validate_density_direction(
    *,
    operation: str,
    density: ArrangementDensitySummary,
    source: CompositionV2,
    candidate: CompositionV2,
    source_set: set[str],
) -> tuple[bool, str, str | None]:
    before = density.before
    after = density.after
    deltas = {
        "event_count": after.event_count - before.event_count,
        "attack_count": after.attack_count - before.attack_count,
        "active_track_count": after.active_track_count - before.active_track_count,
        "union_occupancy_ticks": after.union_occupancy_ticks - before.union_occupancy_ticks,
        "max_simultaneity": after.max_simultaneity - before.max_simultaneity,
    }
    if operation == "increase_texture_density":
        if any(value > 0 for value in deltas.values()):
            return True, "Texture density increased on at least one metric", None
        return False, "increase_texture_density did not raise any density metric", None
    if operation == "decrease_texture_density":
        if any(value < 0 for value in deltas.values()):
            return True, "Texture density decreased on at least one metric", None
        return False, "decrease_texture_density did not lower any density metric", None
    if operation == "simplify_arrangement":
        if not any(value < 0 for value in deltas.values()):
            return False, "simplify_arrangement did not reduce selected density", None
        # No new musical material: candidate selected fingerprints ⊆ source.
        source_tracks = [t for t in source.tracks if t.id in source_set]
        source_fp = _event_fingerprint_multiset(source_tracks)
        candidate_fp = _event_fingerprint_multiset(
            [t for t in candidate.tracks if t.id not in ({t.id for t in source.tracks} - source_set)]
        )
        extras = candidate_fp - source_fp
        if sum(extras.values()) > 0:
            return False, "simplify_arrangement introduced new musical material", None
        return True, "Arrangement simplified with reduced density and no new material", None
    return True, "Density direction not required for this operation", "density_metric_advisory"


def _validate_audible_effect(
    *,
    operation: str,
    source: CompositionV2,
    candidate: CompositionV2,
    realization: ArrangementRealizationResult,
) -> tuple[bool, str]:
    source_events = sum(len(track.events) for track in source.tracks)
    candidate_events = sum(len(track.events) for track in candidate.tracks)
    source_tracks = len(source.tracks)
    candidate_tracks = len(candidate.tracks)

    if candidate_events == 0:
        return False, "Candidate has no audible note events"

    empty_targets = [
        track_id
        for track_id in realization.manifest.added_track_ids
        if not next((t for t in candidate.tracks if t.id == track_id and t.events), None)
    ]
    if empty_targets:
        return False, "Added arrangement target track is empty/inaudible"

    if operation == "add_accompaniment":
        if candidate_events > source_events or candidate_tracks > source_tracks:
            return True, "Audible accompaniment material increased"
        return False, "add_accompaniment produced no audible increase"
    if operation == "remove_accompaniment":
        if candidate_events < source_events or candidate_tracks < source_tracks:
            return True, "Accompaniment material was audibly removed"
        return False, "remove_accompaniment produced no audible decrease"
    if operation in {"create_countermelody", "double_melody", "increase_texture_density"}:
        if candidate_events > source_events or realization.event_counts.generated > 0:
            return True, "Operation produced audible new material"
        if candidate_tracks > source_tracks:
            return True, "Operation added audible tracks"
        return False, "Operation produced no audible effect"
    if operation in {"simplify_arrangement", "decrease_texture_density"}:
        if candidate_events < source_events or realization.event_counts.removed > 0:
            return True, "Operation audibly reduced material"
        return False, "Operation produced no audible reduction"
    if operation == "change_instrumentation":
        if realization.manifest.reinstrumented_track_ids:
            return True, "Instrumentation change is audible via program/identity"
        return False, "change_instrumentation had no reinstrumented tracks"
    # Redistribution / piano_to_ensemble: require non-empty targets and topology change.
    if realization.manifest.added_track_ids or realization.manifest.removed_track_ids:
        if all(
            track.events
            for track in candidate.tracks
            if track.id in set(realization.manifest.added_track_ids)
            or track.id in set(realization.manifest.reinstrumented_track_ids)
            or True
        ):
            return True, "Redistribution produced audible target material"
    if candidate_events > 0:
        return True, "Candidate remains audible"
    return False, "Candidate is inaudible"


def _validate_declared_doubling(
    *,
    request: CompositionArrangementPreviewRequest,
    realization: ArrangementRealizationResult,
    source: CompositionV2,
    candidate: CompositionV2,
    doubling_track_ids: set[str],
    doubling_source_ids: set[str],
) -> tuple[bool, str, str | None]:
    doubling_parts = [
        part
        for part in request.instrumentation.after
        if part.doubling_policy in AUTHORIZED_DOUBLING_POLICIES
    ]
    if not doubling_parts:
        return True, "No declared doubling relationship", None

    if request.operation == "double_melody" and len(doubling_parts) != 1:
        return False, "double_melody requires exactly one declared doubling", None

    warning = "declared_doubling_applied"
    for part in doubling_parts:
        target_item = next(
            (item for item in realization.after_inventory if item.part_id == part.part_id),
            None,
        )
        if target_item is None:
            return False, "Declared doubling target part missing", None
        target = next(
            (track for track in candidate.tracks if track.id == target_item.track_id),
            None,
        )
        if target is None or not target.events:
            return False, "Declared doubling target has no events", None
        source_ids = list(part.source_track_ids) or list(doubling_source_ids) or list(
            request.source_track_ids
        )
        source_notes = _logical_notes_for_tracks(source, source_ids)
        target_notes = collapse_track_tie_chains(target)
        if not source_notes or not target_notes:
            return False, "Declared doubling lacks comparable note material", None
        related = (
            _is_unison_or_octave_related(source_notes, target_notes, allow_octave=True)
            or classify_content_relationship(
                [n for tid in source_ids for n in _events_for_track(source, tid)],
                target.events,
            )
            in {"exact", "high_overlap"}
            or _contour_similarity(source_notes, target_notes) >= _CLONE_CONTOUR_MIN
        )
        if not related:
            return False, "Declared doubling target is unrelated to its source", None
    del doubling_track_ids
    return True, "Declared doubling relationship satisfied", warning


def _events_for_track(composition: CompositionV2, track_id: str) -> list:
    track = next((item for item in composition.tracks if item.id == track_id), None)
    return list(track.events) if track is not None else []


def _find_duplicate_clones(
    *,
    source: CompositionV2,
    candidate: CompositionV2,
    request: CompositionArrangementPreviewRequest,
    doubling_track_ids: set[str],
    doubling_source_ids: set[str],
    catalog: ArrangementInstrumentCatalog,
) -> tuple[list[ArrangementDuplicateFinding], str | None]:
    findings: list[ArrangementDuplicateFinding] = []
    error: str | None = None
    tracks = [track for track in candidate.tracks if not track.is_drum]
    profiles = {
        track.id: resolve_profile_for_track(track, catalog=catalog) for track in tracks
    }

    # Same-instrument / same-role accidental clones.
    for index, left in enumerate(tracks):
        for right in tracks[index + 1 :]:
            if left.id in doubling_track_ids or right.id in doubling_track_ids:
                # Declared doubles are exempt.
                pair_is_declared = (
                    left.id in doubling_track_ids and right.id in doubling_source_ids
                ) or (right.id in doubling_track_ids and left.id in doubling_source_ids)
                if pair_is_declared or left.id in doubling_track_ids or right.id in doubling_track_ids:
                    if left.id in doubling_track_ids or right.id in doubling_track_ids:
                        findings.append(
                            ArrangementDuplicateFinding(
                                severity="info",
                                code="declared_doubling",
                                source_track_id=left.id,
                                target_track_id=right.id,
                                detail="Overlap permitted by declared doubling policy",
                            )
                        )
                        continue

            left_role = normalize_role(left.role)
            right_role = normalize_role(right.role)
            left_identity = normalize_instrument_identity(left.instrument)
            right_identity = normalize_instrument_identity(right.instrument)
            left_profile = profiles.get(left.id)
            right_profile = profiles.get(right.id)
            if left_profile and right_profile:
                same_instrument = left_profile.instrument_id == right_profile.instrument_id
            else:
                same_instrument = left_identity is not None and left_identity == right_identity

            relationship = classify_content_relationship(left.events, right.events)
            if same_instrument and left_role == right_role and relationship in {
                "exact",
                "high_overlap",
            }:
                # Legitimate Violin I/II style parts: same instrument, same role,
                # but content is distinct — already filtered by relationship.
                code = "exact_clone" if relationship == "exact" else "high_overlap_clone"
                findings.append(
                    ArrangementDuplicateFinding(
                        severity="error",
                        code=code,
                        source_track_id=left.id,
                        target_track_id=right.id,
                        detail="Accidental same-instrument/same-role clone detected",
                    )
                )
                error = "Accidental same-instrument/same-role clone rejected"
                continue

            if same_instrument and left_role == right_role and relationship == "distinct":
                findings.append(
                    ArrangementDuplicateFinding(
                        severity="info",
                        code="legitimate_same_instrument_parts",
                        source_track_id=left.id,
                        target_track_id=right.id,
                        detail="Distinct same-instrument parts allowed",
                    )
                )
                continue

            # Cross-instrument / cross-role contour clones (source vs target).
            if relationship == "exact" and (not same_instrument or left_role != right_role):
                code = (
                    "cross_instrument_clone"
                    if not same_instrument
                    else "cross_role_clone"
                )
                findings.append(
                    ArrangementDuplicateFinding(
                        severity="error",
                        code=code,
                        source_track_id=left.id,
                        target_track_id=right.id,
                        detail="Cross-instrument or cross-role exact clone detected",
                    )
                )
                error = "Cross-instrument/cross-role clone rejected"
                continue

            left_notes = collapse_track_tie_chains(left)
            right_notes = collapse_track_tie_chains(right)
            if _is_octave_melody_clone(left_notes, right_notes):
                if left.id in doubling_track_ids or right.id in doubling_track_ids:
                    continue
                findings.append(
                    ArrangementDuplicateFinding(
                        severity="error",
                        code="octave_melody_clone",
                        source_track_id=left.id,
                        target_track_id=right.id,
                        detail="Undeclared octave melody clone detected",
                    )
                )
                error = "Undeclared octave melody clone rejected"
                continue

            if (
                (not same_instrument or left_role != right_role)
                and _contour_similarity(left_notes, right_notes) >= _CLONE_CONTOUR_MIN
                and len(left_notes) >= _CLONE_MIN_NOTES
                and len(right_notes) >= _CLONE_MIN_NOTES
            ):
                if left.id in doubling_track_ids or right.id in doubling_track_ids:
                    continue
                code = (
                    "cross_instrument_clone"
                    if not same_instrument
                    else "cross_role_clone"
                )
                findings.append(
                    ArrangementDuplicateFinding(
                        severity="error",
                        code=code,
                        source_track_id=left.id,
                        target_track_id=right.id,
                        detail="High contour-similarity cross clone detected",
                    )
                )
                error = "Cross-instrument/cross-role contour clone rejected"

    # Piano-to-ensemble: reject wholesale cloning of the same source onto 2+ targets,
    # or dumping the entire selected sketch onto a single target part.
    if request.operation == "piano_to_ensemble":
        selected_sources = [
            track
            for track in source.tracks
            if track.id in set(request.source_track_ids) and not track.is_drum
        ]
        for source_track in selected_sources:
            if len(source_track.events) < _CLONE_MIN_NOTES:
                continue
            matches = [
                target
                for target in tracks
                if target.id not in doubling_track_ids
                and classify_content_relationship(source_track.events, target.events)
                == "exact"
            ]
            if len(matches) >= 2:
                findings.append(
                    ArrangementDuplicateFinding(
                        severity="error",
                        code="exact_clone",
                        source_track_id=source_track.id,
                        target_track_id=matches[1].id,
                        detail="Full-part piano source cloned onto multiple targets",
                    )
                )
                error = "Full-part piano clone rejected during piano_to_ensemble"

        source_union = _event_fingerprint_multiset(selected_sources)
        if sum(source_union.values()) >= _CLONE_MIN_NOTES:
            for target in tracks:
                if target.id in doubling_track_ids:
                    continue
                if _event_fingerprint_multiset([target]) == source_union:
                    findings.append(
                        ArrangementDuplicateFinding(
                            severity="error",
                            code="exact_clone",
                            source_track_id=selected_sources[0].id if selected_sources else None,
                            target_track_id=target.id,
                            detail="Entire piano sketch cloned onto a single ensemble target",
                        )
                    )
                    error = "Full-part piano clone rejected during piano_to_ensemble"

    return findings, error


def _validate_catalog_ranges(
    *,
    source: CompositionV2,
    candidate: CompositionV2,
    changed_track_ids: set[str],
    catalog: ArrangementInstrumentCatalog,
) -> tuple[bool, list[ArrangementRangeFinding], list[str]]:
    findings: list[ArrangementRangeFinding] = []
    warnings: list[str] = []
    ok = True
    source_by_id = {track.id: track for track in source.tracks}

    for track in candidate.tracks:
        if track.is_drum:
            continue
        profile = resolve_profile_for_track(track, catalog=catalog)
        source_track = source_by_id.get(track.id)
        unchanged = (
            source_track is not None
            and track.id not in changed_track_ids
            and _track_events_byte_equal(source_track, track)
        )

        if profile is None:
            if track.id in changed_track_ids:
                ok = False
                findings.append(
                    ArrangementRangeFinding(
                        severity="error",
                        code="unknown_policy",
                        track_id=track.id,
                        instrument_id=None,
                        detail="Changed target instrument could not be resolved in catalog",
                    )
                )
            continue

        if profile.range_policy in {"unbounded", "unknown"}:
            findings.append(
                ArrangementRangeFinding(
                    severity="info",
                    code="unbounded_policy" if profile.range_policy == "unbounded" else "unknown_policy",
                    track_id=track.id,
                    instrument_id=profile.instrument_id,
                    detail=f"Instrument uses {profile.range_policy} range policy",
                )
            )
            continue

        assert profile.playable_low is not None and profile.playable_high is not None
        assert profile.preferred_low is not None and profile.preferred_high is not None

        for event in track.events:
            try:
                midi = midi_pitch_number(event.pitch)
            except ValueError:
                if track.id in changed_track_ids:
                    ok = False
                continue

            absolute_fail = midi < profile.playable_low or midi > profile.playable_high
            preferred_fail = midi < profile.preferred_low or midi > profile.preferred_high

            if unchanged:
                # Baseline-only: suppress hard errors for byte-identical source material.
                if absolute_fail or preferred_fail:
                    if "baseline_range_retained" not in warnings:
                        warnings.append("baseline_range_retained")
                    findings.append(
                        ArrangementRangeFinding(
                            severity="info",
                            code="baseline_range_retained",
                            track_id=track.id,
                            instrument_id=profile.instrument_id,
                            detail="Unchanged source outlier retained as baseline finding",
                        )
                    )
                continue

            if track.id not in changed_track_ids and source_track is not None:
                # Untouched track with possible metadata-only differences: baseline.
                if absolute_fail or preferred_fail:
                    if "baseline_range_retained" not in warnings:
                        warnings.append("baseline_range_retained")
                    findings.append(
                        ArrangementRangeFinding(
                            severity="info",
                            code="baseline_range_retained",
                            track_id=track.id,
                            instrument_id=profile.instrument_id,
                            detail="Untouched source track range finding retained as baseline",
                        )
                    )
                continue

            # Byte-identical event at same track/event location may be suppressed
            # even on reinstrumented tracks when the note body is unchanged.
            if (
                source_track is not None
                and event.id
                and _source_event_byte_identical(source_track, event)
            ):
                if absolute_fail or preferred_fail:
                    if "baseline_range_retained" not in warnings:
                        warnings.append("baseline_range_retained")
                    findings.append(
                        ArrangementRangeFinding(
                            severity="info",
                            code="baseline_range_retained",
                            track_id=track.id,
                            instrument_id=profile.instrument_id,
                            detail="Byte-identical source note range finding suppressed",
                        )
                    )
                continue

            if absolute_fail:
                ok = False
                findings.append(
                    ArrangementRangeFinding(
                        severity="error",
                        code="absolute_out_of_range",
                        track_id=track.id,
                        instrument_id=profile.instrument_id,
                        detail="Note outside absolute playable range for target instrument",
                    )
                )
            elif preferred_fail:
                if "questionable_range" not in warnings:
                    warnings.append("questionable_range")
                findings.append(
                    ArrangementRangeFinding(
                        severity="warning",
                        code="questionable_range",
                        track_id=track.id,
                        instrument_id=profile.instrument_id,
                        detail="Note outside preferred range for target instrument",
                    )
                )

    return ok, findings, warnings


def _source_event_byte_identical(
    source_track: CompositionV2Track,
    event: CompositionV2NoteEvent,
) -> bool:
    if event.id is None:
        return False
    for source_event in source_track.events:
        if source_event.id != event.id:
            continue
        return source_event.model_dump(mode="json") == event.model_dump(mode="json")
    return False


def _check_harmony_compatibility(
    *,
    source: CompositionV2,
    candidate: CompositionV2,
    preserve_harmony: bool,
    changed_track_ids: set[str],
) -> tuple[ArrangementHarmonyCompatibilitySummary, list[str]]:
    warnings: list[str] = []
    del source  # metadata exactness is checked separately
    if not candidate.harmony:
        warnings.append("empty_harmony_context")
        return (
            ArrangementHarmonyCompatibilitySummary(
                checked_note_count=0,
                compatible_note_count=0,
                tension_note_count=0,
                failed=False,
                detail="No harmony spans to check",
            ),
            warnings,
        )

    # Advisory chord-tone compatibility only — do not re-run source preservation
    # topology checks here (those are owned by arrangement assertions).
    report = analyze_harmony_compatibility(
        candidate,
        authorized_target_ids=sorted(changed_track_ids),
    )
    checked = int(report.evidence_counts.get("note_count", 0))
    tension = sum(
        1
        for item in report.findings
        if item.code
        in {
            "melody_non_chord_tone",
            "tension_delta",
            "avoid_tone",
            "accompaniment_non_chord_tone",
        }
        and item.severity in {"warning", "info"}
    )
    # Hard incompatibility is reserved for explicit structural span corruption.
    hard_codes = {
        "unsupported_chord_symbol",
        "competing_tonal_center",
        "harmony_span_invalid",
    }
    hard_errors = [
        item
        for item in report.findings
        if item.severity == "error" and item.code in hard_codes
    ]
    if tension > 0:
        warnings.append("mild_harmony_tension")
    failed = bool(preserve_harmony and hard_errors)
    compatible = max(0, checked - tension) if checked else 0
    detail = None
    if failed:
        detail = "Harmony compatibility reported incompatible generated material"
    return (
        ArrangementHarmonyCompatibilitySummary(
            checked_note_count=checked,
            compatible_note_count=compatible,
            tension_note_count=tension,
            failed=failed,
            detail=detail,
        ),
        warnings,
    )


def _is_unison_or_octave_related(
    left: Sequence[CollapsedLogicalNote],
    right: Sequence[CollapsedLogicalNote],
    *,
    allow_octave: bool,
) -> bool:
    if not left or not right:
        return False
    # Align by onset; require pitch-class match and optional constant octave shift.
    left_by_onset = sorted(left, key=lambda n: (n.start_tick, n.midi_number))
    right_by_onset = sorted(right, key=lambda n: (n.start_tick, n.midi_number))
    if len(left_by_onset) != len(right_by_onset):
        # Allow subset doubling (target doubles a subset of source melody).
        if len(right_by_onset) > len(left_by_onset):
            return False
        left_map = {(n.start_tick, n.pitch_class): n for n in left_by_onset}
        shifts: list[int] = []
        for note in right_by_onset:
            match = left_map.get((note.start_tick, note.pitch_class))
            if match is None:
                return False
            shifts.append(note.midi_number - match.midi_number)
        if not shifts:
            return False
        if allow_octave:
            return all(shift % 12 == 0 for shift in shifts)
        return all(shift == 0 for shift in shifts)

    shifts = [
        right_by_onset[i].midi_number - left_by_onset[i].midi_number
        for i in range(len(left_by_onset))
        if right_by_onset[i].start_tick == left_by_onset[i].start_tick
        and right_by_onset[i].duration_ticks == left_by_onset[i].duration_ticks
    ]
    if len(shifts) != len(left_by_onset):
        return False
    if allow_octave:
        return all(shift % 12 == 0 for shift in shifts) and any(shift != 0 for shift in shifts)
    return all(shift == 0 for shift in shifts)


def _is_octave_melody_clone(
    left: Sequence[CollapsedLogicalNote],
    right: Sequence[CollapsedLogicalNote],
) -> bool:
    if len(left) < _CLONE_MIN_NOTES or len(right) < _CLONE_MIN_NOTES:
        return False
    if len(left) != len(right):
        return False
    left_o = sorted(left, key=lambda n: (n.start_tick, n.midi_number))
    right_o = sorted(right, key=lambda n: (n.start_tick, n.midi_number))
    shifts: list[int] = []
    for a, b in zip(left_o, right_o, strict=True):
        if a.start_tick != b.start_tick or a.duration_ticks != b.duration_ticks:
            return False
        if a.pitch_class != b.pitch_class:
            return False
        shifts.append(b.midi_number - a.midi_number)
    if not shifts:
        return False
    return all(shift % 12 == 0 for shift in shifts) and any(abs(shift) >= 12 for shift in shifts)


def _contour_similarity(
    left: Sequence[CollapsedLogicalNote],
    right: Sequence[CollapsedLogicalNote],
) -> float:
    if len(left) < 2 or len(right) < 2:
        return 0.0
    left_o = sorted(left, key=lambda n: (n.start_tick, n.midi_number))
    right_o = sorted(right, key=lambda n: (n.start_tick, n.midi_number))
    left_intervals = [
        left_o[i + 1].midi_number - left_o[i].midi_number for i in range(len(left_o) - 1)
    ]
    right_intervals = [
        right_o[i + 1].midi_number - right_o[i].midi_number for i in range(len(right_o) - 1)
    ]
    left_ioi = [
        left_o[i + 1].start_tick - left_o[i].start_tick for i in range(len(left_o) - 1)
    ]
    right_ioi = [
        right_o[i + 1].start_tick - right_o[i].start_tick for i in range(len(right_o) - 1)
    ]
    # Transposition-normalized: compare interval sequences.
    interval_score = _sequence_ratio(left_intervals, right_intervals)
    # Rhythm-normalized IOI (scale-invariant).
    left_norm = _normalize_positive(left_ioi)
    right_norm = _normalize_positive(right_ioi)
    rhythm_score = _sequence_ratio(left_norm, right_norm)
    # Pitch-class contour (sign of intervals).
    left_signs = [0 if v == 0 else (1 if v > 0 else -1) for v in left_intervals]
    right_signs = [0 if v == 0 else (1 if v > 0 else -1) for v in right_intervals]
    contour_score = _sequence_ratio(left_signs, right_signs)
    return (0.4 * interval_score) + (0.3 * rhythm_score) + (0.3 * contour_score)


def _normalize_positive(values: Sequence[int]) -> list[int]:
    positive = [max(0, int(v)) for v in values]
    gcd = 0
    for value in positive:
        gcd = math.gcd(gcd, value) if gcd else value
    if gcd <= 1:
        return positive
    return [value // gcd for value in positive]


def _sequence_ratio(left: Sequence[int], right: Sequence[int]) -> float:
    if not left or not right:
        return 0.0
    length = min(len(left), len(right))
    if length == 0:
        return 0.0
    matches = sum(1 for i in range(length) if left[i] == right[i])
    # Penalize length mismatch lightly.
    length_penalty = length / float(max(len(left), len(right)))
    return (matches / float(length)) * length_penalty


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


def _normalized_instrument_role_pairs(composition: CompositionV2) -> list[str]:
    pairs: list[str] = []
    for track in composition.tracks:
        identity = normalize_instrument_identity(track.instrument) or track.instrument.lower()
        role = normalize_role(track.role) if track.role else "other"
        pairs.append(f"{identity}:{role}")
    return sorted(pairs)


__all__ = [
    "ARRANGEMENT_VALIDATION_VERSION",
    "ArrangementValidationResult",
    "validate_arrangement_candidate",
]
