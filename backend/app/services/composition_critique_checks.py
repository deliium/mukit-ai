"""Deterministic critique check adapters (warnings, constraints, climax, contrast)."""

from __future__ import annotations

import logging
from typing import Any, Sequence

from app.analysis_schemas import CompositionAnalysisReport, SectionSummaryResult
from app.composition_schemas import CompositionV2
from app.critique_schemas import (
    CRITIQUE_FINDING_CODES,
    CritiqueAffectedRange,
    CritiqueFindingEvidence,
    CritiqueFindingV1,
)
from app.critique_settings import CritiqueEngineSettings
from app.services.composition_critique_scope import ResolvedCritiqueScope
from app.services.generation_constraints import (
    GenerationConstraints,
    GenerationValidationReport,
    validate_generation_constraints,
)

logger = logging.getLogger(__name__)

# Analysis warning codes → finding category / default stratum.
_WARNING_CATEGORY: dict[str, str] = {
    "note_outside_instrument_range": "instrumentation",
    "dense_overlapping_material": "density",
    "overlapping_same_pitch_timing": "collision",
    "excessive_duplicate_notes": "duplication",
    "declared_key_conflicts_with_inference": "tonality",
    "declared_key_change_conflicts_with_inference": "tonality",
    "declared_harmony_conflicts_with_inference": "harmony",
    "declared_harmony_unparseable": "harmony",
    "timing_grid_anomaly": "rhythm",
    "empty_analysis_scope": "other",
}

_HARD_WARNING_CODES = frozenset(
    {
        "note_outside_instrument_range",
    }
)


def _finding(
    *,
    code: str,
    stratum: str,
    category: str,
    severity: str,
    explanation: str,
    suggested_action: str | None = None,
    affected_range: CritiqueAffectedRange | None = None,
    affected_tracks: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
    refs: list[str] | None = None,
) -> CritiqueFindingV1:
    return CritiqueFindingV1(
        code=code,
        stratum=stratum,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        explanation=explanation[:500],
        suggested_action=suggested_action,
        affected_range=affected_range,
        affected_tracks=affected_tracks or [],
        evidence=CritiqueFindingEvidence(
            metrics=metrics or {},
            refs=refs or [],
        ),
    )


def map_analysis_warnings_to_findings(
    report: CompositionAnalysisReport,
    *,
    resolved: ResolvedCritiqueScope,
) -> list[CritiqueFindingV1]:
    findings: list[CritiqueFindingV1] = []
    for warning in report.warnings or []:
        code = str(warning.code)
        category = _WARNING_CATEGORY.get(code, "other")
        stratum = "hard_constraint" if code in _HARD_WARNING_CODES else "technical"
        severity = warning.severity if warning.severity in ("info", "warning", "error") else "warning"
        if stratum == "hard_constraint" and severity == "info":
            severity = "error"
        affected_range = None
        affected_tracks: list[str] = []
        if warning.locator is not None:
            loc = warning.locator
            if loc.start_bar is not None or loc.end_bar is not None:
                affected_range = CritiqueAffectedRange(
                    start_bar=loc.start_bar,
                    end_bar=loc.end_bar or loc.start_bar,
                    start_tick=loc.start_tick,
                    end_tick=loc.end_tick,
                )
            if loc.track_id:
                affected_tracks = [loc.track_id]
        # Scope filter: drop findings clearly outside bar window when bars/section scoped.
        if affected_range and affected_range.start_bar is not None:
            if (
                affected_range.end_bar is not None
                and affected_range.end_bar < resolved.start_bar
            ) or (
                affected_range.start_bar is not None
                and affected_range.start_bar > resolved.end_bar
            ):
                continue
        if resolved.track_id and affected_tracks and resolved.track_id not in affected_tracks:
            continue
        findings.append(
            _finding(
                code=code if code in CRITIQUE_FINDING_CODES or code.replace("-", "_") else code,
                stratum=stratum,
                category=category,
                severity=severity,
                explanation=str(warning.message)[:500],
                affected_range=affected_range,
                affected_tracks=affected_tracks,
                refs=[f"analysis_warning:{code}"],
            )
        )
    logger.debug(
        "Mapped analysis warnings to findings",
        extra={"warning_count": len(report.warnings or []), "finding_count": len(findings)},
    )
    return findings


def findings_from_constraints(
    composition: CompositionV2,
    constraints: GenerationConstraints | None,
) -> list[CritiqueFindingV1]:
    if constraints is None:
        logger.debug("Constraint checks skipped", extra={"skip_reason": "no_constraints"})
        return []
    report: GenerationValidationReport = validate_generation_constraints(
        composition, constraints
    )
    findings: list[CritiqueFindingV1] = []
    for issue in report.errors:
        code = str(issue.code)
        category = "structure"
        if "key" in code:
            mapped = "requested_key_mismatch"
            category = "tonality"
        elif "instrument" in code:
            mapped = "requested_instrumentation_mismatch"
            category = "instrumentation"
        elif "section" in code or "bar" in code or "meter" in code:
            mapped = "requested_structure_mismatch"
            category = "structure"
        else:
            mapped = "requested_structure_mismatch"
        findings.append(
            _finding(
                code=mapped,
                stratum="hard_constraint",
                category=category,
                severity="error",
                explanation=str(issue.message)[:500],
                refs=[f"constraint:{code}"],
                metrics={"constraint_code": code},
            )
        )
    logger.debug(
        "Constraint findings",
        extra={"error_count": len(report.errors), "finding_count": len(findings)},
    )
    return findings


