"""Rights-governance registry HTTP surface.

Does not write ``composition.v2`` notes. Status lives on
``GET /rights-governance/status`` only — never ``/ready``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.rights_governance_schemas import (
    RightsEvaluateRequestV1,
    RightsEvaluateResultV1,
    RightsGovernanceError,
    RightsGovernanceStatusV1,
    RightsRegistryEntryV1,
    RightsSourceKind,
    map_legacy_dataset_provenance,
    map_rights_governance_error_to_http,
    parse_rights_registry_entry,
    reject_embedded_note_material,
)
from app.rights_governance_settings import load_rights_governance_settings
from app.services.persistence_secret_guard import PersistenceSecretError
from app.services.rights_governance_policy import evaluate_rights_use
from app.services.rights_governance_store import get_rights_entry, upsert_rights_entry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rights-governance"])

_SOURCE_KINDS: frozenset[str] = frozenset(
    {
        "dataset_item",
        "dataset_source",
        "project",
        "composition_revision",
        "external_file",
        "personal_snapshot_project",
    }
)


def _raise(exc: RightsGovernanceError) -> None:
    status, detail = map_rights_governance_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


def _finish(method: str, source_kind: str, status: int, started: float) -> None:
    logger.info(
        "Rights governance route finished",
        extra={
            "method": method,
            "source_kind": source_kind,
            "status": status,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )


def _validate_kind(kind: str) -> RightsSourceKind:
    if kind not in _SOURCE_KINDS:
        raise RightsGovernanceError(
            "rights_invalid",
            "Unknown rights source kind.",
            http_status=404,
            details={"source_kind": kind},
        )
    return kind  # type: ignore[return-value]


@router.get("/rights-governance/status")
def rights_governance_status() -> RightsGovernanceStatusV1:
    started = time.perf_counter()
    settings = load_rights_governance_settings()
    body = RightsGovernanceStatusV1(
        registry_enabled=True,
        max_entries_per_project=settings.max_entries_per_project,
        max_attribution_chars=settings.max_attribution_chars,
    )
    _finish("GET", "status", 200, started)
    return body


@router.get("/rights-governance/entries/{source_kind}/{source_id}")
def get_rights_governance_entry(source_kind: str, source_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        kind = _validate_kind(source_kind)
        entry = get_rights_entry(kind, source_id)
        if entry is None:
            raise RightsGovernanceError(
                "rights_not_found",
                "Rights registry entry was not found.",
                http_status=404,
            )
        status = 200
        _finish("GET", kind, status, started)
        return entry.model_dump(mode="json")
    except RightsGovernanceError as exc:
        status = exc.http_status
        _finish("GET", source_kind, status, started)
        _raise(exc)
    raise AssertionError("unreachable")


@router.put("/rights-governance/entries/{source_kind}/{source_id}")
def put_rights_governance_entry(
    source_kind: str,
    source_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    status = 500
    try:
        kind = _validate_kind(source_kind)
        if not isinstance(body, dict):
            raise RightsGovernanceError("rights_invalid", "Request body must be an object.")
        reject_embedded_note_material(body)
        payload = dict(body)
        payload["source_kind"] = kind
        payload["source_id"] = source_id
        expected_version = payload.pop("expected_version", None)
        entry = parse_rights_registry_entry(payload)
        stored = upsert_rights_entry(
            entry,
            expected_version=int(expected_version) if expected_version is not None else None,
        )
        status = 200
        _finish("PUT", kind, status, started)
        return stored.model_dump(mode="json")
    except PersistenceSecretError as exc:
        status = 400
        _finish("PUT", source_kind, status, started)
        raise HTTPException(
            status_code=400,
            detail={"code": "forbidden_secret_field", "message": str(exc)},
        ) from exc
    except RightsGovernanceError as exc:
        status = exc.http_status
        _finish("PUT", source_kind, status, started)
        _raise(exc)
    except ValidationError as exc:
        status = 422
        _finish("PUT", source_kind, status, started)
        raise HTTPException(
            status_code=422,
            detail={"code": "rights_invalid", "message": "Rights entry failed validation."},
        ) from exc
    raise AssertionError("unreachable")


@router.post("/rights-governance/evaluate")
def evaluate_rights_governance(body: RightsEvaluateRequestV1) -> RightsEvaluateResultV1:
    started = time.perf_counter()
    status = 500
    try:
        if body.entry is not None:
            entry = body.entry
        else:
            assert body.legacy is not None
            entry = map_legacy_dataset_provenance(
                body.legacy,
                source_kind=body.source_kind or "project",
                source_id=body.source_id or "evaluate",
            )
        result = evaluate_rights_use(entry, body.use)
        status = 200
        _finish("POST", entry.source_kind, status, started)
        return result
    except RightsGovernanceError as exc:
        status = exc.http_status
        _finish("POST", "evaluate", status, started)
        _raise(exc)
    raise AssertionError("unreachable")
