"""Synthetic mix-analysis WAV builders (stdlib only; no numpy).

Writes short PCM fixtures under this package directory for DSP unit tests.
Never writes under DATASET_ROOT or neural render roots.
"""

from __future__ import annotations

import logging
import math
import struct
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parent
SAMPLE_RATE = 22050
DEFAULT_FRAMES = 2205  # 0.1 s


def fixture_path(name: str) -> Path:
    return FIXTURE_DIR / name


def write_pcm16_wav(
    path: Path,
    frames: list[tuple[int, ...]],
    *,
    sample_rate: int = SAMPLE_RATE,
    channels: int = 1,
) -> Path:
    """Write interleaved int16 frames; ``frames`` is list of channel tuples."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        packed: list[int] = []
        for sample in frames:
            if len(sample) != channels:
                raise ValueError("frame channel count mismatch")
            packed.extend(sample)
        wf.writeframes(struct.pack(f"<{len(packed)}h", *packed))
    logger.debug(
        "Mix analysis fixture written",
        extra={
            "name": path.name,
            "channels": channels,
            "sample_rate": sample_rate,
            "frame_count": len(frames),
        },
    )
    return path


def _clamp_i16(value: float) -> int:
    return int(max(-32768, min(32767, round(value))))


def build_hot_peak_wav(path: Path | None = None, *, frames: int = DEFAULT_FRAMES) -> Path:
    """Near-full-scale mono DC-ish tone → hot peak / low headroom."""
    dest = path or fixture_path("hot_peak.wav")
    amp = 0.999
    samples = [(_clamp_i16(amp * 32767),) for _ in range(frames)]
    return write_pcm16_wav(dest, samples, channels=1)


def build_clipped_wav(path: Path | None = None, *, frames: int = DEFAULT_FRAMES) -> Path:
    """Hard-clipped mono (all samples at int16 rail) → high clip_ratio."""
    dest = path or fixture_path("clipped.wav")
    samples = [(32767,) for _ in range(frames)]
    return write_pcm16_wav(dest, samples, channels=1)


def build_stereo_imbalance_wav(
    path: Path | None = None,
    *,
    frames: int = DEFAULT_FRAMES,
) -> Path:
    """Stereo with L ≫ R (~14 dB RMS imbalance)."""
    dest = path or fixture_path("stereo_imbalance.wav")
    left = 0.8
    right = 0.15  # ~14.5 dB below left
    samples = [
        (_clamp_i16(left * 32767), _clamp_i16(right * 32767)) for _ in range(frames)
    ]
    return write_pcm16_wav(dest, samples, channels=2)


def build_sine_wav(
    path: Path,
    *,
    freq_hz: float,
    amplitude: float = 0.6,
    frames: int = DEFAULT_FRAMES * 4,
    sample_rate: int = SAMPLE_RATE,
) -> Path:
    """Mono sine for spectral / masking overlap tests."""
    samples: list[tuple[int, ...]] = []
    for i in range(frames):
        t = i / float(sample_rate)
        value = amplitude * math.sin(2.0 * math.pi * freq_hz * t)
        samples.append((_clamp_i16(value * 32767),))
    return write_pcm16_wav(path, samples, sample_rate=sample_rate, channels=1)


def build_masking_pair(
    *,
    bass_path: Path | None = None,
    strings_path: Path | None = None,
) -> tuple[Path, Path]:
    """Two mono sines in the same low-mid band (220 Hz) for masking_proxy overlap."""
    bass = bass_path or fixture_path("masking_bass_220hz.wav")
    strings = strings_path or fixture_path("masking_strings_220hz.wav")
    build_sine_wav(bass, freq_hz=220.0, amplitude=0.7)
    build_sine_wav(strings, freq_hz=220.0, amplitude=0.65)
    return bass, strings


def ensure_all_fixtures() -> dict[str, Path]:
    """Build (or rebuild) the canonical fixture set; returns path map."""
    hot = build_hot_peak_wav()
    clipped = build_clipped_wav()
    stereo = build_stereo_imbalance_wav()
    bass, strings = build_masking_pair()
    logger.info(
        "Mix analysis fixtures ensured",
        extra={
            "fixture_count": 5,
            "names": [hot.name, clipped.name, stereo.name, bass.name, strings.name],
        },
    )
    return {
        "hot_peak": hot,
        "clipped": clipped,
        "stereo_imbalance": stereo,
        "masking_bass": bass,
        "masking_strings": strings,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    paths = ensure_all_fixtures()
    for key, path in paths.items():
        print(f"{key}: {path} ({path.stat().st_size} bytes)")
