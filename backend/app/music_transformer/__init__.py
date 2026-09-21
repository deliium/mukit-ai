"""Symbolic Music Transformer (PyTorch) — train/inference library + CLI.

Hard isolation rules
--------------------
* Offline library / CLI: ``python -m app.music_transformer.cli``.
* May load ``.pt`` checkpoints **only** inside this package / inference adapter.
* Never write to ``PROJECT_DB_PATH``, ``project_store``, or revision history.
* Playable notes come **only** from tokenizer decode → ``tracks[].events[]``.
  Never invent notes from harmony, markers, sections, or analysis sidecars.
* Model / train modules must **not** import FastAPI or routers.
* Consume ``tokenizer.v1`` only (same vocab for train + inference).
* Do not load GGUF / llama.cpp weights; do not put ``nn.Module`` in
  ``ai_runtime/runtimes/``.
* Schemas and settings import without requiring ``torch`` (lazy import).
"""

from __future__ import annotations

from app.music_transformer.settings import (
    MUSIC_TRANSFORMER_VERSION,
    MusicTransformerSettings,
    load_music_transformer_settings,
)

__all__ = [
    "MUSIC_TRANSFORMER_VERSION",
    "MusicTransformerSettings",
    "load_music_transformer_settings",
]
