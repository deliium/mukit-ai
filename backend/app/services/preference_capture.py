"""Stash a preference ballot after a development or arrangement preview.

The preview services do not import this module. A capture failure does not
change the preview HTTP status.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.composition_schemas import CompositionV2
from app.preference_schemas import (
    PreferenceCandidateFeaturesV1,
    PreferenceContextV1,
    PreferencePendingBallotV1,
)
from app.services.preference_features import project_preference_features
from app.services.preference_store import effective_collection, effective_ranking, stash_pending

logger = logging.getLogger(__name__)


def preference_request_digest(
    *,
    surface: str,
    operation: str,
    source_fingerprint: str,
    candidate_ids: list[str],
    profile_id: str | None,
    profile_strength: str | None,
) -> str:
    """SHA-256 of the ballot context. The digest input has no prompt and no notes."""
    payload = {
        "candidate_ids": sorted(candidate_ids),
        "operation": operation,
        "profile_id": profile_id,
        "profile_strength": profile_strength,
        "source_fingerprint": source_fingerprint,
        "surface": surface,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def capture_preference_ballot(
    *,
    surface: str,
    operation: str,
    source_fingerprint: str,
    active_project_id: str | None,
    profile_id: str | None,
    profile_strength: str | None,
    candidates: list[tuple[str, str, int, CompositionV2]],
) -> None:
    """Write a pending ballot when collection or ranking is effectively on.

    ``candidates`` entries are ``(candidate_id, fingerprint, original_index, composition)``.
    A candidate whose feature extraction raises is omitted. Fewer than two
    remaining vectors writes nothing.
    """
    try:
        logger.debug(
            "preference capture start",
            extra={"surface": surface, "candidate_count": len(candidates)},
        )
        if not effective_collection() and not effective_ranking():
            logger.debug("preference capture skipped", extra={"surface": surface, "code": "gates_off"})
            return
        extracted: list[PreferenceCandidateFeaturesV1] = []
        for candidate_id, fingerprint, original_index, composition in candidates:
            try:
                vector = project_preference_features(composition, candidate_id=candidate_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "preference feature omitted",
                    extra={"candidate_id": candidate_id, "error_type": type(exc).__name__},
                )
                continue
            extracted.append(
                PreferenceCandidateFeaturesV1(
                    candidate_id=candidate_id,
                    candidate_fingerprint=fingerprint,
                    original_index=original_index,
                    feature_vector=vector,
                )
            )
        if len(extracted) < 2:
            logger.debug(
                "preference capture skipped",
                extra={"surface": surface, "candidate_count": len(extracted)},
            )
            return
        ballot = PreferencePendingBallotV1(
            context=PreferenceContextV1(
                surface=surface,  # type: ignore[arg-type]
                operation=str(operation)[:64],
                project_id=active_project_id,
                source_fingerprint=source_fingerprint,
                request_digest=preference_request_digest(
                    surface=surface,
                    operation=str(operation),
                    source_fingerprint=source_fingerprint,
                    candidate_ids=[item.candidate_id for item in extracted],
                    profile_id=profile_id,
                    profile_strength=profile_strength,
                ),
                profile_id=profile_id,
                profile_strength=profile_strength,  # type: ignore[arg-type]
            ),
            candidates=extracted,
        )
        stash_pending(ballot)
        logger.info(
            "preference ballot stashed",
            extra={"surface": surface, "candidate_count": len(extracted)},
        )
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None) or "preference_capture_failed"
        logger.warning(
            "preference_capture_failed",
            extra={"code": code, "error_type": type(exc).__name__, "surface": surface},
        )


def _rows_from_candidates(candidates: list[Any]) -> list[tuple[str, str, int, CompositionV2]]:
    return [
        (item.candidate_id, item.candidate_fingerprint, index, item.composition)
        for index, item in enumerate(candidates)
    ]


def capture_development_preview(request: Any, response: Any) -> None:
    """Stash development candidates after the preview object is complete."""
    capture_preference_ballot(
        surface="development",
        operation=str(response.operation),
        source_fingerprint=response.edit_source_fingerprint,
        active_project_id=request.active_project_id,
        profile_id=request.profile_id,
        profile_strength=request.profile_strength,
        candidates=_rows_from_candidates(response.candidates),
    )


def capture_arrangement_preview(request: Any, response: Any) -> None:
    """Stash arrangement successes. A rejected attempt is not a ballot entry."""
    capture_preference_ballot(
        surface="arrangement",
        operation=str(response.operation),
        source_fingerprint=response.edit_source_fingerprint,
        active_project_id=request.active_project_id,
        profile_id=request.profile_id,
        profile_strength=request.profile_strength,
        candidates=_rows_from_candidates(response.candidates),
    )
