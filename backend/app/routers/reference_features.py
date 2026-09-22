"""HTTP routes for reference feature analysis (``/reference-features``).

Dedicated router — do not add these routes to ``embeddings.py``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.reference_feature_schemas import (
    ReferenceFeatureAnalyzeRequest,
    ReferenceFeatureAnalyzeResponse,
    ReferenceFeatureError,
    map_reference_feature_error_to_http,
)
from app.services.reference_feature_analyze import analyze_reference_features

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reference-features", tags=["reference-features"])


def _raise_reference_feature_error(exc: ReferenceFeatureError) -> None:
    status, detail = map_reference_feature_error_to_http(exc)
    logger.warning(
        "Reference feature client error",
        extra={"error_code": exc.code, "http_status": status},
    )
    raise HTTPException(status_code=status, detail=detail) from exc


@router.post("/analyze", response_model=ReferenceFeatureAnalyzeResponse)
def analyze_reference_features_route(
    body: ReferenceFeatureAnalyzeRequest,
) -> ReferenceFeatureAnalyzeResponse:
    """Analyze a musical reference into ``reference.features.v1`` (derived only)."""
    try:
        report = analyze_reference_features(body)
    except ReferenceFeatureError as exc:
        _raise_reference_feature_error(exc)
    except ValidationError as exc:
        logger.warning(
            "Reference feature analyze validation failed",
            extra={"error_type": "ValidationError", "error_count": exc.error_count()},
        )
        raise HTTPException(
            status_code=422,
            detail={
                "code": "reference_feature_invalid",
                "message": "Reference feature request failed validation.",
            },
        ) from exc

    logger.info(
        "Reference features analyze route",
        extra={
            "requested_count": len(report.requested_dimensions),
            "dimension_count": len(report.dimensions),
            "unavailable_count": len(report.unavailable),
            "scope_kind": getattr(report.scope, "kind", None),
            "fingerprint_prefix": report.source.source_fingerprint[:12],
            "has_affinity": report.embedding_affinity is not None,
        },
    )
    return ReferenceFeatureAnalyzeResponse(
        report=report,
        warning_codes=list(report.warning_codes),
    )
