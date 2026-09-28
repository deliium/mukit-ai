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
    for transition in score.transitions:
        material = transition.realization.phrase_material
        if transition.realization.kind == "phrase" and material is not None:
            refs.append((transition.id, material))
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


def _log_findings(
    findings: list[AdaptiveScoreFindingV1],
    score: AdaptiveScoreV1,
    *,
    component_sizes: dict[str, int] | None = None,
) -> None:
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
    sizes = component_sizes or {}
    for item in findings:
        extra: dict[str, Any] = {
            "code": item.code,
            "target_id": item.target_id,
            "severity": item.severity,
        }
        if item.code == "transition_deadlock" and item.target_id in sizes:
            extra["component_size"] = sizes[item.target_id]
        logger.debug("Adaptive score finding", extra=extra)


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


def _eligible_transitions(
    score: AdaptiveScoreV1,
    transitions: dict[str, Any],
    state_ids: set[str],
) -> list[Any]:
    eligible = []
    for state in score.states:
        for transition_id in state.transition_ids:
            transition = transitions.get(transition_id)
            if transition is None or transition.from_state_id != state.id:
                continue
            if transition.to_state_id not in state_ids:
                continue
            eligible.append(transition)
    return eligible


def _variants_for(score: AdaptiveScoreV1, state_id: str) -> list[Any]:
    return [variant for variant in score.variants if variant.state_id == state_id]


def _material_cannot_name_end(state: Any) -> bool:
    if state.exit.kind != "material_end":
        return False
    material = state.material
    if material.kind in {"asset", "motif"}:
        return True
    return material.kind == "track_range" and material.end_bar is None and material.end_tick is None


def _impossible_reason(transition: Any, source: Any, variants: list[Any]) -> str | None:
    """Return a short reason when an eligible transition can never fire.

    Conditions are a conjunction. ``manual`` and ``flag_equals`` are always
    treated as satisfiable. An empty condition list is satisfiable unless
    ``next_exit`` has no end to land on.
    """
    for condition in transition.conditions:
        kind = condition.kind
        if kind in {"manual", "flag_equals"}:
            continue
        if kind == "min_time_in_state_bars" and source.max_duration_bars is not None:
            if condition.value > source.max_duration_bars:
                return (
                    f"min_time_in_state_bars {condition.value} exceeds "
                    f"max_duration_bars {source.max_duration_bars}"
                )
        if kind == "intensity_at_least":
            if condition.value > source.intensity and all(
                condition.value > variant.intensity_max for variant in variants
            ):
                ceiling = max(
                    [source.intensity, *[variant.intensity_max for variant in variants]]
                )
                return f"intensity_at_least {condition.value} exceeds intensity {ceiling}"
        if kind == "intensity_at_most":
            if condition.value < source.intensity and all(
                condition.value < variant.intensity_min for variant in variants
            ):
                floor = min(
                    [source.intensity, *[variant.intensity_min for variant in variants]]
                )
                return f"intensity_at_most {condition.value} is below intensity {floor}"
    if transition.quantization == "next_exit" and _material_cannot_name_end(source):
        return f"quantization next_exit but {source.material.kind} material has no end"
    if transition.quantization == "loop_end" and not source.loop.enabled:
        return f"loop on {source.id} is disabled"
    return None


def _impossible_ids(
    score: AdaptiveScoreV1,
    eligible: list[Any],
    states: dict[str, Any],
) -> dict[str, str]:
    reasons: dict[str, str] = {}
    for transition in eligible:
        source = states.get(transition.from_state_id)
        if source is None:
            continue
        reason = _impossible_reason(transition, source, _variants_for(score, source.id))
        if reason is not None:
            reasons[transition.id] = reason
    return reasons


def _strongly_connected(nodes: set[str], edges: list[tuple[str, str]]) -> list[set[str]]:
    index = 0
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[set[str]] = []
    outgoing: dict[str, list[str]] = {node: [] for node in nodes}
    for src, dst in edges:
        if src in outgoing and dst in nodes:
            outgoing[src].append(dst)

    def strongconnect(node: str) -> None:
        nonlocal index
        indices[node] = index
        low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for nxt in outgoing[node]:
            if nxt not in indices:
                strongconnect(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], indices[nxt])
        if low[node] == indices[node]:
            component: set[str] = set()
            while stack:
                popped = stack.pop()
                on_stack.discard(popped)
                component.add(popped)
                if popped == node:
                    break
            components.append(component)

    for node in sorted(nodes):
        if node not in indices:
            strongconnect(node)
    return components


