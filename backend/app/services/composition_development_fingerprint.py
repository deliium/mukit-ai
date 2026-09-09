"""Development-owned candidate identity helpers.

Composition-level hashing lives in ``composition_edit_fingerprint``; this module
derives development request-scoped candidate ids only.
"""

from __future__ import annotations

import hashlib
import logging

from app.composition_development_schemas import (
    DEVELOPMENT_ALGORITHM_VERSION,
    CompositionDevelopmentPreviewRequest,
    normalized_development_request_fingerprint_payload,
)
from app.services.composition_edit_fingerprint import (
    canonical_edit_json_dumps,
    edit_fingerprint_log_prefix,
)


logger = logging.getLogger(__name__)


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
