"""Pure rule observation builder — measurement thresholds → coded findings.

No AI. Caps observation count. Bars only when present on measurement loci.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Sequence

from app.mix_analysis_schemas import (
    MixAnalysisEvidence,
    MixAnalysisLocus,
    MixAnalysisMeasurement,
    MixAnalysisObservation,
)
from app.mix_analysis_settings import MixAnalysisSettings

logger = logging.getLogger(__name__)


def _by_code(
    measurements: Sequence[MixAnalysisMeasurement],
    code: str,
) -> list[MixAnalysisMeasurement]:
    return [m for m in measurements if m.code == code]


def build_observations(
    measurements: Sequence[MixAnalysisMeasurement],
    settings: MixAnalysisSettings,
) -> list[MixAnalysisObservation]:
    """Map measurement thresholds to objective observations with locus + reason."""
    out: list[MixAnalysisObservation] = []

    for m in _by_code(measurements, "clip_ratio"):
        if m.value is not None and m.value >= settings.clip_ratio:
            out.append(
                _obs(
                    code="clipping_detected",
                    severity="error",
                    message=_msg_clip(m.locus),
                    locus=m.locus,
                    reason=f"clip_ratio={m.value:.6f} >= threshold {settings.clip_ratio}",
                    codes=["clip_ratio", "clip_count"],
                    action="Reduce gain or limit the affected stem before the clip point.",
                )
            )

    for m in _by_code(measurements, "peak_dbfs"):
        if m.value is not None and m.value >= settings.peak_hot_dbfs:
            out.append(
                _obs(
                    code="peak_hot",
                    severity="warning",
                    message=_msg_peak(m.locus, m.value),
                    locus=m.locus,
                    reason=f"peak_dbfs={m.value:.2f} >= threshold {settings.peak_hot_dbfs}",
                    codes=["peak_dbfs"],
                    action="Lower stem peak level to leave more headroom.",
                )
            )

    for m in _by_code(measurements, "headroom_db"):
        if m.value is not None and m.value <= settings.low_headroom_db:
            out.append(
                _obs(
                    code="low_headroom",
                    severity="warning",
                    message=_msg_headroom(m.locus, m.value),
                    locus=m.locus,
                    reason=f"headroom_db={m.value:.2f} <= threshold {settings.low_headroom_db}",
                    codes=["headroom_db", "peak_dbfs"],
                    action="Increase headroom on the stem (pull fader or upper dynamics).",
                )
            )

    for m in _by_code(measurements, "stereo_lr_rms_balance_db"):
        if m.value is not None and abs(m.value) >= settings.stereo_imbalance_db:
            out.append(
                _obs(
                    code="stereo_imbalance",
                    severity="warning",
                    message=_msg_stereo(m.locus, m.value),
                    locus=m.locus,
                    reason=(
                        f"|stereo_lr_rms_balance_db|={abs(m.value):.2f} "
                        f">= threshold {settings.stereo_imbalance_db}"
                    ),
                    codes=["stereo_lr_rms_balance_db"],
                    action="Check L/R balance or mono-compatibility on the stem.",
                )
            )

    for m in _by_code(measurements, "lf_buildup_score"):
        if m.value is not None and m.value >= settings.lf_buildup_score:
            out.append(
                _obs(
                    code="lf_buildup",
                    severity="warning",
                    message=_msg_lf(m.locus, m.value),
                    locus=m.locus,
                    reason=f"lf_buildup_score={m.value:.3f} >= threshold {settings.lf_buildup_score}",
                    codes=["lf_buildup_score"],
                    action="High-pass or carve competing low energy on the affected stem.",
                )
            )

    for m in _by_code(measurements, "masking_proxy"):
        if m.value is not None and m.value >= settings.masking_proxy:
            out.append(
                _obs(
                    code="spectral_masking_proxy",
                    severity="warning",
                    message=_msg_masking(m.locus, m.value),
                    locus=m.locus,
                    reason=(
                        f"masking_proxy={m.value:.3f} >= threshold {settings.masking_proxy} "
                        f"(spectral overlap proxy, not MOS)"
                    ),
                    codes=["masking_proxy", "band_energy_low_mid"],
                    action=_masking_action(m.locus),
                )
            )

    for m in _by_code(measurements, "section_loudness_contrast_db"):
        if m.value is not None and m.value <= settings.section_loudness_flat_db:
            out.append(
                _obs(
                    code="section_loudness_flat",
                    severity="info",
                    message=_msg_section_flat(m.locus, m.value),
                    locus=m.locus,
                    reason=(
                        f"section_loudness_contrast_db={m.value:.2f} "
                        f"<= threshold {settings.section_loudness_flat_db}"
                    ),
                    codes=["section_loudness_contrast_db"],
                    action="Shape section loudness contrast if a louder climax is intended.",
                )
            )

    capped = out[: settings.observation_max]
    counts = Counter(o.code for o in capped)
    logger.debug(
        "Mix analysis observations emitted",
        extra={"codes": dict(counts)},
    )
    logger.info(
        "Mix analysis observation_count",
        extra={"observation_count": len(capped)},
    )
    return capped


def _obs(
    *,
    code: str,
    severity: str,
    message: str,
    locus: MixAnalysisLocus,
    reason: str,
    codes: list[str],
    action: str | None,
) -> MixAnalysisObservation:
    return MixAnalysisObservation(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        locus=locus,
        reason=reason,
        evidence=MixAnalysisEvidence(measurement_codes=codes),
        suggested_action=action,
    )


def _role_label(locus: MixAnalysisLocus) -> str:
    if locus.stem_roles:
        return " and ".join(locus.stem_roles)
    if locus.source_track_ids:
        return " and ".join(locus.source_track_ids)
    if locus.stem_ids:
        return " and ".join(locus.stem_ids[:2])
    return "stem"


def _time_label(locus: MixAnalysisLocus) -> str:
    if locus.start_bar is not None and locus.end_bar is not None:
        return f" during bars {locus.start_bar}–{locus.end_bar}"
    if locus.start_seconds is not None and locus.end_seconds is not None:
        return (
            f" during {locus.start_seconds:.1f}–{locus.end_seconds:.1f} s"
        )
    return ""


def _freq_label(locus: MixAnalysisLocus) -> str:
    if locus.freq_hz_low is not None and locus.freq_hz_high is not None:
        return (
            f" around {int(locus.freq_hz_low)}–{int(locus.freq_hz_high)} Hz"
        )
    return ""


def _msg_clip(locus: MixAnalysisLocus) -> str:
    return f"{_role_label(locus).capitalize()} shows digital clipping{_time_label(locus)}."


def _msg_peak(locus: MixAnalysisLocus, value: float) -> str:
    return (
        f"{_role_label(locus).capitalize()} peak is hot at {value:.1f} dBFS"
        f"{_time_label(locus)}."
    )


def _msg_headroom(locus: MixAnalysisLocus, value: float) -> str:
    return (
        f"{_role_label(locus).capitalize()} has only {value:.1f} dB headroom to 0 dBFS"
        f"{_time_label(locus)}."
    )


def _msg_stereo(locus: MixAnalysisLocus, value: float) -> str:
    side = "left" if value > 0 else "right"
    return (
        f"{_role_label(locus).capitalize()} stereo balance leans {side} "
        f"by {abs(value):.1f} dB{_time_label(locus)}."
    )


def _msg_lf(locus: MixAnalysisLocus, value: float) -> str:
    return (
        f"{_role_label(locus).capitalize()} shows LF buildup "
        f"(score {value:.2f}){_freq_label(locus)}{_time_label(locus)}."
    )


def _msg_masking(locus: MixAnalysisLocus, value: float) -> str:
    roles = _role_label(locus)
    band = _freq_label(locus) or " in shared bands"
    return (
        f"{roles[:1].upper()}{roles[1:] if roles else 'Stems'} show strong spectral overlap"
        f"{band}{_time_label(locus)} "
        f"(masking_proxy={value:.2f})."
    )


def _msg_section_flat(locus: MixAnalysisLocus, value: float) -> str:
    return (
        f"{_role_label(locus).capitalize()} section loudness contrast is low "
        f"({value:.1f} dB){_time_label(locus)}."
    )


def _masking_action(locus: MixAnalysisLocus) -> str:
    roles = locus.stem_roles or ["affected stems"]
    lo = int(locus.freq_hz_low) if locus.freq_hz_low is not None else 200
    hi = int(locus.freq_hz_high) if locus.freq_hz_high is not None else 400
    time = _time_label(locus).strip() or "the overlapping window"
    return (
        f"Carve {lo}–{hi} Hz on {' or '.join(roles)} during {time}."
    )
