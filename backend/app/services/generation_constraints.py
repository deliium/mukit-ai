"""Immutable generation constraint snapshots derived from LLM music requests."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Iterable, Sequence

from ..schemas import (
    Composition,
    CompositionV2,
    GenerationRepairAction,
    GenerationValidationIssue,
    GenerationValidationReport,
    InstrumentSatisfactionEntry,
    InstrumentationReport,
    LLMMusicGenerationRequest,
    LLMMusicSection,
    LLMPromptParameters,
    SuspiciousDuplicateGroupReport,
)
from .composition_planner import ComposerFormPlan, ComposerFormSection, ValidationDiagnostic
from .composition_timing import bar_duration_ticks
from .composition_tonality import analyze_composition_tonality
from .instrument_identity import (
    analyze_instrumentation,
    collect_instrument_families as _collect_instrument_requirement_keys,
    instrument_satisfies_family as _instrument_satisfies_requirement,
    is_drum_family as _is_drum_family,
    missing_required_identities,
    normalize_instrument_family as _normalize_instrument_family,
    unexpected_instrument_identities,
)


logger = logging.getLogger(__name__)

CONSTRAINT_CATEGORIES = (
    "key_metadata",
    "tempo",
    "time_signature",
    "bar_count",
    "duration_ticks",
    "sections",
    "instrument_families",
    "extra_instruments",
    "duplicate_instruments",
    "tonality_harmony",
    "tonality_notes",
)

DUPLICATE_INSTRUMENT_ROLE_CODE = "constraint_duplicate_instrument_role"


@dataclass(frozen=True)
class LockedSectionConstraint:
    """Contiguous section lock used by generation and conformance checks."""

    type: str
    start_bar: int
    bar_count: int


@dataclass(frozen=True)
class GenerationConstraints:
    """Authoritative hard/soft generation constraints for one request.

    Hard fields are immutable once resolved. Soft fields guide creativity only.
    """

    # Hard
    key: str | None
    key_user_specified: bool
    time_signature: str
    duration_bars: int
    tempo_min: int
    tempo_max: int
    sections: tuple[LockedSectionConstraint, ...] | None
    sections_user_specified: bool
    required_instrument_families: tuple[str, ...]
    requested_instruments: tuple[str, ...]
    allow_extra_instrument_families: bool

    # Soft
    mood: str
    genre: str
    complexity: str
    has_instructions: bool

    @property
    def key_resolved(self) -> bool:
        return self.key is not None

    @property
    def sections_resolved(self) -> bool:
        return self.sections is not None

    def hard_summary(self) -> dict[str, Any]:
        """Sanitized hard-constraint summary safe for structured logs."""
        return {
            "key": self.key,
            "key_user_specified": self.key_user_specified,
            "time_signature": self.time_signature,
            "duration_bars": self.duration_bars,
            "tempo_min": self.tempo_min,
            "tempo_max": self.tempo_max,
            "sections_user_specified": self.sections_user_specified,
            "section_count": len(self.sections) if self.sections else 0,
            "section_types": [section.type for section in self.sections] if self.sections else [],
            "required_instrument_families": list(self.required_instrument_families),
            "allow_extra_instrument_families": self.allow_extra_instrument_families,
            "mood": self.mood,
            "genre": self.genre,
            "complexity": self.complexity,
            "has_instructions": self.has_instructions,
        }


def normalize_instrument_family(instrument: str) -> str | None:
    """Map an instrument label to a compatibility/family token, or None if empty."""
    return _normalize_instrument_family(instrument)


def is_drum_family(family: str | None) -> bool:
    return _is_drum_family(family)


def instrument_satisfies_family(instrument: str, family: str) -> bool:
    """Return True when a track instrument label covers a required family/identity."""
    return _instrument_satisfies_requirement(instrument, family)


def collect_instrument_families(instruments: Iterable[str]) -> tuple[str, ...]:
    """Deduplicate required sound-source keys preserving request order."""
    return _collect_instrument_requirement_keys(instruments)


def sections_from_prompt(sections: Sequence[LLMMusicSection]) -> tuple[LockedSectionConstraint, ...]:
    """Convert ordered prompt sections into contiguous locked section constraints."""
    locked: list[LockedSectionConstraint] = []
    start = 1
    for section in sections:
        locked.append(
            LockedSectionConstraint(
                type=section.type,
                start_bar=start,
                bar_count=section.bars,
            )
        )
        start += section.bars
    return tuple(locked)


def sections_from_form(sections: Sequence[ComposerFormSection]) -> tuple[LockedSectionConstraint, ...]:
    return tuple(
        LockedSectionConstraint(
            type=section.type,
            start_bar=section.start_bar,
            bar_count=section.bar_count,
        )
        for section in sections
    )


def build_generation_constraints(
    request: LLMMusicGenerationRequest,
    *,
    allow_extra_instrument_families: bool = False,
) -> GenerationConstraints:
    """Derive an immutable constraint snapshot exactly once from the request."""
    prompt = request.prompt
    key_user_specified = prompt.key is not None
    sections_user_specified = bool(prompt.sections)
    sections = sections_from_prompt(prompt.sections) if sections_user_specified else None
    families = collect_instrument_families(prompt.instruments)

    constraints = GenerationConstraints(
        key=prompt.key,
        key_user_specified=key_user_specified,
        time_signature=prompt.time_signature,
        duration_bars=prompt.duration_bars,
        tempo_min=prompt.tempo_min,
        tempo_max=prompt.tempo_max,
        sections=sections,
        sections_user_specified=sections_user_specified,
        required_instrument_families=families,
        requested_instruments=tuple(prompt.instruments),
        allow_extra_instrument_families=allow_extra_instrument_families,
        mood=prompt.mood,
        genre=prompt.genre,
        complexity=prompt.complexity,
        has_instructions=bool(prompt.instructions and prompt.instructions.strip()),
    )
    logger.debug(
        "Built generation constraint snapshot",
        extra={
            **constraints.hard_summary(),
            "key_source": "user" if key_user_specified else "form_pending",
            "sections_source": "user" if sections_user_specified else "form_pending",
        },
    )
    logger.info(
        "Generation constraint snapshot created",
        extra={
            "duration_bars": constraints.duration_bars,
            "time_signature": constraints.time_signature,
            "key_user_specified": constraints.key_user_specified,
            "sections_user_specified": constraints.sections_user_specified,
            "required_family_count": len(constraints.required_instrument_families),
            "allow_extra_instrument_families": constraints.allow_extra_instrument_families,
        },
    )
    return constraints


def freeze_form_resolved_fields(
    constraints: GenerationConstraints,
    form: ComposerFormPlan,
) -> tuple[GenerationConstraints, list[ValidationDiagnostic]]:
    """Freeze form-selected key/sections when the user omitted those hard fields.

    Returns updated constraints plus diagnostics when the form contradicts already
    locked user-specified hard fields (callers should treat those as stage drift).
    """
    diagnostics: list[ValidationDiagnostic] = []
    updates: dict[str, Any] = {}

    if constraints.key_user_specified:
        if form.key != constraints.key:
            diagnostics.append(
                ValidationDiagnostic(
                    code="constraint_key_mismatch",
                    message="Form stage replaced the requested key",
                    severity="error",
                    context={
                        "expected": constraints.key,
                        "actual": form.key,
                        "stage": "plan_form",
                    },
                )
            )
    else:
        updates["key"] = form.key

    if constraints.sections_user_specified:
        expected = constraints.sections or ()
        actual = sections_from_form(form.sections)
        if not _sections_match(expected, actual):
            diagnostics.append(
                ValidationDiagnostic(
                    code="constraint_sections_mismatch",
                    message="Form stage changed requested section sequence or bar counts",
                    severity="error",
                    context={
                        "expected": [_section_summary(item) for item in expected],
                        "actual": [_section_summary(item) for item in actual],
                        "stage": "plan_form",
                    },
                )
            )
    else:
        updates["sections"] = sections_from_form(form.sections)

    if form.time_signature != constraints.time_signature:
        diagnostics.append(
            ValidationDiagnostic(
                code="constraint_meter_mismatch",
                message="Form stage replaced the requested time signature",
                severity="error",
                context={
                    "expected": constraints.time_signature,
                    "actual": form.time_signature,
                    "stage": "plan_form",
                },
            )
        )

    if form.bar_count != constraints.duration_bars:
        diagnostics.append(
            ValidationDiagnostic(
                code="constraint_bar_count_mismatch",
                message="Form stage replaced the requested duration in bars",
                severity="error",
                context={
                    "expected": constraints.duration_bars,
                    "actual": form.bar_count,
                    "stage": "plan_form",
                },
            )
        )

    if form.tempo < constraints.tempo_min or form.tempo > constraints.tempo_max:
        diagnostics.append(
            ValidationDiagnostic(
                code="constraint_tempo_out_of_range",
                message="Form stage tempo is outside the requested inclusive bounds",
                severity="error",
                context={
                    "expected_min": constraints.tempo_min,
                    "expected_max": constraints.tempo_max,
                    "actual": form.tempo,
                    "stage": "plan_form",
                },
            )
        )

    frozen = replace(constraints, **updates) if updates else constraints
    if diagnostics:
        logger.warning(
            "Form plan conflicted with hard generation constraints",
            extra={
                "codes": [item.code for item in diagnostics],
                "diagnostic_count": len(diagnostics),
                "key_user_specified": constraints.key_user_specified,
                "sections_user_specified": constraints.sections_user_specified,
            },
        )
    else:
        logger.debug(
            "Froze form-resolved constraint fields",
            extra={
                "key": frozen.key,
                "key_source": "user" if frozen.key_user_specified else "form",
                "sections_source": "user" if frozen.sections_user_specified else "form",
                "section_count": len(frozen.sections) if frozen.sections else 0,
            },
        )
    return frozen, diagnostics


def prompt_parameters_hard_block(constraints: GenerationConstraints) -> dict[str, Any]:
    """Machine-readable hard/soft split for stage prompts (no freeform instructions)."""
    return {
        "hard": {
            "key": constraints.key,
            "key_locked": constraints.key_resolved,
            "time_signature": constraints.time_signature,
            "duration_bars": constraints.duration_bars,
            "tempo_min": constraints.tempo_min,
            "tempo_max": constraints.tempo_max,
            "sections": (
                [_section_summary(section) for section in constraints.sections]
                if constraints.sections
                else None
            ),
            "sections_locked": constraints.sections_resolved,
            "required_instrument_families": list(constraints.required_instrument_families),
            "allow_extra_instrument_families": constraints.allow_extra_instrument_families,
        },
        "soft": {
            "mood": constraints.mood,
            "genre": constraints.genre,
            "complexity": constraints.complexity,
            "has_instructions": constraints.has_instructions,
        },
    }


def missing_required_families(
    present_instruments: Iterable[str],
    constraints: GenerationConstraints,
) -> list[str]:
    return missing_required_identities(
        present_instruments,
        constraints.required_instrument_families,
    )


def unexpected_instrument_families(
    present_instruments: Iterable[str],
    constraints: GenerationConstraints,
) -> list[str]:
    return unexpected_instrument_identities(
        present_instruments,
        constraints.required_instrument_families,
        allow_extra=constraints.allow_extra_instrument_families,
    )


def log_instrumentation_analysis(
    analysis,
    *,
    stage: str | None = None,
) -> None:
    """DEBUG-log sanitized instrumentation analysis summaries at call sites."""
    logger.debug(
        "Instrumentation analysis summary",
        extra={
            "stage": stage,
            "normalized_requirements": [item.key for item in analysis.requirements],
            "satisfied": [
                {"key": item.key, "track_ids": list(item.track_ids)} for item in analysis.satisfied
            ],
            "missing_keys": list(analysis.missing_keys),
            "present_identities": list(analysis.present_identities),
            "unexpected_identities": list(analysis.unexpected_identities),
            "duplicate_groups": [
                {
                    "identity": group.identity,
                    "role": group.role,
                    "track_ids": list(group.track_ids),
                    "content_relationship": group.content_relationship,
                    "actionable": group.actionable,
                }
                for group in analysis.duplicate_groups
            ],
        },
    )


def _sections_match(
    expected: Sequence[LockedSectionConstraint],
    actual: Sequence[LockedSectionConstraint],
) -> bool:
    if len(expected) != len(actual):
        return False
    return all(
        left.type == right.type
        and left.start_bar == right.start_bar
        and left.bar_count == right.bar_count
        for left, right in zip(expected, actual, strict=True)
    )


def _section_summary(section: LockedSectionConstraint) -> dict[str, Any]:
    return {
        "type": section.type,
        "start_bar": section.start_bar,
        "bar_count": section.bar_count,
    }


def summarize_prompt_for_constraints(prompt: LLMPromptParameters) -> dict[str, Any]:
    """Sanitized prompt summary used by callers before snapshot creation."""
    return {
        "duration_bars": prompt.duration_bars,
        "time_signature": prompt.time_signature,
        "key_present": prompt.key is not None,
        "section_count": len(prompt.sections),
        "instrument_count": len(prompt.instruments),
        "tempo_min": prompt.tempo_min,
        "tempo_max": prompt.tempo_max,
        "has_instructions": bool(prompt.instructions and prompt.instructions.strip()),
    }


def validate_generation_constraints(
    composition: Composition | CompositionV2,
    constraints: GenerationConstraints,
    *,
    repair_attempts: int = 0,
) -> GenerationValidationReport:
    """Deterministic final request-conformance validation against locked constraints."""
    errors: list[GenerationValidationIssue] = []
    warnings: list[GenerationValidationIssue] = []
    checked: list[str] = []

    logger.debug(
        "Starting generation constraint validation",
        extra={
            **constraints.hard_summary(),
            "composition_key": composition.key,
            "composition_bar_count": composition.bar_count,
            "track_count": len(composition.tracks),
        },
    )

    # Key metadata
    checked.append("key_metadata")
    expected_key = constraints.key
    if expected_key is not None and composition.key != expected_key:
        errors.append(
            _issue(
                code="constraint_key_mismatch",
                message="Composition key metadata does not match the locked generation key",
                expected=expected_key,
                actual=composition.key,
                stage="assemble_composition",
            )
        )
    logger.debug(
        "Validated key metadata",
        extra={"expected": expected_key, "actual": composition.key},
    )

    # Tempo inclusion
    checked.append("tempo")
    if composition.tempo < constraints.tempo_min or composition.tempo > constraints.tempo_max:
        errors.append(
            _issue(
                code="constraint_tempo_out_of_range",
                message="Composition tempo is outside the requested inclusive bounds",
                expected={"min": constraints.tempo_min, "max": constraints.tempo_max},
                actual=composition.tempo,
                stage="plan_form",
            )
        )
    logger.debug(
        "Validated tempo bounds",
        extra={
            "tempo": composition.tempo,
            "tempo_min": constraints.tempo_min,
            "tempo_max": constraints.tempo_max,
        },
    )

    # Time signature
    checked.append("time_signature")
    if composition.time_signature != constraints.time_signature:
        errors.append(
            _issue(
                code="constraint_meter_mismatch",
                message="Composition time signature does not match the requested meter",
                expected=constraints.time_signature,
                actual=composition.time_signature,
                stage="plan_form",
            )
        )

    # Bar count
    checked.append("bar_count")
    if composition.bar_count != constraints.duration_bars:
        errors.append(
            _issue(
                code="constraint_bar_count_mismatch",
                message="Composition bar_count does not match requested duration_bars",
                expected=constraints.duration_bars,
                actual=composition.bar_count,
                stage="plan_form",
            )
        )

    # Derived duration
    checked.append("duration_ticks")
    expected_duration = constraints.duration_bars * bar_duration_ticks(
        constraints.time_signature, composition.ticks_per_quarter
    )
    if composition.duration_ticks != expected_duration:
        errors.append(
            _issue(
                code="constraint_duration_mismatch",
                message="Composition duration_ticks does not match locked bars and meter",
                expected=expected_duration,
                actual=composition.duration_ticks,
                stage="assemble_composition",
            )
        )

    # Sections
    checked.append("sections")
    if constraints.sections is not None:
        actual_sections = tuple(
            LockedSectionConstraint(
                type=section.type,
                start_bar=section.start_bar,
                bar_count=section.bar_count,
            )
            for section in composition.sections
        )
        if not _sections_match(constraints.sections, actual_sections):
            errors.append(
                _issue(
                    code="constraint_sections_mismatch",
                    message="Composition sections do not match the locked section sequence",
                    expected=[_section_summary(item) for item in constraints.sections],
                    actual=[_section_summary(item) for item in actual_sections],
                    stage="plan_form",
                )
            )

    # Required instrument families / sound sources
    checked.append("instrument_families")
    analysis = analyze_instrumentation(
        list(constraints.requested_instruments),
        composition.tracks,
        allow_extra=constraints.allow_extra_instrument_families,
    )
    log_instrumentation_analysis(analysis, stage="validate_generation_constraints")
    instrumentation = _instrumentation_report_from_analysis(analysis)
    logger.debug(
        "Instrumentation report categories",
        extra={
            "satisfied_count": len(instrumentation.satisfied),
            "missing_count": len(instrumentation.missing),
            "unexpected_count": len(instrumentation.unexpected_identities),
            "suspicious_duplicate_count": len(instrumentation.suspicious_duplicates),
            "actionable_duplicate_count": sum(
                1 for item in instrumentation.suspicious_duplicates if item.actionable
            ),
            "duplicate_evidence": [
                {
                    "identity": item.identity,
                    "role": item.role,
                    "track_ids": item.track_ids,
                    "content_relationship": item.content_relationship,
                    "actionable": item.actionable,
                }
                for item in instrumentation.suspicious_duplicates
            ],
        },
    )

    missing = list(analysis.missing_keys)
    if missing:
        # One authoritative missing diagnostic per validation pass.
        errors.append(
            _issue(
                code="constraint_missing_instrument_family",
                message="One or more requested instrument families are missing",
                expected=list(constraints.required_instrument_families),
                actual=[track.instrument for track in composition.tracks],
                stage="compose_accompaniment",
                context={"missing_families": missing},
            )
        )
        logger.warning(
            "Missing requested instrument requirements",
            extra={"code": "constraint_missing_instrument_family", "missing_keys": missing},
        )

    # Extra instrument policy
    checked.append("extra_instruments")
    unexpected = list(analysis.unexpected_identities)
    if unexpected:
        errors.append(
            _issue(
                code="constraint_unexpected_instrument_family",
                message="Composition includes instrument families that were not requested",
                expected=list(constraints.required_instrument_families),
                actual=unexpected,
                stage="compose_accompaniment",
                context={"allow_extra_instrument_families": constraints.allow_extra_instrument_families},
            )
        )
        logger.warning(
            "Unexpected instrument identities present",
            extra={
                "code": "constraint_unexpected_instrument_family",
                "unexpected_identities": unexpected,
            },
        )

    # Actionable same-instrument/same-role duplicates
    checked.append("duplicate_instruments")
    for group in analysis.duplicate_groups:
        if not group.actionable:
            continue
        errors.append(
            _issue(
                code=DUPLICATE_INSTRUMENT_ROLE_CODE,
                message=(
                    "Generated tracks reuse the same normalized instrument and role "
                    "with overlapping playable content"
                ),
                expected={"identity": group.identity, "role": group.role},
                actual={
                    "track_ids": list(group.track_ids),
                    "event_counts": list(group.event_counts),
                    "content_relationship": group.content_relationship,
                },
                stage="compose_accompaniment",
                track_id=group.track_ids[-1] if group.track_ids else None,
                context={
                    "identity": group.identity,
                    "role": group.role,
                    "track_ids": list(group.track_ids),
                    "event_counts": list(group.event_counts),
                    "content_relationship": group.content_relationship,
                    "actionable": True,
                },
            )
        )
        logger.warning(
            "Actionable duplicate instrument/role group",
            extra={
                "code": DUPLICATE_INSTRUMENT_ROLE_CODE,
                "identity": group.identity,
                "role": group.role,
                "track_ids": list(group.track_ids),
                "content_relationship": group.content_relationship,
            },
        )

    # Tonality: harmony + note events
    tonality_summary: dict[str, Any] | None = None
    if expected_key is not None:
        checked.append("tonality_harmony")
        checked.append("tonality_notes")
        tonality = analyze_composition_tonality(composition, expected_key)
        tonality_summary = tonality.summary()
        if tonality.status == "contradiction":
            code = (
                "constraint_tonality_metadata"
                if tonality.reason == "metadata_key_mismatch"
                else "constraint_tonality_center"
            )
            stage = (
                "plan_form"
                if tonality.reason == "metadata_key_mismatch"
                else ("plan_harmony" if tonality.harmony_evidence_count else "compose_melody")
            )
            errors.append(
                _issue(
                    code=code,
                    message="Composition tonal center contradicts the requested key",
                    expected=expected_key,
                    actual=tonality.winning_candidate,
                    stage=stage,
                    context={
                        "reason": tonality.reason,
                        "margin": round(tonality.margin, 4),
                        "confidence": round(tonality.confidence, 4),
                        "harmony_evidence_count": tonality.harmony_evidence_count,
                        "note_evidence_weight": round(tonality.note_evidence_weight, 3),
                    },
                )
            )
        elif tonality.status == "warning":
            warnings.append(
                _issue(
                    code="constraint_tonality_ambiguous",
                    message="Tonal center evidence is sparse or ambiguous relative to the requested key",
                    severity="warning",
                    expected=expected_key,
                    actual=tonality.winning_candidate,
                    stage="plan_harmony",
                    context={"reason": tonality.reason},
                )
            )

    status = "failed" if errors else ("repaired" if repair_attempts > 0 else "passed")
    report = GenerationValidationReport(
        status=status,
        constraints_checked=checked,
        errors=errors,
        warnings=warnings,
        repair_attempts=repair_attempts,
        tonality=tonality_summary,
        instrumentation=instrumentation,
        repair_actions=[],
    )

    logger.info(
        "Generation constraint instrumentation counts",
        extra={
            "status": status,
            "satisfied_count": len(instrumentation.satisfied),
            "missing_count": len(instrumentation.missing),
            "suspicious_duplicate_count": len(instrumentation.suspicious_duplicates),
            "actionable_duplicate_count": sum(
                1 for item in instrumentation.suspicious_duplicates if item.actionable
            ),
            "repair_action_count": len(report.repair_actions),
        },
    )

    if errors:
        logger.warning(
            "Generation constraint validation failed",
            extra={
                "status": status,
                "error_codes": [item.code for item in errors],
                "warning_codes": [item.code for item in warnings],
                "error_count": len(errors),
                "warning_count": len(warnings),
                "repair_attempts": repair_attempts,
            },
        )
        if status == "failed":
            logger.error(
                "Hard generation constraint failures remain unrepaired",
                extra={
                    "error_codes": [item.code for item in errors],
                    "expected_key": expected_key,
                    "actual_key": composition.key,
                },
            )
    else:
        logger.info(
            "Generation constraint validation passed",
            extra={
                "status": status,
                "checked_count": len(checked),
                "warning_count": len(warnings),
                "repair_attempts": repair_attempts,
            },
        )
    logger.debug(
        "Generation constraint validation category summary",
        extra={
            "constraints_checked": checked,
            "error_count": len(errors),
            "warning_count": len(warnings),
        },
    )
    return report


def diagnostics_from_validation_report(
    report: GenerationValidationReport,
) -> list[ValidationDiagnostic]:
    """Convert response-safe issues into internal ValidationDiagnostic objects.

    Actionable duplicate instrument/role issues keep stage=compose_accompaniment so
    existing repair routing can target accompaniment regeneration.
    """
    items: list[ValidationDiagnostic] = []
    for issue in [*report.errors, *report.warnings]:
        context = {
            **issue.context,
            **({"expected": issue.expected} if issue.expected is not None else {}),
            **({"actual": issue.actual} if issue.actual is not None else {}),
            **({"stage": issue.stage} if issue.stage else {}),
            **({"track_id": issue.track_id} if issue.track_id else {}),
        }
        if issue.code == DUPLICATE_INSTRUMENT_ROLE_CODE:
            context.setdefault("stage", "compose_accompaniment")
            context.setdefault("repairable", True)
        items.append(
            ValidationDiagnostic(
                code=issue.code,
                message=issue.message,
                severity=issue.severity,
                context=context,
            )
        )
    return items


def _instrumentation_report_from_analysis(analysis) -> InstrumentationReport:
    raw_by_key = {req.key: list(req.raw_labels) for req in analysis.requirements}
    satisfied = [
        InstrumentSatisfactionEntry(
            key=item.key,
            identity=item.identity,
            family=item.family,
            status="satisfied",
            track_ids=list(item.track_ids),
            raw_labels=raw_by_key.get(item.key, []),
        )
        for item in analysis.satisfied
    ]
    missing = [
        InstrumentSatisfactionEntry(
            key=item.key,
            identity=item.identity,
            family=item.family,
            status="missing",
            track_ids=[],
            raw_labels=list(item.raw_labels),
        )
        for item in analysis.missing
    ]
    duplicates = [
        SuspiciousDuplicateGroupReport(
            identity=group.identity,
            role=group.role,
            track_ids=list(group.track_ids),
            event_counts=list(group.event_counts),
            content_relationship=group.content_relationship,
            actionable=group.actionable,
        )
        for group in analysis.duplicate_groups
    ]
    return InstrumentationReport(
        satisfied=satisfied,
        missing=missing,
        present_identities=list(analysis.present_identities),
        unexpected_identities=list(analysis.unexpected_identities),
        suspicious_duplicates=duplicates,
    )


def _issue(
    *,
    code: str,
    message: str,
    severity: str = "error",
    expected: Any = None,
    actual: Any = None,
    stage: str | None = None,
    track_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> GenerationValidationIssue:
    return GenerationValidationIssue(
        code=code,
        message=message,
        severity=severity,  # type: ignore[arg-type]
        expected=expected,
        actual=actual,
        stage=stage,
        track_id=track_id,
        context=context or {},
    )
