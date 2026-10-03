"""Locked ship-1 constants for the performance conductor layer.

Rubato and microtiming map to per-note ``tick_delta`` only — never Transport.bpm.
Fingerprint algorithm is ``composition_snapshot_fingerprint``.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

ENGINE_VERSION: str = "performance.conductor.v1.0"
PLAN_SCHEMA_VERSION: Literal["performance.plan.v1"] = "performance.plan.v1"
REALIZATION_SCHEMA_VERSION: Literal["performance.realization.v1"] = (
    "performance.realization.v1"
)

# Microtiming / rubato hard caps (Task 1 freeze).
MICROTIMING_MAX_ABS_TICKS: int = 60
RUBATO_TEMPO_FACTOR_MIN: float = 0.85
RUBATO_TEMPO_FACTOR_MAX: float = 1.15
# |tick_delta| ≤ min(MICROTIMING_MAX_ABS_TICKS, max(0, duration_ticks - 1))
TICK_DELTA_HARD_CAP: int = MICROTIMING_MAX_ABS_TICKS

VELOCITY_MIN: int = 1
VELOCITY_MAX: int = 127
DURATION_FLOOR_TICKS: int = 1  # duration_ticks + duration_delta >= 1

PresetId = Literal["intimate", "dramatic", "restrained", "mechanical", "custom"]
PRESET_IDS: frozenset[str] = frozenset(
    {"intimate", "dramatic", "restrained", "mechanical", "custom"}
)
CATALOG_PRESET_IDS: tuple[str, ...] = (
    "mechanical",
    "restrained",
    "intimate",
    "dramatic",
)

PhraseAnchor = Literal["bar", "section"]
PedalStyle = Literal["none", "literal", "harmonic", "dry"]

# Dimension parameter ranges (closed numeric caps).
TEMPO_RUBATO_DEPTH_MAX: float = 1.0
TEMPO_RUBATO_RATE_MAX: float = 4.0
DYNAMICS_STRENGTH_MAX: float = 1.0
DYNAMICS_CONTRAST_MAX: float = 1.0
PHRASING_BREATH_GAP_MAX: int = 120
PHRASING_ARC_MAX: float = 1.0
ARTICULATION_BIAS_ABS_MAX: float = 1.0
PEDALING_DEPTH_MAX: float = 1.0
MICROTIMING_SWING_MAX: float = 1.0
MICROTIMING_HUMANIZE_MAX: float = 1.0
ACCENT_STRENGTH_MAX: float = 1.0
ORCHESTRAL_BALANCE_GAIN_MIN: float = 0.25
ORCHESTRAL_BALANCE_GAIN_MAX: float = 1.5
TRACK_GAIN_MIN: float = 0.0
TRACK_GAIN_MAX: float = 1.0

# Seed parameter tables — Task 1 freeze (distinguishable across presets).
PRESET_DIMENSIONS: dict[str, dict[str, Any]] = {
    "mechanical": {
        "tempo_rubato": {"depth": 0.0, "rate": 0.0, "phrase_anchor": "bar"},
        "dynamics": {
            "curve_strength": 0.0,
            "contrast": 0.0,
            "velocity_floor": 1,
            "velocity_ceiling": 127,
        },
        "phrasing": {"breath_gap_ticks": 0, "phrase_arc": 0.0},
        "articulation": {"legato_bias": 0.0, "staccato_bias": 0.0},
        "pedaling": {"style": "literal", "depth": 0.0},
        "microtiming": {"swing": 0.0, "humanize": 0.0},
        "accent": {"downbeat": 0.0, "offbeat": 0.0},
        "orchestral_balance": {"role_gains": {}, "track_gains": {}},
    },
    "restrained": {
        "tempo_rubato": {"depth": 0.18, "rate": 0.6, "phrase_anchor": "bar"},
        "dynamics": {
            "curve_strength": 0.25,
            "contrast": 0.2,
            "velocity_floor": 40,
            "velocity_ceiling": 110,
        },
        "phrasing": {"breath_gap_ticks": 12, "phrase_arc": 0.2},
        "articulation": {"legato_bias": 0.1, "staccato_bias": 0.05},
        "pedaling": {"style": "dry", "depth": 0.25},
        "microtiming": {"swing": 0.05, "humanize": 0.12},
        "accent": {"downbeat": 0.15, "offbeat": 0.05},
        "orchestral_balance": {
            "role_gains": {"melody": 1.05, "harmony": 0.95, "bass": 1.0},
            "track_gains": {},
        },
    },
    "intimate": {
        "tempo_rubato": {"depth": 0.35, "rate": 0.9, "phrase_anchor": "section"},
        "dynamics": {
            "curve_strength": 0.45,
            "contrast": 0.35,
            "velocity_floor": 35,
            "velocity_ceiling": 95,
        },
        "phrasing": {"breath_gap_ticks": 24, "phrase_arc": 0.45},
        "articulation": {"legato_bias": 0.45, "staccato_bias": -0.1},
        "pedaling": {"style": "harmonic", "depth": 0.55},
        "microtiming": {"swing": 0.08, "humanize": 0.22},
        "accent": {"downbeat": 0.2, "offbeat": 0.08},
        "orchestral_balance": {
            "role_gains": {"melody": 1.1, "harmony": 0.9, "bass": 0.95},
            "track_gains": {},
        },
    },
    "dramatic": {
        "tempo_rubato": {"depth": 0.7, "rate": 1.4, "phrase_anchor": "section"},
        "dynamics": {
            "curve_strength": 0.85,
            "contrast": 0.8,
            "velocity_floor": 25,
            "velocity_ceiling": 127,
        },
        "phrasing": {"breath_gap_ticks": 36, "phrase_arc": 0.75},
        "articulation": {"legato_bias": 0.15, "staccato_bias": 0.35},
        "pedaling": {"style": "harmonic", "depth": 0.75},
        "microtiming": {"swing": 0.15, "humanize": 0.45},
        "accent": {"downbeat": 0.55, "offbeat": 0.2},
        "orchestral_balance": {
            "role_gains": {"melody": 1.2, "harmony": 0.85, "bass": 1.1, "percussion": 1.15},
            "track_gains": {},
        },
    },
}

PRESET_DEFAULT_SEEDS: dict[str, int] = {
    "mechanical": 0,
    "restrained": 17,
    "intimate": 42,
    "dramatic": 99,
}


def log_engine_version() -> None:
    """DEBUG-only engine_version emission for tests / boot probes."""
    logger.debug(
        "Performance conductor engine_version",
        extra={"engine_version": ENGINE_VERSION},
    )
