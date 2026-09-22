"""HTTP routes for session-only Music Evaluation Engine (``POST /critique/evaluate``)."""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.ai_agents.schemas import AgentCritiqueV1
from app.critique_schemas import (
    CritiqueError,
    CritiqueScopeBars,
    CritiqueScopeComposition,
    CritiqueScopeSection,
    CritiqueScopeTrack,
    map_critique_error_to_http,
)
from app.services.composition_critique import evaluate_composition
from app.services.llm_composition_critique import run_model_critique
from app.services.composition_critique_scope import resolve_critique_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/critique", tags=["critique"])

CritiqueHttpScope = (
    CritiqueScopeComposition
    | CritiqueScopeSection
    | CritiqueScopeTrack
    | CritiqueScopeBars
)


class CritiqueEvaluateHttpRequest(BaseModel):
    """Read-only evaluate body — never writes projects / SQLite."""

    model_config = ConfigDict(extra="forbid")

    composition: dict[str, Any]
    scope: CritiqueHttpScope = Field(default_factory=CritiqueScopeComposition)
    include_model_critique: bool = False
    revise_on_technical: bool = False
    requested_climax_section_index: int | None = Field(default=None, ge=0)
    brief_excerpt: str | None = Field(default=None, max_length=200)


class CritiqueEvaluateHttpResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    critique: AgentCritiqueV1
    analysis_status: str | None = None
    analysis_warning_count: int = 0
    mutates_composition: Literal[False] = False


@router.post("/evaluate", response_model=CritiqueEvaluateHttpResponse)
async def evaluate_critique_route(
    request: CritiqueEvaluateHttpRequest,
) -> CritiqueEvaluateHttpResponse:
    """Evaluate composition musically; returns session critique only (no DB writes)."""
    started = time.perf_counter()
    scope_kind = getattr(request.scope, "kind", "composition")
    try:
        # Resolve scope early for model path; engine re-resolves safely.
        from app.composition_schemas import CompositionV2

        composition = CompositionV2.model_validate(request.composition)
        resolved = resolve_critique_scope(composition, request.scope)
        model_findings, model_status = run_model_critique(
            analysis_report=None,
            resolved=resolved,
            brief_excerpt=request.brief_excerpt,
            include_model_critique=request.include_model_critique,
        )
        result = evaluate_composition(
            composition,
            scope=request.scope,
            revise_on_technical=request.revise_on_technical,
            requested_climax_section_index=request.requested_climax_section_index,
            brief_text=request.brief_excerpt,
            extra_findings=model_findings,
            model_critique_status=model_status,
        )
    except CritiqueError as exc:
        status, detail = map_critique_error_to_http(exc)
        raise HTTPException(status_code=status, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Critique evaluate unexpected error",
            extra={"reason": type(exc).__name__, "scope_kind": scope_kind},
        )
        raise HTTPException(
            status_code=422,
            detail={
                "code": "critique_invalid_composition",
                "message": "Composition could not be evaluated",
            },
        ) from exc

    duration_ms = (time.perf_counter() - started) * 1000.0
    counts = result.critique.stratum_counts
    logger.info(
        "Critique evaluate route",
        extra={
            "scope_kind": scope_kind,
            "hard_constraint": counts.hard_constraint,
            "technical": counts.technical,
            "stylistic": counts.stylistic,
            "subjective": counts.subjective,
            "recommendation": result.critique.recommendation.value,
            "duration_ms": round(duration_ms, 2),
            "model_critique_status": result.critique.model_critique_status,
        },
    )
    analysis_status = (
        result.analysis_report.status if result.analysis_report is not None else None
    )
    warning_count = (
        len(result.analysis_report.warnings) if result.analysis_report is not None else 0
    )
    return CritiqueEvaluateHttpResponse(
        critique=result.critique,
        analysis_status=analysis_status,
        analysis_warning_count=warning_count,
    )
