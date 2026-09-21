"""Project-scoped similarity index for symbolic embeddings.

Corpus default is ``projects`` (user working compositions). Entries store a
source fingerprint so search can filter stale hits when the composition has
changed. Motif scopes are optional / lazy — default build indexes composition
+ each section only.

Never copies projects into ``DATASET_ROOT`` unless
``EMBEDDING_ALLOW_DATASET_CORPUS`` is enabled **and**
``export_to_dataset_corpus`` is called explicitly.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.composition_schemas import CompositionV2
from app.embeddings.errors import (
    DatasetExportForbiddenError,
    EmbeddingError,
    SimilarityIndexError,
)
from app.embeddings.features import embed_composition_scope
from app.embeddings.schemas import (
    CompositionEmbedScope,
    CompositionEmbeddingV1,
    CompositionSimilarityHitV1,
    EmbedScopeComposition,
    EmbedScopeMotif,
    EmbedScopeSection,
    SimilarityCorpusKind,
    SimilarityTargetRef,
)
from app.embeddings.settings import (
    EMBEDDING_DEFAULT_DISTANCE_METRIC,
    EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN,
    EMBEDDING_PROFILE_ID,
    EmbeddingSettings,
    load_embedding_settings,
)
from app.embeddings.vector import cosine_distance, cosine_similarity
from app.services.composition_fingerprint import composition_source_fingerprint


logger = logging.getLogger(__name__)

EmbedComputeFn = Callable[
    [CompositionV2, CompositionEmbedScope],
    CompositionEmbeddingV1,
]


@dataclass
class SimilarityIndexEntry:
    """One indexed scoped embedding (projects corpus by default)."""

    project_id: str
    scope: CompositionEmbedScope
    source_fingerprint: str
    embedding: CompositionEmbeddingV1
    revision_id: str | None = None
    corpus: SimilarityCorpusKind = "projects"

    @property
    def vector(self) -> list[float]:
        return self.embedding.vector

    def to_public_dict(self) -> dict[str, Any]:
        """Metadata without the full vector (safe for INFO logs)."""
        return {
            "project_id": self.project_id,
            "revision_id": self.revision_id,
            "scope_kind": self.scope.kind,
            "source_fingerprint_prefix": self.source_fingerprint[
                :EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN
            ],
            "corpus": self.corpus,
            "model_id": self.embedding.model_id,
            "dims": self.embedding.dims,
            "note_count": self.embedding.note_count,
        }


@dataclass
class ProjectSimilarityIndex:
    """In-memory similarity index with optional filesystem snapshot."""

    settings: EmbeddingSettings = field(default_factory=load_embedding_settings)
    enable_filesystem: bool = False
    index_dir: Path | None = None
    _entries: list[SimilarityIndexEntry] = field(default_factory=list, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.index_dir is None:
            self.index_dir = self.settings.cache_dir / "similarity_index"
        if self.enable_filesystem:
            try:
                self.index_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning(
                    "Similarity index directory unavailable; memory-only",
                    extra={"error_type": type(exc).__name__},
                )
                self.enable_filesystem = False

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def project_ids(self) -> set[str]:
        with self._lock:
            return {e.project_id for e in self._entries}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
        logger.info("Similarity index cleared", extra={"indexed_project_count": 0})

    def build_index_from_compositions(
        self,
        items: Iterable[tuple[str, CompositionV2 | dict[str, Any], Sequence[CompositionEmbedScope] | None]],
        *,
        embed_fn: EmbedComputeFn | None = None,
        include_motifs: bool = False,
        revision_id: str | None = None,
        replace: bool = True,
    ) -> int:
        """Index scoped embeddings for project compositions.

        Each item is ``(project_id, composition, scopes_or_None)``. When scopes
        is ``None``, defaults to composition + each section (motif optional).
        Caps: ``index_max_projects``, ``index_max_entries``.
        """
        compute = embed_fn or (
            lambda composition, scope: embed_composition_scope(composition, scope)
        )
        new_entries: list[SimilarityIndexEntry] = []
        seen_projects: set[str] = set()
        scope_counts: dict[str, int] = {}
        skipped_empty = 0
        truncated_projects = False
        truncated_entries = False

        for project_id, composition_raw, scopes in items:
            pid = str(project_id).strip()
            if not pid:
                continue
            if pid not in seen_projects and len(seen_projects) >= self.settings.index_max_projects:
                truncated_projects = True
                logger.warning(
                    "Similarity index project cap reached",
                    extra={"index_max_projects": self.settings.index_max_projects},
                )
                break
            seen_projects.add(pid)

            try:
                composition = (
                    composition_raw
                    if isinstance(composition_raw, CompositionV2)
                    else CompositionV2.model_validate(composition_raw)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Skipping invalid composition for similarity index",
                    extra={"project_id": pid, "error_type": type(exc).__name__},
                )
                continue

            fingerprint = composition_source_fingerprint(composition)
            resolved_scopes = list(scopes) if scopes is not None else default_index_scopes(
                composition, include_motifs=include_motifs
            )

            for scope in resolved_scopes:
                if len(new_entries) >= self.settings.index_max_entries:
                    truncated_entries = True
                    break
                try:
                    card = compute(composition, scope)
                except EmbeddingError as exc:
                    if exc.code == "embed_empty_scope":
                        skipped_empty += 1
                        continue
                    logger.warning(
                        "Skipping scope embed failure in similarity index",
                        extra={
                            "project_id": pid,
                            "scope_kind": getattr(scope, "kind", None),
                            "error_code": exc.code,
                        },
                    )
                    continue
                entry = SimilarityIndexEntry(
                    project_id=pid,
                    revision_id=revision_id,
                    scope=card.scope,
                    source_fingerprint=fingerprint,
                    embedding=card,
                    corpus="projects",
                )
                new_entries.append(entry)
                kind = card.scope.kind
                scope_counts[kind] = scope_counts.get(kind, 0) + 1
            if truncated_entries:
                break

        with self._lock:
            if replace:
                self._entries = new_entries
            else:
                # Drop existing rows for rebuilt project ids, then append.
                rebuilt = {e.project_id for e in new_entries}
                kept = [e for e in self._entries if e.project_id not in rebuilt]
                merged = kept + new_entries
                if len(merged) > self.settings.index_max_entries:
                    merged = merged[: self.settings.index_max_entries]
                    truncated_entries = True
                project_ids = {e.project_id for e in merged}
                if len(project_ids) > self.settings.index_max_projects:
                    # Keep earliest projects by first occurrence.
                    allowed: set[str] = set()
                    filtered: list[SimilarityIndexEntry] = []
                    for entry in merged:
                        if entry.project_id not in allowed:
                            if len(allowed) >= self.settings.index_max_projects:
                                truncated_projects = True
                                continue
                            allowed.add(entry.project_id)
                        if entry.project_id in allowed:
                            filtered.append(entry)
                    merged = filtered
                self._entries = merged
            count = len(self._entries)
            project_count = len({e.project_id for e in self._entries})

        if self.enable_filesystem:
            self._persist_snapshot()

        logger.info(
            "Similarity index built",
            extra={
                "indexed_project_count": project_count,
                "indexed_entry_count": count,
                "scope_counts": scope_counts,
                "skipped_empty_scopes": skipped_empty,
                "truncated_projects": truncated_projects,
                "truncated_entries": truncated_entries,
                "include_motifs": include_motifs,
            },
        )
        return count

    def search(
        self,
        query_vector: Sequence[float],
        *,
        top_k: int | None = None,
        exclude_project_id: str | None = None,
        exclude_source_fingerprint: str | None = None,
        project_ids: Sequence[str] | None = None,
        current_fingerprints: Mapping[str, str] | None = None,
        distance_metric: str = EMBEDDING_DEFAULT_DISTANCE_METRIC,
        filter_stale: bool = True,
    ) -> list[CompositionSimilarityHitV1]:
        """Rank indexed entries by cosine similarity (affinity, not quality)."""
        k = top_k if top_k is not None else self.settings.default_top_k
        k = max(1, min(int(k), self.settings.max_top_k))
        allow = {pid.strip() for pid in project_ids} if project_ids else None
        stale_filtered = 0
        scored: list[tuple[float, SimilarityIndexEntry]] = []

        with self._lock:
            entries = list(self._entries)

        if not entries:
            logger.warning(
                "Similarity search against empty index",
                extra={"top_k": k},
            )
            raise SimilarityIndexError(
                "similarity_index_empty",
                details={"corpus": "projects"},
            )

        for entry in entries:
            if exclude_project_id and entry.project_id == exclude_project_id:
                continue
            if (
                exclude_source_fingerprint
                and entry.source_fingerprint == exclude_source_fingerprint
            ):
                continue
            if allow is not None and entry.project_id not in allow:
                continue
            if filter_stale and current_fingerprints is not None:
                current = current_fingerprints.get(entry.project_id)
                if current is not None and current != entry.source_fingerprint:
                    stale_filtered += 1
                    continue
            if len(entry.vector) != len(query_vector):
                logger.debug(
                    "Skipping index entry with dim mismatch",
                    extra={
                        "project_id": entry.project_id,
                        "entry_dims": len(entry.vector),
                        "query_dims": len(query_vector),
                    },
                )
                continue
            if distance_metric == "cosine":
                score = cosine_similarity(query_vector, entry.vector)
            else:
                # Treat non-cosine as ranked by inverted euclidean via cosine fallback.
                score = cosine_similarity(query_vector, entry.vector)
            scored.append((score, entry))

        scored.sort(key=lambda item: item[0], reverse=True)
        truncated = len(scored) > k
        top = scored[:k]
        hits: list[CompositionSimilarityHitV1] = []
        for rank, (score, entry) in enumerate(top, start=1):
            distance = cosine_distance(query_vector, entry.vector)
            hits.append(
                CompositionSimilarityHitV1(
                    rank=rank,
                    score=float(score),
                    distance=float(distance),
                    distance_metric="cosine",
                    target=SimilarityTargetRef(
                        project_id=entry.project_id,
                        revision_id=entry.revision_id,
                        scope=entry.scope,
                        source_fingerprint=entry.source_fingerprint,
                        corpus=entry.corpus,
                    ),
                    model_id=entry.embedding.model_id,
                    profile_id=entry.embedding.profile_id or EMBEDDING_PROFILE_ID,
                    musical_quality_claim=False,
                )
            )

        logger.info(
            "Similarity search completed",
            extra={
                "hit_count": len(hits),
                "top_k": k,
                "stale_filtered": stale_filtered,
                "truncated": truncated,
                "indexed_entry_count": len(entries),
                "distance_metric": "cosine",
                "exclude_project_id": exclude_project_id,
            },
        )
        if stale_filtered:
            logger.debug(
                "Filtered stale similarity hits",
                extra={"stale_filtered": stale_filtered},
            )
        return hits

    def mark_project_stale(self, project_id: str) -> int:
        """Remove all entries for a project (caller may rebuild later)."""
        pid = project_id.strip()
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.project_id != pid]
            removed = before - len(self._entries)
        logger.info(
            "Similarity index project marked stale",
            extra={"project_id": pid, "removed_count": removed},
        )
        return removed

    def refuse_dataset_export(self) -> None:
        """Always raise ``DatasetExportForbiddenError`` (hard-closed helper)."""
        logger.error(
            "Dataset export from project similarity index refused",
            extra={"allow_dataset_corpus": self.settings.allow_dataset_corpus},
        )
        raise DatasetExportForbiddenError(
            details={"reason": "refuse_dataset_export", "corpus": "projects"},
        )

    def export_to_dataset_corpus(
        self,
        *,
        destination: Path | str,
        explicit_confirm: bool = False,
    ) -> Path:
        """Export index metadata only when explicitly allowed and confirmed.

        Default settings refuse. Even when enabled, this writes a **manifest**
        of project ids / fingerprints / scopes — never full composition JSON.
        """
        if not self.settings.allow_dataset_corpus or not explicit_confirm:
            logger.error(
                "Dataset corpus export forbidden",
                extra={
                    "allow_dataset_corpus": self.settings.allow_dataset_corpus,
                    "explicit_confirm": explicit_confirm,
                },
            )
            raise DatasetExportForbiddenError(
                details={
                    "allow_dataset_corpus": self.settings.allow_dataset_corpus,
                    "explicit_confirm": explicit_confirm,
                },
            )

        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            manifest = {
                "schema": "embedding.dataset_export.manifest.v1",
                "corpus": "dataset",
                "entry_count": len(self._entries),
                "entries": [
                    {
                        "project_id": e.project_id,
                        "revision_id": e.revision_id,
                        "scope": e.scope.model_dump(mode="json"),
                        "source_fingerprint": e.source_fingerprint,
                        "model_id": e.embedding.model_id,
                        "profile_id": e.embedding.profile_id,
                        "dims": e.embedding.dims,
                        # Vectors intentionally omitted from dataset export manifest.
                    }
                    for e in self._entries
                ],
            }
        dest.write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
        logger.info(
            "Dataset corpus export manifest written",
            extra={
                "entry_count": manifest["entry_count"],
                "destination_basename": dest.name,
            },
        )
        return dest

    def _persist_snapshot(self) -> None:
        if not self.enable_filesystem or self.index_dir is None:
            return
        path = self.index_dir / "index_snapshot.json"
        with self._lock:
            payload = {
                "schema": "embedding.similarity_index.v1",
                "entries": [
                    {
                        "project_id": e.project_id,
                        "revision_id": e.revision_id,
                        "corpus": e.corpus,
                        "source_fingerprint": e.source_fingerprint,
                        "embedding": e.embedding.model_dump(mode="json"),
                    }
                    for e in self._entries
                ],
            }
        try:
            path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "Failed to persist similarity index snapshot",
                extra={"error_type": type(exc).__name__},
            )


def default_index_scopes(
    composition: CompositionV2,
    *,
    include_motifs: bool = False,
) -> list[CompositionEmbedScope]:
    """Composition + each section; motifs only when ``include_motifs``."""
    scopes: list[CompositionEmbedScope] = [EmbedScopeComposition()]
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
        for motif in composition.motifs:
            scopes.append(EmbedScopeMotif(motif_id=motif.id))
    return scopes
