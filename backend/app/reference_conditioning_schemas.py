"""``reference.conditioning.policy.v1`` — preserve / borrow / regenerate partition.

Request-scoped soft policy only. Not a Composer Profile, not ``reference.features.v1``,
not an embedding dump. Strengths are policy-owned — never on ``StyleReferenceRequest``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Literal, Sequence

from pydantic import Field, field_validator, model_validator

from app.reference_feature_schemas import (
    DIMENSION_ID_SET,
    DimensionId,
    ReferenceFeatureError,
    StrictModel,
    parse_dimension_id,
)

logger = logging.getLogger(__name__)

REFERENCE_CONDITIONING_POLICY_SCHEMA: Literal["reference.conditioning.policy.v1"] = (
    "reference.conditioning.policy.v1"
)

ConditioningStrength = Literal["off", "light", "normal", "strong"]
CONDITIONING_STRENGTHS: tuple[ConditioningStrength, ...] = (
    "off",
    "light",
    "normal",
    "strong",
)
CONDITIONING_STRENGTH_SET: frozenset[str] = frozenset(CONDITIONING_STRENGTHS)

# Stable invent-new instruction lines (regenerate disposition).
REGENERATE_INSTRUCTION_BY_DIMENSION: dict[DimensionId, str] = {
    "harmony": "Invent new harmony; do not imitate reference chord sequences.",
    "harmonic_rhythm": "Invent new harmonic rhythm; do not freeze reference chord-change rates.",
    "rhythm": "Invent new rhythmic character; do not copy reference onset patterns as note lists.",
    "melodic_contour": (
        "Invent new melodic contour; do not imitate reference pitch patterns."
    ),
    "texture": "Invent new orchestration texture; do not copy reference voicing layouts.",
    "instrumentation": "Invent new instrumentation choices within hard instrument constraints.",
    "density": "Invent new rhythmic density; do not lock to reference note-per-bar bands.",
    "dynamics": "Invent new dynamics shaping; do not paste reference velocity curves.",
    "form": "Invent new form pacing within hard section constraints when present.",
    "tension_curve": "Invent a new tension curve; do not copy reference tension shapes.",
    "motif_characteristics": (
        "Invent new motif characteristics; do not paste motif note sequences."
    ),
    "performance_characteristics": (
        "Invent new performance shaping; do not copy reference expression curves."
    ),
}

STRENGTH_LEGEND = (
    "Strength legend: off=omit borrow line; light=gentle preference; "
    "normal=standard soft guidance; strong=emphasize abstract property "
    "(still never overrides hard constraints; never copies melodies)."
)

MOTIF_REUSE_INSTRUCTION = (
    "Motif characteristic reuse from the user's own project is allowed as abstract "
    "guidance; still do not paste note sequences."
)


class ReferenceConditioningPolicy(StrictModel):
    """Operation-level preserve / regenerate disposition + borrow strengths.

    Borrow bindings themselves come from ``style_reference(s).dimensions``.
    ``strict_partition`` means disjoint-only — unspecified dims get no soft guidance.
    """

    schema_version: Literal["reference.conditioning.policy.v1"] = (
        REFERENCE_CONDITIONING_POLICY_SCHEMA
    )
    preserve_dimensions: list[DimensionId] = Field(default_factory=list, max_length=32)
    regenerate_dimensions: list[DimensionId] = Field(default_factory=list, max_length=32)
    dimension_strengths: dict[DimensionId, ConditioningStrength] = Field(
        default_factory=dict, max_length=32
    )
    default_borrow_strength: ConditioningStrength = "normal"
    allow_motif_reuse: bool = False
    strict_partition: bool = True

    @field_validator("preserve_dimensions", "regenerate_dimensions", mode="before")
    @classmethod
    def _normalize_dim_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("dimension lists must be arrays")
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            dim = parse_dimension_id(str(item))
            if dim in seen:
                continue
            seen.add(dim)
            out.append(dim)
        return out

    @field_validator("dimension_strengths", mode="before")
    @classmethod
    def _normalize_strengths(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("dimension_strengths must be an object")
        out: dict[str, str] = {}
        for key, raw in value.items():
            dim = parse_dimension_id(str(key))
            strength = str(raw).strip().lower()
            if strength not in CONDITIONING_STRENGTH_SET:
                raise ValueError(f"invalid conditioning strength: {raw!r}")
            out[dim] = strength
        return out

    @model_validator(mode="after")
    def _log_shape(self) -> ReferenceConditioningPolicy:
        logger.debug(
            "Reference conditioning policy validated",
            extra={
                "field_names": [
                    "preserve_dimensions",
                    "regenerate_dimensions",
                    "dimension_strengths",
                    "default_borrow_strength",
                    "allow_motif_reuse",
                    "strict_partition",
                ],
                "preserve_count": len(self.preserve_dimensions),
                "regenerate_count": len(self.regenerate_dimensions),
                "strength_key_count": len(self.dimension_strengths),
                "allow_motif_reuse": self.allow_motif_reuse,
                "strict_partition": self.strict_partition,
            },
        )
        return self


class ReferenceConditioningBorrowDimProvenance(StrictModel):
    id: DimensionId
    strength: ConditioningStrength


class ReferenceConditioningBorrowProvenance(StrictModel):
    project_id: str | None = Field(default=None, max_length=80)
    revision_id: str | None = Field(default=None, max_length=80)
    scope_kind: str = Field(..., min_length=1, max_length=40)
    fingerprint_prefix: str = Field(..., min_length=4, max_length=32)
    dimensions: list[ReferenceConditioningBorrowDimProvenance] = Field(
        default_factory=list, max_length=32
    )


class ReferenceConditioningPolicyProvenance(StrictModel):
    """Secret-safe policy digest nest under ``generation_parameters``."""

    schema_version: Literal["reference.conditioning.policy.v1"] = (
        REFERENCE_CONDITIONING_POLICY_SCHEMA
    )
    preserve_dimensions: list[DimensionId] = Field(default_factory=list, max_length=32)
    regenerate_dimensions: list[DimensionId] = Field(default_factory=list, max_length=32)
    borrow: list[ReferenceConditioningBorrowProvenance] = Field(
        default_factory=list, max_length=8
    )
    allow_motif_reuse: bool = False
    active_project_id: str | None = Field(default=None, max_length=80)
    policy_digest: str = Field(..., min_length=8, max_length=64)


def normalize_strength(raw: str | None, *, default: ConditioningStrength = "normal") -> ConditioningStrength:
    if raw is None or str(raw).strip() == "":
        return default
    value = str(raw).strip().lower()
    if value not in CONDITIONING_STRENGTH_SET:
        logger.debug(
            "Invalid conditioning strength; using default",
            extra={"raw": str(raw)[:40], "default": default},
        )
        return default
    return value  # type: ignore[return-value]


def strength_for_dimension(
    policy: ReferenceConditioningPolicy | None,
    dimension: DimensionId | str,
) -> ConditioningStrength:
    if policy is None:
        return "normal"
    mapped = policy.dimension_strengths.get(dimension)  # type: ignore[arg-type]
    if mapped is not None:
        return mapped
    return policy.default_borrow_strength


def collect_borrow_dimension_union(
    binding_dimension_lists: Sequence[Sequence[str]],
) -> list[DimensionId]:
    """Return ordered unique borrow dims; raise on duplicate across bindings."""
    seen: dict[str, int] = {}
    ordered: list[DimensionId] = []
    for binding_index, dims in enumerate(binding_dimension_lists):
        for raw in dims:
            dim = parse_dimension_id(str(raw))
            if dim in seen:
                raise ReferenceFeatureError(
                    "reference_conditioning_borrow_dimension_conflict",
                    details={
                        "dimension": dim,
                        "first_binding_index": seen[dim],
                        "conflict_binding_index": binding_index,
                    },
                )
            seen[dim] = binding_index
            ordered.append(dim)
    return ordered


def validate_policy_partition(
    policy: ReferenceConditioningPolicy,
    *,
    borrow_dimensions: Sequence[DimensionId | str],
) -> None:
    """Enforce disjoint preserve / borrow / regenerate (strict_partition = disjoint-only).

    Does **not** require covering the full registry. Unspecified dims get no soft guidance.
    """
    preserve = set(policy.preserve_dimensions)
    regenerate = set(policy.regenerate_dimensions)
    borrow: set[str] = set()
    for raw in borrow_dimensions:
        borrow.add(parse_dimension_id(str(raw)))

    overlap_pb = sorted(preserve & borrow)
    overlap_pr = sorted(preserve & regenerate)
    overlap_br = sorted(borrow & regenerate)
    if overlap_pb or overlap_pr or overlap_br:
        raise ReferenceFeatureError(
            "reference_conditioning_partition_overlap",
            details={
                "preserve_borrow": overlap_pb,
                "preserve_regenerate": overlap_pr,
                "borrow_regenerate": overlap_br,
            },
        )

    # Strength keys must be subset of borrow union (even when strength=off).
    unknown_strength_keys = sorted(
        key for key in policy.dimension_strengths if key not in borrow
    )
    if unknown_strength_keys:
        raise ReferenceFeatureError(
            "reference_conditioning_unknown_strength",
            details={"unknown_keys": unknown_strength_keys},
        )

    logger.debug(
        "Reference conditioning partition validated",
        extra={
            "preserve_count": len(preserve),
            "borrow_count": len(borrow),
            "regenerate_count": len(regenerate),
            "strict_partition": policy.strict_partition,
        },
    )


def validate_motif_reuse_gate(
    *,
    allow_motif_reuse: bool,
    active_project_id: str | None,
    borrow_binding_project_ids: Sequence[str | None],
) -> None:
    """Gate own-project motif reuse; foreign / inline-only / missing ids → 422."""
    if not allow_motif_reuse:
        return
    active = (active_project_id or "").strip()
    if not active:
        raise ReferenceFeatureError(
            "reference_conditioning_motif_reuse_forbidden",
            details={"reason": "missing_active_project_id"},
        )
    for index, project_id in enumerate(borrow_binding_project_ids):
        binding_id = (project_id or "").strip()
        if not binding_id or binding_id != active:
            raise ReferenceFeatureError(
                "reference_conditioning_motif_reuse_forbidden",
                details={
                    "reason": "binding_project_mismatch",
                    "binding_index": index,
                    "active_project_id_present": True,
                    "binding_has_project_id": bool(binding_id),
                },
            )


def validate_preserve_requires_source(
    *,
    preserve_dimensions: Sequence[DimensionId | str],
    has_current_composition: bool,
) -> None:
    if preserve_dimensions and not has_current_composition:
        raise ReferenceFeatureError(
            "reference_conditioning_preserve_without_source",
            details={"preserve_count": len(list(preserve_dimensions))},
        )


def canonical_policy_digest_payload(
    policy: ReferenceConditioningPolicy,
    *,
    borrow_provenance: list[dict[str, Any]],
    active_project_id: str | None,
) -> dict[str, Any]:
    """Canonical JSON-able payload for digest (no soft fragments / vectors / events)."""
    return {
        "schema": REFERENCE_CONDITIONING_POLICY_SCHEMA,
        "preserve_dimensions": list(policy.preserve_dimensions),
        "regenerate_dimensions": list(policy.regenerate_dimensions),
        "dimension_strengths": {
            key: policy.dimension_strengths[key]
            for key in sorted(policy.dimension_strengths)
        },
        "default_borrow_strength": policy.default_borrow_strength,
        "allow_motif_reuse": policy.allow_motif_reuse,
        "strict_partition": policy.strict_partition,
        "borrow": borrow_provenance,
        "active_project_id": active_project_id,
    }


def compute_policy_digest(
    payload: dict[str, Any],
    *,
    prefix_len: int = 16,
) -> str:
    """SHA-256 hex prefix of canonical policy JSON."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    length = max(8, min(64, int(prefix_len)))
    return digest[:length]


def empty_policy() -> ReferenceConditioningPolicy:
    """Backward-compatible implied policy when only masks are present."""
    return ReferenceConditioningPolicy()
