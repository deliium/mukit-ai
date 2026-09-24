"""Audio-native scaffolding estimators (tempo / beat / structure / key / harmony).

Outputs nest under recovery preview scaffolding. Fake estimators return golden
values for fixture digests. Optional local DSP degrades with issue codes —
never invents high-confidence harmony when estimators are weak.
"""

from __future__ import annotations

import hashlib
import logging
import math
import struct
import wave
from dataclasses import dataclass, field
from pathlib import Path

from app.audio_recovery_schemas import (
    AudioRecoveryBeatGrid,
    AudioRecoveryHarmonySpan,
    AudioRecoveryIssue,
    AudioRecoveryKeyEstimate,
    AudioRecoveryScaffolding,
    AudioRecoveryStructureSection,
    AudioRecoveryTempoSource,
)


logger = logging.getLogger(__name__)


@dataclass
class ScaffoldingEstimate:
    scaffolding: AudioRecoveryScaffolding
    issues: list[AudioRecoveryIssue] = field(default_factory=list)
    fake: bool = False


# Golden scaffolding keyed by digest prefix for CI fixtures.
FAKE_SCAFFOLDING_TABLE: dict[str, dict] = {
    "default": {
        "tempo_bpm": 120,
        "tempo_confidence": 0.85,
        "tempo_source": "estimated",
        "meter": "4/4",
        "key": {"tonic": "C", "mode": "major", "confidence": 0.7},
        "structure_labels": ["intro", "A"],
        "harmony_symbols": ["C", "G", "Am", "F"],
    },
    "slow": {
        "tempo_bpm": 90,
        "tempo_confidence": 0.75,
        "tempo_source": "estimated",
        "meter": "4/4",
        "key": {"tonic": "A", "mode": "minor", "confidence": 0.65},
        "structure_labels": ["A"],
        "harmony_symbols": ["Am", "Em"],
    },
}


def _sha256_prefix(data: bytes, *, length: int = 12) -> str:
    return hashlib.sha256(data).hexdigest()[:length]


def _read_wav_mono(path: Path) -> tuple[list[float], int, float]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        nframes = handle.getnframes()
        raw = handle.readframes(nframes)
    duration = nframes / float(rate) if rate > 0 else 0.0
    if width != 2 or rate <= 0 or nframes <= 0:
        return [], rate, duration
    fmt = f"<{nframes * channels}h"
    ints = struct.unpack(fmt, raw)
    if channels == 1:
        samples = [v / 32768.0 for v in ints]
    else:
        samples = []
        for i in range(0, len(ints), channels):
            frame = ints[i : i + channels]
            samples.append(sum(frame) / (channels * 32768.0))
    return samples, rate, duration


