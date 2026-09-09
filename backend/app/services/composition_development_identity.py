"""Versioned identity and seam diagnostics for composition development candidates.

Strength profiles classify checks as required errors or advisory warnings.
Musical relatedness is scored with bounded deterministic metrics — not generative.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from app.composition_development_schemas import (
    DEVELOPMENT_IDENTITY_DIAGNOSTIC_CODES,
    CompositionDevelopmentDraft,
    DevelopmentIdentityDiagnostic,
    VariationStrength,
)
from app.composition_schemas import midi_pitch_number
from app.services.composition_development_context import (
    DevelopmentSourceContext,
    RelativeContextNote,
    TrackContextSummary,
)


logger = logging.getLogger(__name__)

DEVELOPMENT_IDENTITY_VERSION = "composition.development.identity.v1"


@dataclass(frozen=True)
class StrengthProfile:
    name: VariationStrength
    min_identity_anchors: int
    max_register_semitones: float
    max_rhythm_divergence: float
    max_density_divergence: float
    max_seam_leap_semitones: float
    max_seam_gap_ticks_ratio: float
    require_motif_or_rhythm_or_harmony: bool


STRENGTH_PROFILES: dict[VariationStrength, StrengthProfile] = {
    "conservative": StrengthProfile(
        name="conservative",
        min_identity_anchors=2,
        max_register_semitones=7.0,
        max_rhythm_divergence=0.35,
        max_density_divergence=0.35,
        max_seam_leap_semitones=7.0,
        max_seam_gap_ticks_ratio=0.5,
        require_motif_or_rhythm_or_harmony=True,
    ),
    "balanced": StrengthProfile(
        name="balanced",
        min_identity_anchors=1,
        max_register_semitones=12.0,
        max_rhythm_divergence=0.55,
        max_density_divergence=0.55,
        max_seam_leap_semitones=12.0,
        max_seam_gap_ticks_ratio=1.0,
        require_motif_or_rhythm_or_harmony=True,
    ),
    "experimental": StrengthProfile(
        name="experimental",
        min_identity_anchors=1,
        max_register_semitones=19.0,
        max_rhythm_divergence=0.8,
        max_density_divergence=0.8,
        max_seam_leap_semitones=19.0,
        max_seam_gap_ticks_ratio=2.0,
        require_motif_or_rhythm_or_harmony=True,
    ),
}


@dataclass(frozen=True)
class IdentityEvaluationResult:
    passed: bool
    strength: VariationStrength
    diagnostics: tuple[DevelopmentIdentityDiagnostic, ...]
    warning_codes: tuple[str, ...]
    error_codes: tuple[str, ...]
    scores: dict[str, float]


def strength_profile(strength: VariationStrength) -> StrengthProfile:
    return STRENGTH_PROFILES[strength]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _diag(
    code: str,
    severity: Literal["error", "warning", "info"],
    message: str,
    *,
    score: float | None = None,
    track_id: str | None = None,
) -> DevelopmentIdentityDiagnostic:
    if code not in DEVELOPMENT_IDENTITY_DIAGNOSTIC_CODES:
        raise ValueError(f"Unknown identity diagnostic code: {code}")
    return DevelopmentIdentityDiagnostic(
        code=code,
        severity=severity,
        message=message,
        score=None if score is None else _clamp01(score),
        track_id=track_id,
    )


def _draft_track_map(draft: CompositionDevelopmentDraft) -> dict[str, Any]:
    return {track.track_id: track for track in draft.tracks}


def _mean_abs(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(abs(value) for value in values) / float(len(values))


def _register_center(notes: tuple[RelativeContextNote, ...] | list[Any], *, absolute: bool = False) -> float | None:
    if not notes:
        return None
    if absolute:
        midis = [midi_pitch_number(note.pitch) for note in notes]  # type: ignore[attr-defined]
    else:
        # Relative offsets — use mean offset as register proxy within the excerpt.
        midis = [float(note.pitch_semitone_offset) for note in notes]  # type: ignore[attr-defined]
    if not midis:
        return None
    return sum(midis) / float(len(midis))


def _onset_set(notes: list[Any] | tuple[RelativeContextNote, ...]) -> set[int]:
    return {int(getattr(note, "relative_start_tick")) for note in notes}


def _rhythm_divergence(source_onsets: set[int], draft_onsets: set[int]) -> float:
    if not source_onsets and not draft_onsets:
        return 0.0
    if not source_onsets or not draft_onsets:
        return 1.0
    union = source_onsets | draft_onsets
    inter = source_onsets & draft_onsets
    return 1.0 - (len(inter) / float(len(union)))


def _density(notes: list[Any] | tuple[Any, ...], span_ticks: int) -> float:
    if span_ticks <= 0:
        return 0.0
    return len(notes) / float(span_ticks)


def _motif_anchor_present(context: DevelopmentSourceContext, draft: CompositionDevelopmentDraft) -> bool:
    if not context.motif_exemplars:
        return False
    # A motif anchor is present when any draft track retains similar relative contour
    # length / first-interval sign as an exemplar.
    for exemplar in context.motif_exemplars:
        if len(exemplar.relative_notes) < 2:
            continue
        ex_interval = (
            exemplar.relative_notes[1].pitch_semitone_offset
            - exemplar.relative_notes[0].pitch_semitone_offset
        )
        for track in draft.tracks:
            if len(track.events) < 2:
                continue
            first = midi_pitch_number(track.events[0].pitch)
            second = midi_pitch_number(track.events[1].pitch)
            if (second - first) == 0 and ex_interval == 0:
                return True
            if (second - first) * ex_interval > 0:
                return True
    return False


def _harmonic_anchor_present(context: DevelopmentSourceContext, draft: CompositionDevelopmentDraft) -> bool:
    if not context.recent_harmony:
        return bool(draft.harmony) or True  # empty harmony remains valid
    if not draft.harmony:
        return False
    source_roots = {str(item["chord"])[:1] for item in context.recent_harmony}
    draft_roots = {item.chord[:1] for item in draft.harmony}
    return bool(source_roots & draft_roots)


def _orchestration_anchor_present(
    context: DevelopmentSourceContext,
    draft: CompositionDevelopmentDraft,
) -> bool:
    source_ids = {summary.track_id for summary in context.track_summaries}
    draft_ids = {track.track_id for track in draft.tracks}
    return source_ids == draft_ids and len(source_ids) > 0


def _seam_metrics(
    context: DevelopmentSourceContext,
    draft: CompositionDevelopmentDraft,
    profile: StrengthProfile,
) -> tuple[list[DevelopmentIdentityDiagnostic], list[str], list[str], dict[str, float]]:
    diagnostics: list[DevelopmentIdentityDiagnostic] = []
    warnings: list[str] = []
    errors: list[str] = []
    scores: dict[str, float] = {}

    # Prefer melody-like cadence notes at the end of context.
    cadence = context.cadence_notes
    last_source_midi: int | None = None
    if cadence:
        # Reconstruct absolute-ish midi from offsets relative to first cadence note.
        # Cadence notes already store pitch strings.
        last_source_midi = midi_pitch_number(cadence[-1].pitch)

    first_draft_midi: int | None = None
    preferred = {"melody", "lead", "countermelody"}
    preferred_tracks = [
        track
        for track in draft.tracks
        if any(
            summary.track_id == track.track_id and summary.role in preferred
            for summary in context.track_summaries
        )
    ]
    scan_tracks = preferred_tracks or list(draft.tracks)
    for track in scan_tracks:
        if track.events:
            first_draft_midi = midi_pitch_number(track.events[0].pitch)
            first_onset = track.events[0].relative_start_tick
            break
    else:
        first_onset = 0

    span = max(1, context.scope.output_end_tick - context.scope.output_start_tick) if context.scope.operation == "vary_section" else max(1, context.composition.ticks_per_quarter * 4)
    gap_ratio = first_onset / float(span)
    scores["seam_gap_ratio"] = round(gap_ratio, 4)

    if gap_ratio > profile.max_seam_gap_ticks_ratio:
        code = "seam_gap"
        severity: Literal["error", "warning"] = "error" if profile.name == "conservative" else "warning"
        diagnostics.append(
            _diag(code, severity, "Opening rest after the seam exceeds the strength profile.", score=_clamp01(gap_ratio))
        )
        if severity == "error":
            errors.append("seam_continuity_failed")
            diagnostics.append(_diag("seam_continuity_failed", "error", "Seam continuity failed on gap."))
        else:
            warnings.append("seam_gap_advisory")

    if last_source_midi is not None and first_draft_midi is not None:
        leap = abs(first_draft_midi - last_source_midi)
        scores["seam_leap_semitones"] = float(leap)
        if leap > profile.max_seam_leap_semitones:
            severity = "error" if profile.name == "conservative" else "warning"
            diagnostics.append(
                _diag(
                    "seam_leap",
                    severity,
                    "Melodic leap across the seam exceeds the strength profile.",
                    score=_clamp01(leap / max(1.0, profile.max_seam_leap_semitones)),
                )
            )
            if severity == "error":
                errors.append("seam_continuity_failed")
            else:
                warnings.append("seam_leap_advisory")
        else:
            diagnostics.append(
                _diag(
                    "seam_continuity_ok",
                    "info",
                    "Seam leap within profile limits.",
                    score=_clamp01(1.0 - (leap / max(1.0, profile.max_seam_leap_semitones))),
                )
            )
    elif not draft.tracks or all(not track.events for track in draft.tracks):
        diagnostics.append(_diag("restart_like_opening", "warning", "Generated scope opens with full rests."))
        warnings.append("restart_like_opening")

    return diagnostics, warnings, errors, scores


def evaluate_development_identity(
    *,
    context: DevelopmentSourceContext,
    draft: CompositionDevelopmentDraft,
    strength: VariationStrength,
    development_intent: Literal["continue", "develop", "contrast"] = "continue",
) -> IdentityEvaluationResult:
    """Evaluate identity anchors and seam continuity for one candidate draft."""
    profile = strength_profile(strength)
    # Contrast intentionally widens divergence budgets while still requiring one anchor.
    if development_intent == "contrast":
        profile = StrengthProfile(
            name=profile.name,
            min_identity_anchors=1,
            max_register_semitones=profile.max_register_semitones + 5.0,
            max_rhythm_divergence=min(0.95, profile.max_rhythm_divergence + 0.15),
            max_density_divergence=min(0.95, profile.max_density_divergence + 0.15),
            max_seam_leap_semitones=profile.max_seam_leap_semitones + 5.0,
            max_seam_gap_ticks_ratio=profile.max_seam_gap_ticks_ratio + 0.5,
            require_motif_or_rhythm_or_harmony=True,
        )

    diagnostics: list[DevelopmentIdentityDiagnostic] = []
    warnings: list[str] = []
    errors: list[str] = []
    scores: dict[str, float] = {}

    draft_map = _draft_track_map(draft)
    missing = [summary.track_id for summary in context.track_summaries if summary.track_id not in draft_map]
    extra = [track_id for track_id in draft_map if track_id not in {s.track_id for s in context.track_summaries}]
    if missing or extra:
        diagnostics.append(
            _diag(
                "missing_track_draft",
                "error",
                "Draft must include an explicit entry for every source track and no extras.",
            )
        )
        errors.append("missing_track_draft")

    anchors = 0

    motif_ok = _motif_anchor_present(context, draft)
    diagnostics.append(
        _diag(
            "motif_anchor_present" if motif_ok else "motif_anchor_missing",
            "info" if motif_ok else "warning",
            "Motif contour/interval anchor check.",
            score=1.0 if motif_ok else 0.0,
        )
    )
    if motif_ok:
        anchors += 1
    elif context.motif_exemplars:
        warnings.append("identity_anchor_weak")

    harmony_ok = _harmonic_anchor_present(context, draft)
    diagnostics.append(
        _diag(
            "harmonic_anchor_present" if harmony_ok else "harmonic_anchor_missing",
            "info" if harmony_ok else "warning",
            "Harmonic root continuity check.",
            score=1.0 if harmony_ok else 0.0,
        )
    )
    if harmony_ok:
        anchors += 1

    orchestration_ok = _orchestration_anchor_present(context, draft)
    diagnostics.append(
        _diag(
            "orchestration_anchor_present" if orchestration_ok else "orchestration_anchor_missing",
            "info" if orchestration_ok else "error",
            "Track topology/orchestration continuity check.",
            score=1.0 if orchestration_ok else 0.0,
        )
    )
    if orchestration_ok:
        anchors += 1
    else:
        errors.append("orchestration_anchor_missing")

    # Rhythm / density / register by role
    rhythm_divs: list[float] = []
    density_divs: list[float] = []
    register_divs: list[float] = []
    span_ticks = max(1, context.composition.ticks_per_quarter * 4)
    for summary in context.track_summaries:
        draft_track = draft_map.get(summary.track_id)
        if draft_track is None:
            continue
        source_onsets = set(summary.onset_ticks)
        draft_onsets = {event.relative_start_tick for event in draft_track.events}
        rhythm_div = _rhythm_divergence(source_onsets, draft_onsets)
        rhythm_divs.append(rhythm_div)

        source_density = _density(summary.relative_notes, span_ticks)
        draft_density = _density(draft_track.events, span_ticks)
        if source_density == 0 and draft_density == 0:
            density_div = 0.0
        elif source_density == 0 or draft_density == 0:
            density_div = 1.0
        else:
            density_div = abs(draft_density - source_density) / max(source_density, draft_density)
        density_divs.append(density_div)

        if not summary.is_drum and summary.relative_notes and draft_track.events:
            source_center = _register_center(summary.relative_notes)
            draft_offsets = []
            anchor = midi_pitch_number(draft_track.events[0].pitch)
            for event in draft_track.events:
                draft_offsets.append(
                    type(
                        "N",
                        (),
                        {"pitch_semitone_offset": midi_pitch_number(event.pitch) - anchor},
                    )()
                )
            draft_center = _register_center(draft_offsets)  # type: ignore[arg-type]
            if source_center is not None and draft_center is not None:
                register_divs.append(abs(draft_center - source_center))

        if not draft_track.events and summary.attack_count > 0 and strength == "conservative":
            warnings.append("sparse_track_draft")

    mean_rhythm = _mean_abs(rhythm_divs)
    mean_density = _mean_abs(density_divs)
    mean_register = _mean_abs(register_divs)
    scores["rhythm_divergence"] = round(mean_rhythm, 4)
    scores["density_divergence"] = round(mean_density, 4)
    scores["register_divergence"] = round(mean_register, 4)

    rhythm_ok = mean_rhythm <= profile.max_rhythm_divergence
    diagnostics.append(
        _diag(
            "rhythmic_anchor_present" if rhythm_ok else "rhythmic_anchor_missing",
            "info" if rhythm_ok else "warning",
            "Onset-set rhythmic relatedness check.",
            score=1.0 - mean_rhythm,
        )
    )
    if rhythm_ok:
        anchors += 1
    else:
        diagnostics.append(
            _diag("rhythm_divergence", "warning" if strength != "conservative" else "error", "Rhythm divergence exceeded profile.", score=_clamp01(mean_rhythm))
        )
        if strength == "conservative":
            errors.append("rhythm_divergence")
        else:
            warnings.append("rhythm_divergence_advisory")

    if mean_density > profile.max_density_divergence:
        severity: Literal["error", "warning"] = "error" if strength == "conservative" else "warning"
        diagnostics.append(_diag("density_divergence", severity, "Density divergence exceeded profile.", score=_clamp01(mean_density)))
        if severity == "error":
            errors.append("density_divergence")
        else:
            warnings.append("density_divergence_advisory")

    if mean_register > profile.max_register_semitones:
        severity = "error" if strength == "conservative" else "warning"
        diagnostics.append(
            _diag(
                "register_divergence",
                severity,
                "Register divergence exceeded profile.",
                score=_clamp01(mean_register / max(1.0, profile.max_register_semitones)),
            )
        )
        if severity == "error":
            errors.append("register_divergence")
        else:
            warnings.append("register_divergence_advisory")

    if context.recent_harmony and draft.harmony:
        diagnostics.append(_diag("harmonic_continuity", "info", "Draft includes harmony within generated scope.", score=1.0))
    elif context.recent_harmony and not draft.harmony and strength == "conservative":
        diagnostics.append(_diag("harmonic_continuity", "warning", "Draft omitted harmony while source had recent harmony.", score=0.0))
        warnings.append("harmonic_continuity_advisory")

    seam_diags, seam_warnings, seam_errors, seam_scores = _seam_metrics(context, draft, profile)
    diagnostics.extend(seam_diags)
    warnings.extend(seam_warnings)
    errors.extend(seam_errors)
    scores.update(seam_scores)

    scores["identity_anchor_count"] = float(anchors)
    if anchors >= profile.min_identity_anchors:
        diagnostics.append(
            _diag(
                "identity_anchor_satisfied",
                "info",
                "Required identity anchors satisfied.",
                score=_clamp01(anchors / max(1.0, float(profile.min_identity_anchors + 2))),
            )
        )
    else:
        diagnostics.append(
            _diag(
                "identity_anchor_failed",
                "error",
                "Insufficient identity anchors for strength profile.",
                score=_clamp01(anchors / max(1.0, float(profile.min_identity_anchors))),
            )
        )
        errors.append("identity_anchor_failed")

    # Deduplicate codes while preserving order
    warning_codes = tuple(dict.fromkeys(warnings))
    error_codes = tuple(dict.fromkeys(errors))
    passed = not error_codes

    logger.info(
        "Evaluated composition development identity",
        extra={
            "strength": strength,
            "intent": development_intent,
            "passed": passed,
            "anchor_count": anchors,
            "warning_code_count": len(warning_codes),
            "error_code_count": len(error_codes),
            "diagnostic_count": len(diagnostics),
        },
    )
    logger.debug(
        "Development identity metric scores",
        extra={
            "metric_names": sorted(scores.keys()),
            "rounded_scores": {key: scores[key] for key in sorted(scores.keys())},
            "diagnostic_codes": [item.code for item in diagnostics],
            "error_codes": list(error_codes),
            "warning_codes": list(warning_codes),
        },
    )
    return IdentityEvaluationResult(
        passed=passed,
        strength=strength,
        diagnostics=tuple(diagnostics),
        warning_codes=warning_codes,
        error_codes=error_codes,
        scores=scores,
    )
