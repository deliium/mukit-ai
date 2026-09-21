"""HTTP routes for symbolic composition embeddings and similarity search."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.embeddings.errors import EmbeddingError
from app.embeddings.schemas import (
    EmbedComputeRequest,
    EmbedComputeResponse,
    ReferenceResolveRequest,
    ReferenceResolveResponse,
    RelatedMotifsRequest,
    RelatedMotifsResponse,
    SimilaritySearchRequest,
    SimilaritySearchResponse,
)
from app.services.composition_embedding import (
    compute_embedding_response,
    search_related_motifs,
    search_similarity,
)
from app.services.composition_style_conditioning import resolve_reference_request


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/embeddings", tags=["embeddings"])

_HTTP_422_CODES = frozenset(
    {
        "embed_scope_invalid",
        "embed_empty_scope",
        "similarity_query_invalid",
        "reference_fingerprint_mismatch",
        "reference_scope_invalid",
    }
)
_HTTP_403_CODES = frozenset(
    {
        "dataset_export_forbidden",
        "similarity_corpus_forbidden",
    }
)


def embedding_error_http_status(code: str) -> int:
    if code == "reference_not_found":
        return 404
    if code == "embedding_model_unavailable":
        return 503
    if code in _HTTP_403_CODES:
        return 403
    if code in _HTTP_422_CODES or (code.startswith("reference_") and code != "reference_not_found"):
        return 422
    return 500


def _map_embedding_error(exc: EmbeddingError) -> HTTPException:
    status = embedding_error_http_status(exc.code)
    detail = exc.to_public_dict()
    if status == 500:
        # Sanitize unexpected / internal failures for clients.
        detail = {
            "code": "embedding_internal_error" if exc.code != "embedding_internal_error" else exc.code,
            "message": "Unexpected embedding failure (sanitized).",
        }
        if exc.code not in {"embedding_internal_error"} and exc.code:
            # Keep original code when it is a known non-sensitive domain code (e.g. stub rejected).
            if exc.code in {
                "embed_invalid_composition",
                "embed_dims_exceeded",
                "embedding_text_stub_rejected",
                "similarity_index_empty",
                "conditioning_mode_invalid",
                "cache_unavailable",
            }:
                detail = exc.to_public_dict()
    return HTTPException(status_code=status, detail=detail)


@router.post("/compute", response_model=EmbedComputeResponse)
async def compute_embedding_route(request: EmbedComputeRequest) -> EmbedComputeResponse:
    """Compute a ``composition.embedding.v1`` card for a composition scope."""
    started = time.perf_counter()
    scope_kind = getattr(request.scope, "kind", "composition")
    logger.info(
        "Embedding compute request started",
        extra={
            "op": "embeddings.compute",
            "scope_kind": scope_kind,
            "model_id": request.model_id,
        },
    )
    try:
        response = compute_embedding_response(
            request.composition,
            request.scope,
            model_id=request.model_id,
        )
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        logger.info(
            "Embedding compute request completed",
            extra={
                "op": "embeddings.compute",
                "model_id": response.embedding.model_id,
                "dims": response.embedding.dims,
                "note_count": response.embedding.note_count,
                "latency_ms": latency_ms,
                "hit_count": 0,
            },
        )
        return response
    except ValidationError as exc:
        logger.warning(
            "Embedding compute validation failed",
            extra={"op": "embeddings.compute", "error_count": exc.error_count()},
        )
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except EmbeddingError as exc:
        logger.warning(
            "Embedding compute domain failure",
            extra={
                "op": "embeddings.compute",
                "error_code": exc.code,
                "http_status": embedding_error_http_status(exc.code),
            },
        )
        raise _map_embedding_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Embedding compute unexpected failure",
            extra={"op": "embeddings.compute", "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=500,
            detail={"code": "embedding_internal_error", "message": "Unexpected embedding failure (sanitized)."},
        ) from exc


@router.post("/similarity", response_model=SimilaritySearchResponse)
async def similarity_search_route(request: SimilaritySearchRequest) -> SimilaritySearchResponse:
    """Rank nearest scopes against a query embedding or composition+scope."""
    started = time.perf_counter()
    corpus_kind = request.query.corpus.kind
    logger.info(
        "Similarity search request started",
        extra={
            "op": "embeddings.similarity",
            "corpus_kind": corpus_kind,
            "top_k": request.query.top_k,
            "model_id": request.model_id,
        },
    )
    try:
        response = search_similarity(request.query, model_id=request.model_id)
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        logger.info(
            "Similarity search request completed",
            extra={
                "op": "embeddings.similarity",
                "model_id": response.model_id,
                "hit_count": len(response.hits),
                "latency_ms": latency_ms,
                "corpus_kind": corpus_kind,
            },
        )
        return response
    except ValidationError as exc:
        logger.warning(
            "Similarity search validation failed",
            extra={"op": "embeddings.similarity", "error_count": exc.error_count()},
        )
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except EmbeddingError as exc:
        logger.warning(
            "Similarity search domain failure",
            extra={
                "op": "embeddings.similarity",
                "error_code": exc.code,
                "http_status": embedding_error_http_status(exc.code),
            },
        )
        raise _map_embedding_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Similarity search unexpected failure",
            extra={"op": "embeddings.similarity", "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=500,
            detail={"code": "embedding_internal_error", "message": "Unexpected embedding failure (sanitized)."},
        ) from exc


@router.post("/related-motifs", response_model=RelatedMotifsResponse)
async def related_motifs_route(request: RelatedMotifsRequest) -> RelatedMotifsResponse:
    """Find related motifs by embedding affinity (not musical quality)."""
    started = time.perf_counter()
    logger.info(
        "Related motifs request started",
        extra={
            "op": "embeddings.related_motifs",
            "motif_id": request.motif_id,
            "top_k": request.top_k,
            "search_cross_project": request.search_cross_project,
            "model_id": request.model_id,
        },
    )
    try:
        response = search_related_motifs(
            request.composition,
            motif_id=request.motif_id,
            occurrence_id=request.occurrence_id,
            top_k=request.top_k,
            search_cross_project=request.search_cross_project,
            model_id=request.model_id,
        )
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        logger.info(
            "Related motifs request completed",
            extra={
                "op": "embeddings.related_motifs",
                "model_id": response.model_id,
                "hit_count": len(response.hits),
                "latency_ms": latency_ms,
            },
        )
        return response
    except ValidationError as exc:
        logger.warning(
            "Related motifs validation failed",
            extra={"op": "embeddings.related_motifs", "error_count": exc.error_count()},
        )
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except EmbeddingError as exc:
        logger.warning(
            "Related motifs domain failure",
            extra={
                "op": "embeddings.related_motifs",
                "error_code": exc.code,
                "http_status": embedding_error_http_status(exc.code),
            },
        )
        raise _map_embedding_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Related motifs unexpected failure",
            extra={"op": "embeddings.related_motifs", "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=500,
            detail={"code": "embedding_internal_error", "message": "Unexpected embedding failure (sanitized)."},
        ) from exc


@router.post("/reference/resolve", response_model=ReferenceResolveResponse)
async def reference_resolve_route(request: ReferenceResolveRequest) -> ReferenceResolveResponse:
    """Resolve a musical style reference into embedding + bounded conditioning."""
    started = time.perf_counter()
    scope_kind = getattr(request.style_reference.scope, "kind", None)
    logger.info(
        "Reference resolve request started",
        extra={
            "op": "embeddings.reference_resolve",
            "conditioning_mode": request.style_reference.mode,
            "project_id": request.style_reference.project_id,
            "scope_kind": scope_kind,
            "model_id": request.model_id,
        },
    )
    try:
        response = resolve_reference_request(request)
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        logger.info(
            "Reference resolve request completed",
            extra={
                "op": "embeddings.reference_resolve",
                "model_id": response.embedding.model_id,
                "dims": response.embedding.dims,
                "note_count": response.embedding.note_count,
                "latency_ms": latency_ms,
                "has_conditioning": response.conditioning is not None,
                "fingerprint_prefix": response.provenance.fingerprint_prefix(),
            },
        )
        return response
    except ValidationError as exc:
        logger.warning(
            "Reference resolve validation failed",
            extra={"op": "embeddings.reference_resolve", "error_count": exc.error_count()},
        )
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except EmbeddingError as exc:
        logger.warning(
            "Reference resolve domain failure",
            extra={
                "op": "embeddings.reference_resolve",
                "error_code": exc.code,
                "http_status": embedding_error_http_status(exc.code),
            },
        )
        raise _map_embedding_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Reference resolve unexpected failure",
            extra={"op": "embeddings.reference_resolve", "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=500,
            detail={"code": "embedding_internal_error", "message": "Unexpected embedding failure (sanitized)."},
        ) from exc
