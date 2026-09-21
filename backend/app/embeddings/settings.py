"""Bounded ``EMBEDDING_*`` defaults for symbolic feature embeddings.

Cache and index artifacts live beside project data (never under
``DATASET_ROOT``). Runtime verbosity stays controlled by ``LOG_LEVEL``.

Artist / composer names are never embedding dimensions — see package docstring.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


logger = logging.getLogger(__name__)

# Mirror backend/app/db/connection.py default without importing Alembic.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROJECT_DB_PATH = _BACKEND_ROOT / "data" / "projects.db"

# Contract / algorithm identity (logged at INFO on settings load).
EMBEDDING_SCHEMA_VERSION = "composition.embedding.v1"
EMBEDDING_SCOPE_SCHEMA_VERSION = "composition.embed_scope.v1"
EMBEDDING_SIMILARITY_QUERY_SCHEMA = "composition.similarity_query.v1"
EMBEDDING_SIMILARITY_HIT_SCHEMA = "composition.similarity_hit.v1"
EMBEDDING_REFERENCE_PROVENANCE_SCHEMA = "composition.reference_provenance.v1"
EMBEDDING_STYLE_CONDITIONING_SCHEMA = "composition.style_conditioning.v1"

# Handcrafted production profile (V3 default).
EMBEDDING_PROFILE_ID = "symbolic.features.v1"
EMBEDDING_ALGORITHM_VERSION = "symbolic.features.v1.algo.1"
EMBEDDING_DEFAULT_MODEL_ID = "local:symbolic-features-v1"
EMBEDDING_DEFAULT_RUNTIME = "symbolic_features"
EMBEDDING_PROJECTION_NONE = "none"

# Distance metric for L2-normalized vectors (cosine affinity).
EMBEDDING_DEFAULT_DISTANCE_METRIC = "cosine"

# Logging / cache key prefixes (never log full fingerprints or vectors at INFO).
EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN = 12
EMBEDDING_VECTOR_DEBUG_PREFIX_DIMS = 8

_DEFAULT_MAX_DIMS = 256
_DEFAULT_TOP_K = 10
_DEFAULT_MAX_TOP_K = 50
_DEFAULT_CACHE_LRU_SIZE = 256
_DEFAULT_INDEX_MAX_PROJECTS = 500
_DEFAULT_INDEX_MAX_ENTRIES = 5000
_DEFAULT_ALLOW_DATASET_CORPUS = False
_DEFAULT_FEATURE_SUMMARY_MAX_CHARS = 1200
_DEFAULT_PROMPT_VECTOR_MAX_DIMS = 0  # 0 = never dump vector into prompts


@dataclass(frozen=True)
class EmbeddingSettings:
    """Env-backed embedding subsystem limits (no secrets)."""

    profile_id: str
    algorithm_version: str
    default_model_id: str
    cache_dir: Path
    cache_lru_size: int
    max_dims: int
    default_top_k: int
    max_top_k: int
    index_max_projects: int
    index_max_entries: int
    allow_dataset_corpus: bool
    distance_metric: str
    feature_summary_max_chars: int
    prompt_vector_max_dims: int
    projection_id: str

    @property
    def dataset_corpus_enabled(self) -> bool:
        return self.allow_dataset_corpus


def _resolve_project_db_path(env: Mapping[str, str]) -> Path:
    raw = (env.get("PROJECT_DB_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_PROJECT_DB_PATH


def default_embedding_cache_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve default cache dir next to the project DB (never DATASET_ROOT)."""
    source = env if env is not None else os.environ
    db_path = _resolve_project_db_path(source)
    return db_path.parent / "embedding_cache"


