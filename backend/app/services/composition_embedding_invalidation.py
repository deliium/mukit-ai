"""Thin hooks to invalidate embedding cache / similarity index on project change.

Callers compare fingerprints and skip when unchanged so autosave does not thrash.
"""

from __future__ import annotations

import logging

from app.embeddings.cache import get_default_embedding_cache
from app.embeddings.index import ProjectSimilarityIndex
from app.embeddings.settings import EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN, load_embedding_settings


logger = logging.getLogger(__name__)


def maybe_invalidate_project_embeddings(
    project_id: str,
    *,
    previous_fingerprint: str | None,
    next_fingerprint: str | None,
) -> int:
    """Invalidate cache + mark similarity index stale when composition fingerprint changes.

    Returns number of cache entries removed (0 when skipped or empty).
    """
    pid = (project_id or "").strip()
    if not pid:
        return 0

    prev = (previous_fingerprint or "").strip() or None
    nxt = (next_fingerprint or "").strip() or None
    if prev == nxt:
        logger.debug(
            "Embedding invalidation skipped; fingerprint unchanged",
            extra={
                "project_id": pid,
                "fingerprint_prefix": (prev or "")[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN] or None,
            },
        )
        return 0

    settings = load_embedding_settings()
    cache = get_default_embedding_cache(settings)
    removed = cache.invalidate_by_project(pid)
    # Also drop the previous fingerprint globally when known (shared snapshot CAS).
    if prev:
        removed += cache.invalidate_by_fingerprint(prev)

    index = ProjectSimilarityIndex(settings=settings)
    stale_removed = index.mark_project_stale(pid)

    logger.info(
        "Embedding cache invalidated after composition change",
        extra={
            "project_id": pid,
            "previous_fingerprint_prefix": (prev or "")[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN] or None,
            "next_fingerprint_prefix": (nxt or "")[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN] or None,
            "cache_entries_removed": removed,
            "index_entries_removed": stale_removed,
        },
    )
    return removed
