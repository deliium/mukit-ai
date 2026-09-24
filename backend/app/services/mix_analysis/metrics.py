"""Deterministic DSP metrics for mix analysis (stdlib core; optional numpy/scipy)."""

from __future__ import annotations

import hashlib
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from app.mix_analysis_schemas import (
    MixAnalysisLocus,
    MixAnalysisMeasurement,
    MixAnalysisSeries,
    MixAnalysisSeriesPoint,
)
from app.mix_analysis_settings import MixAnalysisSettings
from app.services.mix_analysis.decode import (
    DecodedAudio,
    channel_frames,
    decode_wav_readonly,
    mono_mix,
)
from app.services.mix_analysis.windows import (
    AnalysisWindow,
    build_section_windows,
    slice_mono,
)

logger = logging.getLogger(__name__)

# Coarse band edges (Hz)
_BANDS: tuple[tuple[str, float, float], ...] = (
    ("sub", 20.0, 60.0),
    ("low", 60.0, 250.0),
    ("low_mid", 250.0, 500.0),
    ("high_mid", 500.0, 4000.0),
    ("high", 4000.0, 16000.0),
)


@dataclass
class StemMeasureResult:
    measurements: list[MixAnalysisMeasurement] = field(default_factory=list)
    series: list[MixAnalysisSeries] = field(default_factory=list)
    band_energies: dict[str, float] = field(default_factory=dict)
    duration_seconds: float = 0.0
    sample_rate: int = 0
    dsp_backend: str = "stdlib"
    sha256_prefix: str = ""
    mono_rms: float = 0.0


def _dbfs(amplitude: float) -> float:
    if amplitude <= 1e-12:
        return -120.0
    return 20.0 * math.log10(amplitude)


def _rms(samples: Sequence[float]) -> float:
    if not samples:
        return 0.0
    acc = 0.0
    for s in samples:
        acc += s * s
    return math.sqrt(acc / len(samples))


def _peak(samples: Sequence[float]) -> float:
    peak = 0.0
    for s in samples:
        a = abs(s)
        if a > peak:
            peak = a
    return peak


def _clip_stats(samples: Sequence[float], *, threshold: float = 0.999) -> tuple[int, float]:
    if not samples:
        return 0, 0.0
    count = sum(1 for s in samples if abs(s) >= threshold)
    return count, count / float(len(samples))


def _downsample_envelope(
    mono: Sequence[float],
    sample_rate: int,
    *,
    max_points: int,
    window_seconds: float = 0.05,
) -> list[MixAnalysisSeriesPoint]:
    if not mono or sample_rate <= 0:
        return []
    win = max(1, int(window_seconds * sample_rate))
    points: list[MixAnalysisSeriesPoint] = []
    for start in range(0, len(mono), win):
        chunk = mono[start : start + win]
        if not chunk:
            break
        t = start / float(sample_rate)
        points.append(MixAnalysisSeriesPoint(t=t, v=_dbfs(_peak(chunk))))
    if len(points) <= max_points:
        return points
    step = len(points) / float(max_points)
    return [points[int(i * step)] for i in range(max_points)]


