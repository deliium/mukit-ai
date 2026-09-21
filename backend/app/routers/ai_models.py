"""AI model discovery routes (`GET /ai/models`). Never expose secrets or weight paths."""

from __future__ import annotations

import logging
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query

from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.errors import ModelNotFoundError
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.registry import get_default_model_id, get_model, list_models, reload_registry
from app.ai_runtime.routing import default_operation_routes
from app.ai_runtime_schemas import AiModelCatalogItem, AiModelsResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai-runtime"])


def _to_catalog_item(descriptor, *, default_model_id: str | None) -> AiModelCatalogItem:
    return AiModelCatalogItem(
        id=descriptor.id,
        display_name=descriptor.display_name,
        provider=descriptor.provider,
        runtime=descriptor.runtime,
        primary_capability=str(descriptor.primary_capability),
        locality=descriptor.locality,
        model_version=descriptor.model_version,
        supported_operations=[str(op) for op in descriptor.supported_operations],
        status=descriptor.status,
        credentials_present=bool(descriptor.health.credentials_present),
        is_default=descriptor.id == default_model_id,
        limits=dict(descriptor.limits or {}),
    )


@router.get("/models", response_model=AiModelsResponse)
async def list_ai_models(
    capability: Annotated[str | None, Query()] = None,
    operation: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
) -> AiModelsResponse:
    logger.info("AI model discovery requested")
    logger.debug(
        "AI model discovery filters",
        extra={"capability": capability, "operation": operation, "status": status},
    )
    reload_registry()
    cap = None
    op = None
    if capability:
        try:
            cap = ModelCapability(capability)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unknown capability: {capability}") from exc
    if operation:
        try:
            op = AiOperation(operation)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unknown operation: {operation}") from exc

    models = list_models(capability=cap, operation=op, status=status)  # type: ignore[arg-type]
    default_id = get_default_model_id()
    items = [_to_catalog_item(m, default_model_id=default_id) for m in models]
    warnings: list[str] = []
    if not any(m.status == "ready" for m in models):
        warnings.append(
            "No ready AI models configured. Set OPENAI_API_KEY or DEEPSEEK_API_KEY, "
            "or enable LLM_FAKE_MODE=1 for credit-free demos/tests."
        )
    return AiModelsResponse(
        models=items,
        default_model_id=default_id,
        operation_defaults=default_operation_routes(),
        warnings=warnings,
    )


@router.get("/models/{model_id:path}", response_model=AiModelCatalogItem)
async def get_ai_model(model_id: str) -> AiModelCatalogItem:
    decoded = unquote(model_id)
    logger.info("AI model detail requested", extra={"model_id": decoded})
    reload_registry()
    try:
        descriptor = get_model(decoded)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_catalog_item(descriptor, default_model_id=get_default_model_id())
