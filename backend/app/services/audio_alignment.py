"""Parametric audio↔symbolic alignment builders and converters.

v1 map:
  source_seconds = downbeat_offset_seconds
                 + tick_to_seconds(timeline, tick - origin_tick)

Inverse via seconds_to_tick. Quality lowers when V2 tempo diverges from
scaffolding beyond epsilon (issue ``alignment_tempo_diverged``).

Logging: INFO build summary; WARN diverged tempo; DEBUG ≤5 sample pairs.
Never logs full event arrays or PCM.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.audio_alignment_schemas import (
    AUDIO_ALIGNMENT_SCHEMA_VERSION,
    AudioAlignmentError,
    AudioAlignmentMapParams,
    AudioAlignmentQuality,
    AudioAlignmentStemBinding,
    AudioAlignmentStemQuality,
    AudioAlignmentV1,
)
from app.composition_schemas import CompositionV2
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.composition_timeline import CompiledTimeline, compile_timeline


logger = logging.getLogger(__name__)

# Soft thresholds for quality issues (product-tunable; not env settings in v1).
TEMPO_DIVERGENCE_EPSILON_BPM = 0.5
LOW_OVERALL_CONFIDENCE = 0.45
SPARSE_BEAT_GRID_CONFIDENCE = 0.4
DEFAULT_OFFSET_UNCERTAINTY_MS = 25.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _id_prefix(value: str | None, length: int = 8) -> str | None:
    if not value:
        return None
    return str(value)[:length]


def tick_to_source_seconds(
    tick: int,
    *,
    timeline: CompiledTimeline,
    downbeat_offset_seconds: float,
    origin_tick: int = 0,
) -> float:
    """Map a composition tick to source-audio seconds."""
    relative = max(0, int(tick) - int(origin_tick))
    # Clamp to timeline duration for seek safety.
    clamped = min(relative, timeline.duration_ticks)
    musical = timeline.tick_to_seconds(clamped)
    seconds = float(downbeat_offset_seconds) + musical
    logger.debug(
        "tick_to_source_seconds",
        extra={"tick": tick, "seconds": round(seconds, 6), "origin_tick": origin_tick},
    )
    return seconds


def source_seconds_to_tick(
    source_seconds: float,
    *,
    timeline: CompiledTimeline,
    downbeat_offset_seconds: float,
    origin_tick: int = 0,
) -> int:
    """Map source-audio seconds to a composition tick (half-up via timeline)."""
    musical = max(0.0, float(source_seconds) - float(downbeat_offset_seconds))
    relative = timeline.seconds_to_tick(musical)
    tick = int(origin_tick) + int(round(relative))
    tick = max(0, min(tick, timeline.duration_ticks + int(origin_tick)))
    logger.debug(
        "source_seconds_to_tick",
        extra={
            "seconds": round(float(source_seconds), 6),
            "tick": tick,
            "origin_tick": origin_tick,
        },
    )
    return tick


def bar_range_to_audio_window(
    start_bar: int,
    end_bar: int,
    *,
    composition: CompositionV2 | Mapping[str, Any],
    alignment: AudioAlignmentV1 | Mapping[str, Any],
) -> dict[str, float | int]:
    """Inclusive bar range → source-audio [start_seconds, end_seconds)."""
    timeline = compile_timeline(composition)
    if isinstance(alignment, AudioAlignmentV1):
        params = alignment.map
    else:
        params = AudioAlignmentMapParams.model_validate(alignment["map"])
    start_tick, end_tick = timeline.bar_range_ticks(int(start_bar), int(end_bar))
    start_seconds = tick_to_source_seconds(
        start_tick,
        timeline=timeline,
        downbeat_offset_seconds=params.downbeat_offset_seconds,
        origin_tick=params.origin_tick,
    )
    end_seconds = tick_to_source_seconds(
        end_tick,
        timeline=timeline,
        downbeat_offset_seconds=params.downbeat_offset_seconds,
        origin_tick=params.origin_tick,
    )
    logger.info(
        "bar_range_to_audio_window",
        extra={
            "start_bar": start_bar,
            "end_bar": end_bar,
            "start_seconds": round(start_seconds, 4),
            "end_seconds": round(end_seconds, 4),
        },
    )
    return {
        "start_bar": int(start_bar),
        "end_bar": int(end_bar),
        "start_tick": int(start_tick),
        "end_tick": int(end_tick),
        "start_seconds": float(start_seconds),
        "end_seconds": float(end_seconds),
    }


def _derive_quality(
    *,
    tempo_confidence: float,
    beat_grid_confidence: float,
    method: str,
    tempo_diverged: bool,
    missing_source: bool,
    defaulted_offset: bool,
    stem_qualities: Sequence[AudioAlignmentStemQuality] | None = None,
) -> AudioAlignmentQuality:
    issues: list[str] = []
    overall = min(float(tempo_confidence), float(beat_grid_confidence))
    offset_uncertainty_ms = DEFAULT_OFFSET_UNCERTAINTY_MS

    if missing_source:
        issues.append("alignment_missing_source")
        overall = 0.0
    if tempo_diverged:
        issues.append("alignment_tempo_diverged")
        overall = min(overall, 0.35)
        offset_uncertainty_ms = max(offset_uncertainty_ms, 80.0)
        logger.warning(
            "Alignment tempo diverged from scaffolding",
            extra={"overall_confidence": round(overall, 4)},
        )
    if defaulted_offset:
        issues.append("alignment_defaulted_offset")
        offset_uncertainty_ms = max(offset_uncertainty_ms, 50.0)
        overall = min(overall, 0.55)
    if beat_grid_confidence < SPARSE_BEAT_GRID_CONFIDENCE:
        issues.append("alignment_sparse_beat_grid")
        offset_uncertainty_ms = max(offset_uncertainty_ms, 60.0)
    if overall < LOW_OVERALL_CONFIDENCE and "alignment_low_confidence" not in issues:
        issues.append("alignment_low_confidence")

    return AudioAlignmentQuality(
        overall_confidence=max(0.0, min(1.0, overall)),
        tempo_confidence=max(0.0, min(1.0, float(tempo_confidence))),
        beat_grid_confidence=max(0.0, min(1.0, float(beat_grid_confidence))),
        offset_uncertainty_ms=float(offset_uncertainty_ms),
        method=method,  # type: ignore[arg-type]
        issues=issues,  # type: ignore[arg-type]
        stem_qualities=list(stem_qualities or []),
    )


def build_alignment_from_scaffolding(
    *,
    composition: CompositionV2,
    scaffolding: Mapping[str, Any],
    source_audio_asset_id: str,
    result_asset_id: str | None = None,
    job_id: str | None = None,
    project_id: str | None = None,
    stem_bindings: Sequence[Mapping[str, Any]] | None = None,
    composition_fingerprint: str | None = None,
    created_at: str | None = None,
) -> AudioAlignmentV1:
    """Build ``audio.alignment.v1`` from recovery scaffolding + V2 timeline."""
    if not source_audio_asset_id:
        raise AudioAlignmentError(
            "audio_alignment_missing_source",
            "Alignment requires a bound source_audio asset.",
            http_status=422,
        )

    timeline = compile_timeline(composition)
    beat_grid = scaffolding.get("beat_grid") or {}
    scaffolding_tempo = float(scaffolding.get("tempo_bpm") or timeline.root_tempo)
    tempo_confidence = float(scaffolding.get("tempo_confidence") or 0.5)
    beat_grid_confidence = float(beat_grid.get("confidence") or 0.5)
    downbeat_offset = float(beat_grid.get("downbeat_offset_seconds") or 0.0)
    ticks_per_quarter = int(
        beat_grid.get("ticks_per_quarter") or timeline.ticks_per_quarter
    )
    meter = str(scaffolding.get("meter") or timeline.root_time_signature or "4/4")

    v2_tempo = float(timeline.root_tempo)
    tempo_diverged = abs(v2_tempo - scaffolding_tempo) > TEMPO_DIVERGENCE_EPSILON_BPM
    defaulted_offset = (
        "tempo_source" in scaffolding
        and scaffolding.get("tempo_source") == "defaulted"
        and downbeat_offset == 0.0
        and beat_grid_confidence < 0.5
    )

    # Prefer timeline_parametric when we have a compiled V2 timeline + offset.
    method = "timeline_parametric"
    if scaffolding.get("tempo_source") == "defaulted" and beat_grid_confidence < 0.3:
        method = "defaulted"
    elif scaffolding.get("tempo_source") in {"estimated", "provided"}:
        method = "scaffolding" if not tempo_diverged else "timeline_parametric"

    quality = _derive_quality(
        tempo_confidence=tempo_confidence,
        beat_grid_confidence=beat_grid_confidence,
        method=method,
        tempo_diverged=tempo_diverged,
        missing_source=False,
        defaulted_offset=bool(defaulted_offset),
    )

    bindings: list[AudioAlignmentStemBinding] = []
    for raw in stem_bindings or []:
        bindings.append(AudioAlignmentStemBinding.model_validate(raw))

    fp = composition_fingerprint or composition_snapshot_fingerprint(composition)
    doc = AudioAlignmentV1(
        schema_version=AUDIO_ALIGNMENT_SCHEMA_VERSION,
        source_audio_asset_id=source_audio_asset_id,
        result_asset_id=result_asset_id,
        job_id=job_id,
        project_id=project_id,
        composition_fingerprint=fp,
        map=AudioAlignmentMapParams(
            tempo_bpm=v2_tempo,
            ticks_per_quarter=ticks_per_quarter,
            downbeat_offset_seconds=downbeat_offset,
            origin_tick=0,
            meter=meter,
            scaffolding_tempo_bpm=scaffolding_tempo,
        ),
        stem_bindings=bindings,
        quality=quality,
        created_at=created_at or _utc_now_iso(),
        playable=False,
    )

    # Sample pairs for DEBUG (≤5).
    sample_ticks = [
        0,
        min(timeline.duration_ticks, timeline.ticks_per_quarter * 4),
        min(timeline.duration_ticks, timeline.ticks_per_quarter * 4 * 9),  # bar 10-ish
    ]
    samples = []
    for tick in sample_ticks:
        samples.append(
            {
                "tick": tick,
                "seconds": round(
                    tick_to_source_seconds(
                        tick,
                        timeline=timeline,
                        downbeat_offset_seconds=downbeat_offset,
                        origin_tick=0,
                    ),
                    4,
                ),
            }
        )
    logger.debug(
        "Alignment sample (tick, seconds) pairs",
        extra={"samples": samples[:5]},
    )
    logger.info(
        "Built audio.alignment.v1",
        extra={
            "method": quality.method,
            "overall_confidence": round(quality.overall_confidence, 4),
            "bar_count": timeline.bar_count,
            "source_asset_prefix": _id_prefix(source_audio_asset_id),
            "tempo_diverged": tempo_diverged,
            "issue_count": len(quality.issues),
        },
    )
    return doc


def alignment_map_params_from_document(
    alignment: AudioAlignmentV1 | Mapping[str, Any],
) -> AudioAlignmentMapParams:
    if isinstance(alignment, AudioAlignmentV1):
        return alignment.map
    return AudioAlignmentMapParams.model_validate(alignment["map"])
