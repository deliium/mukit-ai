"""Plan ↔ hard GenerationConstraints conformance helpers.

Shared by the LLM plan parse path and hybrid ``validate_plan`` nodes.
Produces a stable ``constraints_digest`` for provenance without logging freeform text.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ..composition_plan_schemas import (
    PLAN_DIGEST_LOG_PREFIX_LEN,
    CompositionPlan,
    CompositionPlanError,
    CompositionPlanErrorCode,
)
from .composition_planner import ValidationDiagnostic
from .generation_constraints import (
    GenerationConstraints,
    freeze_form_resolved_fields,
    normalize_instrument_family,
    unexpected_instrument_families,
)
from .instrument_identity import missing_required_identities


logger = logging.getLogger(__name__)

CONSTRAINTS_DIGEST_ALGORITHM = "sha256"


def constraints_digest_for(constraints: GenerationConstraints) -> str:
    """Stable digest of hard constraint fields for plan provenance."""
    payload = {
        "key": constraints.key,
        "key_user_specified": constraints.key_user_specified,
        "time_signature": constraints.time_signature,
        "duration_bars": constraints.duration_bars,
        "tempo_min": constraints.tempo_min,
        "tempo_max": constraints.tempo_max,
        "sections_user_specified": constraints.sections_user_specified,
        "sections": (
            [
                {
                    "type": section.type,
                    "start_bar": section.start_bar,
                    "bar_count": section.bar_count,
                }
                for section in constraints.sections
            ]
            if constraints.sections
            else None
        ),
        "required_instrument_families": list(constraints.required_instrument_families),
        "allow_extra_instrument_families": constraints.allow_extra_instrument_families,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    result = f"{CONSTRAINTS_DIGEST_ALGORITHM}:{digest}"
    logger.debug(
        "Computed constraints digest",
        extra={
            "digest_prefix": result[: PLAN_DIGEST_LOG_PREFIX_LEN + len(CONSTRAINTS_DIGEST_ALGORITHM) + 1],
            "payload_bytes": len(encoded),
        },
    )
    return result


def attach_constraints_digest(
    plan: CompositionPlan,
    constraints: GenerationConstraints,
) -> CompositionPlan:
    """Return a copy of ``plan`` with ``constraints_digest`` locked from hard constraints."""
    digest = constraints_digest_for(constraints)
    updated = plan.model_copy(update={"constraints_digest": digest})
    logger.info(
        "Attached constraints digest to composition plan",
        extra={
            "digest_prefix": digest[: PLAN_DIGEST_LOG_PREFIX_LEN + len(CONSTRAINTS_DIGEST_ALGORITHM) + 1],
            **constraints.hard_summary(),
            "bar_count": plan.form.bar_count,
            "section_count": len(plan.form.sections),
        },
    )
    return updated


def validate_plan_against_constraints(
    plan: CompositionPlan,
    constraints: GenerationConstraints,
    *,
    stage: str = "validate_plan",
    require_digest_match: bool = False,
) -> list[ValidationDiagnostic]:
    """Validate plan form/instrumentation against frozen hard constraints.

    Returns diagnostics (errors for hard mismatches, warnings for soft drift).
    Does not mutate ``plan`` or ``constraints``.
    """
    logger.info(
        "Validating composition plan against hard constraints",
        extra={
            "stage": stage,
            "digest_prefix": (plan.constraints_digest or "")[
                : PLAN_DIGEST_LOG_PREFIX_LEN + len(CONSTRAINTS_DIGEST_ALGORITHM) + 1
            ]
            or None,
            **constraints.hard_summary(),
        },
    )

    diagnostics: list[ValidationDiagnostic] = []
    _, form_diagnostics = freeze_form_resolved_fields(constraints, plan.form)
    for item in form_diagnostics:
        # Retarget stage label for hybrid/plan path clarity.
        ctx = dict(item.context or {})
        ctx["stage"] = stage
        diagnostics.append(
            ValidationDiagnostic(
                code=item.code,
                message=item.message,
                severity=item.severity,
                context=ctx,
            )
        )

    present = list(plan.form.instrumentation)
    for hint in plan.instrumentation.hints:
        present.append(hint.family)

    missing = missing_required_identities(present, constraints.required_instrument_families)
    if missing:
        diagnostics.append(
            ValidationDiagnostic(
                code="constraint_missing_instruments",
                message="Plan instrumentation is missing required instrument families",
                severity="error",
                context={
                    "stage": stage,
                    "missing": missing,
                    "required": list(constraints.required_instrument_families),
                },
            )
        )

    unexpected = unexpected_instrument_families(present, constraints)
    if unexpected:
        diagnostics.append(
            ValidationDiagnostic(
                code="constraint_unexpected_instruments",
                message="Plan instrumentation includes unexpected instrument families",
                severity="error" if not constraints.allow_extra_instrument_families else "warning",
                context={
                    "stage": stage,
                    "unexpected": unexpected,
                    "allow_extra": constraints.allow_extra_instrument_families,
                },
            )
        )

    # Soft: modulation keys that conflict with a locked user key when only one key allowed.
    if constraints.key_user_specified and constraints.key:
        for target in plan.modulation.targets:
            if target.key != constraints.key:
                diagnostics.append(
                    ValidationDiagnostic(
                        code="plan_modulation_soft_mismatch",
                        message="Modulation target differs from locked user key (soft)",
                        severity="warning",
                        context={
                            "stage": stage,
                            "expected": constraints.key,
                            "actual": target.key,
                            "section_index": target.section_index,
                        },
                    )
                )
                logger.warning(
                    "Plan modulation soft mismatch vs locked key",
                    extra={
                        "stage": stage,
                        "expected": constraints.key,
                        "actual": target.key,
                        "section_index": target.section_index,
                    },
                )

    expected_digest = constraints_digest_for(constraints)
    if require_digest_match:
        if not plan.constraints_digest:
            diagnostics.append(
                ValidationDiagnostic(
                    code="plan_constraint_mismatch",
                    message="Plan is missing constraints_digest after lock",
                    severity="error",
                    context={"stage": stage, "expected_digest_prefix": expected_digest[:20]},
                )
            )
        elif plan.constraints_digest != expected_digest:
            diagnostics.append(
                ValidationDiagnostic(
                    code="plan_constraint_mismatch",
                    message="Plan constraints_digest does not match current hard constraints",
                    severity="error",
                    context={
                        "stage": stage,
                        "expected_digest_prefix": expected_digest[:20],
                        "actual_digest_prefix": plan.constraints_digest[:20],
                    },
                )
            )
    elif plan.constraints_digest and plan.constraints_digest != expected_digest:
        diagnostics.append(
            ValidationDiagnostic(
                code="plan_constraint_digest_stale",
                message="Plan constraints_digest is stale relative to current hard constraints",
                severity="warning",
                context={
                    "stage": stage,
                    "expected_digest_prefix": expected_digest[:20],
                    "actual_digest_prefix": plan.constraints_digest[:20],
                },
            )
        )
        logger.warning(
            "Plan constraints digest stale",
            extra={
                "stage": stage,
                "expected_digest_prefix": expected_digest[:20],
                "actual_digest_prefix": plan.constraints_digest[:20],
            },
        )

    error_codes = [item.code for item in diagnostics if item.severity == "error"]
    warning_codes = [item.code for item in diagnostics if item.severity == "warning"]
    if error_codes:
        logger.error(
            "Composition plan failed hard constraint conformance",
            extra={
                "stage": stage,
                "error_codes": error_codes,
                "warning_codes": warning_codes,
                "diagnostic_count": len(diagnostics),
            },
        )
    else:
        logger.info(
            "Composition plan passed hard constraint conformance",
            extra={
                "stage": stage,
                "warning_codes": warning_codes,
                "diagnostic_count": len(diagnostics),
                "digest_prefix": expected_digest[
                    : PLAN_DIGEST_LOG_PREFIX_LEN + len(CONSTRAINTS_DIGEST_ALGORITHM) + 1
                ],
            },
        )
    return diagnostics


def ensure_plan_conforms(
    plan: CompositionPlan,
    constraints: GenerationConstraints,
    *,
    stage: str = "validate_plan",
) -> CompositionPlan:
    """Attach digest and raise ``CompositionPlanError`` when hard conformance fails."""
    locked = attach_constraints_digest(plan, constraints)
    diagnostics = validate_plan_against_constraints(
        locked,
        constraints,
        stage=stage,
        require_digest_match=True,
    )
    errors = [item for item in diagnostics if item.severity == "error"]
    if not errors:
        return locked
    code: CompositionPlanErrorCode = "plan_constraint_mismatch"
    raise CompositionPlanError(
        "Composition plan contradicts hard generation constraints",
        code=code,
        context={
            "stage": stage,
            "error_codes": [item.code for item in errors],
            "warning_codes": [item.code for item in diagnostics if item.severity == "warning"],
        },
    )


def plan_instrument_families(plan: CompositionPlan) -> list[str]:
    """Deduplicated instrument family tokens present on the plan (form + hints)."""
    families: list[str] = []
    seen: set[str] = set()
    for label in list(plan.form.instrumentation) + [h.family for h in plan.instrumentation.hints]:
        normalized = normalize_instrument_family(label) or label.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        families.append(normalized)
    return families


def conformance_summary(diagnostics: list[ValidationDiagnostic]) -> dict[str, Any]:
    return {
        "diagnostic_count": len(diagnostics),
        "error_codes": [item.code for item in diagnostics if item.severity == "error"],
        "warning_codes": [item.code for item in diagnostics if item.severity == "warning"],
    }


__all__ = [
    "CONSTRAINTS_DIGEST_ALGORITHM",
    "attach_constraints_digest",
    "conformance_summary",
    "constraints_digest_for",
    "ensure_plan_conforms",
    "plan_instrument_families",
    "validate_plan_against_constraints",
]
