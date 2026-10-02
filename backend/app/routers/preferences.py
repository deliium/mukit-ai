"""HTTP routes for explicit preference learning.

Inspect and reset stay available when the deployment flag is off. A choice
is inserted only when that flag and the user collection switch are both on.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Query

from app.preference_schemas import (
    PreferenceChoiceRecordRequest,
    PreferenceChoiceSummaryV1,
    PreferenceChoiceV1,
    PreferenceLearningError,
    PreferenceRankRequest,
    PreferenceRankingV1,
    PreferenceSettingsResponse,
    PreferenceSettingsUpdate,
    map_preference_error_to_http,
)
from app.preference_settings import preference_learning_enabled
from app.routers.collaboration_guard import enforce_current
from app.services import preference_store as store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/preferences", tags=["preferences"])


def _raise_preference(exc: PreferenceLearningError) -> None:
    status, detail = map_preference_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


def _log_route(method: str, path: str, status: int, started: float) -> None:
    logger.info(
        "preference route",
        extra={
            "method": method,
            "path": path,
            "status": status,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )


def _authorize_pending(surface: str) -> None:
    pending = store.get_pending(surface)
    project_id = None if pending is None else pending.context.project_id
    if project_id:
        enforce_current(project_id, "read")


@router.get("/settings", response_model=PreferenceSettingsResponse)
def get_preference_settings() -> PreferenceSettingsResponse:
    started = time.perf_counter()
    status = 200
    try:
        stored = store.get_settings()
        logger.debug(
            "preference settings route",
            extra={"collection_enabled": stored.collection_enabled, "ranking_enabled": stored.ranking_enabled},
        )
        return PreferenceSettingsResponse(
            collection_enabled=stored.collection_enabled,
            ranking_enabled=stored.ranking_enabled,
            feature_available=preference_learning_enabled(),
            updated_at=stored.updated_at,
        )
    except HTTPException as exc:
        status = exc.status_code
        raise
    finally:
        _log_route("GET", "/preferences/settings", status, started)


@router.put("/settings", response_model=PreferenceSettingsResponse)
def put_preference_settings(body: PreferenceSettingsUpdate) -> PreferenceSettingsResponse:
    started = time.perf_counter()
    status = 200
    try:
        try:
            stored = store.put_settings(
                collection_enabled=body.collection_enabled,
                ranking_enabled=body.ranking_enabled,
            )
        except PreferenceLearningError as exc:
            status = exc.http_status
            _raise_preference(exc)
        return PreferenceSettingsResponse(
            collection_enabled=stored.collection_enabled,
            ranking_enabled=stored.ranking_enabled,
            feature_available=preference_learning_enabled(),
            updated_at=stored.updated_at,
        )
    except HTTPException as exc:
        status = exc.status_code
        raise
    finally:
        _log_route("PUT", "/preferences/settings", status, started)


@router.get("/choices", response_model=list[PreferenceChoiceSummaryV1])
def list_preference_choices(
    limit: int = Query(default=20, ge=1, le=50),
) -> list[PreferenceChoiceSummaryV1]:
    started = time.perf_counter()
    status = 200
    try:
        items = store.list_choices(limit=limit)
        logger.debug("preference choices route", extra={"count": len(items)})
        return items
    except HTTPException as exc:
        status = exc.status_code
        raise
    finally:
        _log_route("GET", "/preferences/choices", status, started)


@router.get("/choices/{choice_id}", response_model=PreferenceChoiceV1)
def get_preference_choice(choice_id: str) -> PreferenceChoiceV1:
    started = time.perf_counter()
    status = 200
    try:
        try:
            choice = store.get_choice(choice_id)
        except PreferenceLearningError as exc:
            status = exc.http_status
            _raise_preference(exc)
        return choice
    except HTTPException as exc:
        status = exc.status_code
        raise
    finally:
        _log_route("GET", "/preferences/choices/{choice_id}", status, started)


@router.delete("/data")
def reset_preference_data() -> dict[str, int]:
    started = time.perf_counter()
    status = 200
    try:
        deleted = store.reset_preference_data()
        return {"deleted_choice_count": deleted}
    except HTTPException as exc:
        status = exc.status_code
        raise
    finally:
        _log_route("DELETE", "/preferences/data", status, started)


@router.post("/choices", response_model=PreferenceChoiceV1)
def record_preference_choice(body: PreferenceChoiceRecordRequest) -> PreferenceChoiceV1:
    started = time.perf_counter()
    status = 200
    try:
        _authorize_pending(body.surface)
        try:
            choice = store.record_choice(
                surface=body.surface,
                chosen_candidate_id=body.chosen_candidate_id,
            )
        except PreferenceLearningError as exc:
            status = exc.http_status
            _raise_preference(exc)
        logger.debug("preference choice recorded route", extra={"choice_id": choice.id})
        return choice
    except HTTPException as exc:
        status = exc.status_code
        raise
    finally:
        _log_route("POST", "/preferences/choices", status, started)


@router.post("/rank", response_model=PreferenceRankingV1)
def rank_preference_candidates(body: PreferenceRankRequest) -> PreferenceRankingV1:
    started = time.perf_counter()
    status = 200
    try:
        _authorize_pending(body.surface)
        try:
            ranking = store.score_pending(surface=body.surface, candidate_ids=body.candidate_ids)
        except PreferenceLearningError as exc:
            status = exc.http_status
            _raise_preference(exc)
        return ranking
    except HTTPException as exc:
        status = exc.status_code
        raise
    finally:
        _log_route("POST", "/preferences/rank", status, started)
