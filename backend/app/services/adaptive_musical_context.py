"""Pure bind, smooth, and hysteresis step for adaptive musical context.

The function accepts a mapping, the previous context clock, and one external
sample. It does not accept a score, a composition, or note events, and it does
not call playback, SQLite, FastAPI, or an LLM.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.adaptive_musical_context_schemas import (
    AdaptiveContextBindingV1,
    AdaptiveContextEmittedFlagsV1,
    AdaptiveContextEmittedIntensityV1,
    AdaptiveContextEmittedStateV1,
    AdaptiveContextEmittedV1,
    AdaptiveContextExternalV1,
    AdaptiveContextFlagRuleV1,
    AdaptiveContextMappingV1,
    AdaptiveContextStateRuleV1,
    AdaptiveMusicalContextTelemetryV1,
    AdaptiveMusicalContextV1,
    _raise_validation,
)
from app.adaptive_musical_context_settings import load_adaptive_musical_context_settings
from app.adaptive_score_schemas import (
    AdaptiveTransitionScheduleWarningV1,
    _FLAG_RE,
)

logger = logging.getLogger(__name__)

_CLAMPED_SLOTS = frozenset({"tension", "health", "danger", "intensity"})


@dataclass
class _Slots:
    state: str | None = None
    intensity: float | None = None
    tension: float | None = None
    location: str | None = None
    health: float | None = None
    danger: float | None = None
    narrative_tags: list[str] = field(default_factory=list)
    characters: dict[str, bool] = field(default_factory=dict)
    custom_numeric: dict[str, float] = field(default_factory=dict)
    custom_boolean: dict[str, bool] = field(default_factory=dict)
    custom_categorical: dict[str, str] = field(default_factory=dict)


@dataclass
class MusicalContextClock:
    """Session memory for one normalized context. Not a score and not playback."""

    context_id: str
    document_revision: int
    sample_index: int
    musical_state_id: str
    slots: _Slots
    enter_counts: dict[str, int]
    exit_counts: dict[str, int]
    last_emitted_intensity: float | None
    last_emitted_flags: dict[str, bool] | None
    dwell_rule_id: str | None
    dwell_count: int
    warnings: list[AdaptiveTransitionScheduleWarningV1]
    emitted: list[AdaptiveContextEmittedV1]
    telemetry: AdaptiveMusicalContextTelemetryV1


def begin_musical_context(
    mapping: AdaptiveContextMappingV1,
    *,
    context_id: str,
    document_revision: int,
) -> MusicalContextClock:
    """Open a clock on the mapping baseline. No sample has been accepted."""
    clock = MusicalContextClock(
        context_id=context_id,
        document_revision=document_revision,
        sample_index=0,
        musical_state_id=mapping.baseline_state_id,
        slots=_Slots(),
        enter_counts={rule.id: 0 for rule in mapping.state_rules},
        exit_counts={rule.id: 0 for rule in mapping.state_rules},
        last_emitted_intensity=None,
        last_emitted_flags=None,
        dwell_rule_id=None,
        dwell_count=0,
        warnings=[],
        emitted=[],
        telemetry=AdaptiveMusicalContextTelemetryV1(
            sample_count=0,
            state_change_count=0,
            intensity_emit_count=0,
            rejected_sample_count=0,
        ),
    )
    musical_context_snapshot(clock)
    logger.debug(
        "Adaptive musical context clock opened",
        extra={
            "dwell_rule_id": None,
            "dwell_count": 0,
            "ignored_key_count": 0,
            "danger": None,
            "health": None,
            "tension": None,
            "intensity": None,
        },
    )
    return clock


def step_musical_context(
    mapping: AdaptiveContextMappingV1,
    clock: MusicalContextClock,
    sample: AdaptiveContextExternalV1,
) -> MusicalContextClock:
    """Return the next clock. The previous clock and the sample are unchanged."""
    slots = _copy_slots(clock.slots)
    warnings: list[AdaptiveTransitionScheduleWarningV1] = []
    bound_keys = {binding.external_key for binding in mapping.bindings}
    ignored_key_count = sum(1 for key in sample.values if key not in bound_keys)
    for binding in mapping.bindings:
        _apply_binding(binding, sample, slots, warnings)
    enter_counts = dict(clock.enter_counts)
    exit_counts = dict(clock.exit_counts)
    advanced: list[tuple[AdaptiveContextStateRuleV1, int]] = []
    for rule in mapping.state_rules:
        _advance_rule(rule, clock.musical_state_id, slots, enter_counts, exit_counts, advanced)
    musical_state_id, state_changed, dwell_rule_id, dwell_count = _resolve_state(
        mapping,
        clock.musical_state_id,
        enter_counts,
        exit_counts,
        advanced,
    )
    if state_changed:
        enter_counts = {rule.id: 0 for rule in mapping.state_rules}
        exit_counts = {rule.id: 0 for rule in mapping.state_rules}
    emitted: list[AdaptiveContextEmittedV1] = []
    last_intensity = clock.last_emitted_intensity
    intensity_emit_count = clock.telemetry.intensity_emit_count
    if mapping.intensity is not None:
        current = _read_numeric(slots, mapping.intensity.slot, mapping.intensity.custom_key)
        if current is not None:
            clamped = _clamp01(current)
            moved = last_intensity is None or abs(clamped - last_intensity) >= mapping.intensity.emit_epsilon
            if moved or state_changed:
                emitted.append(
                    AdaptiveContextEmittedIntensityV1(op="set_intensity", intensity=clamped)
                )
                last_intensity = clamped
                intensity_emit_count += 1
    last_flags = None if clock.last_emitted_flags is None else dict(clock.last_emitted_flags)
    flag_map = _flag_map(mapping.flag_rules, slots)
    if last_flags is None:
        flags_differ = bool(flag_map)
    else:
        flags_differ = flag_map != last_flags
    if flags_differ:
        emitted.append(AdaptiveContextEmittedFlagsV1(op="set_flags", flags=flag_map))
        last_flags = dict(flag_map)
    if state_changed:
        emitted.append(
            AdaptiveContextEmittedStateV1(op="request_state", to_state_id=musical_state_id)
        )
    nxt = MusicalContextClock(
        context_id=clock.context_id,
        document_revision=clock.document_revision,
        sample_index=clock.sample_index + 1,
        musical_state_id=musical_state_id,
        slots=slots,
        enter_counts=enter_counts,
        exit_counts=exit_counts,
        last_emitted_intensity=last_intensity,
        last_emitted_flags=last_flags,
        dwell_rule_id=dwell_rule_id,
        dwell_count=dwell_count,
        warnings=warnings,
        emitted=emitted,
        telemetry=AdaptiveMusicalContextTelemetryV1(
            sample_count=clock.telemetry.sample_count + 1,
            state_change_count=clock.telemetry.state_change_count + (1 if state_changed else 0),
            intensity_emit_count=intensity_emit_count,
            rejected_sample_count=clock.telemetry.rejected_sample_count,
        ),
    )
    musical_context_snapshot(nxt)
    logger.debug(
        "Adaptive musical context step",
        extra={
            "dwell_rule_id": nxt.dwell_rule_id,
            "dwell_count": nxt.dwell_count,
            "ignored_key_count": ignored_key_count,
            "danger": nxt.slots.danger,
            "health": nxt.slots.health,
            "tension": nxt.slots.tension,
            "intensity": nxt.slots.intensity,
        },
    )
    return nxt


def musical_context_snapshot(clock: MusicalContextClock) -> AdaptiveMusicalContextV1:
    """Public normalized document. Raw sample numbers are not included."""
    settings = load_adaptive_musical_context_settings()
    characters = [
        {"id": character_id, "present": present}
        for character_id, present in sorted(clock.slots.characters.items())
    ]
    if len(characters) > settings.max_characters:
        characters = characters[: settings.max_characters]
    body = {
        "schema_version": "adaptive.musical_context.v1",
        "context_id": clock.context_id,
        "sample_index": clock.sample_index,
        "state": clock.slots.state,
        "intensity": clock.slots.intensity,
        "tension": clock.slots.tension,
        "location": clock.slots.location,
        "health": clock.slots.health,
        "danger": clock.slots.danger,
        "narrative_tags": list(clock.slots.narrative_tags),
        "characters": characters,
        "custom_numeric": dict(clock.slots.custom_numeric),
        "custom_boolean": dict(clock.slots.custom_boolean),
        "custom_categorical": dict(clock.slots.custom_categorical),
        "musical_state_id": clock.musical_state_id,
        "emitted": [item.model_dump(mode="json") for item in clock.emitted],
        "dwell_rule_id": clock.dwell_rule_id,
        "dwell_count": clock.dwell_count,
        "warnings": [item.model_dump(mode="json") for item in clock.warnings][:8],
        "telemetry": clock.telemetry.model_dump(mode="json"),
        "document_revision": clock.document_revision,
    }
    try:
        return AdaptiveMusicalContextV1.model_validate(body)
    except ValidationError as exc:
        _raise_validation("AdaptiveMusicalContextV1", exc)
    raise AssertionError("musical context snapshot")


def _copy_slots(slots: _Slots) -> _Slots:
    return _Slots(
        state=slots.state,
        intensity=slots.intensity,
        tension=slots.tension,
        location=slots.location,
        health=slots.health,
        danger=slots.danger,
        narrative_tags=list(slots.narrative_tags),
        characters=dict(slots.characters),
        custom_numeric=dict(slots.custom_numeric),
        custom_boolean=dict(slots.custom_boolean),
        custom_categorical=dict(slots.custom_categorical),
    )


def _warn(warnings: list[AdaptiveTransitionScheduleWarningV1]) -> None:
    if any(item.code == "context_value_rejected" for item in warnings):
        return
    warnings.append(
        AdaptiveTransitionScheduleWarningV1(
            code="context_value_rejected",
            message="A sample value was rejected for a binding.",
        )
    )


def _apply_binding(
    binding: AdaptiveContextBindingV1,
    sample: AdaptiveContextExternalV1,
    slots: _Slots,
    warnings: list[AdaptiveTransitionScheduleWarningV1],
) -> None:
    if binding.external_key not in sample.values:
        return
    raw = sample.values[binding.external_key]
    if binding.kind == "numeric":
        number = _finite_number(raw)
        if number is None:
            _warn(warnings)
            return
        stored = _smooth(binding.smooth_alpha, binding.slot, _transform(binding, number), _read_numeric(slots, binding.slot, binding.custom_key))
        if not _write_numeric(slots, binding.slot, binding.custom_key, stored):
            _warn(warnings)
        return
    if binding.kind == "boolean":
        if not isinstance(raw, bool):
            _warn(warnings)
            return
        if binding.slot == "character":
            assert binding.character_id is not None
            if (
                binding.character_id not in slots.characters
                and len(slots.characters) >= load_adaptive_musical_context_settings().max_characters
            ):
                _warn(warnings)
                return
            slots.characters[binding.character_id] = raw
            return
        assert binding.custom_key is not None
        if (
            binding.custom_key not in slots.custom_boolean
            and len(slots.custom_boolean) >= load_adaptive_musical_context_settings().max_custom
        ):
            _warn(warnings)
            return
        slots.custom_boolean[binding.custom_key] = raw
        return
    if binding.kind == "categorical":
        if not isinstance(raw, str) or _FLAG_RE.fullmatch(raw) is None:
            _warn(warnings)
            return
        if binding.allowed is not None and raw not in binding.allowed:
            _warn(warnings)
            return
        if binding.slot == "custom_categorical":
            assert binding.custom_key is not None
            if (
                binding.custom_key not in slots.custom_categorical
                and len(slots.custom_categorical) >= load_adaptive_musical_context_settings().max_custom
            ):
                _warn(warnings)
                return
            slots.custom_categorical[binding.custom_key] = raw
            return
        setattr(slots, binding.slot, raw)
        return
    if not isinstance(raw, list) or any(
        not isinstance(item, str) or _FLAG_RE.fullmatch(item) is None for item in raw
    ):
        _warn(warnings)
        return
    tags = sorted(set(raw))
    if len(tags) > load_adaptive_musical_context_settings().max_tags:
        _warn(warnings)
        return
    slots.narrative_tags = tags


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _transform(binding: AdaptiveContextBindingV1, number: float) -> float:
    if binding.kind != "numeric":
        return number
    if binding.transform == "clamp01":
        return _clamp01(number)
    if binding.transform == "invert":
        return 1.0 - _clamp01(number)
    if binding.transform == "scale":
        assert binding.in_min is not None and binding.in_max is not None
        return _clamp01((number - binding.in_min) / (binding.in_max - binding.in_min))
    return float(number)


def _smooth(alpha: float, slot: str, transformed: float, previous: float | None) -> float:
    if previous is None:
        blended = transformed
    else:
        blended = alpha * transformed + (1.0 - alpha) * previous
    if slot in _CLAMPED_SLOTS:
        return _clamp01(blended)
    return blended


def _clamp01(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return float(value)


def _read_numeric(slots: _Slots, slot: str, custom_key: str | None) -> float | None:
    if slot == "custom_numeric":
        if custom_key is None:
            return None
        return slots.custom_numeric.get(custom_key)
    value = getattr(slots, slot)
    if value is None:
        return None
    return float(value)


def _write_numeric(slots: _Slots, slot: str, custom_key: str | None, value: float) -> bool:
    if slot == "custom_numeric":
        if custom_key is None:
            return False
        if (
            custom_key not in slots.custom_numeric
            and len(slots.custom_numeric) >= load_adaptive_musical_context_settings().max_custom
        ):
            return False
        slots.custom_numeric[custom_key] = value
        return True
    setattr(slots, slot, value)
    return True


def _advance_rule(
    rule: AdaptiveContextStateRuleV1,
    musical_state_id: str,
    slots: _Slots,
    enter_counts: dict[str, int],
    exit_counts: dict[str, int],
    advanced: list[tuple[AdaptiveContextStateRuleV1, int]],
) -> None:
    enter_claim, release_claim = _claims(rule, slots)
    dwell = int(rule.min_dwell_samples or 1)
    if musical_state_id != rule.target_state_id:
        if enter_claim:
            updated = min(enter_counts.get(rule.id, 0) + 1, dwell)
            if updated > enter_counts.get(rule.id, 0):
                advanced.append((rule, updated))
            enter_counts[rule.id] = updated
        else:
            enter_counts[rule.id] = 0
        exit_counts[rule.id] = 0
        return
    if release_claim:
        updated = min(exit_counts.get(rule.id, 0) + 1, dwell)
        if updated > exit_counts.get(rule.id, 0):
            advanced.append((rule, updated))
        exit_counts[rule.id] = updated
    else:
        exit_counts[rule.id] = 0
    enter_counts[rule.id] = 0


def _claims(rule: AdaptiveContextStateRuleV1, slots: _Slots) -> tuple[bool, bool]:
    if rule.kind == "numeric_band":
        value = _read_numeric(slots, rule.slot, rule.custom_key)
        if value is None:
            return False, False
        if rule.polarity == "high":
            return value >= rule.enter, value <= rule.exit
        return value <= rule.enter, value >= rule.exit
    if rule.kind == "category_equals":
        current = _read_category(slots, rule.slot, rule.custom_key)
        return current == rule.equals, current != rule.equals
    if rule.kind == "tag_present":
        present = rule.tag in slots.narrative_tags
        return present, not present
    present = slots.characters.get(rule.character_id) is True
    return present, not present


def _read_category(slots: _Slots, slot: str, custom_key: str | None) -> str | None:
    if slot == "custom_categorical":
        if custom_key is None:
            return None
        return slots.custom_categorical.get(custom_key)
    value = getattr(slots, slot)
    if isinstance(value, str):
        return value
    return None


def _resolve_state(
    mapping: AdaptiveContextMappingV1,
    musical_state_id: str,
    enter_counts: dict[str, int],
    exit_counts: dict[str, int],
    advanced: list[tuple[AdaptiveContextStateRuleV1, int]],
) -> tuple[str, bool, str | None, int]:
    enter_candidates = [
        rule
        for rule in mapping.state_rules
        if musical_state_id != rule.target_state_id
        and enter_counts.get(rule.id, 0) >= int(rule.min_dwell_samples or 1)
    ]
    release_rules = [
        rule
        for rule in mapping.state_rules
        if musical_state_id == rule.target_state_id
        and exit_counts.get(rule.id, 0) >= int(rule.min_dwell_samples or 1)
    ]
    if enter_candidates:
        winner = _prefer(enter_candidates)
        nxt = winner.target_state_id
        return nxt, nxt != musical_state_id, winner.id, enter_counts.get(winner.id, 0)
    if release_rules:
        # An enter candidate on the same sample already replaced the held state.
        releaser = _prefer(release_rules)
        nxt = mapping.baseline_state_id
        return nxt, nxt != musical_state_id, releaser.id, exit_counts.get(releaser.id, 0)
    if advanced:
        rule, count = min(advanced, key=lambda item: (-item[0].priority, item[0].id))
        return musical_state_id, False, rule.id, count
    return musical_state_id, False, None, 0


def _prefer(rules: list[AdaptiveContextStateRuleV1]) -> AdaptiveContextStateRuleV1:
    return min(rules, key=lambda rule: (-rule.priority, rule.id))


def _flag_map(rules: list[AdaptiveContextFlagRuleV1], slots: _Slots) -> dict[str, bool]:
    emitted: dict[str, bool] = {}
    for rule in rules:
        if _flag_source_true(rule, slots):
            emitted[rule.flag] = True
    return emitted


def _flag_source_true(rule: AdaptiveContextFlagRuleV1, slots: _Slots) -> bool:
    if rule.source == "custom_boolean":
        return slots.custom_boolean.get(rule.source_key) is True
    if rule.source == "character":
        return slots.characters.get(rule.source_key) is True
    return rule.source_key in slots.narrative_tags
