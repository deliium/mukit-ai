"""Full-document edit fingerprints for composition development concurrency.

Unlike analysis fingerprints (which intentionally omit expression/markers),
edit fingerprints hash every persisted Composition V2 field so any authored
change invalidates candidate apply.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.composition_development_schemas import (
    DEVELOPMENT_ALGORITHM_VERSION,
    EDIT_FINGERPRINT_LOG_PREFIX_LEN,
    EDIT_FINGERPRINT_PROFILE,
    CompositionDevelopmentPreviewRequest,
    normalized_development_request_fingerprint_payload,
)
from app.composition_schemas import CompositionV2


logger = logging.getLogger(__name__)


def edit_fingerprint_log_prefix(fingerprint: str) -> str:
    """Short prefix safe for structured logs."""
    return fingerprint[:EDIT_FINGERPRINT_LOG_PREFIX_LEN]


def canonical_edit_json_dumps(value: Any) -> str:
    """Compact ASCII JSON with recursively sorted object keys; array order preserved."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def full_document_edit_projection(composition: CompositionV2) -> dict[str, Any]:
    """Project the complete persisted V2 document plus fingerprint profile metadata.

    Uses validated ``model_dump(mode=\"json\")`` so defaults (expression, motifs, …)
    participate in identity. Array order is preserved; object keys are sorted at dump time.
    """
    document = composition.model_dump(mode="json")
    return {
        "fingerprint_profile": EDIT_FINGERPRINT_PROFILE,
        "document": document,
    }


def composition_edit_fingerprint(composition: CompositionV2) -> str:
    """Return SHA-256 hex digest of the full-document edit projection."""
    projection = full_document_edit_projection(composition)
    encoded = canonical_edit_json_dumps(projection)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    note_count = sum(len(track.events) for track in composition.tracks)
    logger.debug(
        "Composition edit fingerprint computed",
        extra={
            "fingerprint_profile": EDIT_FINGERPRINT_PROFILE,
            "fingerprint_prefix": edit_fingerprint_log_prefix(digest),
            "encoded_bytes": len(encoded),
            "track_count": len(composition.tracks),
            "section_count": len(composition.sections),
            "note_count": note_count,
            "marker_count": len(composition.markers),
            "motif_count": len(composition.motifs),
            "prefix_len": EDIT_FINGERPRINT_LOG_PREFIX_LEN,
        },
    )
    return digest


def derive_development_candidate_id(
    *,
    edit_source_fingerprint: str,
    request: CompositionDevelopmentPreviewRequest,
    candidate_fingerprint: str,
    candidate_ordinal: int,
    algorithm_version: str = DEVELOPMENT_ALGORITHM_VERSION,
) -> str:
    """Derive a stable candidate id from source, operation, candidate, and algorithm version.

    ``candidate_ordinal`` is included so independently generated candidates with
    identical realized documents still receive distinct ids within one request.
    """
    if candidate_ordinal < 1:
        raise ValueError("candidate_ordinal must be >= 1")

    payload = {
        "algorithm_version": algorithm_version,
        "candidate_fingerprint": candidate_fingerprint,
        "candidate_ordinal": candidate_ordinal,
        "edit_source_fingerprint": edit_source_fingerprint,
        "request": normalized_development_request_fingerprint_payload(request),
    }
    encoded = canonical_edit_json_dumps(payload)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    logger.debug(
        "Development candidate id derived",
        extra={
            "algorithm_version": algorithm_version,
            "candidate_ordinal": candidate_ordinal,
            "edit_source_prefix": edit_fingerprint_log_prefix(edit_source_fingerprint),
            "candidate_prefix": edit_fingerprint_log_prefix(candidate_fingerprint),
            "candidate_id_prefix": edit_fingerprint_log_prefix(digest),
            "encoded_bytes": len(encoded),
        },
    )
    return digest
