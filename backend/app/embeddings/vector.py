"""Vector helpers for symbolic embeddings (L2 normalize, distances).

Similarity is distributional / structural affinity — never musical quality.
Full vectors must not be logged at INFO; DEBUG may log a short prefix only.
"""

from __future__ import annotations

import logging
import math
from typing import Sequence

from app.embeddings.settings import EMBEDDING_VECTOR_DEBUG_PREFIX_DIMS


logger = logging.getLogger(__name__)


def l2_norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(float(v) * float(v) for v in vector))


def l2_normalize(vector: Sequence[float], *, eps: float = 1e-12) -> list[float]:
    """Return an L2-normalized copy. Zero vectors stay zero (caller may reject)."""
    norm = l2_norm(vector)
    if norm < eps:
        logger.debug(
            "L2 normalize encountered near-zero vector",
            extra={"dims": len(vector), "norm": norm},
        )
        return [0.0] * len(vector)
    inv = 1.0 / norm
    return [float(v) * inv for v in vector]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"cosine_similarity dim mismatch: {len(a)} != {len(b)}")
    if not a:
        return 0.0
    # Prefer assuming L2-normalized inputs (dot product); still safe if not.
    dot = sum(float(x) * float(y) for x, y in zip(a, b, strict=True))
    na = l2_norm(a)
    nb = l2_norm(b)
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    return 1.0 - cosine_similarity(a, b)


def euclidean_distance(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"euclidean_distance dim mismatch: {len(a)} != {len(b)}")
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b, strict=True)))


def vector_debug_prefix(
    vector: Sequence[float],
    *,
    dims: int = EMBEDDING_VECTOR_DEBUG_PREFIX_DIMS,
) -> list[float]:
    return [round(float(v), 6) for v in vector[: max(0, dims)]]


def feature_vector_digest(vector: Sequence[float], *, precision: int = 6) -> str:
    """Stable short digest of a rounded vector (for fixture assertions)."""
    import hashlib

    payload = ",".join(f"{round(float(v), precision):.{precision}f}" for v in vector)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
