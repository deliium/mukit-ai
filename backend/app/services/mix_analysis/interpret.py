"""Optional AI interpretation for mix analysis — critique-shaped, no new AiOperation.

Under ``LLM_FAKE_MODE`` or ``MIX_ANALYSIS_FAKE_MODE`` emits deterministic
interpretations that cite existing measurement/observation codes.
Without fake and without a wired language client → ``interpretation_unavailable``.
Never invents numeric measurement values. Never fails the whole analyze.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Mapping, Sequence

from app.mix_analysis_schemas import (
    MIX_ANALYSIS_INTERPRETATION_UNAVAILABLE,
    MixAnalysisInterpretation,
    MixAnalysisMeasurement,
    MixAnalysisObservation,
    MixAnalysisWarning,
)

logger = logging.getLogger(__name__)

InterpretStatus = str  # skipped | ok | unavailable


def run_mix_interpretation(
    *,
    measurements: Sequence[MixAnalysisMeasurement],
    observations: Sequence[MixAnalysisObservation],
    include_ai_interpretation: bool,
    env: Mapping[str, str] | None = None,
) -> tuple[list[MixAnalysisInterpretation], list[MixAnalysisWarning], InterpretStatus]:
    source = env if env is not None else os.environ
    if not include_ai_interpretation:
        logger.info(
            "Mix interpretation skipped",
            extra={"include_flag": False, "model_id_prefix": "skipped", "interpretation_count": 0},
        )
        return [], [], "skipped"

    started = time.perf_counter()
    fake = _fake_enabled(source)
    if fake:
        interpretations = _fake_interpretations(measurements, observations)
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "Mix interpretation fake ok",
            extra={
                "include_flag": True,
                "model_id_prefix": "fake",
                "interpretation_count": len(interpretations),
                "duration_ms": duration_ms,
            },
        )
        return interpretations, [], "ok"

    # No production language client wired for mix analysis in v1.
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Mix interpretation unavailable",
        extra={
            "include_flag": True,
            "model_id_prefix": "unavailable",
            "interpretation_count": 0,
            "duration_ms": duration_ms,
        },
    )
    warning = MixAnalysisWarning(
        code=MIX_ANALYSIS_INTERPRETATION_UNAVAILABLE,
        severity="info",
        message=(
            "AI interpretation unavailable without MIX_ANALYSIS_FAKE_MODE or LLM_FAKE_MODE; "
            "DSP measurements and observations are still returned."
        ),
    )
    return [], [warning], "unavailable"


def _fake_enabled(env: Mapping[str, str]) -> bool:
    for key in ("MIX_ANALYSIS_FAKE_MODE", "LLM_FAKE_MODE"):
        raw = str(env.get(key, "")).strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
    return False


def _fake_interpretations(
    measurements: Sequence[MixAnalysisMeasurement],
    observations: Sequence[MixAnalysisObservation],
) -> list[MixAnalysisInterpretation]:
    if not observations:
        # Still cite a measurement if present
        if not measurements:
            return []
        m = measurements[0]
        return [
            MixAnalysisInterpretation(
                code="ai_mix_note",
                message=(
                    f"Advisory: review {_roles(m.locus)} based on measured "
                    f"{m.code} (value cited from measurements only)."
                ),
                locus=m.locus,
                cites_measurement_codes=[m.code],
                cites_observation_codes=[],
                suggested_action=(
                    f"Inspect {_roles(m.locus)} around the measured window; "
                    "do not treat this prose as a new numeric reading."
                ),
            )
        ]

    out: list[MixAnalysisInterpretation] = []
    for obs in observations[:3]:
        cite_m = list(obs.evidence.measurement_codes) if obs.evidence else []
        out.append(
            MixAnalysisInterpretation(
                code="ai_mix_note",
                message=(
                    f"Advisory production note on {_roles(obs.locus)}: {obs.message} "
                    f"This restates observation `{obs.code}` — it does not invent new levels."
                ),
                locus=obs.locus,
                cites_measurement_codes=cite_m,
                cites_observation_codes=[obs.code],
                suggested_action=obs.suggested_action,
            )
        )
    return out


def _roles(locus) -> str:
    if locus.stem_roles:
        return " and ".join(locus.stem_roles)
    if locus.source_track_ids:
        return " and ".join(locus.source_track_ids)
    return "the analyzed stems"
