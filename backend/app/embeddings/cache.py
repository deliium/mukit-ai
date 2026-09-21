"""Process LRU + optional filesystem cache for ``composition.embedding.v1``.

Cache key
---------
``(embedding_model_id, profile_id, algorithm_version, source_fingerprint,
scope_digest)``. Filesystem entries are keyed by a SHA-256 of that tuple.

Invalidation tracks ``project_id → {fingerprints}`` in memory and an optional
sidecar index under ``cache_dir`` so project / fingerprint clears can drop
matching entries without scanning every vector file.

Never logs full vectors at INFO. Cache artifacts live under
``EMBEDDING_CACHE_DIR`` (beside project data), never ``DATASET_ROOT``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.embeddings.errors import EmbeddingError
from app.embeddings.schemas import CompositionEmbeddingV1
from app.embeddings.settings import (
    EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN,
    EmbeddingSettings,
    load_embedding_settings,
)


logger = logging.getLogger(__name__)

CacheKeyParts = tuple[str, str, str, str, str]
_INDEX_FILENAME = "project_fingerprint_index.json"
_ENTRY_SUFFIX = ".embedding.json"


def hash_cache_key(key_parts: Sequence[str]) -> str:
    """Stable hex digest used as filesystem object id (not a secret)."""
    if len(key_parts) != 5:
        raise ValueError("cache key must be (model_id, profile_id, algorithm_version, fingerprint, scope_digest)")
    payload = "\0".join(str(part) for part in key_parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fingerprint_prefix(fingerprint: str) -> str:
    return fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN]


def _normalize_key_parts(key_parts: Sequence[str] | Mapping[str, str]) -> CacheKeyParts:
    if isinstance(key_parts, Mapping):
        try:
            return (
                str(key_parts["embedding_model_id"]),
                str(key_parts["profile_id"]),
                str(key_parts["algorithm_version"]),
                str(key_parts["source_fingerprint"]),
                str(key_parts["scope_digest"]),
            )
        except KeyError as exc:
            raise ValueError(f"missing cache key field: {exc}") from exc
    parts = tuple(str(p) for p in key_parts)
    if len(parts) != 5:
        raise ValueError(
            "cache key must be (embedding_model_id, profile_id, algorithm_version, "
            "source_fingerprint, scope_digest)"
        )
    return parts  # type: ignore[return-value]


class EmbeddingCache:
    """Get-or-compute cache with process LRU and optional JSON files on disk."""

    def __init__(
        self,
        settings: EmbeddingSettings | None = None,
        *,
        enable_filesystem: bool = True,
    ) -> None:
        self._settings = settings or load_embedding_settings()
        self._enable_filesystem = enable_filesystem
        self._lru: OrderedDict[str, CompositionEmbeddingV1] = OrderedDict()
        self._lru_max = max(8, int(self._settings.cache_lru_size))
        # project_id → set of source_fingerprints seen while caching under that project.
        self._project_fingerprints: dict[str, set[str]] = {}
        # fingerprint → set of cache object ids (for invalidate_by_fingerprint).
        self._fingerprint_keys: dict[str, set[str]] = {}
        # object id → (project_id|None, fingerprint) for reverse lookup on eviction.
        self._key_meta: dict[str, tuple[str | None, str]] = {}
        self._lock = threading.RLock()
        self._cache_dir = self._settings.cache_dir
        self._index_path = self._cache_dir / _INDEX_FILENAME
        if self._enable_filesystem:
            self._ensure_cache_dir()
            self._load_sidecar_index()
        logger.info(
            "Embedding cache initialized",
            extra={
                "cache_lru_size": self._lru_max,
                "filesystem_enabled": self._enable_filesystem,
                "cache_dir_basename": self._cache_dir.name,
            },
        )

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    @property
    def lru_size(self) -> int:
        with self._lock:
            return len(self._lru)

    def get_or_compute(
        self,
        key_parts: Sequence[str] | Mapping[str, str],
        compute_fn: Callable[[], CompositionEmbeddingV1],
        *,
        project_id: str | None = None,
    ) -> CompositionEmbeddingV1:
        """Return cached embedding or compute, store, and return a fresh card."""
        parts = _normalize_key_parts(key_parts)
        object_id = hash_cache_key(parts)
        model_id, profile_id, algorithm_version, fingerprint, scope_digest = parts
        fp_prefix = _fingerprint_prefix(fingerprint)
        scope_prefix = scope_digest[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN]

        with self._lock:
            hit = self._get_locked(object_id)
            if hit is not None:
                logger.info(
                    "Embedding cache hit",
                    extra={
                        "cache_event": "hit",
                        "fingerprint_prefix": fp_prefix,
                        "scope_digest_prefix": scope_prefix,
                        "model_id": model_id,
                        "profile_id": profile_id,
                        "algorithm_version": algorithm_version,
                        "project_id": project_id,
                        "object_id_prefix": object_id[:12],
                    },
                )
                if project_id:
                    self._remember_project_fingerprint(project_id, fingerprint, object_id)
                return hit

        logger.info(
            "Embedding cache miss",
            extra={
                "cache_event": "miss",
                "fingerprint_prefix": fp_prefix,
                "scope_digest_prefix": scope_prefix,
                "model_id": model_id,
                "profile_id": profile_id,
                "algorithm_version": algorithm_version,
                "project_id": project_id,
                "object_id_prefix": object_id[:12],
            },
        )

        try:
            computed = compute_fn()
        except EmbeddingError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Embedding compute failed inside cache",
                extra={
                    "fingerprint_prefix": fp_prefix,
                    "error_type": type(exc).__name__,
                },
            )
            raise EmbeddingError(
                "embedding_internal_error",
                details={"error_type": type(exc).__name__},
            ) from exc

        if not isinstance(computed, CompositionEmbeddingV1):
            raise EmbeddingError(
                "embedding_internal_error",
                details={"reason": "compute_fn_did_not_return_CompositionEmbeddingV1"},
            )

        with self._lock:
            # Another thread may have filled the slot; prefer stored card.
            existing = self._get_locked(object_id)
            if existing is not None:
                if project_id:
                    self._remember_project_fingerprint(project_id, fingerprint, object_id)
                return existing
            self._put_locked(object_id, computed, project_id=project_id, fingerprint=fingerprint)
        return computed

    def invalidate_by_project(self, project_id: str) -> int:
        """Drop all cached entries associated with ``project_id``. Returns count."""
        normalized = project_id.strip()
        if not normalized:
            return 0
        with self._lock:
            fingerprints = set(self._project_fingerprints.pop(normalized, set()))
            removed = 0
            object_ids: set[str] = set()
            for fp in fingerprints:
                object_ids |= set(self._fingerprint_keys.get(fp, set()))
            # Also drop any meta rows tagged with this project.
            for oid, (pid, fp) in list(self._key_meta.items()):
                if pid == normalized:
                    object_ids.add(oid)
                    fingerprints.add(fp)
            for oid in object_ids:
                if self._drop_object_locked(oid):
                    removed += 1
            self._persist_sidecar_index_locked()
            logger.info(
                "Embedding cache invalidated by project",
                extra={
                    "project_id": normalized,
                    "removed_count": removed,
                    "fingerprint_count": len(fingerprints),
                },
            )
            return removed

    def invalidate_by_fingerprint(self, fingerprint: str) -> int:
        """Drop entries whose source fingerprint matches. Returns count."""
        fp = fingerprint.strip()
        if not fp:
            return 0
        with self._lock:
            object_ids = set(self._fingerprint_keys.pop(fp, set()))
            for oid, (_pid, stored_fp) in list(self._key_meta.items()):
                if stored_fp == fp:
                    object_ids.add(oid)
            removed = 0
            for oid in object_ids:
                if self._drop_object_locked(oid):
                    removed += 1
            # Prune empty project → fingerprint sets.
            for pid, fps in list(self._project_fingerprints.items()):
                fps.discard(fp)
                if not fps:
                    del self._project_fingerprints[pid]
            self._persist_sidecar_index_locked()
            logger.info(
                "Embedding cache invalidated by fingerprint",
                extra={
                    "fingerprint_prefix": _fingerprint_prefix(fp),
                    "removed_count": removed,
                },
            )
            return removed

    def invalidate_all(self) -> int:
        """Clear process LRU, fingerprint maps, and optional filesystem entries."""
        with self._lock:
            removed = len(self._lru)
            self._lru.clear()
            self._project_fingerprints.clear()
            self._fingerprint_keys.clear()
            self._key_meta.clear()
            disk_removed = 0
            if self._enable_filesystem and self._cache_dir.is_dir():
                for path in self._cache_dir.glob(f"*{_ENTRY_SUFFIX}"):
                    try:
                        path.unlink(missing_ok=True)
                        disk_removed += 1
                    except OSError as exc:
                        logger.warning(
                            "Failed to remove embedding cache file",
                            extra={"error_type": type(exc).__name__},
                        )
                self._persist_sidecar_index_locked()
            logger.info(
                "Embedding cache cleared",
                extra={"removed_lru": removed, "removed_disk": disk_removed},
            )
            return removed + disk_removed

    # --- internals -----------------------------------------------------------

    def _ensure_cache_dir(self) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error(
                "Embedding cache directory unavailable",
                extra={
                    "error_type": type(exc).__name__,
                    "cache_dir_basename": self._cache_dir.name,
                },
            )
            raise EmbeddingError(
                "cache_unavailable",
                details={"error_type": type(exc).__name__},
            ) from exc

    def _entry_path(self, object_id: str) -> Path:
        return self._cache_dir / f"{object_id}{_ENTRY_SUFFIX}"

    def _get_locked(self, object_id: str) -> CompositionEmbeddingV1 | None:
        if object_id in self._lru:
            self._lru.move_to_end(object_id)
            return self._lru[object_id]
        if not self._enable_filesystem:
            return None
        path = self._entry_path(object_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            card = CompositionEmbeddingV1.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Embedding cache disk entry unreadable; treating as miss",
                extra={
                    "object_id_prefix": object_id[:12],
                    "error_type": type(exc).__name__,
                },
            )
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        self._lru[object_id] = card
        self._lru.move_to_end(object_id)
        self._evict_if_needed_locked()
        return card

    def _put_locked(
        self,
        object_id: str,
        card: CompositionEmbeddingV1,
        *,
        project_id: str | None,
        fingerprint: str,
    ) -> None:
        self._lru[object_id] = card
        self._lru.move_to_end(object_id)
        self._key_meta[object_id] = (project_id, fingerprint)
        self._fingerprint_keys.setdefault(fingerprint, set()).add(object_id)
        if project_id:
            self._remember_project_fingerprint(project_id, fingerprint, object_id)
        self._evict_if_needed_locked()
        if self._enable_filesystem:
            self._write_disk_entry(object_id, card)
            self._persist_sidecar_index_locked()

    def _remember_project_fingerprint(
        self, project_id: str, fingerprint: str, object_id: str
    ) -> None:
        self._project_fingerprints.setdefault(project_id, set()).add(fingerprint)
        self._fingerprint_keys.setdefault(fingerprint, set()).add(object_id)
        pid, _fp = self._key_meta.get(object_id, (None, fingerprint))
        self._key_meta[object_id] = (project_id or pid, fingerprint)

    def _evict_if_needed_locked(self) -> None:
        while len(self._lru) > self._lru_max:
            evicted_id, _card = self._lru.popitem(last=False)
            # Keep disk + fingerprint maps so FS can still hit; only process LRU shrinks.
            logger.debug(
                "Embedding cache LRU eviction",
                extra={
                    "object_id_prefix": evicted_id[:12],
                    "lru_size": len(self._lru),
                    "lru_max": self._lru_max,
                },
            )

    def _drop_object_locked(self, object_id: str) -> bool:
        removed = False
        if object_id in self._lru:
            del self._lru[object_id]
            removed = True
        meta = self._key_meta.pop(object_id, None)
        if meta is not None:
            _pid, fingerprint = meta
            keys = self._fingerprint_keys.get(fingerprint)
            if keys is not None:
                keys.discard(object_id)
                if not keys:
                    del self._fingerprint_keys[fingerprint]
            removed = True
        if self._enable_filesystem:
            path = self._entry_path(object_id)
            if path.is_file():
                try:
                    path.unlink()
                    removed = True
                except OSError as exc:
                    logger.warning(
                        "Failed to unlink embedding cache entry",
                        extra={"error_type": type(exc).__name__},
                    )
        return removed

    def _write_disk_entry(self, object_id: str, card: CompositionEmbeddingV1) -> None:
        path = self._entry_path(object_id)
        try:
            path.write_text(
                card.model_dump_json(),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "Failed to write embedding cache entry",
                extra={
                    "object_id_prefix": object_id[:12],
                    "error_type": type(exc).__name__,
                },
            )

    def _load_sidecar_index(self) -> None:
        if not self._index_path.is_file():
            return
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Embedding cache sidecar index unreadable",
                extra={"error_type": type(exc).__name__},
            )
            return
        projects = raw.get("projects") if isinstance(raw, dict) else None
        if not isinstance(projects, dict):
            return
        for project_id, fingerprints in projects.items():
            if not isinstance(project_id, str) or not isinstance(fingerprints, list):
                continue
            cleaned = {str(fp) for fp in fingerprints if isinstance(fp, str) and fp}
            if cleaned:
                self._project_fingerprints[project_id] = cleaned

    def _persist_sidecar_index_locked(self) -> None:
        if not self._enable_filesystem:
            return
        payload: dict[str, Any] = {
            "schema": "embedding.cache.project_index.v1",
            "projects": {
                pid: sorted(fps) for pid, fps in sorted(self._project_fingerprints.items())
            },
        }
        try:
            self._ensure_cache_dir()
            self._index_path.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
        except (OSError, EmbeddingError) as exc:
            logger.warning(
                "Failed to persist embedding cache sidecar index",
                extra={"error_type": type(exc).__name__},
            )


_default_cache: EmbeddingCache | None = None
_default_lock = threading.Lock()


def get_default_embedding_cache(
    settings: EmbeddingSettings | None = None,
    *,
    reset: bool = False,
) -> EmbeddingCache:
    """Process-wide cache singleton (tests may ``reset=True``)."""
    global _default_cache
    with _default_lock:
        if reset or _default_cache is None:
            _default_cache = EmbeddingCache(settings=settings)
        return _default_cache
