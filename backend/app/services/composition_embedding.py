"""Orchestration for symbolic composition embeddings (cache + runtime resolve).

Services layer entry point used by future HTTP routes. Never logs full vectors
at INFO, never logs composition JSON / event arrays.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.ai_runtime.errors import (
    AiRuntimeError,
    FallbackNotConfiguredError,
    ModelNotFoundError,
    ModelUnavailableError,
)
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.routing import ModelSelectionInput, resolve_model_for_operation
from app.ai_runtime.runtimes.symbolic_features import (
    SYMBOLIC_FEATURES_RUNTIME,
    SymbolicFeaturesEmbeddingModel,
    build_symbolic_features_embedding_model,
)
from app.composition_schemas import CompositionV2
from app.embeddings.cache import EmbeddingCache, get_default_embedding_cache
from app.embeddings.errors import EmbeddingError, EmbeddingModelUnavailableError, SimilarityIndexError
from app.embeddings.index import ProjectSimilarityIndex, default_index_scopes
from app.embeddings.schemas import (
    CompositionEmbedScope,
    CompositionEmbeddingV1,
    CompositionSimilarityHitV1,
    CompositionSimilarityQueryV1,
    EmbedComputeResponse,
    EmbedScopeComposition,
    EmbedScopeMotif,
    EmbedScopeSection,
    RelatedMotifsResponse,
    SimilaritySearchResponse,
    SimilarityTargetRef,
    embed_scope_digest,
)
from app.embeddings.settings import (
    EMBEDDING_ALGORITHM_VERSION,
    EMBEDDING_DEFAULT_MODEL_ID,
    EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN,
    EMBEDDING_PROFILE_ID,
    EmbeddingSettings,
    load_embedding_settings,
)
from app.embeddings.vector import cosine_distance, cosine_similarity
from app.llm_settings import FAKE_PROVIDER, fake_mode_enabled
from app.services import project_store
from app.services.composition_fingerprint import composition_source_fingerprint


logger = logging.getLogger(__name__)


def compute_embedding(
    composition: CompositionV2 | dict[str, Any],
    scope: CompositionEmbedScope | None = None,
    *,
    model_id: str | None = None,
    project_id: str | None = None,
    use_cache: bool = True,
    cache: EmbeddingCache | None = None,
    settings: EmbeddingSettings | None = None,
) -> CompositionEmbeddingV1:
    """Resolve embed model, optionally cache, and return ``composition.embedding.v1``.

    When ``model_id`` is None, uses AI runtime resolve for ``AiOperation.EMBED``.
    Cache key: ``(model_id, profile_id, algorithm_version, fingerprint, scope_digest)``.
    """
    started = time.perf_counter()
    resolved_settings = settings or load_embedding_settings()
    resolved_scope: CompositionEmbedScope = scope or EmbedScopeComposition()

    try:
        validated = (
            composition
            if isinstance(composition, CompositionV2)
            else CompositionV2.model_validate(composition)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Invalid composition for embedding",
            extra={"error_type": type(exc).__name__, "project_id": project_id},
        )
        raise EmbeddingError(
            "embed_invalid_composition",
            details={"error_type": type(exc).__name__},
        ) from exc

    fingerprint = composition_source_fingerprint(validated)
    digest = embed_scope_digest(resolved_scope)
    model = _resolve_embedding_model(model_id)
    resolved_model_id = model.model_id

    key_parts = (
        resolved_model_id,
        EMBEDDING_PROFILE_ID,
        EMBEDDING_ALGORITHM_VERSION,
        fingerprint,
        digest,
    )

    def _compute() -> CompositionEmbeddingV1:
        return model.embed_composition_scope(validated, resolved_scope)

    if use_cache:
        active_cache = cache or get_default_embedding_cache(resolved_settings)
        card = active_cache.get_or_compute(
            key_parts,
            _compute,
            project_id=project_id,
        )
    else:
        card = _compute()

    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Composition embedding computed",
        extra={
            "model_id": card.model_id,
            "profile_id": card.profile_id,
            "algorithm_version": card.algorithm_version,
            "scope_kind": resolved_scope.kind,
            "dims": card.dims,
            "note_count": card.note_count,
            "fingerprint_prefix": fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN],
            "scope_digest_prefix": digest[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN],
            "project_id": project_id,
            "use_cache": use_cache,
            "latency_ms": latency_ms,
            "default_model_id": EMBEDDING_DEFAULT_MODEL_ID,
        },
    )
    return card


def compute_embedding_response(
    composition: CompositionV2 | dict[str, Any],
    scope: CompositionEmbedScope | None = None,
    *,
    model_id: str | None = None,
) -> EmbedComputeResponse:
    """HTTP-facing wrapper around ``compute_embedding``."""
    card = compute_embedding(composition, scope, model_id=model_id)
    warnings: list[str] = []
    if card.projection_id == "none":
        warnings.append("projection_none")
    return EmbedComputeResponse(embedding=card, warning_codes=warnings)  # type: ignore[arg-type]


def search_similarity(
    query: CompositionSimilarityQueryV1,
    *,
    model_id: str | None = None,
) -> SimilaritySearchResponse:
    """Rank nearest corpus scopes for a query embedding / composition+scope."""
    started = time.perf_counter()
    settings = load_embedding_settings()
    warnings: list[str] = []

    if query.corpus.kind == "dataset":
        if not settings.allow_dataset_corpus:
            logger.error(
                "Dataset similarity corpus forbidden",
                extra={"op": "embeddings.similarity", "allow_dataset_corpus": False},
            )
            raise SimilarityIndexError(
                "similarity_corpus_forbidden",
                details={"corpus_kind": "dataset"},
            )
        raise SimilarityIndexError(
            "similarity_corpus_forbidden",
            details={"corpus_kind": "dataset", "reason": "dataset_index_not_implemented"},
        )

    top_k = min(query.top_k, settings.max_top_k)
    if query.embedding is not None:
        query_card = query.embedding
        resolved_model_id = query_card.model_id
    else:
        if query.composition is None or query.scope is None:
            raise SimilarityIndexError("similarity_query_invalid")
        query_card = compute_embedding(query.composition, query.scope, model_id=model_id)
        resolved_model_id = query_card.model_id

    def _embed(composition: CompositionV2, scope: CompositionEmbedScope) -> CompositionEmbeddingV1:
        return compute_embedding(composition, scope, model_id=resolved_model_id)

    hits: list[CompositionSimilarityHitV1] = []
    if query.corpus.kind == "in_request":
        if query.composition is None:
            raise SimilarityIndexError(
                "similarity_query_invalid",
                details={"reason": "in_request_requires_composition"},
            )
        try:
            validated = (
                query.composition
                if isinstance(query.composition, CompositionV2)
                else CompositionV2.model_validate(query.composition)
            )
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(
                "embed_invalid_composition",
                details={"error_type": type(exc).__name__},
            ) from exc
        hits = _search_in_request(
            validated,
            query_card,
            include_sections=query.corpus.include_composition_sections,
            include_motifs=query.corpus.include_motifs,
            top_k=top_k,
            embed_fn=_embed,
        )
        if not hits:
            warnings.append("empty_index_partial")
            logger.warning(
                "Similarity in_request corpus empty",
                extra={"op": "embeddings.similarity", "model_id": resolved_model_id},
            )
    elif query.corpus.kind == "projects":
        hits = _search_projects(
            query_card,
            top_k=top_k,
            embed_fn=_embed,
            project_ids=list(query.corpus.project_ids) or None,
            include_sections=query.corpus.include_composition_sections,
            include_motifs=query.corpus.include_motifs,
            exclude_project_id=query.exclude_project_id,
            exclude_source_fingerprint=query.exclude_source_fingerprint,
            settings=settings,
        )
        if not hits:
            warnings.append("empty_index_partial")
    else:
        raise SimilarityIndexError(
            "similarity_query_invalid",
            details={"corpus_kind": query.corpus.kind},
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Similarity search orchestration completed",
        extra={
            "op": "embeddings.similarity",
            "model_id": resolved_model_id,
            "hit_count": len(hits),
            "latency_ms": latency_ms,
            "corpus_kind": query.corpus.kind,
            "top_k": top_k,
        },
    )
    return SimilaritySearchResponse(
        hits=hits,
        query_fingerprint_prefix=query_card.source_fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN],
        model_id=resolved_model_id,
        profile_id=EMBEDDING_PROFILE_ID,
        warning_codes=list(dict.fromkeys(warnings))[:32],  # type: ignore[arg-type]
        musical_quality_claim=False,
    )


def search_related_motifs(
    composition: CompositionV2 | dict[str, Any],
    *,
    motif_id: str,
    occurrence_id: str | None = None,
    top_k: int = 10,
    search_cross_project: bool = False,
    model_id: str | None = None,
) -> RelatedMotifsResponse:
    """Find related motifs by embedding affinity (not musical quality)."""
    started = time.perf_counter()
    settings = load_embedding_settings()
    scope = EmbedScopeMotif(motif_id=motif_id, occurrence_id=occurrence_id)
    query_card = compute_embedding(composition, scope, model_id=model_id)
    warnings: list[str] = []

    try:
        validated = (
            composition
            if isinstance(composition, CompositionV2)
            else CompositionV2.model_validate(composition)
        )
    except Exception as exc:  # noqa: BLE001
        raise EmbeddingError(
            "embed_invalid_composition",
            details={"error_type": type(exc).__name__},
        ) from exc

    def _embed(comp: CompositionV2, embed_scope: CompositionEmbedScope) -> CompositionEmbeddingV1:
        return compute_embedding(comp, embed_scope, model_id=query_card.model_id)

    candidates: list[tuple[CompositionEmbeddingV1, SimilarityTargetRef]] = []
    query_digest = embed_scope_digest(scope)
    for motif in validated.motifs or []:
        motif_scope = EmbedScopeMotif(motif_id=motif.id)
        if embed_scope_digest(motif_scope) == query_digest:
            continue
        if motif.id == motif_id and occurrence_id is None:
            continue
        try:
            card = _embed(validated, motif_scope)
        except EmbeddingError:
            continue
        candidates.append(
            (
                card,
                SimilarityTargetRef(
                    project_id=None,
                    revision_id=None,
                    scope=card.scope,
                    source_fingerprint=card.source_fingerprint,
                    corpus="in_request",
                ),
            )
        )

    if search_cross_project:
        for record in project_store.list_projects():
            if not record.composition_json:
                continue
            try:
                other = CompositionV2.model_validate(json.loads(record.composition_json))
            except Exception:  # noqa: BLE001
                continue
            for motif_scope in default_index_scopes(other, include_motifs=True):
                if motif_scope.kind != "motif":
                    continue
                try:
                    card = _embed(other, motif_scope)
                except EmbeddingError:
                    continue
                candidates.append(
                    (
                        card,
                        SimilarityTargetRef(
                            project_id=record.id,
                            revision_id=record.current_revision_id,
                            scope=card.scope,
                            source_fingerprint=card.source_fingerprint,
                            corpus="projects",
                        ),
                    )
                )

    capped = min(top_k, settings.max_top_k, 50)
    ranked = _rank_candidate_cards(query_card, candidates, top_k=capped)
    if not candidates:
        warnings.append("empty_index_partial")

    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Related motifs search completed",
        extra={
            "op": "embeddings.related_motifs",
            "model_id": query_card.model_id,
            "hit_count": len(ranked),
            "latency_ms": latency_ms,
            "search_cross_project": search_cross_project,
            "motif_id": motif_id,
        },
    )
    return RelatedMotifsResponse(
        hits=ranked,
        model_id=query_card.model_id,
        warning_codes=list(dict.fromkeys(warnings))[:32],  # type: ignore[arg-type]
        musical_quality_claim=False,
    )


def _search_in_request(
    composition: CompositionV2,
    query_card: CompositionEmbeddingV1,
    *,
    include_sections: bool,
    include_motifs: bool,
    top_k: int,
    embed_fn: Any,
) -> list[CompositionSimilarityHitV1]:
    scopes: list[CompositionEmbedScope] = []
    if include_sections:
        for index, section in enumerate(composition.sections):
            scopes.append(
                EmbedScopeSection(
                    section_index=index,
                    section_id=section.id,
                    expected_start_bar=section.start_bar,
                    expected_bar_count=section.bar_count,
                )
            )
    if include_motifs:
        for motif in composition.motifs or []:
            scopes.append(EmbedScopeMotif(motif_id=motif.id))
    if not scopes:
        scopes = [EmbedScopeComposition()]

    query_digest = embed_scope_digest(query_card.scope)
    candidates: list[tuple[CompositionEmbeddingV1, SimilarityTargetRef]] = []
    for scope in scopes:
        if (
            embed_scope_digest(scope) == query_digest
            and composition_source_fingerprint(composition) == query_card.source_fingerprint
        ):
            continue
        try:
            card = embed_fn(composition, scope)
        except EmbeddingError:
            continue
        candidates.append(
            (
                card,
                SimilarityTargetRef(
                    project_id=None,
                    revision_id=None,
                    scope=card.scope,
                    source_fingerprint=card.source_fingerprint,
                    corpus="in_request",
                ),
            )
        )
    return _rank_candidate_cards(query_card, candidates, top_k=top_k)


def _search_projects(
    query_card: CompositionEmbeddingV1,
    *,
    top_k: int,
    embed_fn: Any,
    project_ids: list[str] | None,
    include_sections: bool,
    include_motifs: bool,
    exclude_project_id: str | None,
    exclude_source_fingerprint: str | None,
    settings: EmbeddingSettings,
) -> list[CompositionSimilarityHitV1]:
    index = ProjectSimilarityIndex(settings=settings)
    items: list[tuple[str, CompositionV2, list[CompositionEmbedScope] | None]] = []
    allow = set(project_ids or [])
    for record in project_store.list_projects():
        if allow and record.id not in allow:
            continue
        if not record.composition_json:
            continue
        try:
            composition = CompositionV2.model_validate(json.loads(record.composition_json))
        except Exception:  # noqa: BLE001
            continue
        if include_sections and not include_motifs:
            scopes = default_index_scopes(composition, include_motifs=False)
        elif include_motifs:
            scopes = default_index_scopes(composition, include_motifs=True)
        else:
            scopes = [EmbedScopeComposition()]
        items.append((record.id, composition, scopes))

    if not items:
        logger.warning(
            "No projects available for similarity corpus",
            extra={"op": "embeddings.similarity"},
        )
        return []

    index.build_index_from_compositions(items, embed_fn=embed_fn, include_motifs=include_motifs)
    try:
        return index.search(
            query_card.vector,
            top_k=top_k,
            exclude_project_id=exclude_project_id,
            exclude_source_fingerprint=exclude_source_fingerprint,
            project_ids=project_ids,
        )
    except SimilarityIndexError as exc:
        if exc.code == "similarity_index_empty":
            return []
        raise


def _rank_candidate_cards(
    query_card: CompositionEmbeddingV1,
    candidates: list[tuple[CompositionEmbeddingV1, SimilarityTargetRef]],
    *,
    top_k: int,
) -> list[CompositionSimilarityHitV1]:
    scored: list[tuple[float, CompositionEmbeddingV1, SimilarityTargetRef]] = []
    for card, target in candidates:
        if card.dims != query_card.dims:
            continue
        score = cosine_similarity(query_card.vector, card.vector)
        scored.append((score, card, target))
    scored.sort(key=lambda item: item[0], reverse=True)
    hits: list[CompositionSimilarityHitV1] = []
    for rank, (score, card, target) in enumerate(scored[:top_k], start=1):
        hits.append(
            CompositionSimilarityHitV1(
                rank=rank,
                score=round(float(score), 6),
                distance=round(float(cosine_distance(query_card.vector, card.vector)), 6),
                distance_metric="cosine",
                target=target,
                model_id=query_card.model_id,
                profile_id=query_card.profile_id or EMBEDDING_PROFILE_ID,
                musical_quality_claim=False,
            )
        )
    return hits


def _resolve_embedding_model(model_id: str | None) -> SymbolicFeaturesEmbeddingModel:
    # Fake language models / LLM_FAKE_MODE still use the handcrafted embedder.
    # Text embedding stubs are rejected (never claim musical similarity).
    requested = (model_id or "").strip()
    if requested.endswith(":embedding-stub") or requested == "local:embedding-stub":
        raise EmbeddingModelUnavailableError(
            "embedding_text_stub_rejected",
            details={"requested_model_id": requested},
        )
    if requested.startswith("browser:") or requested == "browser:symbolic-features-v1":
        logger.debug(
            "Embedding resolve refused browser_model",
            extra={
                "requested_model_id": requested,
                "skip_reason": "browser_model_not_server_executable",
            },
        )
        raise EmbeddingModelUnavailableError(
            "embedding_model_unavailable",
            details={
                "requested_model_id": requested,
                "runtime": "browser_model",
                "skip_reason": "browser_model_not_server_executable",
            },
        )
    if fake_mode_enabled() or (
        requested and (requested.startswith(f"{FAKE_PROVIDER}:") or "fake" in requested.lower())
    ):
        from app.ai_runtime.runtimes.symbolic_features import default_symbolic_features_descriptor

        if requested and requested != EMBEDDING_DEFAULT_MODEL_ID:
            logger.info(
                "Embedding request redirected to handcrafted symbolic features",
                extra={
                    "requested_model_id": requested or None,
                    "model_id": EMBEDDING_DEFAULT_MODEL_ID,
                    "fake_mode": fake_mode_enabled(),
                },
            )
        return build_symbolic_features_embedding_model(default_symbolic_features_descriptor())

    selection = ModelSelectionInput(model_id=model_id) if model_id else None
    try:
        resolved = resolve_model_for_operation(AiOperation.EMBED, selection)
    except (
        ModelNotFoundError,
        ModelUnavailableError,
        FallbackNotConfiguredError,
        AiRuntimeError,
    ) as exc:
        logger.error(
            "Embedding model unavailable",
            extra={
                "requested_model_id": model_id,
                "error_type": type(exc).__name__,
                "error_code": getattr(exc, "code", None),
            },
        )
        raise EmbeddingModelUnavailableError(
            "embedding_model_unavailable",
            details={
                "requested_model_id": model_id,
                "error_type": type(exc).__name__,
            },
        ) from exc

    descriptor = resolved.descriptor
    if str(descriptor.runtime) == "browser_model":
        logger.debug(
            "Embedding resolve refused browser_model descriptor",
            extra={
                "resolved_model_id": descriptor.id,
                "runtime": "browser_model",
                "skip_reason": "browser_model_not_server_executable",
            },
        )
        raise EmbeddingModelUnavailableError(
            "embedding_model_unavailable",
            details={
                "resolved_model_id": descriptor.id,
                "runtime": "browser_model",
                "skip_reason": "browser_model_not_server_executable",
            },
        )
    if descriptor.runtime != SYMBOLIC_FEATURES_RUNTIME:
        logger.error(
            "Resolved embed model is not symbolic_features runtime",
            extra={
                "resolved_model_id": descriptor.id,
                "runtime": descriptor.runtime,
            },
        )
        raise EmbeddingModelUnavailableError(
            "embedding_text_stub_rejected",
            details={
                "resolved_model_id": descriptor.id,
                "runtime": descriptor.runtime,
            },
        )

    try:
        return build_symbolic_features_embedding_model(descriptor)
    except ModelUnavailableError as exc:
        raise EmbeddingModelUnavailableError(
            "embedding_model_unavailable",
            details={"resolved_model_id": descriptor.id},
        ) from exc
