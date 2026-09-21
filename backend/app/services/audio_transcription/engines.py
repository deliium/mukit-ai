"""Pluggable monophonic transcription engines."""

from __future__ import annotations

import hashlib
import logging
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.audio_transcription_schemas import (
    AudioTranscriptionError,
    TranscriptionPreviewEngine,
    TranscriptionPreviewIssue,
)
from app.audio_transcription_settings import AudioTranscriptionSettings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EngineRawResult:
    """Second-based note candidates before tick alignment."""

    notes: list[dict[str, Any]]
    issues: list[TranscriptionPreviewIssue]
    engine: TranscriptionPreviewEngine
    duration_seconds: float
    sample_rate: int


@runtime_checkable
class AudioTranscriptionEngine(Protocol):
    engine_id: str

    def is_available(self) -> bool: ...

    def transcribe_mono(
        self,
        path: Path,
        *,
        settings: AudioTranscriptionSettings,
    ) -> EngineRawResult: ...


def _sha256_prefix(data: bytes, *, length: int = 12) -> str:
    return hashlib.sha256(data).hexdigest()[:length]


def read_wav_pcm_mono(path: Path) -> tuple[list[float], int, float]:
    """Read a PCM WAV as mono float samples in [-1, 1]."""
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            frame_count = handle.getnframes()
            raw = handle.readframes(frame_count)
    except wave.Error as exc:
        raise AudioTranscriptionError(
            "audio_malformed_source",
            "WAV source is malformed or undecodable",
            http_status=422,
        ) from exc

    if sample_width not in (1, 2, 3, 4) or sample_rate <= 0 or frame_count <= 0:
        raise AudioTranscriptionError(
            "audio_malformed_source",
            "WAV PCM parameters are unsupported",
            http_status=422,
            details={"sample_width": sample_width, "sample_rate": sample_rate},
        )

    if sample_width == 1:
        fmt = f"{frame_count * channels}B"
        ints = struct.unpack(fmt, raw)
        samples = [(v - 128) / 128.0 for v in ints]
    elif sample_width == 2:
        fmt = f"<{frame_count * channels}h"
        ints = struct.unpack(fmt, raw)
        samples = [v / 32768.0 for v in ints]
    elif sample_width == 3:
        samples = []
        for i in range(0, len(raw), 3 * channels):
            for ch in range(channels):
                offset = i + ch * 3
                chunk = raw[offset : offset + 3]
                if len(chunk) < 3:
                    break
                value = int.from_bytes(chunk, "little", signed=True)
                samples.append(value / 8388608.0)
    else:
        fmt = f"<{frame_count * channels}i"
        ints = struct.unpack(fmt, raw)
        samples = [v / 2147483648.0 for v in ints]

    if channels > 1:
        mono: list[float] = []
        for i in range(0, len(samples), channels):
            frame = samples[i : i + channels]
            mono.append(sum(frame) / float(len(frame)))
        samples = mono

    duration = len(samples) / float(sample_rate)
    return samples, sample_rate, duration


