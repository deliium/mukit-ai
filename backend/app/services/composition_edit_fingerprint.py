"""Full-document composition.edit.v1 fingerprints.

Hashes every persisted Composition V2 field so any authored change invalidates
candidate apply. Domain-specific candidate-id derivation lives in development /
arrangement helpers — this module stays composition-level only.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.composition_schemas import CompositionV2


logger = logging.getLogger(__name__)

EDIT_FINGERPRINT_PROFILE = "composition.edit.v1"
EDIT_FINGERPRINT_LOG_PREFIX_LEN = 12


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
