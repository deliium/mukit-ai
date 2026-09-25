"""Deterministic short WAVs for mix-plan tests.

These are fixtures, not a quality render. ``build_role_wav`` writes a
steady tone so stem bytes can be hashed before and after apply.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

ROLE_FREQ_HZ = {
    "bass": 110.0,
    "piano": 440.0,
    "strings": 220.0,
}


def build_role_wav(
    path: Path,
    *,
    role: str = "bass",
    seconds: float = 0.2,
    sample_rate: int = 22050,
    amplitude: float = 0.4,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(seconds * sample_rate))
    freq = ROLE_FREQ_HZ.get(role, 330.0)
    samples: list[int] = []
    for index in range(frames):
        value = amplitude * math.sin(2.0 * math.pi * freq * index / sample_rate)
        sample = int(max(-1.0, min(1.0, value)) * 32767)
        samples.append(sample)
        samples.append(int(sample * 0.8))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path
