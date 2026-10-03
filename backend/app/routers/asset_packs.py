"""HTTP routes for soundtrack / production asset packs.

Plan preview never creates projects. Generate/regenerate are explicit POSTs.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.asset_pack_schemas import (
    ASSET_PACK_ERROR_MESSAGES,
    AssetPackCreateRequest,
    AssetPackError,
    AssetPackGenerateRequest,
    AssetPackPlanPreviewRequest,
    AssetPackPlanV1,
    AssetPackRegenerateRequest,
    AssetPackSlotRecordV1,
    AssetPackV1,
)
from app.services.asset_pack_plan import compile_asset_pack_plan
from app.services.asset_pack_store import (
    AssetPackRecord,
    create_pack,
    get_pack,
    list_packs,
    list_slot_records,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["asset-packs"])


class AssetPackPlanPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: AssetPackPlanV1


class AssetPackGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack: AssetPackV1
    plan: AssetPackPlanV1


class AssetPackListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    packs: list[AssetPackV1] = Field(default_factory=list)


class AssetPackSlotsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str
    slots: list[AssetPackSlotRecordV1] = Field(default_factory=list)


def _http_detail(exc: AssetPackError) -> dict[str, Any]:
    return {
        "code": exc.code,
        "message": exc.message or ASSET_PACK_ERROR_MESSAGES.get(exc.code, exc.code),
    }


def _raise(exc: AssetPackError) -> None:
    raise HTTPException(status_code=exc.http_status, detail=_http_detail(exc)) from exc


def _pack_response(record: AssetPackRecord) -> AssetPackGetResponse:
    return AssetPackGetResponse(pack=record.body, plan=record.plan)


@router.post(
    "/asset-packs/plan/preview",
    response_model=AssetPackPlanPreviewResponse,
)
async def preview_asset_pack_plan(
    request: AssetPackPlanPreviewRequest,
) -> AssetPackPlanPreviewResponse:
    """Compile AssetPackPlan from a brief. No SQLite project create."""
    started = time.perf_counter()
    try:
        plan = compile_asset_pack_plan(request.brief)
    except AssetPackError as exc:
        logger.warning(
            "Asset pack plan preview rejected",
            extra={"code": exc.code},
        )
        _raise(exc)
    logger.info(
        "Asset pack plan preview",
        extra={
            "slot_count": len(plan.slots),
            "digest_prefix": plan.plan_digest[:12],
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return AssetPackPlanPreviewResponse(plan=plan)


@router.post("/asset-packs", response_model=AssetPackGetResponse, status_code=201)
async def create_asset_pack(request: AssetPackCreateRequest) -> AssetPackGetResponse:
    """Persist pack + plan (status planned). Does not generate."""
    started = time.perf_counter()
    try:
        if request.plan is not None:
            plan = request.plan
        else:
            assert request.brief is not None
            plan = compile_asset_pack_plan(request.brief)
        record = create_pack(plan)
    except AssetPackError as exc:
        logger.warning(
            "Asset pack create rejected",
            extra={"code": exc.code},
        )
        _raise(exc)
    logger.info(
        "Asset pack create route",
        extra={
            "pack_id": record.id,
            "status": "planned",
            "slot_count": len(record.slots),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return _pack_response(record)


@router.get("/asset-packs", response_model=AssetPackListResponse)
async def list_asset_packs() -> AssetPackListResponse:
    records = list_packs()
    return AssetPackListResponse(packs=[record.body for record in records])


@router.get("/asset-packs/{pack_id}", response_model=AssetPackGetResponse)
async def get_asset_pack(pack_id: str) -> AssetPackGetResponse:
    try:
        record = get_pack(pack_id)
    except AssetPackError as exc:
        _raise(exc)
    return _pack_response(record)


@router.get("/asset-packs/{pack_id}/slots", response_model=AssetPackSlotsResponse)
async def get_asset_pack_slots(pack_id: str) -> AssetPackSlotsResponse:
    try:
        slots = list_slot_records(pack_id)
    except AssetPackError as exc:
        _raise(exc)
    return AssetPackSlotsResponse(pack_id=pack_id, slots=slots)


@router.post("/asset-packs/{pack_id}/generate", response_model=AssetPackGetResponse)
async def generate_asset_pack(
    pack_id: str,
    request: AssetPackGenerateRequest,
) -> AssetPackGetResponse:
    """Explicit synchronous generate. Implemented in Task 3."""
    from app.services.asset_pack_service import generate_pack

    try:
        record = await generate_pack(
            pack_id,
            expected_plan_digest=request.expected_plan_digest,
            expected_revision=request.expected_revision,
        )
    except AssetPackError as exc:
        _raise(exc)
    return _pack_response(record)


@router.post("/asset-packs/{pack_id}/regenerate", response_model=AssetPackGetResponse)
async def regenerate_asset_pack(
    pack_id: str,
    request: AssetPackRegenerateRequest,
) -> AssetPackGetResponse:
    """Partial regenerate of selected slots."""
    from app.services.asset_pack_service import regenerate_pack_slots

    try:
        record = await regenerate_pack_slots(
            pack_id,
            slot_ids=request.slot_ids,
            expected_revision=request.expected_revision,
        )
    except AssetPackError as exc:
        _raise(exc)
    return _pack_response(record)