def _velocity_stats(
    composition: CompositionV2,
    *,
    start_tick: int,
    end_tick: int,
) -> tuple[float | None, float | None]:
    velocities: list[int] = []
    for track in composition.tracks or []:
        for event in track.events or []:
            s = int(event.start_tick)
            e = s + int(event.duration_ticks)
            if e <= start_tick or s >= end_tick:
                continue
            vel = getattr(event, "velocity", None)
            if isinstance(vel, int):
                velocities.append(vel)
    if not velocities:
        return None, None
    return float(sum(velocities) / len(velocities)), float(max(velocities))


def _relative_delta(prior: float, current: float) -> float:
    denom = max(abs(prior), 1e-9)
    return abs(current - prior) / denom


def resolve_climax_section_index(
    composition: CompositionV2,
    *,
    requested_climax_section_index: int | None = None,
    brief_text: str | None = None,
    settings: CritiqueEngineSettings,
) -> int | None:
    """Resolve climax intent. Prefer explicit index / label; avoid false chorus hits."""
    sections = list(composition.sections or [])
    if not sections:
        return None
    if requested_climax_section_index is not None:
        if 0 <= requested_climax_section_index < len(sections):
            return requested_climax_section_index
        return None
    for idx, section in enumerate(sections):
        label = str(getattr(section, "label", None) or "").lower()
        sid = str(getattr(section, "id", None) or "").lower()
        stype = str(getattr(section, "type", None) or "").lower()
        if "climax" in label or "climax" in sid:
            return idx
        if settings.treat_chorus_as_climax and stype == "chorus":
            return idx
    if brief_text and "climax" in brief_text.lower():
        # Prefer last non-intro/outro section when brief mentions climax without index.
        for idx in range(len(sections) - 1, -1, -1):
            stype = str(getattr(sections[idx], "type", None) or "").lower()
            if stype not in {"intro", "outro", "unsectioned"}:
                return idx
    return None


def check_climax_contrast(
    composition: CompositionV2,
    report: CompositionAnalysisReport,
    *,
    settings: CritiqueEngineSettings,
    requested_climax_section_index: int | None = None,
    brief_text: str | None = None,
) -> list[CritiqueFindingV1]:
    """Emit ``climax_lacks_contrast`` when climax ≈ prior section density/dynamics."""
    climax_idx = resolve_climax_section_index(
        composition,
        requested_climax_section_index=requested_climax_section_index,
        brief_text=brief_text,
        settings=settings,
    )
    if climax_idx is None or climax_idx <= 0:
        logger.debug(
            "Climax contrast skipped",
            extra={"skip_reason": "no_climax_or_no_prior", "climax_idx": climax_idx},
        )
        return []

    summaries: Sequence[SectionSummaryResult] = report.section_summaries or []
    by_index = {s.section_index: s for s in summaries}
    climax = by_index.get(climax_idx)
    prior = by_index.get(climax_idx - 1)
    if climax is None or prior is None:
        # Fall back to section tick windows from composition.
        sections = list(composition.sections or [])
        if climax_idx >= len(sections) or climax_idx - 1 < 0:
            return []
        climax_sec = sections[climax_idx]
        prior_sec = sections[climax_idx - 1]
        climax_load = None
        prior_load = None
        climax_start, climax_end = int(climax_sec.start_tick), int(
            climax_sec.start_tick + climax_sec.duration_ticks
        )
        prior_start, prior_end = int(prior_sec.start_tick), int(
            prior_sec.start_tick + prior_sec.duration_ticks
        )
    else:
        climax_load = climax.note_load
        prior_load = prior.note_load
        climax_start, climax_end = climax.start_tick, climax.end_tick
        prior_start, prior_end = prior.start_tick, prior.end_tick

    if climax_load is None or prior_load is None:
        # Compute crude note_load from events if summaries missing values.
        def _load(start: int, end: int) -> float:
            duration = max(1, end - start)
            load_ticks = 0
            for track in composition.tracks or []:
                for event in track.events or []:
                    s = int(event.start_tick)
                    e = s + int(event.duration_ticks)
                    overlap = max(0, min(e, end) - max(s, start))
                    load_ticks += overlap
            return load_ticks / float(duration)

        if climax_load is None:
            climax_load = _load(climax_start, climax_end)
        if prior_load is None:
            prior_load = _load(prior_start, prior_end)

    note_delta = _relative_delta(float(prior_load), float(climax_load))
    _, prior_peak = _velocity_stats(composition, start_tick=prior_start, end_tick=prior_end)
    _, climax_peak = _velocity_stats(composition, start_tick=climax_start, end_tick=climax_end)
    velocity_close = True
    velocity_delta = None
    if prior_peak is not None and climax_peak is not None:
        velocity_delta = _relative_delta(prior_peak, climax_peak)
        velocity_close = velocity_delta <= settings.climax_peak_velocity_relative_delta_max

    load_close = note_delta <= settings.climax_note_load_relative_delta_max
    if not (load_close and velocity_close):
        logger.debug(
            "Climax contrast ok",
            extra={
                "note_load_relative_delta": round(note_delta, 6),
                "velocity_delta": velocity_delta,
            },
        )
        return []

    sections = list(composition.sections or [])
    climax_sec = sections[climax_idx]
    start_bar = int(climax_sec.start_bar)
    end_bar = start_bar + int(climax_sec.bar_count) - 1
    prior_sec = sections[climax_idx - 1]
    prior_start_bar = int(prior_sec.start_bar)
    prior_end_bar = prior_start_bar + int(prior_sec.bar_count) - 1

    logger.info(
        "Climax contrast finding",
        extra={"code": "climax_lacks_contrast", "climax_section_index": climax_idx},
    )
    return [
        _finding(
            code="climax_lacks_contrast",
            stratum="stylistic",
            category="contrast",
            severity="info",
            explanation=(
                "Requested climax section has nearly identical note density and peak "
                "velocity to the preceding section."
            ),
            suggested_action=(
                f"Raise density and peak dynamics in bars {start_bar}–{end_bar} "
                f"relative to bars {prior_start_bar}–{prior_end_bar} so the climax "
                "reads as a contrast peak."
            ),
            affected_range=CritiqueAffectedRange(start_bar=start_bar, end_bar=end_bar),
            metrics={
                "prior_note_load": round(float(prior_load), 6),
                "climax_note_load": round(float(climax_load), 6),
                "note_load_relative_delta": round(note_delta, 6),
                "prior_peak_velocity": prior_peak,
                "climax_peak_velocity": climax_peak,
                "velocity_relative_delta": (
                    round(velocity_delta, 6) if velocity_delta is not None else None
                ),
            },
            refs=[f"section_index:{climax_idx - 1}", f"section_index:{climax_idx}"],
        )
    ]