def load_embedding_settings(env: Mapping[str, str] | None = None) -> EmbeddingSettings:
    source = env if env is not None else os.environ

    cache_raw = (source.get("EMBEDDING_CACHE_DIR") or "").strip()
    if cache_raw:
        cache_dir = Path(cache_raw).expanduser()
        # Refuse pointing cache at an obvious dataset root.
        dataset_root = (source.get("DATASET_ROOT") or "").strip()
        if dataset_root:
            try:
                if cache_dir.resolve() == Path(dataset_root).expanduser().resolve():
                    logger.error(
                        "EMBEDDING_CACHE_DIR must not equal DATASET_ROOT; using default",
                        extra={"setting_name": "EMBEDDING_CACHE_DIR"},
                    )
                    cache_dir = default_embedding_cache_dir(source)
            except OSError:
                cache_dir = default_embedding_cache_dir(source)
    else:
        cache_dir = default_embedding_cache_dir(source)

    max_top_k = _int_env(
        source,
        "EMBEDDING_MAX_TOP_K",
        _DEFAULT_MAX_TOP_K,
        minimum=1,
        maximum=200,
    )
    default_top_k = _int_env(
        source,
        "EMBEDDING_DEFAULT_TOP_K",
        _DEFAULT_TOP_K,
        minimum=1,
        maximum=max_top_k,
    )
    if default_top_k > max_top_k:
        logger.warning(
            "EMBEDDING_DEFAULT_TOP_K exceeds MAX_TOP_K; clamping default",
            extra={"default_top_k": default_top_k, "max_top_k": max_top_k},
        )
        default_top_k = max_top_k

    settings = EmbeddingSettings(
        profile_id=EMBEDDING_PROFILE_ID,
        algorithm_version=EMBEDDING_ALGORITHM_VERSION,
        default_model_id=EMBEDDING_DEFAULT_MODEL_ID,
        cache_dir=cache_dir,
        cache_lru_size=_int_env(
            source,
            "EMBEDDING_CACHE_LRU_SIZE",
            _DEFAULT_CACHE_LRU_SIZE,
            minimum=8,
            maximum=10_000,
        ),
        max_dims=_int_env(
            source,
            "EMBEDDING_MAX_DIMS",
            _DEFAULT_MAX_DIMS,
            minimum=16,
            maximum=4096,
        ),
        default_top_k=default_top_k,
        max_top_k=max_top_k,
        index_max_projects=_int_env(
            source,
            "EMBEDDING_INDEX_MAX_PROJECTS",
            _DEFAULT_INDEX_MAX_PROJECTS,
            minimum=1,
            maximum=10_000,
        ),
        index_max_entries=_int_env(
            source,
            "EMBEDDING_INDEX_MAX_ENTRIES",
            _DEFAULT_INDEX_MAX_ENTRIES,
            minimum=1,
            maximum=100_000,
        ),
        allow_dataset_corpus=_bool_env(
            source,
            "EMBEDDING_ALLOW_DATASET_CORPUS",
            _DEFAULT_ALLOW_DATASET_CORPUS,
        ),
        distance_metric=_str_env(
            source,
            "EMBEDDING_DISTANCE_METRIC",
            EMBEDDING_DEFAULT_DISTANCE_METRIC,
        ),
        feature_summary_max_chars=_int_env(
            source,
            "EMBEDDING_FEATURE_SUMMARY_MAX_CHARS",
            _DEFAULT_FEATURE_SUMMARY_MAX_CHARS,
            minimum=64,
            maximum=8_000,
        ),
        prompt_vector_max_dims=_int_env(
            source,
            "EMBEDDING_PROMPT_VECTOR_MAX_DIMS",
            _DEFAULT_PROMPT_VECTOR_MAX_DIMS,
            minimum=0,
            maximum=64,
        ),
        projection_id=_str_env(source, "EMBEDDING_PROJECTION_ID", EMBEDDING_PROJECTION_NONE),
    )

    logger.info(
        "Embedding settings loaded",
        extra={
            "schema_version": EMBEDDING_SCHEMA_VERSION,
            "profile_id": settings.profile_id,
            "algorithm_version": settings.algorithm_version,
            "default_model_id": settings.default_model_id,
            "max_dims": settings.max_dims,
            "default_top_k": settings.default_top_k,
            "max_top_k": settings.max_top_k,
            "index_max_projects": settings.index_max_projects,
            "allow_dataset_corpus": settings.allow_dataset_corpus,
            "distance_metric": settings.distance_metric,
            "projection_id": settings.projection_id,
            "cache_dir_basename": settings.cache_dir.name,
        },
    )
    logger.debug(
        "Embedding settings cache details",
        extra={
            "cache_lru_size": settings.cache_lru_size,
            "feature_summary_max_chars": settings.feature_summary_max_chars,
            "prompt_vector_max_dims": settings.prompt_vector_max_dims,
            "index_max_entries": settings.index_max_entries,
        },
    )
    return settings


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    logger.warning(
        "Invalid boolean embedding setting; using default",
        extra={"setting_name": name, "fallback": default},
    )
    return default


def _int_env(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = env.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning(
            "Invalid integer embedding setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Embedding setting out of range; clamped",
            extra={
                "setting_name": name,
                "fallback": clamped,
                "minimum": minimum,
                "maximum": maximum,
            },
        )
        return clamped
    return value


def _str_env(env: Mapping[str, str], name: str, default: str) -> str:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()
