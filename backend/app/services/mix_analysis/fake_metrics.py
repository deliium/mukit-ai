"""Deterministic fake mix-analysis measurements for CI (digest-driven).

Never claims calibrated quality. Values are chosen so rule observations fire
for AC journeys under ``MIX_ANALYSIS_FAKE_MODE``.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Sequence

from app.mix_analysis_schemas import (
    MixAnalysisLocus,
    MixAnalysisMeasurement,
    MixAnalysisSeries,
    MixAnalysisSeriesPoint,
)
from app.mix_analysis_settings import MixAnalysisSettings
from app.services.mix_analysis.metrics import StemMeasureResult

logger = logging.getLogger(__name__)


def _digest_prefix(path: Path, *, length: int = 16) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        digest.update(path.name.encode("utf-8"))
    return digest.hexdigest()[:length]


def fake_measure_stem(
    path: Path,
    settings: MixAnalysisSettings,
    *,
    stem_id: str,
    stem_role: str,
    source_track_ids: Sequence[str] | None = None,
    duration_seconds: float = 4.0,
) -> StemMeasureResult:
    """Emit deterministic measurements from file sha256 + stem_role."""
    _ = settings
    sha = _digest_prefix(path)
    # Role-biased values that trigger observations for bass/strings pairs etc.
    role = (stem_role or "other").lower()
    nibble = int(sha[:2], 16) / 255.0

    # Hot peaks / low headroom for most roles so AC gets ≥1 observation
    peak_db = -0.4 - (0.2 * nibble) if role in {"bass", "piano", "strings"} else -6.0
    rms_db = peak_db - 8.0
    headroom = max(0.0, -peak_db)
    clip_ratio = 0.001 if peak_db > -1.0 else 0.0
    stereo_bal = 7.5 if role == "piano" else 0.5
    lf = 0.78 if role in {"bass", "drums"} else 0.25
    bands = {
        "sub": 0.35 if role == "bass" else 0.05,
        "low": 0.40 if role in {"bass", "drums"} else 0.10,
        "low_mid": 0.45 if role in {"bass", "strings"} else 0.15,
        "high_mid": 0.20 if role == "strings" else 0.25,
        "high": 0.10,
    }
    # Normalize
    s = sum(bands.values()) or 1.0
    bands = {k: v / s for k, v in bands.items()}

    locus = MixAnalysisLocus(
        stem_ids=[stem_id],
        stem_roles=[role],
        source_track_ids=list(source_track_ids or []),
        start_seconds=0.0,
        end_seconds=duration_seconds,
    )
    measurements = [
        MixAnalysisMeasurement(code="peak_dbfs", unit="dbfs", value=round(peak_db, 3), locus=locus),
        MixAnalysisMeasurement(code="rms_dbfs", unit="dbfs", value=round(rms_db, 3), locus=locus),
        MixAnalysisMeasurement(
            code="crest_factor_db", unit="db", value=round(peak_db - rms_db, 3), locus=locus
        ),
        MixAnalysisMeasurement(
            code="clip_count", unit="count", value=float(int(clip_ratio * 1000)), locus=locus
        ),
        MixAnalysisMeasurement(
            code="clip_ratio", unit="ratio", value=round(clip_ratio, 6), locus=locus
        ),
        MixAnalysisMeasurement(
            code="headroom_db", unit="db", value=round(headroom, 3), locus=locus
        ),
        MixAnalysisMeasurement(
            code="stereo_lr_rms_balance_db",
            unit="db",
            value=round(stereo_bal, 3),
            locus=locus,
        ),
        MixAnalysisMeasurement(
            code="lf_buildup_score",
            unit="score",
            value=round(lf, 4),
            locus=MixAnalysisLocus(
                stem_ids=[stem_id],
                stem_roles=[role],
                source_track_ids=list(source_track_ids or []),
                freq_hz_low=20.0,
                freq_hz_high=250.0,
                start_seconds=0.0,
                end_seconds=duration_seconds,
            ),
        ),
        MixAnalysisMeasurement(
            code="section_loudness_contrast_db",
            unit="db",
            value=0.4,
            locus=locus,
        ),
        MixAnalysisMeasurement(
            code="metric_unavailable",
            unit="",
            value=None,
            locus=locus,
            confidence="unavailable",
            unavailable_code="reverb_estimate_unavailable",
        ),
    ]
    for name, energy in bands.items():
        measurements.append(
            MixAnalysisMeasurement(
                code=f"band_energy_{name}",
                unit="relative",
                value=round(energy, 4),
                locus=MixAnalysisLocus(
                    stem_ids=[stem_id],
                    stem_roles=[role],
                    source_track_ids=list(source_track_ids or []),
                    start_seconds=0.0,
                    end_seconds=duration_seconds,
                ),
            )
        )

    series = [
        MixAnalysisSeries(
            id=f"peak_{stem_id[:8]}",
            kind="peak_envelope",
            unit="dbfs",
            stem_id=stem_id,
            points=[
                MixAnalysisSeriesPoint(t=0.0, v=round(peak_db, 3)),
                MixAnalysisSeriesPoint(t=duration_seconds, v=round(peak_db - 1.0, 3)),
            ],
        ),
        MixAnalysisSeries(
            id=f"loud_{stem_id[:8]}",
            kind="loudness_envelope",
            unit="dbfs",
            stem_id=stem_id,
            points=[
                MixAnalysisSeriesPoint(t=0.0, v=round(rms_db, 3)),
                MixAnalysisSeriesPoint(t=duration_seconds, v=round(rms_db, 3)),
            ],
        ),
        MixAnalysisSeries(
            id=f"band_low_mid_{stem_id[:8]}",
            kind="band_energy",
            unit="relative",
            stem_id=stem_id,
            band="low_mid",
            points=[
                MixAnalysisSeriesPoint(t=0.0, v=round(bands.get("low_mid", 0.2), 4)),
                MixAnalysisSeriesPoint(
                    t=duration_seconds, v=round(bands.get("low_mid", 0.2), 4)
                ),
            ],
        ),
    ]
    logger.info(
        "Mix analysis fake stem measured",
        extra={
            "stem_id": stem_id,
            "role": role,
            "metric_count": len(measurements),
            "dsp_backend": "fake",
            "sha256_prefix": sha,
        },
    )
    return StemMeasureResult(
        measurements=measurements,
        series=series,
        band_energies=bands,
        duration_seconds=duration_seconds,
        sample_rate=22050,
        dsp_backend="fake",
        sha256_prefix=sha,
        mono_rms=10 ** (rms_db / 20.0),
    )
