"""Symbolic composition embeddings (handcrafted-first; registry-compatible).

Hard isolation rules
--------------------
* Playable notes come **only** from ``composition.v2`` ``tracks[].events[]``.
  Never invent notes from ``harmony``, markers, sections, or analysis sidecars.
* Never persist ``composition.analysis.v1`` reports as embedding rows.
* Never copy user projects into ``DATASET_ROOT`` unless
  ``EMBEDDING_ALLOW_DATASET_CORPUS`` is explicitly enabled **and** an export
  helper is invoked — default is refuse with ``dataset_export_forbidden``.
* Artist / composer names are **not** embedding dimensions or style ids.
* Cache lives under ``EMBEDDING_CACHE_DIR`` (beside project data), never under
  ``DATASET_ROOT``.
* Similarity is distributional / structural affinity — not musical quality.

Default production model: ``local:symbolic-features-v1`` (profile
``symbolic.features.v1``). Learned adapters are deferred.
"""

from __future__ import annotations

from app.embeddings.settings import (
    EMBEDDING_ALGORITHM_VERSION,
    EMBEDDING_DEFAULT_MODEL_ID,
    EMBEDDING_PROFILE_ID,
    EmbeddingSettings,
    load_embedding_settings,
)

__all__ = [
    "EMBEDDING_ALGORITHM_VERSION",
    "EMBEDDING_DEFAULT_MODEL_ID",
    "EMBEDDING_PROFILE_ID",
    "EmbeddingSettings",
    "load_embedding_settings",
]