class FakeAudioMonoEngine:
    """Deterministic CI engine — maps WAV digests / simple sine heuristics to notes."""

    engine_id = "fake:audio-mono"

    # Fixture digest prefixes → golden second-based notes (C4 E4 G4).
    _FIXTURE_MELODY: list[dict[str, Any]] = [
        {
            "provisional_id": "a1",
            "pitch": 60,
            "start_seconds": 0.0,
            "duration_seconds": 0.45,
            "velocity": 90,
            "confidence": 0.95,
        },
        {
            "provisional_id": "a2",
            "pitch": 64,
            "start_seconds": 0.5,
            "duration_seconds": 0.45,
            "velocity": 88,
            "confidence": 0.92,
        },
        {
            "provisional_id": "a3",
            "pitch": 67,
            "start_seconds": 1.0,
            "duration_seconds": 0.45,
            "velocity": 86,
            "confidence": 0.4,
        },
    ]

    def is_available(self) -> bool:
        return True

    def transcribe_mono(
        self,
        path: Path,
        *,
        settings: AudioTranscriptionSettings,
    ) -> EngineRawResult:
        data = path.read_bytes()
        digest = _sha256_prefix(data)
        samples, sample_rate, duration = read_wav_pcm_mono(path)
        logger.info(
            "Fake audio engine transcription",
            extra={
                "engine_id": self.engine_id,
                "sha256_prefix": digest,
                "duration_seconds": round(duration, 3),
                "sample_rate": sample_rate,
                "sample_count": len(samples),
            },
        )
        # Always return the golden triad scaled into the clip; no scientific claim.
        notes = [dict(note) for note in self._FIXTURE_MELODY]
        # Drop notes that start beyond clip duration.
        notes = [n for n in notes if float(n["start_seconds"]) < duration + 1e-6]
        issues: list[TranscriptionPreviewIssue] = []
        if max(abs(s) for s in samples[: min(len(samples), sample_rate)]) < 0.01:
            issues.append(
                TranscriptionPreviewIssue(
                    code="low_energy_segment_omitted",
                    severity="warning",
                    message="Low-energy or unvoiced segments were omitted.",
                )
            )
        return EngineRawResult(
            notes=notes,
            issues=issues,
            engine=TranscriptionPreviewEngine(
                id=self.engine_id, version="1", fake=True
            ),
            duration_seconds=duration,
            sample_rate=sample_rate,
        )


class LibrosaPyinEngine:
    engine_id = "librosa_pyin"

    def is_available(self) -> bool:
        try:
            import librosa  # noqa: F401
            import numpy  # noqa: F401
            import soundfile  # noqa: F401
        except ImportError:
            return False
        return True

    def transcribe_mono(
        self,
        path: Path,
        *,
        settings: AudioTranscriptionSettings,
    ) -> EngineRawResult:
        if not self.is_available():
            raise AudioTranscriptionError(
                "audio_engine_unavailable",
                "librosa_pyin engine dependencies are not installed",
                http_status=503,
                details={"engine": self.engine_id, "hint": "pip install -r requirements-audio-transcription.txt"},
            )
        import librosa
        import numpy as np

        try:
            y, sr = librosa.load(str(path), sr=None, mono=True)
        except Exception as exc:  # noqa: BLE001
            raise AudioTranscriptionError(
                "audio_malformed_source",
                "Audio could not be decoded for librosa_pyin",
                http_status=422,
            ) from exc

        if sr > settings.max_sample_rate:
            raise AudioTranscriptionError(
                "audio_sample_rate_unsupported",
                "Sample rate exceeds configured maximum",
                http_status=422,
                details={"sample_rate": int(sr), "limit": settings.max_sample_rate},
            )

        duration = float(len(y) / sr) if sr else 0.0
        issues: list[TranscriptionPreviewIssue] = []
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y,
            fmin=librosa.note_to_hz("C2"),
            fmax=librosa.note_to_hz("C7"),
            sr=sr,
        )
        hop = 512
        times = librosa.times_like(f0, sr=sr, hop_length=hop)
        notes: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None

        for index, (freq, voiced, prob) in enumerate(
            zip(f0, voiced_flag, voiced_probs, strict=False)
        ):
            t = float(times[index])
            if (
                voiced
                and freq is not None
                and not np.isnan(freq)
                and float(prob or 0.0) > 0.2
            ):
                midi = int(round(librosa.hz_to_midi(float(freq))))
                midi = max(0, min(127, midi))
                conf = float(prob)
                if current is None:
                    current = {
                        "provisional_id": f"a{len(notes) + 1}",
                        "pitch": midi,
                        "start_seconds": t,
                        "duration_seconds": hop / float(sr),
                        "velocity": 80,
                        "confidence": conf,
                        "_last_t": t,
                    }
                elif abs(midi - int(current["pitch"])) <= 1:
                    current["duration_seconds"] = t - float(current["start_seconds"]) + (
                        hop / float(sr)
                    )
                    current["confidence"] = min(
                        1.0, (float(current["confidence"]) + conf) / 2.0
                    )
                    current["_last_t"] = t
                else:
                    notes.append(_finalize_raw(current))
                    current = {
                        "provisional_id": f"a{len(notes) + 1}",
                        "pitch": midi,
                        "start_seconds": t,
                        "duration_seconds": hop / float(sr),
                        "velocity": 80,
                        "confidence": conf,
                        "_last_t": t,
                    }
            elif current is not None:
                notes.append(_finalize_raw(current))
                current = None

        if current is not None:
            notes.append(_finalize_raw(current))

        # Drop very short blips.
        before = len(notes)
        notes = [n for n in notes if float(n["duration_seconds"]) >= 0.06]
        if len(notes) < before:
            issues.append(
                TranscriptionPreviewIssue(
                    code="low_energy_segment_omitted",
                    severity="info",
                    message="Low-energy or unvoiced segments were omitted.",
                )
            )

        logger.info(
            "librosa_pyin transcription complete",
            extra={
                "engine_id": self.engine_id,
                "note_count": len(notes),
                "duration_seconds": round(duration, 3),
                "sample_rate": int(sr),
            },
        )
        logger.debug(
            "librosa_pyin segment counts",
            extra={"raw_frames": len(f0), "kept_notes": len(notes)},
        )
        return EngineRawResult(
            notes=notes,
            issues=issues,
            engine=TranscriptionPreviewEngine(
                id=self.engine_id, version="1", fake=False
            ),
            duration_seconds=duration,
            sample_rate=int(sr),
        )


