"""Resolve musical style references into bounded conditioning + provenance.

Never dumps full vectors, prompts, or event arrays. Artist/composer names are
not conditioning dimensions.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.composition_schemas import CompositionV2
from app.embeddings.errors import EmbeddingError, ReferenceResolveError
from app.embeddings.features import extract_symbolic_features_v1
from app.embeddings.schemas import (
    CompositionEmbeddingV1,
    CompositionReferenceProvenanceV1,
    CompositionStyleConditioningV1,
    ReferenceResolveRequest,
    ReferenceResolveResponse,
    StyleReferenceRequest,
)
from app.embeddings.settings import (
    EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN,
    load_embedding_settings,
)
from app.embeddings.vector import cosine_distance, cosine_similarity
from app.services.composition_embedding import compute_embedding
from app.services.composition_fingerprint import composition_source_fingerprint
from app.services import project_store
from app.services.project_history import ProjectHistoryNotFoundError, get_revision_detail
from app.services.project_history_store import ProjectHistoryError


logger = logging.getLogger(__name__)

_PC_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def resolve_style_reference(
    style_reference: StyleReferenceRequest,
    *,
    model_id: str | None = None,
    include_conditioning: bool = True,
) -> ReferenceResolveResponse:
    """Load reference composition, embed scope, and build optional conditioning."""
    composition = _load_reference_composition(style_reference)
    fingerprint = composition_source_fingerprint(composition)
    if (
        style_reference.expected_fingerprint is not None
        and style_reference.expected_fingerprint != fingerprint
    ):
        logger.info(
            "Style reference fingerprint mismatch",
            extra={
                "project_id": style_reference.project_id,
                "expected_prefix": style_reference.expected_fingerprint[
                    :EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN
                ],
                "actual_prefix": fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN],
            },
        )
        raise ReferenceResolveError(
            "reference_fingerprint_mismatch",
            details={
                "project_id": style_reference.project_id,
                "expected_prefix": style_reference.expected_fingerprint[
                    :EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN
                ],
                "actual_prefix": fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN],
            },
        )

    try:
        embedding = compute_embedding(
            composition,
            style_reference.scope,
            model_id=model_id,
            project_id=style_reference.project_id,
        )
    except EmbeddingError as exc:
        if exc.code in {"embed_scope_invalid", "embed_empty_scope"}:
            raise ReferenceResolveError(
                "reference_scope_invalid",
                details={"cause": exc.code, "scope_kind": style_reference.scope.kind},
            ) from exc
        raise

    provenance = CompositionReferenceProvenanceV1(
        project_id=style_reference.project_id,
        revision_id=style_reference.revision_id,
        scope=embedding.scope,
        source_fingerprint=embedding.source_fingerprint,
        embedding_model_id=embedding.model_id,
        profile_id=embedding.profile_id,
        algorithm_version=embedding.algorithm_version,
        scope_digest=embedding.scope_digest,
        artist_label_used=False,
    )

    warnings: list[str] = []
    conditioning: CompositionStyleConditioningV1 | None = None
    if include_conditioning:
        conditioning = build_style_conditioning(
            composition,
            embedding,
            provenance=provenance,
            mode=style_reference.mode,
        )
        if not conditioning.tokenizer_labels:
            warnings.append("tokenizer_labels_skipped")
        if embedding.projection_id == "none":
            warnings.append("projection_none")

    logger.info(
        "Style reference resolved",
        extra={
            "conditioning_mode": style_reference.mode,
            "project_id": style_reference.project_id,
            "revision_id": style_reference.revision_id,
            "scope_kind": style_reference.scope.kind,
            "fingerprint_prefix": fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN],
            "embedding_model_id": embedding.model_id,
            "note_count": embedding.note_count,
            "include_conditioning": include_conditioning,
        },
    )
    return ReferenceResolveResponse(
        embedding=embedding,
        provenance=provenance,
        conditioning=conditioning,
        warning_codes=list(dict.fromkeys(warnings))[:32],  # type: ignore[arg-type]
    )


def resolve_reference_request(
    request: ReferenceResolveRequest,
) -> ReferenceResolveResponse:
    return resolve_style_reference(
        request.style_reference,
        model_id=request.model_id,
        include_conditioning=True,
    )


def build_style_conditioning(
    composition: CompositionV2,
    embedding: CompositionEmbeddingV1,
    *,
    provenance: CompositionReferenceProvenanceV1,
    mode: str = "prompt_features",
) -> CompositionStyleConditioningV1:
    settings = load_embedding_settings()
    feature_summary = build_feature_summary(
        composition,
        embedding,
        max_chars=settings.feature_summary_max_chars,
    )
    tokenizer_labels = _infer_tokenizer_labels(composition, embedding)
    vector_hint: list[float] | None = None
    if mode == "vector_hint":
        max_dims = max(1, settings.prompt_vector_max_dims)
        if embedding.dims <= max_dims:
            vector_hint = [round(float(v), 4) for v in embedding.vector]
        else:
            # Compact hint: first N dims only (never full vector when oversized).
            vector_hint = [round(float(v), 4) for v in embedding.vector[:max_dims]]

    if mode not in {"prompt_features", "tokenizer_labels", "vector_hint"}:
        raise ReferenceResolveError(
            "conditioning_mode_invalid",
            details={"mode": mode},
        )

    return CompositionStyleConditioningV1(
        mode=mode,  # type: ignore[arg-type]
        reference_provenance=provenance,
        feature_summary=feature_summary,
        tokenizer_labels=tokenizer_labels if mode in {"prompt_features", "tokenizer_labels"} else {},
        vector_hint=vector_hint if mode == "vector_hint" else None,
        artist_label_used=False,
    )


def build_feature_summary(
    composition: CompositionV2,
    embedding: CompositionEmbeddingV1,
    *,
    max_chars: int = 4000,
) -> str:
    """Human-readable rounded feature summary — never a full vector dump."""
    try:
        raw, window = extract_symbolic_features_v1(composition, embedding.scope)
    except EmbeddingError:
        return (
            f"scope={embedding.scope.kind}; notes={embedding.note_count}; "
            f"fp={embedding.fingerprint_prefix()}; dims={embedding.dims}"
        )[:max_chars]

    pc = raw[0:12]
    ranked_pc = sorted(enumerate(pc), key=lambda item: item[1], reverse=True)
    top_pc = ", ".join(
        f"{_PC_NAMES[idx]}={round(weight, 3)}" for idx, weight in ranked_pc[:4] if weight > 0.01
    ) or "flat"
    midi_min = round(raw[12] * 127.0, 1)
    midi_max = round(raw[13] * 127.0, 1)
    midi_mean = round(raw[14] * 127.0, 1)
    density = round(raw[12 + 3 + 8 + 8], 3)  # after pc+range+dur+onset
    section = window.section_type or "n/a"
    lines = [
        f"scope={window.scope_kind} bars={window.start_bar}-{window.end_bar_inclusive}",
        f"notes={embedding.note_count} section_type={section}",
        f"pitch_range_midi=[{midi_min},{midi_max}] mean={midi_mean}",
        f"density_norm={density}",
        f"top_pitch_classes=[{top_pc}]",
        f"key={composition.key} meter={composition.time_signature} tempo={composition.tempo}",
        f"embedding_fp={embedding.fingerprint_prefix()} model={embedding.model_id}",
    ]
    text = "; ".join(lines)
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 3)] + "..."
    return text


def candidate_reference_similarity(
    reference: CompositionEmbeddingV1,
    candidate_composition: CompositionV2,
    *,
    model_id: str | None = None,
) -> tuple[float, float]:
    """Return (cosine_similarity, cosine_distance) for candidate vs reference scope."""
    candidate_card = compute_embedding(
        candidate_composition,
        reference.scope,
        model_id=model_id or reference.model_id,
        use_cache=True,
    )
    if candidate_card.dims != reference.dims:
        return 0.0, 1.0
    sim = float(cosine_similarity(reference.vector, candidate_card.vector))
    dist = float(cosine_distance(reference.vector, candidate_card.vector))
    return round(sim, 6), round(dist, 6)


def load_style_reference_composition(style_reference: StyleReferenceRequest) -> CompositionV2:
    """Public loader for style / reference-feature analysis (read-only V2)."""
    return _load_reference_composition(style_reference)


def _load_reference_composition(style_reference: StyleReferenceRequest) -> CompositionV2:
    if style_reference.composition is not None:
        try:
            return CompositionV2.model_validate(style_reference.composition)
        except Exception as exc:  # noqa: BLE001
            raise ReferenceResolveError(
                "reference_scope_invalid",
                details={"error_type": type(exc).__name__, "source": "inline"},
            ) from exc

    project_id = style_reference.project_id
    if not project_id:
        raise ReferenceResolveError("reference_not_found", details={"reason": "missing_source"})

    if style_reference.revision_id:
        try:
            detail = get_revision_detail(project_id, style_reference.revision_id)
        except (LookupError, ProjectHistoryError, ProjectHistoryNotFoundError) as exc:
            raise ReferenceResolveError(
                "reference_not_found",
                details={
                    "project_id": project_id,
                    "revision_id": style_reference.revision_id,
                },
            ) from exc
        if detail.composition is None:
            raise ReferenceResolveError(
                "reference_not_found",
                details={"project_id": project_id, "revision_id": style_reference.revision_id},
            )
        return detail.composition

    try:
        record = project_store.get_project(project_id)
    except project_store.ProjectNotFoundError as exc:
        raise ReferenceResolveError(
            "reference_not_found",
            details={"project_id": project_id},
        ) from exc
    if not record.composition_json:
        raise ReferenceResolveError(
            "reference_not_found",
            details={"project_id": project_id, "reason": "empty_composition"},
        )
    try:
        return CompositionV2.model_validate(json.loads(record.composition_json))
    except Exception as exc:  # noqa: BLE001
        raise ReferenceResolveError(
            "reference_scope_invalid",
            details={"project_id": project_id, "error_type": type(exc).__name__},
        ) from exc


def _infer_tokenizer_labels(
    composition: CompositionV2,
    embedding: CompositionEmbeddingV1,
) -> dict[str, str]:
    labels: dict[str, str] = {}
    key = (composition.key or "").strip()
    if key and len(key) <= 80:
        labels["key"] = key
    meter = (composition.time_signature or "").strip()
    if meter and len(meter) <= 80:
        labels["meter"] = meter
    if embedding.scope.kind == "section" and getattr(embedding.scope, "section_id", None):
        labels["scope"] = f"section:{embedding.scope.section_id}"  # type: ignore[union-attr]
    elif embedding.scope.kind:
        labels["scope"] = embedding.scope.kind
    return labels


def conditioning_context_fragment(
    conditioning: CompositionStyleConditioningV1,
) -> dict[str, Any]:
    """Bounded fragment for development LLM context (no full vectors unless hint mode)."""
    fragment: dict[str, Any] = {
        "schema_version": conditioning.schema_version,
        "mode": conditioning.mode,
        "feature_summary": conditioning.feature_summary,
        "tokenizer_labels": dict(conditioning.tokenizer_labels),
        "artist_label_used": False,
        "reference_provenance": conditioning.reference_provenance.to_generation_parameters_fragment()[
            "reference_provenance"
        ],
    }
    if conditioning.mode == "vector_hint" and conditioning.vector_hint is not None:
        fragment["vector_hint_dims"] = len(conditioning.vector_hint)
        fragment["vector_hint"] = list(conditioning.vector_hint)
    return fragment
