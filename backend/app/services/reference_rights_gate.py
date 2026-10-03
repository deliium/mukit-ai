"""Hard-fail rights gate for reference analyze / conditioning.

Pure resolve + evaluate via rights-governance policy. Does not import
``ai_agents`` or write scores.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

from app.reference_feature_schemas import ReferenceFeatureError
from app.rights_governance_schemas import RightsRegistryEntryV1
from app.services.rights_governance_policy import evaluate_rights_use, resolve_rights_entry
from app.services.rights_governance_store import get_rights_entry

logger = logging.getLogger(__name__)


def assert_reference_rights_allowed(
    *,
    project_id: str | None,
    revision_id: str | None = None,
    rights: Mapping[str, Any] | None = None,
    db_path: Path | str | None = None,
) -> RightsRegistryEntryV1:
    """Part K resolve + ``reference_analyze`` evaluate. Raises ReferenceFeatureError."""
    if project_id:
        source_kind = "project"
        source_id = project_id
        registry_row = get_rights_entry(source_kind, source_id, db_path=db_path)
        if registry_row is None and revision_id:
            registry_row = get_rights_entry(
                "composition_revision",
                f"{project_id}:{revision_id}",
                db_path=db_path,
            )
        entry, resolution = resolve_rights_entry(
            source_kind,
            source_id,
            registry_row=registry_row,
            request_rights=rights,
        )
    else:
        # Inline composition — require explicit attestation (never invent training_allowed).
        if rights is None:
            logger.warning(
                "Reference rights refused",
                extra={"code": "rights_reference_refused", "kind": "inline"},
            )
            raise ReferenceFeatureError(
                "rights_reference_refused",
                "Inline reference analysis requires an explicit rights attestation.",
                http_status=422,
            )
        entry, resolution = resolve_rights_entry(
            "external_file",
            "inline",
            registry_row=None,
            request_rights=rights,
        )

    result = evaluate_rights_use(entry, "reference_analyze")
    if not result.allowed:
        logger.warning(
            "Reference rights refused",
            extra={
                "code": "rights_reference_refused",
                "project_id": project_id,
                "use_policy": entry.use_policy,
                "resolution": resolution,
                "refuse_code": result.code,
            },
        )
        raise ReferenceFeatureError(
            "rights_reference_refused",
            result.message or "This source is not eligible for reference analysis.",
            http_status=422,
            details={
                "project_id": project_id,
                "use_policy": entry.use_policy,
                "rights_code": result.code,
            },
        )
    logger.debug(
        "Reference rights allowed",
        extra={
            "project_id": project_id,
            "use_policy": entry.use_policy,
            "resolution": resolution,
        },
    )
    return entry
