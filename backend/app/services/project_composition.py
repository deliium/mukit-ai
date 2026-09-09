"""Normalize and validate compositions for project open/save paths."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.composition_schemas import (
    COMPOSITION_SCHEMA_VERSION_V1,
    COMPOSITION_SCHEMA_VERSION_V2,
    CompositionV1,
    CompositionV2,
    UnsupportedSchemaVersionError,
)
from .composition_migration import CompositionMigrationError
from .composition_normalizer import CompositionNormalizationError, normalize_composition_json

logger = logging.getLogger(__name__)


class ProjectCompositionError(ValueError):
    """Raised when stored or submitted composition cannot be normalized/validated."""


@dataclass(frozen=True)
class NormalizedProjectComposition:
    composition: CompositionV2
    migration_path: str
    rewritten: bool
    previous_schema_version: str | None


def parse_composition_payload(raw: Any) -> Any:
    """Accept CompositionV1/V2, dict, or JSON string payloads."""
    if raw is None:
        return None
    if isinstance(raw, (CompositionV1, CompositionV2)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(
                "Composition JSON string is not parseable",
                extra={"error_type": type(exc).__name__, "error_detail": str(exc)[:200]},
            )
            raise ProjectCompositionError("Composition JSON is not valid JSON") from exc
    return raw


def _schema_version(payload: Any) -> str | None:
    if isinstance(payload, (CompositionV1, CompositionV2)):
        return payload.schema_version
    if isinstance(payload, dict):
        value = payload.get("schema_version")
        return str(value) if value is not None else None
    return None


def normalize_project_composition(
    raw: Any,
    *,
    project_id: str | None = None,
    persist_canonical: bool = False,
) -> NormalizedProjectComposition:
    """Normalize legacy/V1 JSON to operational Composition V2."""
    payload = parse_composition_payload(raw)
    previous = _schema_version(payload)

    try:
        composition = normalize_composition_json(payload)
    except (
        CompositionNormalizationError,
        CompositionMigrationError,
        UnsupportedSchemaVersionError,
        ValidationError,
        ValueError,
    ) as exc:
        logger.error(
            "Project composition migration/validation failed",
            extra={
                "project_id": project_id,
                "previous_schema_version": previous,
                "error_type": type(exc).__name__,
                "error_detail": str(exc)[:300],
                "code": getattr(exc, "code", None),
            },
        )
        raise ProjectCompositionError(str(exc)[:500]) from exc

    if previous == COMPOSITION_SCHEMA_VERSION_V2 or isinstance(payload, CompositionV2):
        migration_path = "canonical"
    elif previous == COMPOSITION_SCHEMA_VERSION_V1 or isinstance(payload, CompositionV1):
        migration_path = "v1_to_v2"
    else:
        migration_path = "legacy"

    rewritten = bool(persist_canonical and migration_path != "canonical")
    logger.info(
        "Project composition normalized",
        extra={
            "project_id": project_id,
            "migration_path": migration_path,
            "previous_schema_version": previous,
            "schema_version": composition.schema_version,
            "harmony_span_count": len(composition.harmony),
            "rewritten": rewritten,
            "track_count": len(composition.tracks),
            "event_count": sum(len(track.events) for track in composition.tracks),
            "bar_count": composition.bar_count,
        },
    )
    if migration_path == "legacy":
        logger.warning(
            "Composition migrated to composition.v2 via legacy path",
            extra={
                "project_id": project_id,
                "previous_schema_version": previous,
                "schema_version": COMPOSITION_SCHEMA_VERSION_V2,
            },
        )
    elif migration_path == "v1_to_v2":
        logger.warning(
            "Composition migrated to composition.v2",
            extra={
                "project_id": project_id,
                "previous_schema_version": previous,
                "schema_version": COMPOSITION_SCHEMA_VERSION_V2,
            },
        )

    return NormalizedProjectComposition(
        composition=composition,
        migration_path=migration_path,
        rewritten=rewritten,
        previous_schema_version=previous,
    )


def composition_to_storage_json(composition: CompositionV2) -> str:
    return json.dumps(composition.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
