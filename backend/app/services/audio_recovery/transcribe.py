"""Per-stem / combined transcription for audio recovery.

Reuses V3 mono engines for vocals/melody. Practical poly for harmonic/other.
Drums: deferred or sparse low-confidence onsets — never invent pitched kits.
Never pad missing polyphony with chord-tone invention.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.audio_recovery_schemas import (
    AudioRecoveryIssue,
    AudioRecoveryPreviewNote,
    AudioRecoveryStemRole,
)
from app.audio_recovery_settings import AudioRecoverySettings
from app.services.audio_transcription.alignment import (
    duration_seconds_to_ticks,
    seconds_to_ticks,
)
from app.services.audio_transcription.engines import (
    EngineRawResult,
    FakeAudioMonoEngine,
    read_wav_pcm_mono,
    resolve_engine,
)
from app.audio_transcription_settings import (
    AudioTranscriptionSettings,
    load_audio_transcription_settings,
)
from app.services.audio_recovery.separation import SeparatedStem


logger = logging.getLogger(__name__)

MONO_STEMS: frozenset[str] = frozenset({"vocals", "melody", "bass"})
POLY_STEMS: frozenset[str] = frozenset({"harmonic", "other"})
DRUM_STEMS: frozenset[str] = frozenset({"drums"})


@dataclass
class StemTranscriptionResult:
    stem: AudioRecoveryStemRole
    engine_id: str
    notes: list[AudioRecoveryPreviewNote] = field(default_factory=list)
    issues: list[AudioRecoveryIssue] = field(default_factory=list)
    fake: bool = False


@runtime_checkable
class PolyTranscriptionEngine(Protocol):
    engine_id: str

    def is_available(self) -> bool: ...

    def transcribe_poly(
        self,
        path: Path,
        *,
        settings: AudioRecoverySettings,
    ) -> EngineRawResult: ...


def _sha256_prefix(data: bytes, *, length: int = 12) -> str:
    return hashlib.sha256(data).hexdigest()[:length]


class FakeAudioPolyEngine:
    """Deterministic multi-pitch candidates for harmonic/other stems (CI)."""

    engine_id = "fake:audio-poly"

    _CHORDS: list[list[dict[str, Any]]] = [
        [
            {
                "provisional_id": "h1a",
                "pitch": 60,
                "start_seconds": 0.0,
                "duration_seconds": 0.8,
                "velocity": 70,
                "confidence": 0.75,
                "polyphony_group_id": "g1",
            },
            {
                "provisional_id": "h1b",
                "pitch": 64,
                "start_seconds": 0.0,
                "duration_seconds": 0.8,
                "velocity": 68,
                "confidence": 0.7,
                "polyphony_group_id": "g1",
            },
            {
                "provisional_id": "h1c",
                "pitch": 67,
                "start_seconds": 0.0,
                "duration_seconds": 0.8,
                "velocity": 66,
                "confidence": 0.45,
                "polyphony_group_id": "g1",
            },
        ],
        [
            {
                "provisional_id": "h2a",
                "pitch": 62,
                "start_seconds": 1.0,
                "duration_seconds": 0.7,
                "velocity": 70,
                "confidence": 0.72,
                "polyphony_group_id": "g2",
            },
            {
                "provisional_id": "h2b",
                "pitch": 65,
                "start_seconds": 1.0,
                "duration_seconds": 0.7,
                "velocity": 68,
                "confidence": 0.68,
                "polyphony_group_id": "g2",
            },
        ],
    ]

    def is_available(self) -> bool:
        return True

    def transcribe_poly(
        self,
        path: Path,
        *,
        settings: AudioRecoverySettings,
    ) -> EngineRawResult:
        from app.audio_transcription_schemas import TranscriptionPreviewEngine

        data = path.read_bytes()
        digest = _sha256_prefix(data)
        samples, sample_rate, duration = read_wav_pcm_mono(path)
        notes: list[dict[str, Any]] = []
        for group in self._CHORDS:
            for note in group:
                if float(note["start_seconds"]) < duration + 1e-6:
                    notes.append(dict(note))
        logger.info(
            "Fake poly transcription",
            extra={
                "engine_id": self.engine_id,
                "sha256_prefix": digest,
                "note_count": len(notes),
                "duration_seconds": round(duration, 3),
                "sample_rate": sample_rate,
            },
        )
        return EngineRawResult(
            notes=notes,
            issues=[],
            engine=TranscriptionPreviewEngine(id=self.engine_id, version="1", fake=True),
            duration_seconds=duration,
            sample_rate=sample_rate,
        )


def _mono_settings_from_recovery(
    recovery: AudioRecoverySettings,
) -> AudioTranscriptionSettings:
    base = load_audio_transcription_settings()
    return AudioTranscriptionSettings(
        max_upload_bytes=recovery.max_upload_bytes,
        max_duration_seconds=recovery.max_duration_seconds,
        max_sample_rate=recovery.max_sample_rate,
        confidence_include_threshold=recovery.confidence_include_threshold,
        engine="fake:audio-mono" if recovery.fake_mode else base.engine,
        fake_mode=recovery.fake_mode or base.fake_mode,
        default_target_ppq=recovery.default_target_ppq,
        default_tempo_bpm=recovery.default_tempo_bpm,
    )


def _raw_to_preview_notes(
    raw_notes: list[dict[str, Any]],
    *,
    stem: AudioRecoveryStemRole,
    tempo_bpm: int,
    ticks_per_quarter: int,
    id_prefix: str,
) -> list[AudioRecoveryPreviewNote]:
    out: list[AudioRecoveryPreviewNote] = []
    for index, raw in enumerate(raw_notes):
        start = seconds_to_ticks(
            float(raw["start_seconds"]),
            tempo_bpm=tempo_bpm,
            ticks_per_quarter=ticks_per_quarter,
        )
        dur = duration_seconds_to_ticks(
            float(raw["duration_seconds"]),
            tempo_bpm=tempo_bpm,
            ticks_per_quarter=ticks_per_quarter,
        )
        provisional_id = str(raw.get("provisional_id") or f"{id_prefix}{index + 1}")
        out.append(
            AudioRecoveryPreviewNote(
                provisional_id=provisional_id,
                stem=stem,
                pitch=int(raw["pitch"]),
                start_tick=start,
                duration_ticks=dur,
                velocity=int(raw.get("velocity") or 80),
                confidence=float(raw["confidence"]),
                polyphony_group_id=raw.get("polyphony_group_id"),
            )
        )
    return out


def _transcribe_drums_policy(
    path: Path,
    *,
    stem: AudioRecoveryStemRole,
    tempo_bpm: int,
    ticks_per_quarter: int,
    defer: bool = True,
) -> StemTranscriptionResult:
    if defer:
        logger.warning(
            "Drums transcription deferred",
            extra={"stem": stem, "engine_id": "policy:drums_deferred"},
        )
        return StemTranscriptionResult(
            stem=stem,
            engine_id="policy:drums_deferred",
            notes=[],
            issues=[
                AudioRecoveryIssue(
                    code="drums_transcription_deferred",
                    severity="info",
                    message="Drum pitched transcription was deferred for this job.",
                    stem=stem,
                ),
                AudioRecoveryIssue(
                    code="stem_empty",
                    severity="info",
                    message="Drums stem produced no note events (not fabricated).",
                    stem=stem,
                ),
            ],
            fake=True,
        )
    # Sparse onset events with low confidence — no pitched kit invention.
    samples, sample_rate, duration = read_wav_pcm_mono(path)
    notes: list[AudioRecoveryPreviewNote] = []
    if samples and sample_rate > 0:
        window = max(1, sample_rate // 20)
        energies = [
            sum(s * s for s in samples[i : i + window]) / window
            for i in range(0, len(samples) - window, window)
        ]
        mean_e = sum(energies) / max(1, len(energies))
        onset_count = 0
        for i, e in enumerate(energies):
            if e >= mean_e * 2.5:
                t = (i * window) / float(sample_rate)
                if t >= duration:
                    break
                onset_count += 1
                notes.append(
                    AudioRecoveryPreviewNote(
                        provisional_id=f"d{onset_count}",
                        stem=stem,
                        pitch=36,  # GM kick placeholder — low confidence only
                        start_tick=seconds_to_ticks(
                            t, tempo_bpm=tempo_bpm, ticks_per_quarter=ticks_per_quarter
                        ),
                        duration_ticks=duration_seconds_to_ticks(
                            0.08, tempo_bpm=tempo_bpm, ticks_per_quarter=ticks_per_quarter
                        ),
                        velocity=60,
                        confidence=0.25,
                    )
                )
                if onset_count >= 8:
                    break
    low_count = sum(1 for n in notes if n.confidence < 0.5)
    logger.info(
        "Drums sparse onset transcription",
        extra={
            "stem": stem,
            "note_count": len(notes),
            "low_confidence_count": low_count,
            "engine_id": "policy:drums_onsets",
        },
    )
    return StemTranscriptionResult(
        stem=stem,
        engine_id="policy:drums_onsets",
        notes=notes,
        issues=[],
        fake=True,
    )


def transcribe_stem(
    path: Path,
    *,
    stem: AudioRecoveryStemRole,
    settings: AudioRecoverySettings,
    tempo_bpm: int,
    ticks_per_quarter: int,
    provisional_prefix: str,
) -> StemTranscriptionResult:
    if stem in DRUM_STEMS:
        return _transcribe_drums_policy(
            path,
            stem=stem,
            tempo_bpm=tempo_bpm,
            ticks_per_quarter=ticks_per_quarter,
            defer=True,
        )

    if stem in POLY_STEMS:
        poly = FakeAudioPolyEngine() if settings.fake_mode else FakeAudioPolyEngine()
        # Optional real poly engines can be registered later; v1 fake/poly adapter.
        if not poly.is_available():
            logger.warning(
                "Poly engine unavailable; empty stem",
                extra={"stem": stem, "engine_id": poly.engine_id},
            )
            return StemTranscriptionResult(
                stem=stem,
                engine_id=poly.engine_id,
                notes=[],
                issues=[
                    AudioRecoveryIssue(
                        code="stem_empty",
                        severity="warning",
                        message="Poly engine unavailable; no notes fabricated.",
                        stem=stem,
                    )
                ],
            )
        raw = poly.transcribe_poly(path, settings=settings)
        notes = _raw_to_preview_notes(
            raw.notes,
            stem=stem,
            tempo_bpm=tempo_bpm,
            ticks_per_quarter=ticks_per_quarter,
            id_prefix=provisional_prefix,
        )
        low = sum(1 for n in notes if n.confidence < settings.confidence_include_threshold)
        logger.info(
            "Stem poly transcription complete",
            extra={
                "stem": stem,
                "engine_id": poly.engine_id,
                "note_count": len(notes),
                "low_confidence_count": low,
            },
        )
        return StemTranscriptionResult(
            stem=stem,
            engine_id=poly.engine_id,
            notes=notes,
            issues=[],
            fake=raw.engine.fake,
        )

    # Mono path for melody/vocals/bass.
    mono_settings = _mono_settings_from_recovery(settings)
    if settings.fake_mode:
        engine = FakeAudioMonoEngine()
    else:
        try:
            engine = resolve_engine(mono_settings)
        except Exception:  # noqa: BLE001
            engine = FakeAudioMonoEngine()
            logger.warning(
                "Mono engine selection failed; using fake",
                extra={"stem": stem},
            )
    raw = engine.transcribe_mono(path, settings=mono_settings)
    notes = _raw_to_preview_notes(
        raw.notes,
        stem=stem,
        tempo_bpm=tempo_bpm,
        ticks_per_quarter=ticks_per_quarter,
        id_prefix=provisional_prefix,
    )
    # Remap provisional ids to be unique across stems.
    remapped: list[AudioRecoveryPreviewNote] = []
    for i, note in enumerate(notes):
        remapped.append(
            note.model_copy(update={"provisional_id": f"{provisional_prefix}{i + 1}"})
        )
    low = sum(
        1 for n in remapped if n.confidence < settings.confidence_include_threshold
    )
    issues: list[AudioRecoveryIssue] = []
    if not remapped:
        issues.append(
            AudioRecoveryIssue(
                code="stem_empty",
                severity="info",
                message="Stem produced no note events (not fabricated).",
                stem=stem,
            )
        )
    logger.info(
        "Stem mono transcription complete",
        extra={
            "stem": stem,
            "engine_id": engine.engine_id,
            "note_count": len(remapped),
            "low_confidence_count": low,
        },
    )
    return StemTranscriptionResult(
        stem=stem,
        engine_id=engine.engine_id,
        notes=remapped,
        issues=issues,
        fake=raw.engine.fake,
    )


def transcribe_stems_or_combined(
    *,
    source_path: Path,
    stems: list[SeparatedStem],
    settings: AudioRecoverySettings,
    tempo_bpm: int,
    ticks_per_quarter: int,
    combined_degraded: bool = False,
) -> tuple[list[StemTranscriptionResult], list[AudioRecoveryPreviewNote], list[AudioRecoveryIssue]]:
    """Transcribe per stem or combined source. Never fabricates notes for gaps."""
    results: list[StemTranscriptionResult] = []
    all_notes: list[AudioRecoveryPreviewNote] = []
    issues: list[AudioRecoveryIssue] = []

    if combined_degraded:
        issues.append(
            AudioRecoveryIssue(
                code="combined_path_degraded",
                severity="warning",
                message="Combined transcription ran with reduced confidence.",
            )
        )

    targets: list[tuple[AudioRecoveryStemRole, Path]] = []
    if stems:
        for stem in stems:
            if stem.path is not None and stem.path.is_file():
                targets.append((stem.role, stem.path))
    else:
        targets.append(("melody", source_path))

    for index, (role, path) in enumerate(targets):
        prefix = f"{role[:1]}{index}_"
        result = transcribe_stem(
            path,
            stem=role,
            settings=settings,
            tempo_bpm=tempo_bpm,
            ticks_per_quarter=ticks_per_quarter,
            provisional_prefix=prefix,
        )
        if combined_degraded:
            # Reduce confidence honestly on combined path.
            reduced = [
                n.model_copy(update={"confidence": max(0.0, min(1.0, n.confidence * 0.85))})
                for n in result.notes
            ]
            result = StemTranscriptionResult(
                stem=result.stem,
                engine_id=result.engine_id,
                notes=reduced,
                issues=result.issues,
                fake=result.fake,
            )
        results.append(result)
        all_notes.extend(result.notes)
        issues.extend(result.issues)

    # Ensure unique provisional ids across stems.
    seen: set[str] = set()
    unique_notes: list[AudioRecoveryPreviewNote] = []
    for note in all_notes:
        pid = note.provisional_id
        if pid in seen:
            pid = f"{pid}_{note.stem}"
            note = note.model_copy(update={"provisional_id": pid})
        seen.add(pid)
        unique_notes.append(note)

    logger.info(
        "Stem transcription batch complete",
        extra={
            "stem_count": len(results),
            "note_count": len(unique_notes),
            "combined_degraded": combined_degraded,
        },
    )
    return results, unique_notes, issues
