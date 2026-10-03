"""Ensemble arbitration HTTP surface. Status is never on ``/ready``.

Preview is flag-gated only — no Collab C / ``authorize_studio_lab``. Opening
status/strategies never starts fan-out.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from app.ensemble_arbitration_schemas import (
    EnsembleArbitrationError,
    map_ensemble_error_to_http,
    reject_ensemble_payload,
)
from app.ensemble_arbitration_settings import load_ensemble_arbitration_settings
from app.services.ensemble_arbitration_service import (
    preview_from_raw,
    status_document,
    strategies_catalog,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ensemble-arbitration"])


def _raise_ensemble(exc: EnsembleArbitrationError) -> None:
    status, detail = map_ensemble_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


@router.get("/ensemble/arbitration/status")
def get_ensemble_arbitration_status() -> dict[str, Any]:
    """Always 200. Not wired into ``/ready`` or ``/health``."""
    body = status_document()
    return body.model_dump(mode="json")


@router.get("/ensemble/arbitration/strategies")
def get_ensemble_arbitration_strategies() -> dict[str, Any]:
    """Closed strategy/selection catalogs. Readable when disabled."""
    body = strategies_catalog()
    return body.model_dump(mode="json")


@router.post("/ensemble/arbitration/preview")
async def post_ensemble_arbitration_preview(request: Request) -> dict[str, Any]:
    """Session-only multi-model arbitration preview. Never writes notes."""
    started = time.perf_counter()
    settings = load_ensemble_arbitration_settings()
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=422,
            detail={
                "code": "ensemble_payload_refused",
                "message": "Ensemble preview body must be JSON.",
            },
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "ensemble_payload_refused",
                "message": "Ensemble preview body must be a JSON object.",
            },
        )

    try:
        reject_ensemble_payload(payload)
        report = preview_from_raw(payload, settings=settings)
    except EnsembleArbitrationError as exc:
        logger.info(
            "Ensemble arbitration preview refused",
            extra={
                "code": exc.code,
                "status_code": exc.http_status,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        _raise_ensemble(exc)
    except ValidationError as exc:
        logger.debug(
            "Ensemble arbitration preview schema rejected",
            extra={"field_name": "body", "code": "ensemble_selection_invalid"},
        )
        raise HTTPException(
            status_code=422,
            detail={
                "code": "ensemble_selection_invalid",
                "message": "Ensemble arbitration request failed validation.",
            },
        ) from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Ensemble arbitration preview route ok",
        extra={
            "survivor_count": len(report.candidates),
            "rejected_count": len(report.rejected_attempts),
            "ranking_applied": report.ranking_applied,
            "duration_ms": duration_ms,
        },
    )
    return report.model_dump(mode="json")