def check_section_contrast(
    report: CompositionAnalysisReport,
    *,
    settings: CritiqueEngineSettings,
    skip_pair: tuple[int, int] | None = None,
) -> list[CritiqueFindingV1]:
    """Adjacent section near-identical density → stylistic observation."""
    summaries = sorted(report.section_summaries or [], key=lambda s: s.section_index)
    findings: list[CritiqueFindingV1] = []
    for i in range(1, len(summaries)):
        prior, current = summaries[i - 1], summaries[i]
        if skip_pair and skip_pair == (prior.section_index, current.section_index):
            continue
        if prior.note_load is None or current.note_load is None:
            continue
        delta = _relative_delta(float(prior.note_load), float(current.note_load))
        if delta >= settings.section_contrast_note_load_delta_min:
            continue
        findings.append(
            _finding(
                code="section_lacks_contrast",
                stratum="stylistic",
                category="contrast",
                severity="info",
                explanation=(
                    f"Sections {prior.section_index} and {current.section_index} "
                    "have nearly identical note density."
                ),
                metrics={
                    "prior_note_load": prior.note_load,
                    "current_note_load": current.note_load,
                    "note_load_relative_delta": round(delta, 6),
                },
                refs=[
                    f"section_index:{prior.section_index}",
                    f"section_index:{current.section_index}",
                ],
            )
        )
    return findings


def check_melody_and_tension_stylistic(
    report: CompositionAnalysisReport,
) -> list[CritiqueFindingV1]:
    """Lightweight stylistic observations from melody contour / tension."""
    findings: list[CritiqueFindingV1] = []
    melody = report.melody
    if melody is not None:
        profiles = getattr(melody, "profiles", None) or []
        for profile in profiles[:4]:
            contour = getattr(profile, "contour", None) or getattr(profile, "shape", None)
            if contour in ("flat", "static") or (
                isinstance(contour, (list, tuple)) and len(set(contour)) <= 1
            ):
                findings.append(
                    _finding(
                        code="melodic_contour_flat",
                        stratum="stylistic",
                        category="melody",
                        severity="info",
                        explanation="Melodic contour appears unusually flat in scoped melody profile.",
                        refs=["melody_profile"],
                    )
                )
                break
    tension = report.tension
    if tension is not None:
        components = getattr(tension, "components", None)
        curve = getattr(components, "curve", None) if components is not None else None
        if isinstance(curve, (list, tuple)) and len(curve) >= 2:
            vals = [float(x) for x in curve if isinstance(x, (int, float))]
            if vals and (max(vals) - min(vals)) < 0.05:
                findings.append(
                    _finding(
                        code="tension_curve_flat",
                        stratum="stylistic",
                        category="tension",
                        severity="info",
                        explanation="Tension curve lacks meaningful shape across the composition.",
                        refs=["tension"],
                    )
                )
    return findings
