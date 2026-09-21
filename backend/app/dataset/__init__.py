"""Offline symbolic music dataset pipeline (filesystem corpora).

Hard isolation rule
-------------------
All dataset I/O uses ``DATASET_ROOT`` / operator ``--out`` paths only.
This package must **never** write to ``PROJECT_DB_PATH``, ``project_store``,
revision history, or user project APIs.

Musical bodies remain strict ``composition.v2``; training metadata lives in
``dataset.item.v1`` / ``dataset.example.v1`` envelopes. CLI entry:
``python -m app.dataset.cli``.
"""

from __future__ import annotations

from app.dataset.settings import DATASET_PIPELINE_VERSION, DatasetSettings, load_dataset_settings

__all__ = [
    "DATASET_PIPELINE_VERSION",
    "DatasetSettings",
    "load_dataset_settings",
]
