"""Versioned Composition V2 symbolic tokenizer (train + inference codec).

Hard isolation rules
--------------------
* Filesystem / library / CLI only — ``python -m app.tokenizer.cli``.
* Never write to ``PROJECT_DB_PATH``, ``project_store``, revision history,
  or user project APIs.
* Playable notes come **only** from ``composition.v2`` ``tracks[].events[]``.
  Never invent notes from ``harmony``, markers, sections, or analysis sidecars.
* No FastAPI routes in this milestone; do not load model weights / GGUF.

Canonical temporal scheme (locked)
----------------------------------
REMI-style bar + in-bar position + duration on a fixed grid derived from
``ticks_per_quarter`` and ``grid_subdivisions_per_quarter``. One vocabulary
serves both training collation and inference detokenization.
"""

from __future__ import annotations

from app.tokenizer.settings import TOKENIZER_VERSION, TokenizerSettings, load_tokenizer_settings

__all__ = [
    "TOKENIZER_VERSION",
    "TokenizerSettings",
    "load_tokenizer_settings",
]
