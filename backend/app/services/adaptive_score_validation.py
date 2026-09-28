"""Pure graph and binding checks for ``adaptive.score.v1``.

No SQLite. No FastAPI. Does not mutate Composition or the score model.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from app.adaptive_score_schemas import (
    FORBIDDEN_NOTE_KEYS,
    AdaptiveBindingStatus,
    AdaptiveMaterialRefV1,
    AdaptiveScoreError,
    AdaptiveScoreFindingV1,
    AdaptiveScoreV1,
)
from app.composition_schemas import CompositionV2

logger = logging.getLogger(__name__)


def _finding(
    code: str,
    severity: str,
    *,
    target_id: str | None = None,
    message: str = "",
) -> AdaptiveScoreFindingV1:
    return AdaptiveScoreFindingV1(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        target_id=target_id,
        message=message[:200],
    )


def _scan_forbidden_keys(payload: Any, *, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_path = f"{path}.{key}" if path else str(key)
            if str(key) in FORBIDDEN_NOTE_KEYS:
                found.append(key_path)
            found.extend(_scan_forbidden_keys(value, path=key_path))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            found.extend(_scan_forbidden_keys(item, path=f"{path}[{index}]"))
    return found


def reject_embedded_note_material(payload: Any) -> None:
    """Raise when a raw tree contains note-event keys. Does not log the tree."""
    hits = _scan_forbidden_keys(payload)
    if not hits:
        logger.debug("Embedded note scan clean", extra={"hit_count": 0})
        return
    logger.debug(
        "Embedded note material rejected",
        extra={"code": "embedded_note_material", "hit_count": len(hits)},
    )
    raise AdaptiveScoreError(
        "embedded_note_material",
        "Adaptive scores cannot embed note events",
        http_status=422,
        details={"hit_count": len(hits)},
    )


def iter_material_refs(score: AdaptiveScoreV1) -> list[tuple[str, AdaptiveMaterialRefV1]]:
    refs: list[tuple[str, AdaptiveMaterialRefV1]] = []
    for state in score.states:
        refs.append((state.id, state.material))
    for variant in score.variants:
        refs.append((variant.id, variant.material))
    for layer in score.layers:
        refs.append((layer.id, layer.material))
    for stinger in score.stingers:
        refs.append((stinger.id, stinger.material))
    return refs


def _kind_counts(score: AdaptiveScoreV1) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for _target, material in iter_material_refs(score):
        counts[material.kind] += 1
    return {
        "section": counts["section"],
        "bar_range": counts["bar_range"],
        "track_range": counts["track_range"],
        "motif": counts["motif"],
        "revision_region": counts["revision_region"],
        "asset": counts["asset"],
    }


def _log_findings(findings: list[AdaptiveScoreFindingV1], score: AdaptiveScoreV1) -> None:
    errors = sum(1 for item in findings if item.severity == "error")
    warnings = sum(1 for item in findings if item.severity == "warning")
    logger.info(
        "Adaptive score graph checked",
        extra={
            "schema_version": score.schema_version,
            "error_count": errors,
            "warning_count": warnings,
            "state_count": len(score.states),
            "variant_count": len(score.variants),
            "transition_count": len(score.transitions),
            "layer_count": len(score.layers),
            "stinger_count": len(score.stingers),
            **{f"kind_{key}": value for key, value in _kind_counts(score).items()},
        },
    )
    for item in findings:
        logger.debug(
            "Adaptive score finding",
            extra={"code": item.code, "target_id": item.target_id, "severity": item.severity},
        )


def _apply_strict(
    findings: list[AdaptiveScoreFindingV1],
    *,
    strict: bool,
) -> list[AdaptiveScoreFindingV1]:
    if not strict:
        return findings
    promoted: list[AdaptiveScoreFindingV1] = []
    for item in findings:
        if item.severity == "warning":
            promoted.append(item.model_copy(update={"severity": "error"}))
        else:
            promoted.append(item)
    return promoted


def validate_adaptive_score_graph(
    score: AdaptiveScoreV1,
    *,
    strict: bool = False,
) -> list[AdaptiveScoreFindingV1]:
    """Cross-entity rules. Does not read or write Composition."""
    findings: list[AdaptiveScoreFindingV1] = []
    state_ids = {state.id for state in score.states}
    buckets: list[tuple[str, list[str]]] = [
        ("state", [state.id for state in score.states]),
        ("variant", [variant.id for variant in score.variants]),
        ("transition", [transition.id for transition in score.transitions]),
        ("layer", [layer.id for layer in score.layers]),
        ("stinger", [stinger.id for stinger in score.stingers]),
    ]
    seen: set[str] = set()
    for _kind, ids in buckets:
        for entity_id in ids:
            if entity_id in seen:
                findings.append(
                    _finding(
                        "duplicate_id",
                        "error",
                        target_id=entity_id,
                        message="Entity ids must be unique within one adaptive score.",
                    )
                )
            seen.add(entity_id)

    if score.states:
        if not score.initial_state_id:
            findings.append(
                _finding(
                    "initial_state_missing",
                    "warning",
                    message="initial_state_id is unset while states exist.",
                )
            )
        elif score.initial_state_id not in state_ids:
            findings.append(
                _finding(
                    "dangling_state_ref",
                    "error",
                    target_id=score.initial_state_id,
                    message="initial_state_id does not name a state.",
                )
            )
        if not score.default_state_id:
            findings.append(
                _finding(
                    "default_state_missing",
                    "warning",
                    message="default_state_id is unset while states exist.",
                )
            )
        elif score.default_state_id not in state_ids:
            findings.append(
                _finding(
                    "dangling_state_ref",
                    "error",
                    target_id=score.default_state_id,
                    message="default_state_id does not name a state.",
                )
            )

    transitions = {item.id: item for item in score.transitions}
    states = {item.id: item for item in score.states}
    for transition in score.transitions:
        if transition.from_state_id not in state_ids:
            findings.append(
                _finding(
                    "dangling_state_ref",
                    "error",
                    target_id=transition.from_state_id,
                    message="Transition source state does not exist.",
                )
            )
        if transition.to_state_id not in state_ids:
            findings.append(
                _finding(
                    "dangling_state_ref",
                    "error",
                    target_id=transition.to_state_id,
                    message="Transition destination state does not exist.",
                )
            )
        source = states.get(transition.from_state_id)
        if source is not None and transition.id not in source.transition_ids:
            findings.append(
                _finding(
                    "transition_eligibility_mismatch",
                    "error",
                    target_id=transition.id,
                    message="Transition is not listed on its source state.",
                )
            )
        if transition.quantization == "custom":
            if transition.custom_grid_bars is None:
                findings.append(
                    _finding(
                        "quantization_grid",
                        "error",
                        target_id=transition.id,
                        message="Custom quantization requires custom_grid_bars.",
                    )
                )
        elif transition.custom_grid_bars is not None:
            findings.append(
                _finding(
                    "quantization_grid",
                    "error",
                    target_id=transition.id,
                    message="custom_grid_bars is only valid when quantization is custom.",
                )
            )
        if (
            transition.fallback_behavior == "alternate_transition"
            and transition.fallback_transition_id
            and transition.fallback_transition_id not in transitions
        ):
            findings.append(
                _finding(
                    "fallback_transition_missing",
                    "error",
                    target_id=transition.fallback_transition_id,
                    message="fallback_transition_id does not name a transition.",
                )
            )

    for state in score.states:
        if (
            state.max_duration_bars is not None
            and state.min_duration_bars > state.max_duration_bars
        ):
            findings.append(
                _finding(
                    "duration_bounds",
                    "error",
                    target_id=state.id,
                    message="min_duration_bars cannot exceed max_duration_bars.",
                )
            )
        if state.loop.enabled:
            if (
                state.loop.start_bar is None
                or state.loop.end_bar is None
                or state.loop.end_bar < state.loop.start_bar
            ):
                findings.append(
                    _finding(
                        "loop_bounds",
                        "error",
                        target_id=state.id,
                        message="Enabled loop requires end_bar >= start_bar.",
                    )
                )
        for transition_id in state.transition_ids:
            transition = transitions.get(transition_id)
            if transition is None or transition.from_state_id != state.id:
                findings.append(
                    _finding(
                        "transition_eligibility_mismatch",
                        "error",
                        target_id=transition_id,
                        message="Eligibility id is missing or does not leave this state.",
                    )
                )

    for variant in score.variants:
        if variant.state_id not in state_ids:
            findings.append(
                _finding(
                    "dangling_state_ref",
                    "error",
                    target_id=variant.state_id,
                    message="Variant state_id does not name a state.",
                )
            )
    for layer in score.layers:
        if layer.state_id is not None and layer.state_id not in state_ids:
            findings.append(
                _finding(
                    "dangling_state_ref",
                    "error",
                    target_id=layer.state_id,
                    message="Layer state_id does not name a state.",
                )
            )
    for stinger in score.stingers:
        if stinger.quantization == "custom" and stinger.custom_grid_bars is None:
            findings.append(
                _finding(
                    "quantization_grid",
                    "error",
                    target_id=stinger.id,
                    message="Custom quantization requires custom_grid_bars.",
                )
            )
        elif stinger.quantization != "custom" and stinger.custom_grid_bars is not None:
            findings.append(
                _finding(
                    "quantization_grid",
                    "error",
                    target_id=stinger.id,
                    message="custom_grid_bars is only valid when quantization is custom.",
                )
            )
        if stinger.associated_state_id is not None and stinger.associated_state_id not in state_ids:
            findings.append(
                _finding(
                    "dangling_state_ref",
                    "error",
                    target_id=stinger.associated_state_id,
                    message="Stinger associated_state_id does not name a state.",
                )
            )

    revision_ids = {
        material.revision_id
        for _target, material in iter_material_refs(score)
        if material.kind != "asset"
    }
    if len(revision_ids) > 1:
        findings.append(
            _finding(
                "mixed_revision_targets",
                "error",
                message="Non-asset material refs must share one revision id.",
            )
        )

    if score.states and score.initial_state_id in state_ids:
        reachable: set[str] = set()
        stack = [score.initial_state_id]
        while stack:
            current = stack.pop()
            if current in reachable or current not in states:
                continue
            reachable.add(current)
            state = states[current]
            for transition_id in state.transition_ids:
                transition = transitions.get(transition_id)
                if transition is not None and transition.to_state_id in state_ids:
                    stack.append(transition.to_state_id)
        for state in score.states:
            if state.id not in reachable:
                findings.append(
                    _finding(
                        "state_unreachable",
                        "warning",
                        target_id=state.id,
                        message="State is not reachable from the initial state.",
                    )
                )

    findings = _apply_strict(findings, strict=strict)
    _log_findings(findings, score)
    return findings


def binding_status_for(
    score: AdaptiveScoreV1,
    fingerprint: str | None,
) -> AdaptiveBindingStatus:
    """Aggregate pin status. Asset-only and empty scores are unchecked."""
    symbolic = [material for _id, material in iter_material_refs(score) if material.kind != "asset"]
    if not symbolic:
        return "unchecked"
    pins = [material.snapshot_fingerprint for material in symbolic if material.snapshot_fingerprint]
    if not pins:
        return "unpinned"
    if fingerprint is None or any(pin != fingerprint for pin in pins):
        return "stale"
    return "fresh"


def score_is_ready(score: AdaptiveScoreV1, findings: list[AdaptiveScoreFindingV1]) -> bool:
    errors = any(item.severity == "error" for item in findings)
    return bool(score.states and score.initial_state_id and score.default_state_id and not errors)


def raise_on_error_findings(findings: list[AdaptiveScoreFindingV1]) -> None:
    errors = [item for item in findings if item.severity == "error"]
    if not errors:
        return
    first = errors[0]
    raise AdaptiveScoreError(
        first.code,
        first.message or first.code,
        http_status=422,
        details={
            "finding_count": len(errors),
            "target_id": first.target_id,
        },
    )


def bind_material_refs(
    score: AdaptiveScoreV1,
    composition: CompositionV2 | None,
    *,
    bar_count: int | None = None,
    section_ids: set[str] | None = None,
    track_ids: set[str] | None = None,
    motif_ids: set[str] | None = None,
    fingerprint: str | None = None,
    strict: bool = False,
) -> list[AdaptiveScoreFindingV1]:
    """Resolve material refs against an already loaded composition or id sets.

    ``composition is None`` with no precomputed indexes and any non-asset ref
    yields ``composition_unavailable``. Asset-only scores return no composition error.
    """
    symbolic = [(target, material) for target, material in iter_material_refs(score) if material.kind != "asset"]
    has_index = composition is not None or any(
        value is not None for value in (bar_count, section_ids, track_ids, motif_ids)
    )
    findings: list[AdaptiveScoreFindingV1] = []
    if symbolic and not has_index:
        findings.append(
            _finding(
                "composition_unavailable",
                "error",
                message="No composition is available to bind material references.",
            )
        )
        findings = _apply_strict(findings, strict=strict)
        _log_findings(findings, score)
        return findings

    if composition is not None:
        resolved_bar_count = composition.bar_count if bar_count is None else bar_count
        resolved_sections = (
            {section.id for section in composition.sections if section.id}
            if section_ids is None
            else section_ids
        )
        resolved_tracks = (
            {track.id for track in composition.tracks} if track_ids is None else track_ids
        )
        resolved_motifs = (
            {motif.id for motif in composition.motifs} if motif_ids is None else motif_ids
        )
    else:
        resolved_bar_count = bar_count
        resolved_sections = section_ids or set()
        resolved_tracks = track_ids or set()
        resolved_motifs = motif_ids or set()

    def _range_outside(target_id: str, start_bar: int | None, end_bar: int | None) -> None:
        if resolved_bar_count is None:
            return
        if start_bar is not None and start_bar > resolved_bar_count:
            findings.append(
                _finding(
                    "material_range_outside",
                    "error",
                    target_id=target_id,
                    message="start_bar is outside the composition.",
                )
            )
        if end_bar is not None and end_bar > resolved_bar_count:
            findings.append(
                _finding(
                    "material_range_outside",
                    "error",
                    target_id=target_id,
                    message="end_bar is outside the composition.",
                )
            )

    for target_id, material in iter_material_refs(score):
        if material.kind == "asset":
            continue
        if material.section_id and material.section_id not in resolved_sections:
            findings.append(
                _finding(
                    "material_target_missing",
                    "error",
                    target_id=target_id,
                    message="section_id is not a non-null section on the composition.",
                )
            )
        if material.motif_id and material.motif_id not in resolved_motifs:
            findings.append(
                _finding(
                    "material_target_missing",
                    "error",
                    target_id=target_id,
                    message="motif_id is not on the composition.",
                )
            )
        for track_id in material.track_ids:
            if track_id not in resolved_tracks:
                findings.append(
                    _finding(
                        "material_target_missing",
                        "error",
                        target_id=target_id,
                        message="track id is not on the composition.",
                    )
                )
        _range_outside(target_id, material.start_bar, material.end_bar)

    if composition is not None or resolved_bar_count is not None:
        for state in score.states:
            if state.loop.enabled:
                _range_outside(state.id, state.loop.start_bar, state.loop.end_bar)
            if state.entry.kind == "bar":
                _range_outside(state.id, state.entry.bar, None)
            if state.exit.kind == "bar":
                _range_outside(state.id, state.exit.bar, None)

    findings = _apply_strict(findings, strict=strict)
    _log_findings(findings, score)
    logger.debug(
        "Adaptive score binding status",
        extra={
            "binding_status": binding_status_for(score, fingerprint),
            "section_count": len(resolved_sections),
            "track_count": len(resolved_tracks),
            "motif_count": len(resolved_motifs),
        },
    )
    return findings