def _reachable_states(
    score: AdaptiveScoreV1,
    transitions: dict[str, Any],
    state_ids: set[str],
) -> set[str]:
    if not score.initial_state_id or score.initial_state_id not in state_ids:
        return set()
    reachable: set[str] = set()
    stack = [score.initial_state_id]
    states = {state.id: state for state in score.states}
    while stack:
        current = stack.pop()
        if current in reachable or current not in states:
            continue
        reachable.add(current)
        for transition_id in states[current].transition_ids:
            transition = transitions.get(transition_id)
            if (
                transition is not None
                and transition.from_state_id == current
                and transition.to_state_id in state_ids
            ):
                stack.append(transition.to_state_id)
    return reachable


def _authoring_graph_findings(
    score: AdaptiveScoreV1,
    *,
    states: dict[str, Any],
    transitions: dict[str, Any],
    state_ids: set[str],
) -> tuple[list[AdaptiveScoreFindingV1], dict[str, int]]:
    findings: list[AdaptiveScoreFindingV1] = []
    eligible = _eligible_transitions(score, transitions, state_ids)
    impossible = _impossible_ids(score, eligible, states)
    for transition in eligible:
        reason = impossible.get(transition.id)
        if reason is None:
            continue
        findings.append(
            _finding(
                "impossible_transition",
                "error",
                target_id=transition.id,
                message=(
                    f"Transition {transition.id} on {transition.from_state_id} "
                    f"is impossible: {reason}."
                ),
            )
        )

    for state in score.states:
        material = state.material
        loop = state.loop
        if (
            loop.enabled
            and loop.start_bar is not None
            and loop.end_bar is not None
            and loop.end_bar >= loop.start_bar
            and material.start_bar is not None
            and material.end_bar is not None
            and (loop.start_bar < material.start_bar or loop.end_bar > material.end_bar)
        ):
            findings.append(
                _finding(
                    "loop_bounds",
                    "error",
                    target_id=state.id,
                    message=(
                        f"Loop on {state.id} spans bars {loop.start_bar}-{loop.end_bar} "
                        f"outside material bars {material.start_bar}-{material.end_bar}."
                    ),
                )
            )
        findings.extend(_timing_findings(state))

    findings.extend(_fallback_findings(score, state_ids))
    deadlock, sizes = _deadlock_findings(
        score,
        eligible=eligible,
        impossible_ids=set(impossible),
        state_ids=state_ids,
        transitions=transitions,
    )
    findings.extend(deadlock)
    return findings, sizes


def _fallback_findings(
    score: AdaptiveScoreV1,
    state_ids: set[str],
) -> list[AdaptiveScoreFindingV1]:
    if not score.states:
        return []
    default_id = score.default_state_id
    default_missing = not default_id or default_id not in state_ids
    if not default_missing:
        return []
    named = default_id or "null"
    findings: list[AdaptiveScoreFindingV1] = []
    for policy_name in ("on_missing_material", "on_invalid_transition", "on_unresolved_condition"):
        if getattr(score.fallback, policy_name) == "default_state":
            findings.append(
                _finding(
                    "missing_fallback_state",
                    "error",
                    target_id=default_id,
                    message=(
                        f"Policy {policy_name} is default_state but default_state_id {named} "
                        "is not a state."
                    ),
                )
            )
    for transition in score.transitions:
        if transition.fallback_behavior != "default_state":
            continue
        findings.append(
            _finding(
                "missing_fallback_state",
                "error",
                target_id=transition.id,
                message=(
                    f"Transition {transition.id} uses fallback default_state but "
                    f"default_state_id {named} is not a state."
                ),
            )
        )
    return findings


