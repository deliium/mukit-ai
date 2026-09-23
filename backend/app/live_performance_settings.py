"""Environment caps for co-performance + AI Jam live predict (cold path).

Bounds only — never secrets. Horizon defaults keep AI fill optional behind
local/fake degradation. LIVE_JAM_* hysteresis/analysis caps mirror FE
VITE_LIVE_JAM_*. See docs/co-performance.md / docs/ai-jam.md.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)

_DEFAULT_HORIZON_BARS_MIN = 1
_DEFAULT_HORIZON_BARS_MAX = 2
_DEFAULT_HORIZON_MS_MIN = 250
_DEFAULT_HORIZON_MS_MAX = 8_000
_DEFAULT_HORIZON_BARS = 1
_DEFAULT_HORIZON_MS = 2_000
_DEFAULT_MAX_EVENTS_PER_CHUNK = 64
_DEFAULT_MAX_FEATURES_BYTES = 4_096
_DEFAULT_PREDICT_TIMEOUT_MS = 1_500
_DEFAULT_MAX_IN_FLIGHT = 1
_DEFAULT_SCHEDULE_SLACK_MS = 80

_DEFAULT_JAM_HARMONY_CONFIDENCE_MIN = 0.55
_DEFAULT_JAM_HARMONY_DWELL_MS = 400
_DEFAULT_JAM_HARMONY_DWELL_MS_FLOOR = 150
_DEFAULT_JAM_HARMONY_HYSTERESIS = 0.15
_DEFAULT_JAM_ANALYSIS_MAX_EVENTS = 48
_DEFAULT_JAM_ANALYSIS_MAX_TICKS = 1920

_caps_logged = False


@dataclass(frozen=True)
class LivePerformanceSettings:
    """Bounded LIVE_* / LIVE_JAM_* knobs for predict and FE-aligned caps."""

    horizon_bars_min: int
    horizon_bars_max: int
    horizon_bars_default: int
    horizon_ms_min: int
    horizon_ms_max: int
    horizon_ms_default: int
    max_events_per_chunk: int
    max_features_bytes: int
    predict_timeout_ms: int
    max_in_flight: int
    schedule_slack_ms: int
    jam_harmony_confidence_min: float
    jam_harmony_dwell_ms: int
    jam_harmony_dwell_ms_floor: int
    jam_harmony_hysteresis: float
    jam_analysis_max_events: int
    jam_analysis_max_ticks: int


def load_live_performance_settings(
    env: Mapping[str, str] | None = None,
) -> LivePerformanceSettings:
    """Load ``LIVE_*`` caps with safe clamped defaults."""
    global _caps_logged
    source = env if env is not None else os.environ
    bars_min = _int_env(
        source,
        "LIVE_HORIZON_BARS_MIN",
        _DEFAULT_HORIZON_BARS_MIN,
        minimum=1,
        maximum=4,
    )
    bars_max = _int_env(
        source,
        "LIVE_HORIZON_BARS_MAX",
        _DEFAULT_HORIZON_BARS_MAX,
        minimum=bars_min,
        maximum=8,
    )
    bars_default = _int_env(
        source,
        "LIVE_HORIZON_BARS",
        _DEFAULT_HORIZON_BARS,
        minimum=bars_min,
        maximum=bars_max,
    )
    ms_min = _int_env(
        source,
        "LIVE_HORIZON_MS_MIN",
        _DEFAULT_HORIZON_MS_MIN,
        minimum=50,
        maximum=60_000,
    )
    ms_max = _int_env(
        source,
        "LIVE_HORIZON_MS_MAX",
        _DEFAULT_HORIZON_MS_MAX,
        minimum=ms_min,
        maximum=60_000,
    )
    ms_default = _int_env(
        source,
        "LIVE_HORIZON_MS",
        _DEFAULT_HORIZON_MS,
        minimum=ms_min,
        maximum=ms_max,
    )
    dwell_ms = _int_env(
        source,
        "LIVE_JAM_HARMONY_DWELL_MS",
        _DEFAULT_JAM_HARMONY_DWELL_MS,
        minimum=100,
        maximum=5_000,
    )
    dwell_floor = _int_env(
        source,
        "LIVE_JAM_HARMONY_DWELL_MS_FLOOR",
        _DEFAULT_JAM_HARMONY_DWELL_MS_FLOOR,
        minimum=50,
        maximum=dwell_ms,
    )
    settings = LivePerformanceSettings(
        horizon_bars_min=bars_min,
        horizon_bars_max=bars_max,
        horizon_bars_default=bars_default,
        horizon_ms_min=ms_min,
        horizon_ms_max=ms_max,
        horizon_ms_default=ms_default,
        max_events_per_chunk=_int_env(
            source,
            "LIVE_ACCOMP_MAX_EVENTS_PER_CHUNK",
            _DEFAULT_MAX_EVENTS_PER_CHUNK,
            minimum=1,
            maximum=256,
        ),
        max_features_bytes=_int_env(
            source,
            "LIVE_PREDICT_MAX_FEATURES_BYTES",
            _DEFAULT_MAX_FEATURES_BYTES,
            minimum=256,
            maximum=65_536,
        ),
        predict_timeout_ms=_int_env(
            source,
            "LIVE_PREDICT_TIMEOUT_MS",
            _DEFAULT_PREDICT_TIMEOUT_MS,
            minimum=100,
            maximum=30_000,
        ),
        max_in_flight=_int_env(
            source,
            "LIVE_PREDICT_MAX_IN_FLIGHT",
            _DEFAULT_MAX_IN_FLIGHT,
            minimum=1,
            maximum=1,  # v1 lock: exactly one in-flight
        ),
        schedule_slack_ms=_int_env(
            source,
            "LIVE_SCHEDULE_SLACK_MS",
            _DEFAULT_SCHEDULE_SLACK_MS,
            minimum=0,
            maximum=2_000,
        ),
        jam_harmony_confidence_min=_float_env(
            source,
            "LIVE_JAM_HARMONY_CONFIDENCE_MIN",
            _DEFAULT_JAM_HARMONY_CONFIDENCE_MIN,
            minimum=0.1,
            maximum=0.95,
        ),
        jam_harmony_dwell_ms=dwell_ms,
        jam_harmony_dwell_ms_floor=dwell_floor,
        jam_harmony_hysteresis=_float_env(
            source,
            "LIVE_JAM_HARMONY_HYSTERESIS",
            _DEFAULT_JAM_HARMONY_HYSTERESIS,
            minimum=0.0,
            maximum=0.5,
        ),
        jam_analysis_max_events=_int_env(
            source,
            "LIVE_JAM_ANALYSIS_MAX_EVENTS",
            _DEFAULT_JAM_ANALYSIS_MAX_EVENTS,
            minimum=8,
            maximum=256,
        ),
        jam_analysis_max_ticks=_int_env(
            source,
            "LIVE_JAM_ANALYSIS_MAX_TICKS",
            _DEFAULT_JAM_ANALYSIS_MAX_TICKS,
            minimum=240,
            maximum=15_360,
        ),
    )
    if not _caps_logged or env is not None:
        logger.info(
            "Live performance settings loaded",
            extra={
                "horizon_bars": settings.horizon_bars_default,
                "horizon_bars_min": settings.horizon_bars_min,
                "horizon_bars_max": settings.horizon_bars_max,
                "horizon_ms": settings.horizon_ms_default,
                "horizon_ms_min": settings.horizon_ms_min,
                "horizon_ms_max": settings.horizon_ms_max,
                "max_events_per_chunk": settings.max_events_per_chunk,
                "max_features_bytes": settings.max_features_bytes,
                "predict_timeout_ms": settings.predict_timeout_ms,
                "max_in_flight": settings.max_in_flight,
                "schedule_slack_ms": settings.schedule_slack_ms,
                "jam_harmony_confidence_min": settings.jam_harmony_confidence_min,
                "jam_harmony_dwell_ms": settings.jam_harmony_dwell_ms,
                "jam_harmony_dwell_ms_floor": settings.jam_harmony_dwell_ms_floor,
                "jam_harmony_hysteresis": settings.jam_harmony_hysteresis,
                "jam_analysis_max_events": settings.jam_analysis_max_events,
                "jam_analysis_max_ticks": settings.jam_analysis_max_ticks,
            },
        )
        if env is None:
            _caps_logged = True
    return settings


def _int_env(
    source: Mapping[str, str],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = source.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        logger.warning(
            "Invalid live performance int env; using default",
            extra={"key": key, "default": default},
        )
        return default
    return max(minimum, min(maximum, value))


def _float_env(
    source: Mapping[str, str],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = source.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(str(raw).strip())
    except ValueError:
        logger.warning(
            "Invalid live performance float env; using default",
            extra={"key": key, "default": default},
        )
        return default
    return max(minimum, min(maximum, value))
