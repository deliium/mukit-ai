"""Embedding group-slice affinity for reference feature reports.

Affinity is advisory distributional similarity only — never musical quality.
Omit entirely when no compare target is provided.
"""

from __future__ import annotations

import logging
from typing import Any

from app.composition_schemas import CompositionV2
from app.embeddings.errors import EmbeddingScopeError, ReferenceResolveError
from app.embeddings.features import (
    DIMENSION_TO_FEATURE_GROUP,
    FEATURE_GROUP_SLICES,
    FeatureGroupId,
    extract_symbolic_features_v1,
    feature_group_vector,
)
from app.embeddings.schemas import CompositionEmbedScope, StyleReferenceRequest
from app.embeddings.settings import EMBEDDING_DEFAULT_MODEL_ID, EMBEDDING_PROFILE_ID
from app.embeddings.vector import cosine_similarity, l2_normalize
from app.reference_feature_schemas import (
    DimensionId,
    ReferenceFeatureAffinity,
    ReferenceFeatureCompareTarget,
)
from app.services.composition_style_conditioning import load_style_reference_composition

logger = logging.getLogger(__name__)


def compute_reference_feature_affinity(
    *,
    reference_composition: CompositionV2,
    reference_scope: CompositionEmbedScope,
    compare_to: ReferenceFeatureCompareTarget,
    requested_dimensions: list[DimensionId] | None = None,
) -> tuple[ReferenceFeatureAffinity | None, list[str]]:
    """Compute group/dimension affinity vs compare target.

    Returns ``(None, warnings)`` when compare material cannot be embedded.
    Always sets ``musical_quality_claim=False``.
    """
    warnings: list[str] = []
    try:
        ref_features, _ = extract_symbolic_features_v1(
            reference_composition, reference_scope
        )
    except EmbeddingScopeError:
        warnings.append("reference_feature_affinity_unsupported")
        return None, warnings

    compare_binding = StyleReferenceRequest(
        project_id=compare_to.project_id,
        revision_id=compare_to.revision_id,
        composition=compare_to.composition,
        scope=compare_to.scope,
    )
    try:
        compare_composition = load_style_reference_composition(compare_binding)
    except ReferenceResolveError:
        warnings.append("reference_feature_affinity_unsupported")
        return None, warnings

    try:
        cmp_features, _ = extract_symbolic_features_v1(
            compare_composition, compare_to.scope
        )
    except EmbeddingScopeError:
        warnings.append("reference_feature_affinity_unsupported")
        return None, warnings

    per_group: dict[FeatureGroupId, float] = {}
    for group_id in FEATURE_GROUP_SLICES:
        ref_slice = l2_normalize(feature_group_vector(ref_features, group_id))
        cmp_slice = l2_normalize(feature_group_vector(cmp_features, group_id))
        score = float(cosine_similarity(ref_slice, cmp_slice))
        per_group[group_id] = round(score, 6)
        logger.debug(
            "Reference feature group affinity",
            extra={"group_id": group_id, "score": round(score, 4)},
        )

    per_dimension: dict[DimensionId, float] = {}
    dims = requested_dimensions or list(DIMENSION_TO_FEATURE_GROUP.keys())
    for dim in dims:
        group = DIMENSION_TO_FEATURE_GROUP.get(dim)
        if group is None:
            warnings.append("reference_feature_affinity_unsupported")
            continue
        per_dimension[dim] = per_group[group]  # type: ignore[index]

    affinity = ReferenceFeatureAffinity(
        model_id=EMBEDDING_DEFAULT_MODEL_ID,
        profile_id=EMBEDDING_PROFILE_ID,
        per_group=per_group,  # type: ignore[arg-type]
        per_dimension=per_dimension,
        musical_quality_claim=False,
    )
    logger.info(
        "Reference feature affinity computed",
        extra={
            "group_count": len(per_group),
            "dimension_count": len(per_dimension),
            "musical_quality_claim": False,
        },
    )
    return affinity, warnings