class BasicPitchEngine:
    engine_id = "basic_pitch"

    def is_available(self) -> bool:
        try:
            import basic_pitch  # noqa: F401
        except ImportError:
            return False
        return True

    def transcribe_mono(
        self,
        path: Path,
        *,
        settings: AudioTranscriptionSettings,
    ) -> EngineRawResult:
        if not self.is_available():
            raise AudioTranscriptionError(
                "audio_engine_unavailable",
                "basic_pitch engine is not installed",
                http_status=503,
                details={
                    "engine": self.engine_id,
                    "hint": "pip install -r requirements-audio-transcription.txt",
                },
            )
        from basic_pitch.inference import predict

        try:
            _model_output, midi_data, _note_events = predict(str(path))
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "basic_pitch inference failed",
                extra={"error_type": type(exc).__name__},
            )
            raise AudioTranscriptionError(
                "audio_malformed_source",
                "basic_pitch could not decode or transcribe the audio",
                http_status=422,
            ) from exc

        # Prefer note_events when available; fall back to empty with issue.
        notes: list[dict[str, Any]] = []
        issues: list[TranscriptionPreviewIssue] = []
        raw_events = _note_events or []
        # Collapse overlapping (polyphonic) to highest-confidence stream.
        collapsed = 0
        for index, event in enumerate(raw_events):
            # basic_pitch note_events: (start, end, pitch, amplitude, …) variants exist
            try:
                start_s = float(event[0])
                end_s = float(event[1])
                pitch = int(round(float(event[2])))
                amp = float(event[3]) if len(event) > 3 else 0.7
            except (TypeError, ValueError, IndexError):
                continue
            conf = max(0.0, min(1.0, amp if amp <= 1.0 else amp / 127.0))
            # Monophonic: skip if overlaps previous kept note.
            if notes:
                prev = notes[-1]
                prev_end = float(prev["start_seconds"]) + float(prev["duration_seconds"])
                if start_s < prev_end - 1e-3:
                    collapsed += 1
                    if conf <= float(prev["confidence"]):
                        continue
                    # Replace previous with higher-confidence overlapping note.
                    notes.pop()
            notes.append(
                {
                    "provisional_id": f"a{index + 1}",
                    "pitch": max(0, min(127, pitch)),
                    "start_seconds": start_s,
                    "duration_seconds": max(0.06, end_s - start_s),
                    "velocity": max(1, min(127, int(round(conf * 100)))),
                    "confidence": conf,
                }
            )
        if collapsed:
            issues.append(
                TranscriptionPreviewIssue(
                    code="polyphony_collapsed",
                    severity="warning",
                    message="Overlapping pitch candidates were collapsed to a monophonic stream.",
                )
            )
            issues.append(
                TranscriptionPreviewIssue(
                    code="secondary_pitch_omitted",
                    severity="info",
                    message="Secondary concurrent pitches were omitted (monophonic policy).",
                )
            )
            logger.debug(
                "Omitted secondary pitches",
                extra={"collapsed_count": collapsed},
            )

        duration = 0.0
        sample_rate = 22050
        try:
            samples, sample_rate, duration = read_wav_pcm_mono(path)
            _ = samples
        except AudioTranscriptionError:
            if notes:
                duration = max(
                    float(n["start_seconds"]) + float(n["duration_seconds"]) for n in notes
                )

        logger.info(
            "basic_pitch transcription complete",
            extra={
                "engine_id": self.engine_id,
                "note_count": len(notes),
                "duration_seconds": round(duration, 3),
            },
        )
        return EngineRawResult(
            notes=notes,
            issues=issues,
            engine=TranscriptionPreviewEngine(
                id=self.engine_id, version="1", fake=False
            ),
            duration_seconds=duration,
            sample_rate=sample_rate,
        )


