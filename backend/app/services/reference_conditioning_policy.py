"""Policy-aware reference conditioning assembly (preserve / borrow / regenerate).

Read-only against current and reference compositions. Soft fragments only —
never mutates prompts, hard constraints, or DATASET_ROOT. Never pastes event
arrays or motif pitch lists into soft lines.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from app.composition_schemas import CompositionV2
from app.embeddings.schemas import (
    CompositionEmbedScope,
    EmbedScopeBarRange,
    EmbedScopeComposition,
    StyleReferenceRequest,
)
from app.embeddings.settings import EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN
from app.reference_conditioning_schemas import (
    MOTIF_REUSE_INSTRUCTION,
    REGENERATE_INSTRUCTION_BY_DIMENSION,
    STRENGTH_LEGEND,
    ReferenceConditioningPolicy,
    ReferenceConditioningPolicyProvenance,
    canonical_policy_digest_payload,
    collect_borrow_dimension_union,
    compute_policy_digest,
    empty_policy,
    strength_for_dimension,
    validate_motif_reuse_gate,
    validate_policy_partition,
    validate_preserve_requires_source,
)
from app.reference_feature_schemas import (
    DimensionId,
    ReferenceFeatureAnalyzeRequest,
    ReferenceFeatureError,
    normalize_requested_dimensions,
)
from app.reference_feature_settings import load_reference_feature_settings
from app.services.reference_feature_analyze import (
    ANTI_MELODY_INSTRUCTION,
    analyze_reference_features,
)
from app.services.reference_feature_condition import (
    ReferenceFeatureConditionResult,
    collect_style_reference_bindings,
    resolve_reference_feature_conditioning,
)

logger = logging.getLogger(__name__)

ReferenceConditioningOperation = Literal["generate", "develop", "edit"]


@dataclass(frozen=True)
class ReferenceConditioningAssemblyResult:
    soft_fragment: str
    legacy_feature_summary: str | None
    provenance_entries: list[dict[str, Any]] = field(default_factory=list)
    policy_provenance: dict[str, Any] | None = None
    warning_codes: list[str] = field(default_factory=list)
    applied_borrow_dimension_ids: list[str] = field(default_factory=list)
    applied_preserve_dimension_ids: list[str] = field(default_factory=list)
    applied_regenerate_dimension_ids: list[str] = field(default_factory=list)
    binding_count: int = 0
    allow_motif_reuse: bool = False
    # Compatibility mirror of ReferenceFeatureConditionResult for callers.
    applied_dimension_ids: list[str] = field(default_factory=list)


def preserve_scope_for_edit(
    *,
    start_bar: int,
    end_bar: int,
    track_id: str | None = None,
) -> EmbedScopeBarRange:
    """Map AI edit selection bars → embed bar_range scope for preserve summarize."""
    return EmbedScopeBarRange(
        start_bar=int(start_bar),
        end_bar=int(end_bar),
        track_id=track_id,
    )


def preserve_scope_for_develop(
    *,
    source_start_bar: int,
    source_end_bar: int,
) -> EmbedScopeBarRange:
    """Map develop source context bars → embed bar_range for preserve summarize."""
    return EmbedScopeBarRange(
        start_bar=int(source_start_bar),
        end_bar=int(source_end_bar),
    )


def assemble_reference_conditioning(
    *,
    style_reference: StyleReferenceRequest | None = None,
    style_references: Sequence[StyleReferenceRequest] | None = None,
    policy: ReferenceConditioningPolicy | None = None,
    active_project_id: str | None = None,
    operation: ReferenceConditioningOperation = "generate",
    current_composition: CompositionV2 | dict[str, Any] | None = None,
    preserve_scope: CompositionEmbedScope | None = None,
) -> ReferenceConditioningAssemblyResult:
    """Validate policy + assemble soft block (preserve → borrow → regenerate → anti-copy).

    Backward compatible: when ``policy`` is None and only masks/legacy refs exist,
    delegates to shipped masked merge (borrow-only, strength=normal).
    """
    settings = load_reference_feature_settings()
    bindings, warnings = collect_style_reference_bindings(
        style_reference=style_reference,
        style_references=style_references,
    )

    # No policy and no preserve/regenerate intent → legacy masked path.
    if policy is None:
        legacy = resolve_reference_feature_conditioning(
            style_reference=style_reference,
            style_references=style_references,
        )
        return ReferenceConditioningAssemblyResult(
            soft_fragment=legacy.soft_fragment,
            legacy_feature_summary=legacy.legacy_feature_summary,
            provenance_entries=list(legacy.provenance_entries),
            policy_provenance=None,
            warning_codes=list(legacy.warning_codes),
            applied_borrow_dimension_ids=list(legacy.applied_dimension_ids),
            applied_preserve_dimension_ids=[],
            applied_regenerate_dimension_ids=[],
            binding_count=legacy.binding_count,
            allow_motif_reuse=False,
            applied_dimension_ids=list(legacy.applied_dimension_ids),
        )

    active_policy = policy
    composition_payload = _composition_as_dict(current_composition)
    has_current = composition_payload is not None

    validate_preserve_requires_source(
        preserve_dimensions=active_policy.preserve_dimensions,
        has_current_composition=has_current,
    )
    if operation == "generate" and active_policy.preserve_dimensions:
        # Generate never has a working composition for preserve.
        raise ReferenceFeatureError(
            "reference_conditioning_preserve_without_source",
            details={"operation": "generate", "preserve_count": len(active_policy.preserve_dimensions)},
        )

    # Collect masked borrow dims per binding (legacy unmasked bindings stay legacy).
    masked_binding_dims: list[list[DimensionId]] = []
    masked_bindings: list[StyleReferenceRequest] = []
    legacy_bindings: list[StyleReferenceRequest] = []
    for binding in bindings:
        dims_raw = getattr(binding, "dimensions", None)
        if dims_raw is None:
            legacy_bindings.append(binding)
            continue
        dims = normalize_requested_dimensions(list(dims_raw), default_all=False)
        masked_bindings.append(binding)
        masked_binding_dims.append(dims)

    borrow_union = collect_borrow_dimension_union(masked_binding_dims)
    validate_policy_partition(active_policy, borrow_dimensions=borrow_union)

    validate_motif_reuse_gate(
        allow_motif_reuse=active_policy.allow_motif_reuse,
        active_project_id=active_project_id,
        borrow_binding_project_ids=[b.project_id for b in masked_bindings],
    )

    preserve_lines: list[str] = []
    preserve_applied: list[str] = []
    if active_policy.preserve_dimensions and has_current:
        scope = preserve_scope or EmbedScopeComposition()
        p_lines, p_applied, p_warnings = _summarize_preserve_dimensions(
            composition=composition_payload,  # type: ignore[arg-type]
            scope=scope,
            dimensions=list(active_policy.preserve_dimensions),
            summary_max_chars=settings.summary_max_chars,
        )
        preserve_lines.extend(p_lines)
        preserve_applied.extend(p_applied)
        for code in p_warnings:
            if code not in warnings:
                warnings.append(code)

    borrow_lines: list[str] = []
    borrow_applied: list[str] = []
    provenance: list[dict[str, Any]] = []
    borrow_prov_for_digest: list[dict[str, Any]] = []
    any_masked_requested = bool(masked_bindings)
    attempted_borrow_analyze = False

    for binding, dims in zip(masked_bindings, masked_binding_dims, strict=True):
        # Drop strength=off before analyze; off → unspecified (not regenerate).
        active_dims: list[DimensionId] = []
        dim_strengths: dict[str, str] = {}
        for dim in dims:
            strength = strength_for_dimension(active_policy, dim)
            if strength == "off":
                if "reference_conditioning_strength_off_dropped" not in warnings:
                    warnings.append("reference_conditioning_strength_off_dropped")
                continue
            active_dims.append(dim)
            dim_strengths[dim] = strength

        if not active_dims:
            # All dims off for this binding — skip (unspecified).
            continue

        attempted_borrow_analyze = True
        lines, prov, entry_warnings, applied, borrow_dims_with_strength = (
            _strength_tagged_masked_binding(
                binding,
                active_dims,
                dim_strengths,
                settings.summary_max_chars,
            )
        )
        if lines:
            borrow_lines.extend(lines)
        provenance.append(prov)
        borrow_prov_for_digest.append(
            {
                "project_id": binding.project_id,
                "revision_id": binding.revision_id,
                "scope_kind": prov.get("scope_kind"),
                "fingerprint_prefix": prov.get("fingerprint_prefix"),
                "dimensions": borrow_dims_with_strength,
            }
        )
        borrow_applied.extend(applied)
        for code in entry_warnings:
            if code not in warnings:
                warnings.append(code)

    # All non-off borrow dims unavailable → 422. All-off with preserve/regenerate is OK.
    if attempted_borrow_analyze and not borrow_applied:
        raise ReferenceFeatureError("reference_feature_mask_empty")

    # Legacy whole-summary bindings (dimensions omitted) — only when no masks
    # OR as sibling soft lines when mixed (same as shipped conditioner).
    legacy_summaries: list[str] = []
    if legacy_bindings:
        from app.services.reference_feature_condition import _legacy_binding

        for binding in legacy_bindings:
            summary, entry_prov, entry_warnings = _legacy_binding(binding)
            if summary:
                legacy_summaries.append(summary)
            provenance.append(entry_prov)
            for code in entry_warnings:
                if code not in warnings:
                    warnings.append(code)

    regenerate_lines: list[str] = []
    regenerate_applied: list[str] = []
    for dim in active_policy.regenerate_dimensions:
        instruction = REGENERATE_INSTRUCTION_BY_DIMENSION.get(
            dim, f"Invent new {dim}; do not imitate the reference."
        )
        regenerate_lines.append(f"[regenerate dim={dim}] {instruction}")
        regenerate_applied.append(dim)

    anti_copy = (
        MOTIF_REUSE_INSTRUCTION
        if active_policy.allow_motif_reuse
        else ANTI_MELODY_INSTRUCTION
    )
    legend = STRENGTH_LEGEND[: settings.strength_legend_max_chars]

    parts: list[str] = [anti_copy, legend]
    if preserve_lines:
        parts.append("PRESERVE (current scope — abstract only):")
        parts.extend(preserve_lines)
    if borrow_lines:
        parts.append("BORROW (reference abstract properties):")
        parts.extend(borrow_lines)
    if regenerate_lines:
        parts.append("REGENERATE (invent new — do not imitate reference):")
        parts.extend(regenerate_lines)

    soft = "\n".join(parts).strip()
    if any_masked_requested and legacy_summaries:
        soft = f"{soft}\n" + "\n".join(legacy_summaries)
    elif legacy_summaries and not any_masked_requested and not preserve_lines and not regenerate_lines:
        # Pure legacy path under an empty-ish policy — still attach anti-copy.
        soft = f"{anti_copy}\n" + "\n".join(legacy_summaries)

    if len(soft) > settings.soft_fragment_max_chars:
        soft = soft[: settings.soft_fragment_max_chars - 3] + "..."

    legacy_out: str | None = None
    if legacy_summaries and not any_masked_requested and not preserve_lines and not regenerate_lines:
        legacy_out = "\n".join(legacy_summaries)
        if len(legacy_out) > settings.soft_fragment_max_chars:
            legacy_out = legacy_out[: settings.soft_fragment_max_chars - 3] + "..."

    digest_payload = canonical_policy_digest_payload(
        active_policy,
        borrow_provenance=borrow_prov_for_digest,
        active_project_id=active_project_id,
    )
    digest = compute_policy_digest(
        digest_payload, prefix_len=settings.policy_digest_prefix_len
    )
    policy_prov = ReferenceConditioningPolicyProvenance.model_validate(
        {
            "preserve_dimensions": list(active_policy.preserve_dimensions),
            "regenerate_dimensions": list(active_policy.regenerate_dimensions),
            "borrow": borrow_prov_for_digest,
            "allow_motif_reuse": active_policy.allow_motif_reuse,
            "active_project_id": active_project_id,
            "policy_digest": digest,
        }
    ).model_dump(mode="json")

    applied_all = list(borrow_applied)
    logger.info(
        "Reference conditioning policy assembled",
        extra={
            "operation": operation,
            "binding_count": len(bindings),
            "preserve_count": len(preserve_applied),
            "borrow_count": len(borrow_applied),
            "regenerate_count": len(regenerate_applied),
            "strengths_present": bool(active_policy.dimension_strengths),
            "motif_reuse": active_policy.allow_motif_reuse,
            "scope_kind": getattr(preserve_scope, "kind", None) if preserve_scope else None,
            "fragment_chars": len(soft),
            "policy_digest_prefix": digest[:8],
            "warning_count": len(warnings),
        },
    )
    logger.debug(
        "Reference conditioning policy digest attached",
        extra={"policy_digest_prefix": digest[:12], "has_policy": True},
    )

    return ReferenceConditioningAssemblyResult(
        soft_fragment=soft,
        legacy_feature_summary=legacy_out,
        provenance_entries=provenance,
        policy_provenance=policy_prov,
        warning_codes=warnings,
        applied_borrow_dimension_ids=borrow_applied,
        applied_preserve_dimension_ids=preserve_applied,
        applied_regenerate_dimension_ids=regenerate_applied,
        binding_count=len(bindings),
        allow_motif_reuse=active_policy.allow_motif_reuse,
        applied_dimension_ids=applied_all,
    )


def merge_reference_conditioning_provenance(
    provenance: dict[str, Any],
    assembly: ReferenceConditioningAssemblyResult | ReferenceFeatureConditionResult,
) -> dict[str, Any]:
    """Nest ``reference_features[]`` + optional policy digest under generation_parameters."""
    out = dict(provenance)
    generation_parameters = dict(out.get("generation_parameters") or {})

    entries = list(getattr(assembly, "provenance_entries", None) or [])
    if entries:
        generation_parameters["reference_features"] = entries

    warning_codes = list(getattr(assembly, "warning_codes", None) or [])
    if warning_codes:
        existing = list(generation_parameters.get("reference_feature_warnings") or [])
        for code in warning_codes:
            if code not in existing:
                existing.append(code)
        generation_parameters["reference_feature_warnings"] = existing

    policy_prov = getattr(assembly, "policy_provenance", None)
    if policy_prov:
        generation_parameters["reference_conditioning_policy"] = policy_prov
        logger.info(
            "Reference conditioning policy provenance attached",
            extra={
                "has_policy": True,
                "policy_digest_prefix": str(policy_prov.get("policy_digest") or "")[:8],
            },
        )
    else:
        logger.debug(
            "Reference conditioning policy provenance omitted",
            extra={"has_policy": False},
        )

    out["generation_parameters"] = generation_parameters
    return out


def _composition_as_dict(
    composition: CompositionV2 | dict[str, Any] | None,
) -> dict[str, Any] | None:
    if composition is None:
        return None
    if isinstance(composition, CompositionV2):
        return composition.model_dump(mode="json")
    if isinstance(composition, dict):
        return composition
    if hasattr(composition, "model_dump"):
        return composition.model_dump(mode="json")  # type: ignore[no-any-return]
    return None


def _summarize_preserve_dimensions(
    *,
    composition: dict[str, Any],
    scope: CompositionEmbedScope,
    dimensions: list[DimensionId],
    summary_max_chars: int,
) -> tuple[list[str], list[str], list[str]]:
    """Run analyzer on current scope inline — read-only; never mutates composition."""
    analyze_req = ReferenceFeatureAnalyzeRequest(
        composition=composition,
        scope=scope,
        requested_dimensions=dimensions,
    )
    # Working-composition preserve is session-local; rights gate is for named sources.
    report = analyze_reference_features(analyze_req, enforce_rights=False)
    lines: list[str] = []
    applied: list[str] = []
    warnings: list[str] = []
    for dim in dimensions:
        payload = report.dimensions.get(dim)
        if payload is None or payload.status == "unavailable":
            # Fallback weak preserve instruction without evidence dump.
            lines.append(
                f"[preserve dim={dim}] Do not alter {dim}; evidence thin — keep musically consistent."
            )
            applied.append(dim)
            warnings.append("reference_conditioning_preserve_degraded")
            continue
        if payload.status == "degraded":
            warnings.append("reference_conditioning_preserve_degraded")
        frag = (payload.soft_fragment or payload.summary or "").strip()
        if frag:
            clipped = frag[:summary_max_chars]
            lines.append(f"[preserve dim={dim}] Keep consistent with: {clipped}")
            applied.append(dim)
        else:
            lines.append(f"[preserve dim={dim}] Do not alter {dim}.")
            applied.append(dim)
    return lines, applied, warnings


def _strength_tagged_masked_binding(
    binding: StyleReferenceRequest,
    dims: list[DimensionId],
    dim_strengths: dict[str, str],
    summary_max_chars: int,
) -> tuple[list[str], dict[str, Any], list[str], list[str], list[dict[str, str]]]:
    if binding.project_id:
        from app.services.reference_rights_gate import assert_reference_rights_allowed

        assert_reference_rights_allowed(
            project_id=binding.project_id,
            revision_id=binding.revision_id,
            rights=None,
        )
    analyze_req = ReferenceFeatureAnalyzeRequest(
        project_id=binding.project_id,
        revision_id=binding.revision_id,
        composition=binding.composition,
        scope=binding.scope,
        expected_fingerprint=binding.expected_fingerprint,
        requested_dimensions=dims,
    )
    report = analyze_reference_features(analyze_req, enforce_rights=False)

    lines: list[str] = []
    applied: list[str] = []
    unavailable_codes: list[str] = []
    warn: list[str] = []
    borrow_dims_with_strength: list[dict[str, str]] = []

    for dim in dims:
        payload = report.dimensions.get(dim)
        strength = dim_strengths.get(dim, "normal")
        if payload is None or payload.status == "unavailable":
            code = (
                payload.warning_codes[0]
                if payload and payload.warning_codes
                else "reference_feature_degraded"
            )
            unavailable_codes.append(f"{dim}:{code}")
            continue
        if payload.status == "degraded":
            warn.append("reference_feature_degraded")
        frag = (payload.soft_fragment or "").strip()
        if not frag:
            continue
        fp = report.source.source_fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN]
        tagged = (
            f"[ref={fp} dim={dim} strength={strength}] {frag[:summary_max_chars]}"
        )
        lines.append(f"- {tagged}")
        applied.append(dim)
        borrow_dims_with_strength.append({"id": dim, "strength": strength})

    if not applied:
        raise ReferenceFeatureError(
            "reference_feature_mask_empty",
            details={"unavailable_codes": unavailable_codes},
        )

    scope_kind = getattr(binding.scope, "kind", "composition")
    fp = report.source.source_fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN]
    # Extend shipped provenance with per-dim strengths (still no summaries).
    from app.reference_feature_schemas import ReferenceFeatureProvenanceEntry

    prov = ReferenceFeatureProvenanceEntry(
        project_id=binding.project_id,
        revision_id=binding.revision_id,
        scope_kind=str(scope_kind),
        fingerprint_prefix=fp,
        dimensions=applied,  # type: ignore[arg-type]
        unavailable_codes=unavailable_codes,
    ).model_dump(mode="json")
    prov["dimension_strengths"] = {
        item["id"]: item["strength"] for item in borrow_dims_with_strength
    }

    return lines, prov, warn, applied, borrow_dims_with_strength


# Re-export helper used by callers that only need empty policy defaults.
__all__ = [
    "ReferenceConditioningAssemblyResult",
    "assemble_reference_conditioning",
    "empty_policy",
    "merge_reference_conditioning_provenance",
    "preserve_scope_for_develop",
    "preserve_scope_for_edit",
]
