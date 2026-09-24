"""Read-only WAV decode for mix analysis (stdlib wave; optional soundfile)."""

from __future__ import annotations

import logging
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.mix_analysis_schemas import (
    MIX_ANALYSIS_INPUT_TOO_LARGE,
    MIX_ANALYSIS_INTERNAL_ERROR,
    MixAnalysisError,
)
from app.mix_analysis_settings import MixAnalysisSettings

logger = logging.getLogger(__name__)

DspBackend = Literal["stdlib", "numpy_scipy", "fake"]


@dataclass(frozen=True)
class DecodedAudio:
    """Interleaved float samples in [-1, 1]; never logged as PCM."""

    sample_rate: int
    channels: int
    samples: list[float]  # interleaved
    duration_seconds: float
    backend: DspBackend
    frame_count: int


def detect_dsp_backend() -> DspBackend:
    try:
        import numpy  # noqa: F401
        import scipy  # noqa: F401

        return "numpy_scipy"
    except ImportError:
        return "stdlib"


def decode_wav_readonly(
    path: Path,
    settings: MixAnalysisSettings,
    *,
    max_seconds: float | None = None,
) -> DecodedAudio:
    """Open WAV read-only. Never write_bytes on the path."""
    if not path.is_file():
        raise MixAnalysisError(
            "Stem audio file not found",
            code=MIX_ANALYSIS_INTERNAL_ERROR,
            http_status=500,
        )
    byte_size = path.stat().st_size
    if byte_size <= 0:
        raise MixAnalysisError(
            "Stem audio file is empty",
            code=MIX_ANALYSIS_INPUT_TOO_LARGE,
            http_status=422,
        )

    backend = detect_dsp_backend()
    cap_seconds = (
        settings.max_audio_seconds if max_seconds is None else min(max_seconds, settings.max_audio_seconds)
    )

    # Prefer soundfile when extras present (better float PCM), else stdlib wave.
    if backend == "numpy_scipy":
        try:
            return _decode_soundfile(path, settings, cap_seconds=cap_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "soundfile decode failed; falling back to wave",
                extra={"error_type": type(exc).__name__, "path_name": path.name},
            )
            backend = "stdlib"

    return _decode_wave(path, settings, cap_seconds=cap_seconds, backend=backend)


def _decode_soundfile(
    path: Path,
    settings: MixAnalysisSettings,
    *,
    cap_seconds: float,
) -> DecodedAudio:
    import soundfile as sf

    info = sf.info(str(path))
    sample_rate = int(info.samplerate)
    channels = int(info.channels)
    frames = int(info.frames)
    max_frames = min(frames, int(cap_seconds * sample_rate), settings.decode_max_samples // max(1, channels))
    data, _ = sf.read(str(path), frames=max_frames, dtype="float32", always_2d=True)
    # Flatten interleaved for uniform handling
    interleaved: list[float] = []
    for frame in data:
        for ch in range(channels):
            interleaved.append(float(frame[ch]))
    duration = max_frames / float(sample_rate) if sample_rate else 0.0
    logger.info(
        "Mix analysis WAV decoded",
        extra={
            "path_name": path.name,
            "sample_rate": sample_rate,
            "channels": channels,
            "duration_s": round(duration, 3),
            "dsp_backend": "numpy_scipy",
            "frame_count": max_frames,
        },
    )
    return DecodedAudio(
        sample_rate=sample_rate,
        channels=channels,
        samples=interleaved,
        duration_seconds=duration,
        backend="numpy_scipy",
        frame_count=max_frames,
    )


def _decode_wave(
    path: Path,
    settings: MixAnalysisSettings,
    *,
    cap_seconds: float,
    backend: DspBackend,
) -> DecodedAudio:
    try:
        with wave.open(str(path), "rb") as wf:
            sample_rate = int(wf.getframerate())
            channels = int(wf.getnchannels())
            sampwidth = int(wf.getsampwidth())
            frames = int(wf.getnframes())
            max_frames = min(
                frames,
                int(cap_seconds * sample_rate),
                settings.decode_max_samples // max(1, channels),
            )
            raw = wf.readframes(max_frames)
    except wave.Error as exc:
        raise MixAnalysisError(
            "Failed to decode stem WAV",
            code=MIX_ANALYSIS_INTERNAL_ERROR,
            http_status=500,
        ) from exc

    samples = _pcm_bytes_to_float(raw, sampwidth=sampwidth, channels=channels)
    duration = max_frames / float(sample_rate) if sample_rate else 0.0
    logger.info(
        "Mix analysis WAV decoded",
        extra={
            "path_name": path.name,
            "sample_rate": sample_rate,
            "channels": channels,
            "duration_s": round(duration, 3),
            "dsp_backend": backend,
            "frame_count": max_frames,
        },
    )
    return DecodedAudio(
        sample_rate=sample_rate,
        channels=channels,
        samples=samples,
        duration_seconds=duration,
        backend=backend,
        frame_count=max_frames,
    )


def _pcm_bytes_to_float(
    raw: bytes,
    *,
    sampwidth: int,
    channels: int,
) -> list[float]:
    if sampwidth == 1:
        # unsigned 8-bit
        return [(b - 128) / 128.0 for b in raw]
    if sampwidth == 2:
        count = len(raw) // 2
        ints = struct.unpack(f"<{count}h", raw)
        return [v / 32768.0 for v in ints]
    if sampwidth == 3:
        # 24-bit little-endian packed
        out: list[float] = []
        for i in range(0, len(raw) - 2, 3):
            b0, b1, b2 = raw[i], raw[i + 1], raw[i + 2]
            val = b0 | (b1 << 8) | (b2 << 16)
            if val & 0x800000:
                val -= 0x1000000
            out.append(val / 8388608.0)
        return out
    if sampwidth == 4:
        count = len(raw) // 4
        ints = struct.unpack(f"<{count}i", raw)
        return [v / 2147483648.0 for v in ints]
    raise MixAnalysisError(
        f"Unsupported PCM sample width: {sampwidth}",
        code=MIX_ANALYSIS_INTERNAL_ERROR,
        http_status=500,
    )


def channel_frames(audio: DecodedAudio) -> list[list[float]]:
    """De-interleave into per-channel frames."""
    ch = max(1, audio.channels)
    channels: list[list[float]] = [[] for _ in range(ch)]
    samples = audio.samples
    for i, sample in enumerate(samples):
        channels[i % ch].append(sample)
    return channels


def mono_mix(audio: DecodedAudio) -> list[float]:
    channels = channel_frames(audio)
    if len(channels) == 1:
        return channels[0]
    length = min(len(c) for c in channels) if channels else 0
    out: list[float] = []
    for i in range(length):
        out.append(sum(c[i] for c in channels) / float(len(channels)))
    return out