def _finalize_raw(current: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in current.items() if not k.startswith("_")}
    out["duration_seconds"] = max(0.06, float(out.get("duration_seconds", 0.06)))
    return out


def resolve_engine(
    settings: AudioTranscriptionSettings,
) -> AudioTranscriptionEngine:
    """Select engine per AUDIO_TRANSCRIPTION_ENGINE / fake mode policy."""
    fake = FakeAudioMonoEngine()
    librosa_engine = LibrosaPyinEngine()
    basic = BasicPitchEngine()
    requested = settings.engine

    logger.debug(
        "Resolving audio transcription engine",
        extra={
            "requested": requested,
            "fake_mode": settings.fake_mode,
            "librosa_available": librosa_engine.is_available(),
            "basic_pitch_available": basic.is_available(),
        },
    )

    if requested == "fake:audio-mono":
        if not settings.fake_mode:
            # Explicit pin still allowed for local QA, but log clearly.
            logger.warning(
                "fake:audio-mono selected without AUDIO_FAKE_MODE; using fake for explicit pin",
                extra={"engine_id": fake.engine_id},
            )
        logger.info("Audio engine selected", extra={"engine_id": fake.engine_id})
        return fake

    if requested == "librosa_pyin":
        if not librosa_engine.is_available():
            raise AudioTranscriptionError(
                "audio_engine_unavailable",
                "librosa_pyin engine dependencies are not installed",
                http_status=503,
                details={
                    "engine": "librosa_pyin",
                    "hint": "pip install -r requirements-audio-transcription.txt",
                },
            )
        logger.info("Audio engine selected", extra={"engine_id": librosa_engine.engine_id})
        return librosa_engine

    if requested == "basic_pitch":
        if not basic.is_available():
            raise AudioTranscriptionError(
                "audio_engine_unavailable",
                "basic_pitch engine is not installed",
                http_status=503,
                details={
                    "engine": "basic_pitch",
                    "hint": "pip install -r requirements-audio-transcription.txt",
                },
            )
        logger.info("Audio engine selected", extra={"engine_id": basic.engine_id})
        return basic

    # auto
    if basic.is_available():
        logger.info("Audio engine selected", extra={"engine_id": basic.engine_id})
        return basic
    if librosa_engine.is_available():
        logger.info("Audio engine selected", extra={"engine_id": librosa_engine.engine_id})
        return librosa_engine
    if settings.fake_mode:
        logger.info(
            "Audio engine selected (fake fallback)",
            extra={"engine_id": fake.engine_id},
        )
        return fake

    logger.warning(
        "No local audio transcription engine available",
        extra={"fake_mode": False},
    )
    raise AudioTranscriptionError(
        "audio_engine_unavailable",
        "No local audio transcription engine available; install extras or set AUDIO_FAKE_MODE=1",
        http_status=503,
        details={
            "engine": "auto",
            "hint": "pip install -r requirements-audio-transcription.txt or AUDIO_FAKE_MODE=1",
        },
    )
