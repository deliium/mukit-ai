"""Thresholds / knobs for the Music Evaluation Engine."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CritiqueEngineSettings:
    """Configurable constants — not scattered magic numbers."""

    climax_note_load_relative_delta_max: float = 0.08
    climax_peak_velocity_relative_delta_max: float = 0.10
    section_contrast_note_load_delta_min: float = 0.05
    max_findings: int = 64
    engine_version: str = "critique.engine.v1"
    treat_chorus_as_climax: bool = False


def load_critique_engine_settings(env: dict[str, str] | None = None) -> CritiqueEngineSettings:
    source = env if env is not None else os.environ
    def _float(key: str, default: float) -> float:
        raw = source.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def _bool(key: str, default: bool) -> bool:
        raw = source.get(key)
        if raw is None or str(raw).strip() == "":
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    return CritiqueEngineSettings(
        climax_note_load_relative_delta_max=_float(
            "CRITIQUE_CLIMAX_NOTE_LOAD_DELTA_MAX", 0.08
        ),
        climax_peak_velocity_relative_delta_max=_float(
            "CRITIQUE_CLIMAX_VELOCITY_DELTA_MAX", 0.10
        ),
        treat_chorus_as_climax=_bool("CRITIQUE_TREAT_CHORUS_AS_CLIMAX", False),
    )
