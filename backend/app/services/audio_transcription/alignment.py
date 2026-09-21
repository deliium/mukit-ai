"""Seconds ↔ tick alignment and confidence summary helpers."""

from __future__ import annotations

import logging
import statistics
from typing import Any, Literal

from app.audio_transcription_schemas import (
    TranscriptionIssueSeverity,
    TranscriptionPreviewIssue,
    TranscriptionPreviewNote,
    TranscriptionPreviewSummary,
    TranscriptionTempoSource,
)
from app.audio_transcription_settings import AudioTranscriptionSettings


logger = logging.getLogger(__name__)

TempoSource = Literal["provided", "estimated", "defaulted"]


def seconds_to_ticks(
    seconds: float,
    *,
    tempo_bpm: float,
    ticks_per_quarter: int,
    origin_tick: int = 0,
) -> int:
    """Map a time in seconds to absolute ticks (half-up rounding)."""
    if seconds <= 0:
        return max(0, origin_tick)
    quarters_per_second = float(tempo_bpm) / 60.0
    ticks = origin_tick + seconds * quarters_per_second * float(ticks_per_quarter)
    return max(0, int(ticks + 0.5))


def duration_seconds_to_ticks(
    duration_seconds: float,
    *,
    tempo_bpm: float,
    ticks_per_quarter: int,
) -> int:
    ticks = seconds_to_ticks(
        duration_seconds,
        tempo_bpm=tempo_bpm,
        ticks_per_quarter=ticks_per_quarter,
        origin_tick=0,
    )
    return max(1, ticks)


def estimate_tempo_bpm(
    onset_seconds: list[float],
    *,
    default_bpm: int,
) -> tuple[int, TempoSource, list[TranscriptionPreviewIssue]]:
    """Estimate tempo from inter-onset intervals; fall back to default."""
    issues: list[TranscriptionPreviewIssue] = []
    if len(onset_seconds) < 2:
        issues.append(
            TranscriptionPreviewIssue(
                code="tempo_defaulted",
                severity="info",
                message="Tempo was missing; used the configured default BPM.",
            )
        )
        logger.info(
            "Tempo alignment source",
            extra={"tempo_source": "defaulted", "tempo_bpm": default_bpm},
        )
        return default_bpm, "defaulted", issues

    intervals = [
        onset_seconds[i + 1] - onset_seconds[i]
        for i in range(len(onset_seconds) - 1)
        if onset_seconds[i + 1] > onset_seconds[i]
    ]
    usable = [iv for iv in intervals if 0.15 <= iv <= 2.0]
    if not usable:
        issues.append(
            TranscriptionPreviewIssue(
                code="tempo_defaulted",
                severity="warning",
                message="Tempo estimation failed; used the configured default BPM.",
            )
        )
        logger.warning(
            "Tempo estimation low confidence; using default",
            extra={"tempo_bpm": default_bpm, "interval_count": len(intervals)},
        )
        return default_bpm, "defaulted", issues

    median_iv = statistics.median(usable)
    # Assume median IOI ≈ one beat.
    bpm = int(round(60.0 / median_iv))
    bpm = max(40, min(240, bpm))
    issues.append(
        TranscriptionPreviewIssue(
            code="tempo_estimated",
            severity="info",
            message="Tempo was estimated from inter-onset intervals.",
        )
    )
    logger.info(
        "Tempo alignment source",
        extra={"tempo_source": "estimated", "tempo_bpm": bpm},
    )
    return bpm, "estimated", issues


def build_confidence_summary(
    notes: list[TranscriptionPreviewNote],
    *,
    threshold: float,
) -> TranscriptionPreviewSummary:
    low = sum(1 for note in notes if note.confidence < threshold)
    return TranscriptionPreviewSummary(
        note_count=len(notes),
        low_confidence_count=low,
        excluded_low_confidence_count=low,
        include_threshold=threshold,
    )


def align_raw_notes(
    raw_notes: list[dict[str, Any]],
    *,
    settings: AudioTranscriptionSettings,
    tempo_bpm: int | None,
    ticks_per_quarter: int | None,
    origin_tick: int = 0,
) -> tuple[
    list[TranscriptionPreviewNote],
    int,
    int,
    TranscriptionTempoSource,
    list[TranscriptionPreviewIssue],
]:
    """Convert second-based raw notes to tick-timed provisional notes."""
    issues: list[TranscriptionPreviewIssue] = []
    ppq = ticks_per_quarter or settings.default_target_ppq
    origin = max(0, int(origin_tick or 0))

    onsets = [float(n.get("start_seconds", 0.0) or 0.0) for n in raw_notes]
    if tempo_bpm is not None and tempo_bpm > 0:
        bpm = int(tempo_bpm)
        tempo_source: TranscriptionTempoSource = "provided"
        logger.info(
            "Tempo alignment source",
            extra={"tempo_source": "provided", "tempo_bpm": bpm},
        )
    else:
        bpm, tempo_source, tempo_issues = estimate_tempo_bpm(
            onsets, default_bpm=settings.default_tempo_bpm
        )
        issues.extend(tempo_issues)

    notes: list[TranscriptionPreviewNote] = []
    for index, raw in enumerate(raw_notes):
        start_s = float(raw.get("start_seconds", 0.0) or 0.0)
        dur_s = float(raw.get("duration_seconds", 0.0) or 0.0)
        pitch = int(raw.get("pitch", 60) or 60)
        velocity = int(raw.get("velocity", 80) or 80)
        confidence = float(raw.get("confidence", 0.5) or 0.0)
        provisional_id = str(raw.get("provisional_id") or f"a{index + 1}")
        start_tick = seconds_to_ticks(
            start_s,
            tempo_bpm=bpm,
            ticks_per_quarter=ppq,
            origin_tick=origin,
        )
        duration_ticks = duration_seconds_to_ticks(
            dur_s, tempo_bpm=bpm, ticks_per_quarter=ppq
        )
        notes.append(
            TranscriptionPreviewNote(
                provisional_id=provisional_id[:64],
                pitch=max(0, min(127, pitch)),
                start_tick=start_tick,
                duration_ticks=duration_ticks,
                velocity=max(1, min(127, velocity)),
                confidence=max(0.0, min(1.0, confidence)),
            )
        )

    logger.debug(
        "Aligned raw notes to ticks",
        extra={"note_count": len(notes), "tempo_bpm": bpm, "ppq": ppq},
    )
    return notes, bpm, ppq, tempo_source, issues


def issue(
    code: str,
    message: str,
    *,
    severity: TranscriptionIssueSeverity = "info",
) -> TranscriptionPreviewIssue:
    return TranscriptionPreviewIssue(code=code, severity=severity, message=message)  # type: ignore[arg-type]