def _timing_findings(state: Any) -> list[AdaptiveScoreFindingV1]:
    findings: list[AdaptiveScoreFindingV1] = []
    entry = state.entry
    exit_boundary = state.exit
    material = state.material
    if entry.kind == "bar" and exit_boundary.kind == "bar" and exit_boundary.bar < entry.bar:
        findings.append(
            _finding(
                "timing_incompatible",
                "error",
                target_id=state.id,
                message=(
                    f"State {state.id} exit bar {exit_boundary.bar} is before entry bar {entry.bar}."
                ),
            )
        )
    if entry.kind == "tick" and exit_boundary.kind == "tick" and exit_boundary.tick < entry.tick:
        findings.append(
            _finding(
                "timing_incompatible",
                "error",
                target_id=state.id,
                message=(
                    f"State {state.id} exit tick {exit_boundary.tick} is before entry tick {entry.tick}."
                ),
            )
        )
    has_bars = material.start_bar is not None and material.end_bar is not None
    has_ticks = material.start_tick is not None and material.end_tick is not None
    for label, boundary in (("entry", entry), ("exit", exit_boundary)):
        if has_bars and boundary.kind == "bar" and boundary.bar is not None:
            if boundary.bar < material.start_bar or boundary.bar > material.end_bar:
                findings.append(
                    _finding(
                        "timing_incompatible",
                        "error",
                        target_id=state.id,
                        message=(
                            f"State {state.id} {label} bar {boundary.bar} is outside material "
                            f"bars {material.start_bar}-{material.end_bar}."
                        ),
                    )
                )
        if has_ticks and boundary.kind == "tick" and boundary.tick is not None:
            if boundary.tick < material.start_tick or boundary.tick > material.end_tick:
                findings.append(
                    _finding(
                        "timing_incompatible",
                        "error",
                        target_id=state.id,
                        message=(
                            f"State {state.id} {label} tick {boundary.tick} is outside material "
                            f"ticks {material.start_tick}-{material.end_tick}."
                        ),
                    )
                )
    unit_kinds = {entry.kind, exit_boundary.kind} - {"material_start", "material_end"}
    if "bar" in unit_kinds and "tick" in unit_kinds:
        findings.append(
            _finding(
                "timing_incompatible",
                "error",
                target_id=state.id,
                message=f"State {state.id} mixes bar and tick boundaries.",
            )
        )
    if has_ticks and not has_bars and ("bar" == entry.kind or exit_boundary.kind == "bar"):
        findings.append(
            _finding(
                "timing_incompatible",
                "error",
                target_id=state.id,
                message=f"State {state.id} uses a bar boundary on tick-only material.",
            )
        )
    if has_bars and not has_ticks and ("tick" == entry.kind or exit_boundary.kind == "tick"):
        findings.append(
            _finding(
                "timing_incompatible",
                "error",
                target_id=state.id,
                message=f"State {state.id} uses a tick boundary on bar-only material.",
            )
        )
    loop = state.loop
    if loop.enabled and loop.start_bar is not None:
        if entry.kind == "bar" and entry.bar is not None and loop.start_bar > entry.bar:
            findings.append(
                _finding(
                    "timing_incompatible",
                    "error",
                    target_id=state.id,
                    message=(
                        f"State {state.id} loop start bar {loop.start_bar} is after "
                        f"entry bar {entry.bar}."
                    ),
                )
            )
        if (
            exit_boundary.kind == "bar"
            and exit_boundary.bar is not None
            and exit_boundary.bar < loop.start_bar
        ):
            findings.append(
                _finding(
                    "timing_incompatible",
                    "error",
                    target_id=state.id,
                    message=(
                        f"State {state.id} exit bar {exit_boundary.bar} is before "
                        f"loop start bar {loop.start_bar}."
                    ),
                )
            )
    return findings


def _deadlock_findings(
    score: AdaptiveScoreV1,
    *,
    eligible: list[Any],
    impossible_ids: set[str],
    state_ids: set[str],
    transitions: dict[str, Any],
) -> tuple[list[AdaptiveScoreFindingV1], dict[str, int]]:
    edges = [(item.from_state_id, item.to_state_id) for item in eligible]
    components = _strongly_connected(state_ids, edges)
    reachable = _reachable_states(score, transitions, state_ids)
    findings: list[AdaptiveScoreFindingV1] = []
    sizes: dict[str, int] = {}
    for component in components:
        inside = [
            item
            for item in eligible
            if item.from_state_id in component and item.to_state_id in component
        ]
        if not inside:
            continue
        leaves = any(
            item.from_state_id in component and item.to_state_id not in component
            for item in eligible
        )
        if leaves:
            continue
        reached = bool(component & reachable) or (
            score.initial_state_id is not None and score.initial_state_id in component
        )
        if not reached:
            continue
        if any(item.id not in impossible_ids for item in inside):
            continue
        policy = score.fallback.on_invalid_transition
        if policy == "stay":
            cannot_leave = True
        elif policy == "default_state":
            cannot_leave = (
                not score.default_state_id or score.default_state_id in component
            )
        else:
            cannot_leave = False
        if not cannot_leave:
            continue
        ordered = sorted(component)
        target_id = ordered[0]
        listed = ", ".join(ordered[:3])
        sizes[target_id] = len(component)
        findings.append(
            _finding(
                "transition_deadlock",
                "error",
                target_id=target_id,
                message=(
                    f"Deadlock among {listed}: eligible exits are impossible and "
                    "fallback cannot leave."
                ),
            )
        )
    return findings, sizes


