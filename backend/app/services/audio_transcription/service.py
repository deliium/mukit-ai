"""Orchestrate ephemeral audio decode → engine → transcription.preview.v1."""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
import time
from pathlib import Path

from app.audio_transcription_schemas import (
    AudioTranscriptionError,
    AudioTranscriptionResponse,
    AudioTranscriptionRetention,
    TranscriptionPreviewIssue,
    TranscriptionPreviewTiming,
    TranscriptionPreviewV1,
)
from app.audio_transcription_settings import (
    AudioTranscriptionSettings,
    load_audio_transcription_settings,
    sniff_audio_format,
)
from app.services.audio_transcription.alignment import (
    align_raw_notes,
    build_confidence_summary,
)
from app.services.audio_transcription.engines import resolve_engine


logger = logging.getLogger(__name__)


def _sha256_prefix(data: bytes, *, length: int = 12) -> str:
    return hashlib.sha256(data).hexdigest()[:length]


def transcribe_audio_bytes(
    payload: bytes,
    *,
    display_filename: str = "upload.wav",
    settings: AudioTranscriptionSettings | None = None,
    tempo_bpm: int | None = None,
    ticks_per_quarter: int | None = None,
    origin_tick: int = 0,
) -> AudioTranscriptionResponse:
    """Transcribe audio bytes into a session-only preview. Always cleans temp files."""
    cfg = settings or load_audio_transcription_settings()
    started = time.perf_counter()
    digest = _sha256_prefix(payload)
    logger.info(
        "Audio transcription started",
        extra={
            "upload_bytes": len(payload),
            "sha256_prefix": digest,
            "basename": Path(display_filename).name[:120],
            "engine_setting": cfg.engine,
            "fake_mode": cfg.fake_mode,
        },
    )

    if not payload:
        raise AudioTranscriptionError(
            "audio_empty_upload",
            "Audio upload is empty",
            http_status=422,
        )
    if len(payload) > cfg.max_upload_bytes:
        raise AudioTranscriptionError(
            "audio_payload_too_large",
            "Upload exceeds configured audio byte limit",
            http_status=413,
            details={"limit_bytes": cfg.max_upload_bytes},
        )

    fmt = sniff_audio_format(payload)
    if fmt is None:
        raise AudioTranscriptionError(
            "audio_format_unsupported",
            "Content signature is not an accepted audio format",
            http_status=415,
        )

    # v1 engines decode WAV natively; other formats require optional extras.
    if fmt != "wav":
        engine_probe = resolve_engine(cfg)
        if engine_probe.engine_id == "fake:audio-mono":
            raise AudioTranscriptionError(
                "audio_format_unsupported",
                "Non-WAV uploads require a local decoder engine (librosa/basic_pitch)",
                http_status=415,
                details={"detected_format": fmt},
            )

    temp_dir: Path | None = None
    deleted = False
    try:
        temp_dir = Path(
            tempfile.mkdtemp(prefix="mukit-audio-")
        )
        suffix = {"wav": ".wav", "flac": ".flac", "ogg": ".ogg", "mp3": ".mp3"}[fmt]
        audio_path = temp_dir / f"upload{suffix}"
        audio_path.write_bytes(payload)
        logger.debug(
            "Wrote ephemeral audio for transcription",
            extra={"sha256_prefix": digest, "format": fmt},
        )

        engine = resolve_engine(cfg)
        raw = engine.transcribe_mono(audio_path, settings=cfg)

        if raw.sample_rate > cfg.max_sample_rate:
            raise AudioTranscriptionError(
                "audio_sample_rate_unsupported",
                "Sample rate exceeds configured maximum",
                http_status=422,
                details={
                    "sample_rate": raw.sample_rate,
                    "limit": cfg.max_sample_rate,
                },
            )
        if raw.duration_seconds > cfg.max_duration_seconds + 1e-6:
            raise AudioTranscriptionError(
                "audio_duration_exceeded",
                "Audio duration exceeded the configured maximum",
                http_status=422,
                details={
                    "duration_seconds": round(raw.duration_seconds, 3),
                    "limit_seconds": cfg.max_duration_seconds,
                },
            )

        notes, bpm, ppq, tempo_source, align_issues = align_raw_notes(
            raw.notes,
            settings=cfg,
            tempo_bpm=tempo_bpm,
            ticks_per_quarter=ticks_per_quarter,
            origin_tick=origin_tick,
        )
        issues: list[TranscriptionPreviewIssue] = list(raw.issues) + list(align_issues)
        if tempo_bpm is None and tempo_source == "defaulted":
            # meter default when tempo also defaulted
            if not any(i.code == "meter_defaulted" for i in issues):
                issues.append(
                    TranscriptionPreviewIssue(
                        code="meter_defaulted",
                        severity="info",
                        message="Meter was missing; used the compatibility default 4/4.",
                    )
                )

        summary = build_confidence_summary(
            notes, threshold=cfg.confidence_include_threshold
        )
        preview = TranscriptionPreviewV1(
            notes=notes,
            issues=issues,
            summary=summary,
            timing=TranscriptionPreviewTiming(
                tempo_bpm=bpm,
                ticks_per_quarter=ppq,
                origin_tick=max(0, int(origin_tick or 0)),
                tempo_source=tempo_source,
                meter="4/4",
            ),
            engine=raw.engine,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.info(
            "Audio transcription completed",
            extra={
                "duration_ms": round(elapsed_ms, 3),
                "upload_bytes": len(payload),
                "sha256_prefix": digest,
                "engine_id": raw.engine.id,
                "note_count": summary.note_count,
                "low_confidence_count": summary.low_confidence_count,
                "audio_duration_seconds": round(raw.duration_seconds, 3),
                "sample_rate": raw.sample_rate,
            },
        )
        return AudioTranscriptionResponse(
            preview=preview,
            engine=raw.engine,
            retention=AudioTranscriptionRetention(deleted=True),
        )
    finally:
        if temp_dir is not None:
            try:
                shutil.rmtree(temp_dir, ignore_errors=False)
                deleted = True
                logger.debug(
                    "Ephemeral audio temp directory deleted",
                    extra={"sha256_prefix": digest},
                )
            except OSError as exc:
                logger.error(
                    "Failed to delete ephemeral audio temp directory",
                    extra={
                        "sha256_prefix": digest,
                        "error_type": type(exc).__name__,
                    },
                )
        if not deleted and temp_dir is not None:
            logger.error(
                "Audio retention cleanup incomplete",
                extra={"sha256_prefix": digest},
            )
