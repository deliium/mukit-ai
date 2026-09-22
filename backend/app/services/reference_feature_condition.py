"""Masked reference-feature soft conditioning for generate and develop.

Additive soft fragments only — never mutates prompt DTOs or hard constraints.
Never copies melodies / event arrays into prompts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.embeddings.errors import EmbeddingError, ReferenceResolveError
from app.embeddings.schemas import StyleReferenceRequest
from app.embeddings.settings import EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN
from app.reference_feature_schemas import (
    DimensionId,
    ReferenceFeatureError,
    ReferenceFeatureProvenanceEntry,
    ReferenceFeatureWarningCode,
    normalize_requested_dimensions,
)
from app.reference_feature_settings import load_reference_feature_settings
from app.services.composition_style_conditioning import (
    conditioning_context_fragment,
    resolve_style_reference,
)
from app.services.reference_feature_analyze import (
    ANTI_MELODY_INSTRUCTION,
    analyze_reference_features,
)
from app.reference_feature_schemas import ReferenceFeatureAnalyzeRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReferenceFeatureConditionResult:
    soft_fragment: str
    legacy_feature_summary: str | None
    provenance_entries: list[dict[str, Any]] = field(default_factory=list)
    warning_codes: list[str] = field(default_factory=list)
    applied_dimension_ids: list[str] = field(default_factory=list)
    binding_count: int = 0


def collect_style_reference_bindings(
    *,
    style_reference: StyleReferenceRequest | None,
    style_references: Sequence[StyleReferenceRequest] | None,
) -> tuple[list[StyleReferenceRequest], list[str]]:
    """Apply locked Part C binding precedence.

    ``style_references`` (non-empty) is authoritative; singular is ignored with warning.
    """
    warnings: list[str] = []
    multi = [item for item in (style_references or []) if item is not None]
    if multi:
        settings = load_reference_feature_settings()
        if len(multi) > settings.max_references:
            raise ReferenceFeatureError(
                "reference_feature_cap_exceeded",
                details={
                    "max_references": settings.max_references,
                    "got": len(multi),
                },
            )
        if style_reference is not None:
            warnings.append("reference_feature_singular_ignored")
            logger.info(
                "Ignoring singular style_reference; style_references authoritative",
                extra={"multi_count": len(multi)},
            )
        return list(multi), warnings
    if style_reference is not None:
        return [style_reference], warnings
    return [], warnings


def resolve_reference_feature_conditioning(
    *,
    style_reference: StyleReferenceRequest | None = None,
    style_references: Sequence[StyleReferenceRequest] | None = None,
) -> ReferenceFeatureConditionResult:
    """Resolve bindings → additive soft fragment + provenance (no prompt mutation)."""
    bindings, warnings = collect_style_reference_bindings(
        style_reference=style_reference,
        style_references=style_references,
    )
    if not bindings:
        return ReferenceFeatureConditionResult(
            soft_fragment="",
            legacy_feature_summary=None,
            warning_codes=warnings,
        )

    settings = load_reference_feature_settings()
    fragment_parts: list[str] = []
    legacy_summaries: list[str] = []
    provenance: list[dict[str, Any]] = []
    applied_dims: list[str] = []
    any_masked = False

    for binding in bindings:
        dims_raw = getattr(binding, "dimensions", None)
        if dims_raw is not None:
            any_masked = True
            dims = normalize_requested_dimensions(list(dims_raw), default_all=False)
            entry_fragment, entry_prov, entry_warnings, entry_dims = _masked_binding(
                binding, dims, settings.summary_max_chars
            )
            if entry_fragment:
                fragment_parts.append(entry_fragment)
            provenance.append(entry_prov)
            applied_dims.extend(entry_dims)
            for code in entry_warnings:
                if code not in warnings:
                    warnings.append(code)
        else:
            # Legacy whole feature_summary path (V3-compatible).
            summary, entry_prov, entry_warnings = _legacy_binding(binding)
            if summary:
                legacy_summaries.append(summary)
            provenance.append(entry_prov)
            for code in entry_warnings:
                if code not in warnings:
                    warnings.append(code)

    if any_masked and not fragment_parts:
        raise ReferenceFeatureError("reference_feature_mask_empty")

    soft = ""
    if fragment_parts:
        body = "\n".join(fragment_parts)
        soft = f"{ANTI_MELODY_INSTRUCTION}\n{body}".strip()
        if len(soft) > settings.soft_fragment_max_chars:
            soft = soft[: settings.soft_fragment_max_chars - 3] + "..."

    legacy = None
    if legacy_summaries and not any_masked:
        # Only emit legacy whole summary when no mask is present on any binding.
        legacy = "\n".join(legacy_summaries)
        if len(legacy) > settings.soft_fragment_max_chars:
            legacy = legacy[: settings.soft_fragment_max_chars - 3] + "..."

    # Mixed multi-ref: some masked, some legacy — include legacy lines only for
    # unmasked bindings already captured in legacy_summaries; when any_masked,
    # still append legacy summaries for unmasked siblings as soft lines.
    if any_masked and legacy_summaries:
        extra = "\n".join(legacy_summaries)
        soft = f"{soft}\n{extra}".strip() if soft else extra
        if len(soft) > settings.soft_fragment_max_chars:
            soft = soft[: settings.soft_fragment_max_chars - 3] + "..."

    logger.info(
        "Reference feature conditioning resolved",
        extra={
            "binding_count": len(bindings),
            "applied_dimension_count": len(applied_dims),
            "fragment_chars": len(soft),
            "has_legacy_summary": bool(legacy),
            "warning_count": len(warnings),
        },
    )
    return ReferenceFeatureConditionResult(
        soft_fragment=soft,
        legacy_feature_summary=legacy,
        provenance_entries=provenance,
        warning_codes=warnings,
        applied_dimension_ids=applied_dims,
        binding_count=len(bindings),
    )


def merge_reference_feature_provenance(
    provenance: dict[str, Any],
    condition: ReferenceFeatureConditionResult,
) -> dict[str, Any]:
    """Nest ``reference_features[]`` under generation_parameters (no summaries).

    Prefer ``merge_reference_conditioning_provenance`` when a policy digest is present.
    """
    if not condition.provenance_entries and not getattr(
        condition, "policy_provenance", None
    ):
        # Still merge warnings if present on a thin result.
        if not condition.warning_codes:
            return provenance
    out = dict(provenance)
    generation_parameters = dict(out.get("generation_parameters") or {})
    if condition.provenance_entries:
        generation_parameters["reference_features"] = list(condition.provenance_entries)
    if condition.warning_codes:
        existing = list(generation_parameters.get("reference_feature_warnings") or [])
        for code in condition.warning_codes:
            if code not in existing:
                existing.append(code)
        generation_parameters["reference_feature_warnings"] = existing
    policy_prov = getattr(condition, "policy_provenance", None)
    if policy_prov:
        generation_parameters["reference_conditioning_policy"] = policy_prov
        logger.info(
            "Reference conditioning policy provenance attached",
            extra={
                "has_policy": True,
                "policy_digest_prefix": str(policy_prov.get("policy_digest") or "")[:8],
            },
        )
    out["generation_parameters"] = generation_parameters
    return out


def reference_features_active_for_fake(request: Any) -> bool:
    """Whether fake LLM should apply a reference soft-marker shift."""
    singular = getattr(request, "style_reference", None)
    multi = getattr(request, "style_references", None) or []
    if multi:
        return True
    if singular is None:
        return False
    dims = getattr(singular, "dimensions", None)
    return True  # any style_reference (masked or legacy) shifts fake contour


def combined_reference_soft_block(
    *,
    masked_fragment: str,
    legacy_summary: str | None,
) -> str:
    """Format soft block for prompt builders (after profile fragment)."""
    parts: list[str] = []
    masked = (masked_fragment or "").strip()
    if masked:
        parts.append(masked)
    legacy = (legacy_summary or "").strip()
    if legacy and not masked:
        parts.append(
            "Musical reference feature summary (abstract; do not copy melodies):\n"
            + legacy
        )
    if not parts:
        return ""
    return "\n" + "\n".join(parts) + "\n"


def _masked_binding(
    binding: StyleReferenceRequest,
    dims: list[DimensionId],
    summary_max_chars: int,
) -> tuple[str, dict[str, Any], list[str], list[str]]:
    analyze_req = ReferenceFeatureAnalyzeRequest(
        project_id=binding.project_id,
        revision_id=binding.revision_id,
        composition=binding.composition,
        scope=binding.scope,
        expected_fingerprint=binding.expected_fingerprint,
        requested_dimensions=dims,
    )
    try:
        report = analyze_reference_features(analyze_req)
    except ReferenceFeatureError:
        raise
    except (ReferenceResolveError, EmbeddingError) as exc:
        code = getattr(exc, "code", "reference_not_found")
        mapped = (
            "reference_feature_not_found"
            if code in {"reference_not_found"}
            else "reference_feature_invalid"
        )
        raise ReferenceFeatureError(
            mapped,  # type: ignore[arg-type]
            http_status=404 if mapped == "reference_feature_not_found" else 422,
            details={"cause": code},
        ) from exc

    lines: list[str] = []
    applied: list[str] = []
    unavailable_codes: list[str] = []
    warn: list[str] = []
    for dim in dims:
        payload = report.dimensions.get(dim)
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
        if frag:
            lines.append(frag[:summary_max_chars])
            applied.append(dim)

    if not applied:
        raise ReferenceFeatureError(
            "reference_feature_mask_empty",
            details={"unavailable_codes": unavailable_codes},
        )

    scope_kind = getattr(binding.scope, "kind", "composition")
    fp = report.source.source_fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN]
    prov = ReferenceFeatureProvenanceEntry(
        project_id=binding.project_id,
        revision_id=binding.revision_id,
        scope_kind=str(scope_kind),
        fingerprint_prefix=fp,
        dimensions=applied,  # type: ignore[arg-type]
        unavailable_codes=unavailable_codes,
    ).model_dump(mode="json")

    tag = binding.project_id or "inline"
    header = f"[reference:{tag} scope={scope_kind}]"
    body = "\n".join(f"- {line}" for line in lines)
    return f"{header}\n{body}", prov, warn, applied


def _legacy_binding(
    binding: StyleReferenceRequest,
) -> tuple[str, dict[str, Any], list[str]]:
    try:
        resolved = resolve_style_reference(binding, include_conditioning=True)
    except EmbeddingError as exc:
        mapped = (
            "reference_feature_not_found"
            if exc.code == "reference_not_found"
            else "reference_feature_invalid"
        )
        raise ReferenceFeatureError(
            mapped,  # type: ignore[arg-type]
            http_status=404 if mapped == "reference_feature_not_found" else 422,
            details={"cause": exc.code},
        ) from exc

    summary = ""
    if resolved.conditioning is not None:
        fragment = conditioning_context_fragment(resolved.conditioning)
        summary = str(fragment.get("feature_summary") or resolved.conditioning.feature_summary)

    scope_kind = getattr(binding.scope, "kind", "composition")
    fp = resolved.provenance.source_fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN]
    prov = ReferenceFeatureProvenanceEntry(
        project_id=binding.project_id,
        revision_id=binding.revision_id,
        scope_kind=str(scope_kind),
        fingerprint_prefix=fp,
        dimensions=[],
        unavailable_codes=[],
    ).model_dump(mode="json")
    return summary, prov, list(resolved.warning_codes or [])