def _estimate_tempo_bpm(samples: list[float], sample_rate: int) -> tuple[int, float, AudioRecoveryTempoSource]:
    """Lightweight onset-interval tempo estimate; degrades to defaulted."""
    if len(samples) < sample_rate // 4 or sample_rate <= 0:
        return 120, 0.35, "defaulted"
    window = max(1, sample_rate // 50)
    energies: list[float] = []
    for i in range(0, len(samples) - window, window):
        chunk = samples[i : i + window]
        energies.append(sum(s * s for s in chunk) / len(chunk))
    if len(energies) < 4:
        return 120, 0.35, "defaulted"
    mean_e = sum(energies) / len(energies)
    threshold = mean_e * 1.5
    onset_idxs = [i for i, e in enumerate(energies) if e >= threshold]
    if len(onset_idxs) < 2:
        return 120, 0.4, "defaulted"
    intervals = [
        (onset_idxs[i] - onset_idxs[i - 1]) * (window / sample_rate)
        for i in range(1, len(onset_idxs))
        if onset_idxs[i] > onset_idxs[i - 1]
    ]
    positive = [iv for iv in intervals if 0.2 <= iv <= 1.5]
    if not positive:
        return 120, 0.4, "defaulted"
    median = sorted(positive)[len(positive) // 2]
    bpm = int(round(60.0 / median))
    bpm = max(40, min(200, bpm))
    confidence = min(0.9, 0.45 + 0.05 * len(positive))
    logger.debug(
        "Tempo estimated from onsets",
        extra={"tempo_bpm": bpm, "tempo_confidence": round(confidence, 3)},
    )
    return bpm, confidence, "estimated"


def _fake_scaffolding_for_digest(
    digest: str,
    *,
    duration_seconds: float,
    ticks_per_quarter: int,
) -> ScaffoldingEstimate:
    entry = FAKE_SCAFFOLDING_TABLE["slow"] if digest.startswith("slow") else FAKE_SCAFFOLDING_TABLE["default"]
    tempo_bpm = int(entry["tempo_bpm"])
    ticks_per_second = (tempo_bpm / 60.0) * ticks_per_quarter
    total_ticks = max(ticks_per_quarter, int(round(duration_seconds * ticks_per_second)))
    labels: list[str] = list(entry["structure_labels"])
    structure: list[AudioRecoveryStructureSection] = []
    span = max(ticks_per_quarter, total_ticks // max(1, len(labels)))
    for i, label in enumerate(labels):
        start = i * span
        end = total_ticks if i == len(labels) - 1 else (i + 1) * span
        structure.append(
            AudioRecoveryStructureSection(
                label=label,
                start_tick=start,
                end_tick=max(start + 1, end),
                confidence=0.72,
            )
        )
    symbols: list[str] = list(entry["harmony_symbols"])
    harmony: list[AudioRecoveryHarmonySpan] = []
    harm_span = max(ticks_per_quarter, total_ticks // max(1, len(symbols)))
    for i, symbol in enumerate(symbols):
        start = i * harm_span
        end = total_ticks if i == len(symbols) - 1 else (i + 1) * harm_span
        harmony.append(
            AudioRecoveryHarmonySpan(
                symbol=symbol,
                start_tick=start,
                end_tick=max(start + 1, end),
                confidence=0.6,
            )
        )
    key_raw = entry["key"]
    scaffolding = AudioRecoveryScaffolding(
        tempo_bpm=tempo_bpm,
        tempo_confidence=float(entry["tempo_confidence"]),
        tempo_source=entry["tempo_source"],  # type: ignore[arg-type]
        meter=str(entry["meter"]),
        beat_grid=AudioRecoveryBeatGrid(
            downbeat_offset_seconds=0.0,
            ticks_per_quarter=ticks_per_quarter,
            confidence=0.8,
        ),
        structure=structure,
        key=AudioRecoveryKeyEstimate(
            tonic=str(key_raw["tonic"]),
            mode=str(key_raw["mode"]),
            confidence=float(key_raw["confidence"]),
        ),
        harmony=harmony,
    )
    issues: list[AudioRecoveryIssue] = []
    if scaffolding.tempo_confidence < 0.5:
        issues.append(
            AudioRecoveryIssue(
                code="scaffolding_low_confidence",
                severity="warning",
                message="Tempo confidence is below the include threshold.",
            )
        )
    logger.info(
        "Fake scaffolding ready",
        extra={
            "tempo_source": scaffolding.tempo_source,
            "tempo_bpm": scaffolding.tempo_bpm,
            "key_confidence": scaffolding.key.confidence if scaffolding.key else None,
            "section_count": len(scaffolding.structure),
            "digest_prefix": digest,
        },
    )
    return ScaffoldingEstimate(scaffolding=scaffolding, issues=issues, fake=True)


def estimate_scaffolding(
    source_path: Path,
    *,
    ticks_per_quarter: int = 480,
    default_tempo_bpm: int = 120,
    fake_mode: bool = False,
) -> ScaffoldingEstimate:
    payload = source_path.read_bytes()
    digest = _sha256_prefix(payload)
    samples, sample_rate, duration = _read_wav_mono(source_path)

    if fake_mode:
        return _fake_scaffolding_for_digest(
            digest,
            duration_seconds=duration or 2.0,
            ticks_per_quarter=ticks_per_quarter,
        )

    issues: list[AudioRecoveryIssue] = []
    if samples:
        tempo_bpm, tempo_confidence, tempo_source = _estimate_tempo_bpm(samples, sample_rate)
    else:
        tempo_bpm, tempo_confidence, tempo_source = default_tempo_bpm, 0.3, "defaulted"
        issues.append(
            AudioRecoveryIssue(
                code="tempo_defaulted",
                severity="warning",
                message="Tempo was missing; used the configured default BPM.",
            )
        )

    if tempo_source == "estimated":
        issues.append(
            AudioRecoveryIssue(
                code="tempo_estimated",
                severity="info",
                message="Tempo was estimated from the audio signal.",
            )
        )

    ticks_per_second = (tempo_bpm / 60.0) * ticks_per_quarter
    total_ticks = max(ticks_per_quarter, int(round(max(duration, 0.5) * ticks_per_second)))

    # Coarse A/B structure split at midpoint — low confidence when no DSP extras.
    mid = total_ticks // 2
    structure = [
        AudioRecoveryStructureSection(
            label="A",
            start_tick=0,
            end_tick=max(1, mid),
            confidence=0.45,
        ),
        AudioRecoveryStructureSection(
            label="B",
            start_tick=mid,
            end_tick=total_ticks,
            confidence=0.4,
        ),
    ]

    # Key/harmony: degrade honestly — no high-confidence invention.
    key = AudioRecoveryKeyEstimate(tonic="C", mode="major", confidence=0.35)
    harmony: list[AudioRecoveryHarmonySpan] = []
    issues.append(
        AudioRecoveryIssue(
            code="scaffolding_low_confidence",
            severity="warning",
            message="Key/harmony estimates are low confidence without optional DSP extras.",
        )
    )
    issues.append(
        AudioRecoveryIssue(
            code="harmony_omitted_low_confidence",
            severity="info",
            message="Low-confidence harmony spans were omitted by default.",
        )
    )

    scaffolding = AudioRecoveryScaffolding(
        tempo_bpm=tempo_bpm,
        tempo_confidence=tempo_confidence,
        tempo_source=tempo_source,
        meter="4/4",
        beat_grid=AudioRecoveryBeatGrid(
            downbeat_offset_seconds=0.0,
            ticks_per_quarter=ticks_per_quarter,
            confidence=min(0.7, tempo_confidence),
        ),
        structure=structure,
        key=key,
        harmony=harmony,
    )
    logger.info(
        "Scaffolding estimated",
        extra={
            "tempo_source": tempo_source,
            "tempo_bpm": tempo_bpm,
            "tempo_confidence": round(tempo_confidence, 3),
            "key_confidence": key.confidence,
            "section_count": len(structure),
            "harmony_count": len(harmony),
        },
    )
    if tempo_confidence < 0.5:
        logger.warning(
            "Low-confidence scaffolding tempo",
            extra={"tempo_confidence": round(tempo_confidence, 3)},
        )
    return ScaffoldingEstimate(scaffolding=scaffolding, issues=issues, fake=False)