def _band_energies_goertzel(
    mono: Sequence[float],
    sample_rate: int,
) -> dict[str, float]:
    """Coarse band energy via short DFT magnitude squares (stdlib)."""
    if not mono or sample_rate <= 0:
        return {name: 0.0 for name, _, _ in _BANDS}
    # Use up to ~4096 samples from the middle for speed
    n = min(len(mono), 4096)
    start = max(0, (len(mono) - n) // 2)
    chunk = mono[start : start + n]
    # Hann window
    energies = {name: 0.0 for name, _, _ in _BANDS}
    # Sample a few bins per band with Goertzel
    for name, lo, hi in _BANDS:
        freqs = _band_probe_freqs(lo, hi, sample_rate)
        total = 0.0
        for freq in freqs:
            total += _goertzel_power(chunk, sample_rate, freq)
        energies[name] = total / max(1, len(freqs))
    # Normalize relative to sum
    s = sum(energies.values()) or 1.0
    return {k: v / s for k, v in energies.items()}


def _band_probe_freqs(lo: float, hi: float, sample_rate: int) -> list[float]:
    nyquist = sample_rate / 2.0
    lo_c = max(1.0, min(lo, nyquist - 1))
    hi_c = max(lo_c + 1.0, min(hi, nyquist - 1))
    # 3 log-spaced probes
    if hi_c <= lo_c:
        return [lo_c]
    ratio = (hi_c / lo_c) ** (1 / 2)
    return [lo_c, lo_c * ratio, hi_c]


def _goertzel_power(samples: Sequence[float], sample_rate: int, freq: float) -> float:
    n = len(samples)
    if n == 0 or sample_rate <= 0:
        return 0.0
    k = int(0.5 + (n * freq) / sample_rate)
    w = (2.0 * math.pi * k) / n
    coeff = 2.0 * math.cos(w)
    s0 = 0.0
    s1 = 0.0
    s2 = 0.0
    for sample in samples:
        s0 = sample + coeff * s1 - s2
        s2 = s1
        s1 = s0
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return max(0.0, power) / n


def _file_sha256_prefix(path: Path, *, length: int = 16) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()[:length]


def measure_stem_file(
    path: Path,
    settings: MixAnalysisSettings,
    *,
    stem_id: str,
    stem_role: str,
    source_track_ids: Sequence[str] | None = None,
    dimensions: Sequence[str] | None = None,
    tempo_bpm: float | None = None,
    origin_tick: int = 0,
    composition: dict[str, Any] | None = None,
    series_id_prefix: str | None = None,
) -> StemMeasureResult:
    """Read-only measure a stem WAV. Never mutates the file."""
    started = time.perf_counter()
    dims = set(dimensions or [])
    if not dims:
        dims = {
            "peak",
            "loudness",
            "dynamic_range",
            "clipping",
            "stereo_balance",
            "spectral_balance",
            "lf_buildup",
            "headroom",
            "section_loudness",
        }

    sha_prefix = _file_sha256_prefix(path)
    audio = decode_wav_readonly(path, settings)
    mono = mono_mix(audio)
    channels = channel_frames(audio)
    locus = MixAnalysisLocus(
        stem_ids=[stem_id],
        stem_roles=[stem_role],
        source_track_ids=list(source_track_ids or []),
        start_seconds=0.0,
        end_seconds=audio.duration_seconds,
    )

    result = StemMeasureResult(
        duration_seconds=audio.duration_seconds,
        sample_rate=audio.sample_rate,
        dsp_backend=audio.backend,
        sha256_prefix=sha_prefix,
        mono_rms=_rms(mono),
    )
    measurements: list[MixAnalysisMeasurement] = []

    peak = _peak(mono)
    peak_db = _dbfs(peak)
    rms = result.mono_rms
    rms_db = _dbfs(rms)
    crest = peak_db - rms_db if rms > 1e-12 else 0.0
    headroom = -peak_db if peak_db < 0 else 0.0
    clip_count, clip_ratio = _clip_stats(mono)

    if "peak" in dims:
        measurements.append(
            MixAnalysisMeasurement(
                code="peak_dbfs", unit="dbfs", value=round(peak_db, 3), locus=locus
            )
        )
        measurements.append(
            MixAnalysisMeasurement(
                code="true_peak_proxy_dbfs",
                unit="dbfs",
                value=round(peak_db, 3),
                locus=locus,
                confidence="estimated",
            )
        )
    if "loudness" in dims:
        measurements.append(
            MixAnalysisMeasurement(
                code="rms_dbfs", unit="dbfs", value=round(rms_db, 3), locus=locus
            )
        )
        if audio.backend == "numpy_scipy":
            measurements.append(
                MixAnalysisMeasurement(
                    code="lufs_approx",
                    unit="lufs",
                    value=round(rms_db + 0.691, 3),  # coarse proxy only
                    locus=locus,
                    confidence="estimated",
                )
            )
    if "dynamic_range" in dims:
        measurements.append(
            MixAnalysisMeasurement(
                code="crest_factor_db", unit="db", value=round(crest, 3), locus=locus
            )
        )
    if "clipping" in dims:
        measurements.append(
            MixAnalysisMeasurement(
                code="clip_count", unit="count", value=float(clip_count), locus=locus
            )
        )
        measurements.append(
            MixAnalysisMeasurement(
                code="clip_ratio", unit="ratio", value=round(clip_ratio, 6), locus=locus
            )
        )
    if "headroom" in dims:
        measurements.append(
            MixAnalysisMeasurement(
                code="headroom_db", unit="db", value=round(headroom, 3), locus=locus
            )
        )

    if "stereo_balance" in dims and len(channels) >= 2:
        left_rms = _rms(channels[0])
        right_rms = _rms(channels[1])
        balance = _dbfs(left_rms + 1e-12) - _dbfs(right_rms + 1e-12)
        measurements.append(
            MixAnalysisMeasurement(
                code="stereo_lr_rms_balance_db",
                unit="db",
                value=round(balance, 3),
                locus=locus,
            )
        )
        corr = _correlation(channels[0], channels[1])
        measurements.append(
            MixAnalysisMeasurement(
                code="stereo_correlation",
                unit="correlation",
                value=round(corr, 4),
                locus=locus,
            )
        )

    bands = _band_energies_goertzel(mono, audio.sample_rate)
    result.band_energies = bands
    if "spectral_balance" in dims:
        for name, energy in bands.items():
            measurements.append(
                MixAnalysisMeasurement(
                    code=f"band_energy_{name}",
                    unit="relative",
                    value=round(energy, 4),
                    locus=MixAnalysisLocus(
                        stem_ids=[stem_id],
                        stem_roles=[stem_role],
                        source_track_ids=list(source_track_ids or []),
                        freq_hz_low=next(lo for n, lo, _ in _BANDS if n == name),
                        freq_hz_high=next(hi for n, _, hi in _BANDS if n == name),
                        start_seconds=0.0,
                        end_seconds=audio.duration_seconds,
                    ),
                )
            )
    if "lf_buildup" in dims:
        lf = bands.get("sub", 0.0) + bands.get("low", 0.0)
        measurements.append(
            MixAnalysisMeasurement(
                code="lf_buildup_score",
                unit="score",
                value=round(min(1.0, lf), 4),
                locus=MixAnalysisLocus(
                    stem_ids=[stem_id],
                    stem_roles=[stem_role],
                    source_track_ids=list(source_track_ids or []),
                    freq_hz_low=20.0,
                    freq_hz_high=250.0,
                    start_seconds=0.0,
                    end_seconds=audio.duration_seconds,
                ),
            )
        )

    # Reverb honest gap
    measurements.append(
        MixAnalysisMeasurement(
            code="metric_unavailable",
            unit="",
            value=None,
            locus=locus,
            confidence="unavailable",
            unavailable_code="reverb_estimate_unavailable",
        )
    )

    if "section_loudness" in dims:
        windows = build_section_windows(
            audio.duration_seconds,
            tempo_bpm=tempo_bpm,
            origin_tick=origin_tick,
            composition=composition,
        )
        section_rms: list[float] = []
        for window in windows:
            if window.label == "full":
                continue
            chunk = slice_mono(mono, audio.sample_rate, window)
            if chunk:
                section_rms.append(_dbfs(_rms(chunk)))
        if len(section_rms) >= 2:
            contrast = max(section_rms) - min(section_rms)
            measurements.append(
                MixAnalysisMeasurement(
                    code="section_loudness_contrast_db",
                    unit="db",
                    value=round(contrast, 3),
                    locus=locus,
                )
            )

    prefix = series_id_prefix or stem_id[:8]
    peak_series_id = f"peak_{prefix}"
    result.series.append(
        MixAnalysisSeries(
            id=peak_series_id,
            kind="peak_envelope",
            unit="dbfs",
            stem_id=stem_id,
            points=_downsample_envelope(
                mono, audio.sample_rate, max_points=settings.series_max_points
            ),
        )
    )
    # Attach series_ref on peak measurement when present
    for m in measurements:
        if m.code == "peak_dbfs":
            m.series_ref = peak_series_id
            break

    loud_series_id = f"loud_{prefix}"
    result.series.append(
        MixAnalysisSeries(
            id=loud_series_id,
            kind="loudness_envelope",
            unit="dbfs",
            stem_id=stem_id,
            points=_loudness_envelope(
                mono, audio.sample_rate, max_points=settings.series_max_points
            ),
        )
    )

    # Band-energy-over-time series (capped): one series per coarse band when spectral dims on
    if "spectral_balance" in dims or "masking_proxy" in dims or "lf_buildup" in dims:
        # Prefer low_mid for masking viz; always include sub/low when LF requested
        band_targets = [
            ("low_mid", 250.0, 500.0),
            ("low", 60.0, 250.0),
            ("sub", 20.0, 60.0),
        ]
        for band_name, lo, hi in band_targets:
            band_series_id = f"band_{band_name}_{prefix}"
            points = _band_energy_envelope(
                mono,
                audio.sample_rate,
                lo=lo,
                hi=hi,
                max_points=settings.series_max_points,
            )
            result.series.append(
                MixAnalysisSeries(
                    id=band_series_id,
                    kind="band_energy",
                    unit="relative",
                    stem_id=stem_id,
                    band=band_name,
                    points=points,
                )
            )
            # Link scalar band measurement to series when present
            code = f"band_energy_{band_name}"
            for m in measurements:
                if m.code == code and m.series_ref is None:
                    m.series_ref = band_series_id
                    break

    result.measurements = measurements
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Mix analysis stem measured",
        extra={
            "stem_id": stem_id,
            "role": stem_role,
            "duration_s": round(audio.duration_seconds, 3),
            "sample_rate": audio.sample_rate,
            "metric_count": len(measurements),
            "series_count": len(result.series),
            "dsp_backend": audio.backend,
            "duration_ms": duration_ms,
            "sha256_prefix": sha_prefix,
        },
    )
    return result


