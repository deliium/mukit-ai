"""Progressive realize trust boundary for ``working_draft_composition``.

Agents may emit plans/patches/recommendations, but any update to the working
draft MUST go through these wrappers around deterministic realize/validate
services. Notes are never invented from brief, critique, plan, sections, or
harmony metadata alone.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

from app.ai_agents.errors import WorkingDraftInvalidError
from app.composition_schemas import CompositionV2
from app.services.composition_edit_fingerprint import (
    composition_edit_fingerprint,
    edit_fingerprint_log_prefix,
)
from app.services.composition_validator import validate_composition_integrity

logger = logging.getLogger(__name__)


class RealizeService(StrEnum):
    """Trusted realize paths — only these may replace the working draft."""

    REHARMONIZE_CANDIDATE = "reharmonize_candidate"
    ARRANGEMENT_CANDIDATE = "arrangement_candidate"
    MOTIF_APPLY = "motif_apply"
    VALIDATED_V2 = "validated_v2"


_TRUSTED = frozenset(RealizeService)


def initial_working_draft(source: CompositionV2) -> CompositionV2:
    """Deep-copy source V2 as the starting working draft."""
    draft = source.model_copy(deep=True)
    logger.info(
        "Working draft initialized from source",
        extra={
            "service": "initial_copy",
            "fingerprint_prefix": edit_fingerprint_log_prefix(composition_edit_fingerprint(draft)),
            "track_count": len(draft.tracks),
        },
    )
    return draft


def _event_count(composition: CompositionV2) -> int:
    return sum(len(track.events) for track in composition.tracks)


def _validate_realized_v2(realized: CompositionV2, *, service: RealizeService) -> CompositionV2:
    if realized.schema_version != "composition.v2":
        raise WorkingDraftInvalidError(
            "Realized composition must be composition.v2",
            details={"service": service.value, "schema_version": realized.schema_version},
        )
    try:
        report = validate_composition_integrity(realized, profile="canonical")
    except Exception as exc:  # noqa: BLE001 — map any validator failure
        logger.debug(
            "Working draft validation failed",
            extra={"service": service.value, "reason": type(exc).__name__},
        )
        raise WorkingDraftInvalidError(
            "Realized composition failed integrity validation",
            details={"service": service.value, "reason": type(exc).__name__},
        ) from exc
    if not report.ok:
        raise WorkingDraftInvalidError(
            "Realized composition integrity status failed",
            details={"service": service.value, "error_count": len(report.errors)},
        )
    return realized


def apply_realized_composition(
    *,
    current_draft: CompositionV2,
    realized: CompositionV2,
    service: RealizeService | str,
) -> CompositionV2:
    """Replace working draft with a fingerprintable V2 from a trusted service.

    ``realized`` must already be produced by reharm / arrangement / motif /
    validator trust-boundary services — never by inventing notes from metadata.
    """
    try:
        service_id = (
            service if isinstance(service, RealizeService) else RealizeService(service)
        )
    except ValueError as exc:
        logger.debug(
            "Realize service rejected",
            extra={"service": str(service)},
        )
        raise WorkingDraftInvalidError(
            "Unknown realize service",
            details={"service": str(service)},
        ) from exc
    if service_id not in _TRUSTED:
        logger.debug(
            "Realize service rejected",
            extra={"service": str(service)},
        )
        raise WorkingDraftInvalidError(
            "Unknown realize service",
            details={"service": str(service)},
        )

    before_fp = composition_edit_fingerprint(current_draft)
    validated = _validate_realized_v2(realized, service=service_id)
    after_fp = composition_edit_fingerprint(validated)
    logger.info(
        "Working draft realized",
        extra={
            "service": service_id.value,
            "before_prefix": edit_fingerprint_log_prefix(before_fp),
            "after_prefix": edit_fingerprint_log_prefix(after_fp),
            "event_count": _event_count(validated),
            "fingerprint_changed": before_fp != after_fp,
        },
    )
    return validated.model_copy(deep=True)


def reject_metadata_only_realize(
    *,
    reason: str = "Cannot invent notes from non-playable metadata",
    source: str = "metadata",
    **_ignored: Any,
) -> None:
    """Hard-reject attempts to realize notes from brief/critique/plan/harmony meta.

    Call sites that receive only non-playable artifacts must invoke this instead
    of mutating ``working_draft_composition``.
    """
    logger.debug(
        "Metadata-only realize rejected",
        extra={"reason": reason[:120], "source": source[:64]},
    )
    raise WorkingDraftInvalidError(
        reason,
        details={"source": source, "code_hint": "metadata_only"},
    )


def ensure_playable_source_for_realize(composition: CompositionV2) -> None:
    """Reject empty shells when a realize path claims to apply note material."""
    if _event_count(composition) <= 0:
        raise WorkingDraftInvalidError(
            "Realized composition has no playable events",
            details={"event_count": 0},
        )


logger.debug("progressive_realize module loaded", extra={"trusted_services": sorted(s.value for s in RealizeService)})
