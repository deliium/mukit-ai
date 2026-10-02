"""Explicit train-eligibility gate for a personal composer.

Provenance arrives on the train request. A stored score does not imply
permission to train.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from pydantic import ValidationError

from app.dataset.provenance import compute_train_eligible
from app.dataset.schemas import DatasetProvenance
from app.personal_composer_schemas import PersonalComposerError

logger = logging.getLogger(__name__)


def require_selected_projects(project_ids: list[str] | None) -> list[str]:
    """Refuse an empty selection before any project is loaded."""
    if not project_ids:
        logger.warning(
            "Personal composer rights refused",
            extra={"code": "personal_projects_required"},
        )
        raise PersonalComposerError(
            "personal_projects_required",
            "Select at least one project to train.",
            http_status=422,
        )
    return list(project_ids)


def evaluate_project_rights(
    project_id: str,
    raw: Mapping[str, Any] | None,
) -> DatasetProvenance:
    """Build provenance and require ``compute_train_eligible``.

    A refusal includes the project id and not the score.
    """
    try:
        provenance = DatasetProvenance.model_validate(dict(raw or {}))
    except ValidationError as exc:
        logger.warning(
            "Personal composer rights refused",
            extra={"code": "personal_rights_refused", "project_id": project_id},
        )
        raise PersonalComposerError(
            "personal_rights_refused",
            "That project is not eligible to train on.",
            http_status=422,
            details={"project_id": project_id},
        ) from exc
    if not compute_train_eligible(provenance):
        logger.warning(
            "Personal composer rights refused",
            extra={"code": "personal_rights_refused", "project_id": project_id},
        )
        raise PersonalComposerError(
            "personal_rights_refused",
            "That project is not eligible to train on.",
            http_status=422,
            details={"project_id": project_id},
        )
    logger.debug(
        "Personal composer rights accepted",
        extra={"project_id": project_id, "status": provenance.status},
    )
    return provenance
