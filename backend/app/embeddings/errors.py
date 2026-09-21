"""Domain errors and closed code registries for symbolic embeddings."""

from __future__ import annotations

import logging
from typing import Any, Literal


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Closed error / warning code registries
# ---------------------------------------------------------------------------

EMBEDDING_ERROR_CODES: dict[str, str] = {
    "embed_scope_invalid": "Embed scope is missing required fields or fails identity checks.",
    "embed_empty_scope": "The selected scope contains no overlapping note events.",
    "embed_invalid_composition": "Composition is not a valid composition.v2 document for embedding.",
    "embed_dims_exceeded": "Embedding vector length exceeds configured maximum dims.",
    "embedding_model_unavailable": "No ready symbolic embedding model is registered for this operation.",
    "embedding_text_stub_rejected": "Text embedding stubs cannot produce musical similarity.",
    "similarity_index_empty": "Similarity corpus has no indexable entries for the requested scopes.",
    "similarity_query_invalid": "Similarity query is missing required fields or exceeds caps.",
    "similarity_corpus_forbidden": "Requested corpus kind is disabled by configuration.",
    "reference_not_found": "Musical reference project, revision, or scope could not be resolved.",
    "reference_fingerprint_mismatch": "Reference composition fingerprint no longer matches the requested scope.",
    "reference_scope_invalid": "Reference embed scope is invalid or empty.",
    "conditioning_mode_invalid": "Style conditioning mode is not supported.",
    "dataset_export_forbidden": "Exporting user projects into DATASET_ROOT is forbidden unless explicitly enabled.",
    "cache_unavailable": "Embedding cache backend is unavailable or misconfigured.",
    "embedding_internal_error": "Unexpected embedding failure (sanitized).",
}

EmbeddingErrorCode = Literal[
    "embed_scope_invalid",
    "embed_empty_scope",
    "embed_invalid_composition",
    "embed_dims_exceeded",
    "embedding_model_unavailable",
    "embedding_text_stub_rejected",
    "similarity_index_empty",
    "similarity_query_invalid",
    "similarity_corpus_forbidden",
    "reference_not_found",
    "reference_fingerprint_mismatch",
    "reference_scope_invalid",
    "conditioning_mode_invalid",
    "dataset_export_forbidden",
    "cache_unavailable",
    "embedding_internal_error",
]

EMBEDDING_WARNING_CODES: dict[str, str] = {
    "reference_similarity_delta": "Candidate vs reference cosine distance is advisory only (not musical quality).",
    "similarity_stale_hit_filtered": "One or more similarity hits were dropped due to fingerprint mismatch.",
    "similarity_result_truncated": "Similarity results were truncated to the configured top-k cap.",
    "embedding_cache_miss": "Embedding was recomputed after a cache miss.",
    "projection_none": "Hybrid projection is none; handcrafted features used as-is.",
    "tokenizer_labels_skipped": "Coarse tokenizer labels were not inferred confidently from features.",
    "empty_index_partial": "Similarity search ran against a partially empty corpus.",
}

EmbeddingWarningCode = Literal[
    "reference_similarity_delta",
    "similarity_stale_hit_filtered",
    "similarity_result_truncated",
    "embedding_cache_miss",
    "projection_none",
    "tokenizer_labels_skipped",
    "empty_index_partial",
]


class EmbeddingError(Exception):
    """Domain error for embedding / similarity / reference failures."""

    def __init__(
        self,
        code: EmbeddingErrorCode,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        resolved = message or EMBEDDING_ERROR_CODES.get(code, code)
        super().__init__(resolved)
        self.code: EmbeddingErrorCode = code
        self.message = resolved
        self.details = details or {}
        logger.debug(
            "EmbeddingError constructed",
            extra={
                "error_code": code,
                "detail_keys": sorted(self.details.keys()),
            },
        )

    def to_public_dict(self) -> dict[str, Any]:
        """Sanitized body for HTTP mapping (no vectors / compositions)."""
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            # Cap detail values; never include vector payloads.
            safe: dict[str, Any] = {}
            for key, value in self.details.items():
                if key in {"vector", "vectors", "events", "composition", "prompt"}:
                    continue
                if isinstance(value, str) and len(value) > 200:
                    safe[key] = value[:200]
                else:
                    safe[key] = value
            if safe:
                body["details"] = safe
        return body


class EmbeddingScopeError(EmbeddingError):
    """Invalid or empty embed scope."""


class EmbeddingModelUnavailableError(EmbeddingError):
    """No ready symbolic embedding model."""


class SimilarityIndexError(EmbeddingError):
    """Similarity corpus / query failures."""


class ReferenceResolveError(EmbeddingError):
    """Musical reference resolution failures."""


class DatasetExportForbiddenError(EmbeddingError):
    """Refuse silent project → DATASET_ROOT export."""

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None) -> None:
        super().__init__("dataset_export_forbidden", message, details=details)