def measure_stem_pair_masking(
    a: StemMeasureResult,
    b: StemMeasureResult,
    *,
    stem_id_a: str,
    stem_id_b: str,
    role_a: str,
    role_b: str,
    tracks_a: Sequence[str],
    tracks_b: Sequence[str],
    start_seconds: float = 0.0,
    end_seconds: float | None = None,
    start_bar: int | None = None,
    end_bar: int | None = None,
) -> MixAnalysisMeasurement:
    """Pairwise spectral overlap proxy (not psychoacoustic MOS)."""
    bands = set(a.band_energies) & set(b.band_energies)
    if not bands:
        score = 0.0
        dominant = "low_mid"
    else:
        # Overlap = sum of min energies; high when both strong in same bands
        score = sum(
            min(a.band_energies.get(name, 0.0), b.band_energies.get(name, 0.0))
            for name in bands
        )
        dominant = max(
            bands,
            key=lambda name: min(
                a.band_energies.get(name, 0.0), b.band_energies.get(name, 0.0)
            ),
        )
    lo, hi = next((lo, hi) for name, lo, hi in _BANDS if name == dominant)
    end_s = end_seconds if end_seconds is not None else max(a.duration_seconds, b.duration_seconds)
    locus = MixAnalysisLocus(
        stem_ids=[stem_id_a, stem_id_b],
        stem_roles=[role_a, role_b],
        source_track_ids=list(dict.fromkeys([*tracks_a, *tracks_b])),
        freq_hz_low=lo,
        freq_hz_high=hi,
        start_seconds=start_seconds,
        end_seconds=end_s,
        start_bar=start_bar,
        end_bar=end_bar,
    )
    return MixAnalysisMeasurement(
        code="masking_proxy",
        unit="score",
        value=round(min(1.0, score), 4),
        locus=locus,
        confidence="estimated",
    )