def _section_loop_findings(
    score: AdaptiveScoreV1,
    composition: CompositionV2 | None,
) -> list[AdaptiveScoreFindingV1]:
    if composition is None:
        return []
    sections = {section.id: section for section in composition.sections if section.id}
    findings: list[AdaptiveScoreFindingV1] = []
    for state in score.states:
        loop = state.loop
        material = state.material
        if not loop.enabled or loop.start_bar is None or loop.end_bar is None:
            continue
        if material.kind == "section":
            section_id = material.section_id
        elif material.kind == "revision_region" and material.section_id:
            section_id = material.section_id
        else:
            continue
        section = sections.get(section_id) if section_id else None
        if section is None:
            continue
        span_start = section.start_bar
        span_end = section.start_bar + section.bar_count - 1
        if loop.start_bar < span_start or loop.end_bar > span_end:
            findings.append(
                _finding(
                    "loop_bounds",
                    "error",
                    target_id=state.id,
                    message=(
                        f"Loop on {state.id} spans bars {loop.start_bar}-{loop.end_bar} "
                        f"outside section {section_id} bars {span_start}-{span_end}."
                    ),
                )
            )
    return findings


def _phrase_unaligned_findings(
    score: AdaptiveScoreV1,
    composition: CompositionV2 | None,
) -> list[AdaptiveScoreFindingV1]:
    if composition is None:
        return []
    sections = {section.id: section for section in composition.sections if section.id}
    states = {state.id: state for state in score.states}
    findings: list[AdaptiveScoreFindingV1] = []
    for transition in score.transitions:
        if transition.quantization != "phrase":
            continue
        source = states.get(transition.from_state_id)
        if source is None:
            continue
        span = _phrase_material_span(source.material, sections)
        intersects = False
        if span is not None:
            start_bar, end_bar = span
            for section in sections.values():
                section_end = section.start_bar + section.bar_count - 1
                if section_end >= start_bar and section.start_bar <= end_bar:
                    intersects = True
                    break
        if intersects:
            continue
        finding = _finding(
            "phrase_unaligned",
            "warning",
            target_id=transition.id,
            message=f"Phrase transition {transition.id} has no intersecting section.",
        )
        logger.debug(
            "Adaptive score transition finding",
            extra={
                "code": finding.code,
                "target_id": finding.target_id,
                "severity": finding.severity,
            },
        )
        findings.append(finding)
    return findings


def _phrase_material_span(material: Any, sections: dict[str, Any]) -> tuple[int, int] | None:
    if material.kind in {"bar_range", "revision_region"} and material.start_bar and material.end_bar:
        return material.start_bar, material.end_bar
    if material.kind == "section" and material.section_id:
        section = sections.get(material.section_id)
        if section is None:
            return None
        return section.start_bar, section.start_bar + section.bar_count - 1
    if material.kind == "track_range" and material.start_bar and material.end_bar:
        return material.start_bar, material.end_bar
    return None


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
        if score.default_state_id and score.default_state_id not in state_ids:
            findings.append(
                _finding(
                    "dangling_state_ref",
                    "error",
                    target_id=score.default_state_id,
                    message="default_state_id does not name a state.",
                )
            )
        elif (
            not score.default_state_id
            and not _fallback_findings(score, state_ids)
        ):
            findings.append(
                _finding(
                    "default_state_missing",
                    "warning",
                    message="default_state_id is unset while states exist.",
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
                end_bar = state.loop.end_bar
                start_bar = state.loop.start_bar
                if start_bar is None or end_bar is None:
                    loop_message = f"Loop on {state.id} is enabled without start_bar and end_bar."
                else:
                    loop_message = (
                        f"Loop on {state.id} ends at bar {end_bar} before start bar {start_bar}."
                    )
                findings.append(
                    _finding(
                        "loop_bounds",
                        "error",
                        target_id=state.id,
                        message=loop_message,
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

    authoring, component_sizes = _authoring_graph_findings(
        score,
        states=states,
        transitions=transitions,
        state_ids=state_ids,
    )
    findings.extend(authoring)
    findings = _apply_strict(findings, strict=strict)
    _log_findings(findings, score, component_sizes=component_sizes)
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
        findings.extend(_section_loop_findings(score, composition))
        findings.extend(_phrase_unaligned_findings(score, composition))

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