def _correlation(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    mean_a = sum(a[:n]) / n
    mean_b = sum(b[:n]) / n
    num = 0.0
    den_a = 0.0
    den_b = 0.0
    for i in range(n):
        da = a[i] - mean_a
        db = b[i] - mean_b
        num += da * db
        den_a += da * da
        den_b += db * db
    denom = math.sqrt(den_a * den_b)
    if denom < 1e-12:
        return 0.0
    return max(-1.0, min(1.0, num / denom))


def _loudness_envelope(
    mono: Sequence[float],
    sample_rate: int,
    *,
    max_points: int,
    window_seconds: float = 0.1,
) -> list[MixAnalysisSeriesPoint]:
    if not mono or sample_rate <= 0:
        return []
    win = max(1, int(window_seconds * sample_rate))
    points: list[MixAnalysisSeriesPoint] = []
    for start in range(0, len(mono), win):
        chunk = mono[start : start + win]
        if not chunk:
            break
        points.append(
            MixAnalysisSeriesPoint(t=start / float(sample_rate), v=_dbfs(_rms(chunk)))
        )
    if len(points) <= max_points:
        return points
    step = len(points) / float(max_points)
    return [points[int(i * step)] for i in range(max_points)]


def _band_energy_envelope(
    mono: Sequence[float],
    sample_rate: int,
    *,
    lo: float,
    hi: float,
    max_points: int,
    window_seconds: float = 0.15,
) -> list[MixAnalysisSeriesPoint]:
    """Relative band energy over time via short Goertzel windows (stdlib)."""
    if not mono or sample_rate <= 0:
        return []
    win = max(64, int(window_seconds * sample_rate))
    points: list[MixAnalysisSeriesPoint] = []
    freqs = _band_probe_freqs(lo, hi, sample_rate)
    for start in range(0, len(mono), win):
        chunk = mono[start : start + win]
        if len(chunk) < 32:
            break
        power = 0.0
        for freq in freqs:
            power += _goertzel_power(chunk, sample_rate, freq)
        power /= max(1, len(freqs))
        # Normalize by chunk RMS power so values stay roughly in [0, 1]
        rms = _rms(chunk)
        denom = max(rms * rms, 1e-12)
        relative = min(1.0, power / denom)
        points.append(
            MixAnalysisSeriesPoint(
                t=start / float(sample_rate),
                v=round(relative, 4),
            )
        )
    if len(points) <= max_points:
        return points
    step = len(points) / float(max_points)
    return [points[int(i * step)] for i in range(max_points)]
