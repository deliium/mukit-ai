"""Deterministic track-role inference over analysis context.

Returns declared / inferred / effective roles without mutating import-time roles.
Scoring uses public ``instrument_identity`` hooks plus register, mono/poly,
highest/lowest-voice participation, attack/duration, repetition, and coverage.

Connection to Task 5 (melody analysis)
--------------------------------------
``select_melodic_candidates_from_roles`` projects accepted melodic/lead role
estimates into a stable feature list. When ``composition_melody_analysis``
exposes ``analyze_melody_from_context`` / a melodic-candidate feature protocol,
the orchestrator (Task 7) should pass these features into final candidate
selection only — not re-run phrase/contour algorithms.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_MAX_CANDIDATES,
    ANALYSIS_MAX_TRACK_SUMMARIES,
    AnalysisEvidence,
    InferenceMeta,
    RoleAnalysisResult,
    TrackRoleEstimate,
    round_analysis_float,
)
from app.composition_schemas import SUPPORTED_TRACK_ROLES
from app.services.composition_analysis_context import (
    AnalysisTrackView,
    CompositionAnalysisContext,
)
from app.services.composition_logical_notes import CollapsedLogicalNote, clip_occupancy
from app.services.instrument_identity import (
    is_drum_identity,
    normalize_instrument,
    normalize_instrument_identity,
    normalize_role,
)


logger = logging.getLogger(__name__)

ROLE_METHOD = "role.native.v1"

# Candidate role vocabulary for inference (subset of SUPPORTED_TRACK_ROLES).
_INFERABLE_ROLES: tuple[str, ...] = (
    "melody",
    "lead",
    "countermelody",
    "bass",
    "harmony",
    "pad",
    "rhythm",
    "drums",
    "percussion",
    "other",
)

# Instrument-identity soft priors (additive score hints).
_IDENTITY_ROLE_PRIORS: dict[str, dict[str, float]] = {
    "bass": {"bass": 0.55, "harmony": 0.05},
    "drums": {"drums": 0.7, "percussion": 0.15, "rhythm": 0.1},
    "piano": {"harmony": 0.35, "pad": 0.15, "melody": 0.1, "rhythm": 0.1},
    "pad": {"pad": 0.55, "harmony": 0.2},
    "synth": {"pad": 0.25, "lead": 0.2, "harmony": 0.15, "melody": 0.1},
    "guitar": {"harmony": 0.25, "rhythm": 0.2, "melody": 0.15, "lead": 0.1},
    "flute": {"melody": 0.45, "lead": 0.25, "countermelody": 0.1},
    "violin": {"melody": 0.4, "lead": 0.2, "countermelody": 0.15},
    "viola": {"countermelody": 0.25, "harmony": 0.2, "melody": 0.15},
    "cello": {"bass": 0.25, "countermelody": 0.2, "melody": 0.15, "harmony": 0.1},
    "trumpet": {"melody": 0.3, "lead": 0.3, "countermelody": 0.1},
    "sax": {"melody": 0.3, "lead": 0.25, "countermelody": 0.15},
    "oboe": {"melody": 0.35, "lead": 0.2, "countermelody": 0.15},
    "clarinet": {"melody": 0.3, "countermelody": 0.2, "lead": 0.15},
    "choir": {"pad": 0.3, "harmony": 0.25, "melody": 0.15},
    "organ": {"pad": 0.3, "harmony": 0.3},
    "harp": {"harmony": 0.25, "melody": 0.2, "pad": 0.1},
}

_MELODIC_ROLES = frozenset({"melody", "lead", "countermelody"})
_MIN_SCORE = 0.18
_MIN_MARGIN = 0.06
_MIN_CONFIDENCE = 0.4


@dataclass(frozen=True)
class MelodicCandidateFeature:
    """Stable melodic-candidate feature interface for Task 5 final selection.

    Task 5 may consume these fields without depending on role scoring internals.
    """

    track_id: str
    track_index: int
    role: str
    score: float
    confidence: float
    source: str  # "declared" | "inferred" | "effective"


def analyze_roles_from_context(
    context: CompositionAnalysisContext,
) -> RoleAnalysisResult:
    """Infer track roles for tracks overlapping the resolved scope."""
    try:
        return _analyze_roles_from_context(context)
    except Exception as exc:
        logger.error(
            "Role analyzer failure",
            extra={"error_type": type(exc).__name__},
        )
        raise


def select_melodic_candidates_from_roles(
    roles: RoleAnalysisResult | Sequence[TrackRoleEstimate],
    *,
    min_confidence: float = _MIN_CONFIDENCE,
) -> list[MelodicCandidateFeature]:
    """Project role estimates into melodic candidates for Task 5 selection.

    Prefer inferred melodic/lead roles with sufficient confidence; fall back to
    declared melody/lead when inference abstains. Deterministic track order.

    ``MelodicCandidateFeature`` satisfies Task 5's ``MelodicCandidateFeatures``
    protocol (``track_id``, ``score``, ``confidence``). Prefer
    ``melodic_role_hints_from_roles`` when calling ``analyze_melody_from_context``.
    """
    estimates: Sequence[TrackRoleEstimate]
    if isinstance(roles, RoleAnalysisResult):
        estimates = roles.tracks
    else:
        estimates = roles

    features: list[MelodicCandidateFeature] = []
    for index, estimate in enumerate(estimates):
        conf = estimate.inference.confidence or 0.0
        inferred = estimate.inferred_role
        declared = estimate.declared_role
        effective = estimate.effective_role

        chosen_role: str | None = None
        source = "inferred"
        score = conf

        if (
            inferred in _MELODIC_ROLES
            and estimate.inference.status == "ok"
            and conf >= min_confidence
        ):
            chosen_role = inferred
            source = "inferred"
            score = conf
        elif declared in _MELODIC_ROLES:
            chosen_role = declared
            source = "declared"
            score = max(conf, 0.55)
        elif effective in _MELODIC_ROLES:
            chosen_role = effective
            source = "effective"
            score = max(conf, 0.4)

        if chosen_role is None:
            continue

        # Prefer winner candidate score when present.
        for candidate in estimate.candidates:
            if candidate.get("role") == chosen_role and "score" in candidate:
                try:
                    score = float(candidate["score"])
                except (TypeError, ValueError):
                    pass
                break

        features.append(
            MelodicCandidateFeature(
                track_id=estimate.track_id,
                track_index=index,
                role=chosen_role,
                score=round_analysis_float(score),
                confidence=round_analysis_float(min(1.0, max(0.0, conf if conf else score))),
                source=source,
            )
        )

    features.sort(key=lambda item: (-item.score, item.track_index, item.track_id))
    logger.debug(
        "Melodic candidate projection",
        extra={"candidate_count": len(features)},
    )
    return features


def melodic_role_hints_from_roles(
    roles: RoleAnalysisResult | Sequence[TrackRoleEstimate],
    *,
    min_confidence: float = _MIN_CONFIDENCE,
):
    """Build Task 5 ``MelodicRoleHints`` from role analysis (final selection only).

    Does not re-run phrase/contour algorithms — only supplies ranked feature
    scores for ``analyze_melody_from_context(..., role_hints=...)``.
    """
    # Local import avoids any future circular dependency with melody analysis.
    from app.services.composition_melody_analysis import MelodicRoleHints

    features = select_melodic_candidates_from_roles(roles, min_confidence=min_confidence)
    # Features path is for inferred winners; declared melodies are already
    # selected by Task 5 before consulting features.
    inferred_only = tuple(item for item in features if item.source == "inferred")
    return MelodicRoleHints(features=inferred_only)


def _analyze_roles_from_context(context: CompositionAnalysisContext) -> RoleAnalysisResult:
    scope = context.resolved_scope
    ensemble_notes = context.notes_overlapping_scope(drums=False)
    ensemble_midi = [note.midi_number for _, _, note in ensemble_notes]

    estimates: list[TrackRoleEstimate] = []
    for track in context.tracks:
        if scope.kind == "track" and track.track_id != scope.track_id:
            continue
        estimate = _estimate_track_role(context, track, ensemble_midi)
        estimates.append(estimate)

    truncated = False
    if len(estimates) > ANALYSIS_MAX_TRACK_SUMMARIES:
        estimates = estimates[:ANALYSIS_MAX_TRACK_SUMMARIES]
        truncated = True
        logger.warning(
            "Role analysis truncated",
            extra={"code": "result_truncated", "kept": ANALYSIS_MAX_TRACK_SUMMARIES},
        )

    accepted = sum(1 for item in estimates if item.inference.status == "ok")
    status: str
    if not estimates:
        status = "insufficient_evidence"
    elif accepted == 0:
        status = "ambiguous"
    elif truncated:
        status = "truncated"
    else:
        status = "ok"

    logger.info(
        "Role analysis complete",
        extra={
            "status": status,
            "track_count": len(estimates),
            "accepted_count": accepted,
        },
    )

    return RoleAnalysisResult(
        tracks=estimates,
        inference=InferenceMeta(
            status=status,  # type: ignore[arg-type]
            confidence=round_analysis_float(accepted / len(estimates)) if estimates else None,
            evidence=AnalysisEvidence(count=len(estimates)),
            method=ROLE_METHOD,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )


def _estimate_track_role(
    context: CompositionAnalysisContext,
    track: AnalysisTrackView,
    ensemble_midi: Sequence[int],
) -> TrackRoleEstimate:
    declared = normalize_role(track.role) if track.role else None
    if declared and declared not in SUPPORTED_TRACK_ROLES:
        declared = track.role

    scoped_notes = [
        note
        for note in track.logical_notes
        if clip_occupancy(note, context.resolved_scope.start_tick, context.resolved_scope.end_tick) > 0
    ]
    attacks = [
        note
        for note in scoped_notes
        if context.resolved_scope.start_tick
        <= note.start_tick
        < context.resolved_scope.end_tick
    ]

    if not scoped_notes:
        return TrackRoleEstimate(
            track_id=track.track_id,
            declared_role=declared,
            inferred_role=None,
            effective_role=declared,
            candidates=[],
            inference=InferenceMeta(
                status="insufficient_evidence",
                confidence=None,
                evidence=AnalysisEvidence(count=0),
                method=ROLE_METHOD,
                method_version=ANALYSIS_ALGORITHM_VERSION,
            ),
        )

    scores = {role: 0.0 for role in _INFERABLE_ROLES}
    identity = normalize_instrument_identity(track.instrument)
    normalized = normalize_instrument(track.instrument)

    if track.is_drum or (normalized and normalized.is_drum) or is_drum_identity(identity):
        scores["drums"] += 0.9
        scores["percussion"] += 0.25
        scores["rhythm"] += 0.05
        # Drum tracks skip pitched register/bass heuristics below.
        features = _behavioral_features(context, track, scoped_notes, attacks, ensemble_midi)
        if float(features["attack_density"]) >= 1.5:
            scores["drums"] += 0.15
            scores["rhythm"] += 0.05
        ranked = sorted(
            ((role, score) for role, score in scores.items() if score > 0.0),
            key=lambda item: (-item[1], item[0]),
        )
        return _finalize_estimate(
            track_id=track.track_id,
            declared=declared,
            ranked=ranked,
            features=features,
            attack_count=len(attacks),
        )

    if identity:
        for role, bonus in _IDENTITY_ROLE_PRIORS.get(identity, {}).items():
            scores[role] = scores.get(role, 0.0) + bonus

    features = _behavioral_features(context, track, scoped_notes, attacks, ensemble_midi)
    _apply_behavioral_scores(scores, features)

    ranked = sorted(
        ((role, score) for role, score in scores.items() if score > 0.0),
        key=lambda item: (-item[1], item[0]),
    )
    return _finalize_estimate(
        track_id=track.track_id,
        declared=declared,
        ranked=ranked,
        features=features,
        attack_count=len(attacks),
    )


def _finalize_estimate(
    *,
    track_id: str,
    declared: str | None,
    ranked: list[tuple[str, float]],
    features: dict[str, float | int | str | bool],
    attack_count: int,
) -> TrackRoleEstimate:
    candidates = [
        {
            "role": role,
            "score": round_analysis_float(score),
            "mono": features["is_monophonic"],
            "register": features["register_band"],
        }
        for role, score in ranked[:ANALYSIS_MAX_CANDIDATES]
    ]

    inferred: str | None = None
    status = "ambiguous"
    confidence: float | None = None
    if ranked:
        winner_role, winner_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = winner_score - second_score
        # Margin-aware confidence: strong separation → higher confidence.
        denom = max(winner_score, 1e-9)
        confidence = round_analysis_float(
            min(1.0, 0.35 + 0.4 * (winner_score / (winner_score + second_score + 1e-9)) + 0.4 * (margin / denom))
        )
        logger.debug(
            "Role score summary",
            extra={
                "track_id": track_id,
                "winner": winner_role,
                "winner_score": round_analysis_float(winner_score),
                "margin": round_analysis_float(margin),
                "candidate_count": len(ranked),
            },
        )
        if winner_score >= _MIN_SCORE and margin >= _MIN_MARGIN and (confidence or 0.0) >= _MIN_CONFIDENCE:
            inferred = winner_role
            status = "ok"
        else:
            status = "ambiguous"
            inferred = None

    if status == "ok" and inferred:
        effective = inferred
    else:
        effective = declared

    return TrackRoleEstimate(
        track_id=track_id,
        declared_role=declared,
        inferred_role=inferred,
        effective_role=effective,
        candidates=candidates,
        inference=InferenceMeta(
            status=status,  # type: ignore[arg-type]
            confidence=confidence,
            evidence=AnalysisEvidence(
                count=attack_count,
                mass=round_analysis_float(float(features["coverage"])),
                coverage=round_analysis_float(float(features["coverage"])),
            ),
            method=ROLE_METHOD,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )


def _behavioral_features(
    context: CompositionAnalysisContext,
    track: AnalysisTrackView,
    scoped_notes: Sequence[CollapsedLogicalNote],
    attacks: Sequence[CollapsedLogicalNote],
    ensemble_midi: Sequence[int],
) -> dict[str, float | int | str | bool]:
    scope = context.resolved_scope
    duration = max(1, scope.end_tick - scope.start_tick)
    tpq = max(1, context.composition.ticks_per_quarter)

    occupancy = sum(clip_occupancy(note, scope.start_tick, scope.end_tick) for note in scoped_notes)
    coverage = min(1.0, occupancy / float(duration))
    attack_density = len(attacks) / (duration / float(tpq))

    mean_dur = (
        statistics_mean([note.duration_ticks for note in scoped_notes]) / float(tpq)
        if scoped_notes
        else 0.0
    )

    max_sim = _track_max_simultaneity(scoped_notes, scope.start_tick, scope.end_tick)
    is_monophonic = max_sim <= 1

    midis = [note.midi_number for note in scoped_notes]
    mean_midi = statistics_mean(midis) if midis else 60.0
    if mean_midi < 48:
        register_band = "low"
    elif mean_midi > 72:
        register_band = "high"
    else:
        register_band = "mid"

    highest_share = 0.0
    lowest_share = 0.0
    if ensemble_midi and midis and not track.is_drum:
        ens_max = max(ensemble_midi)
        ens_min = min(ensemble_midi)
        highest_share = sum(1 for m in midis if m >= ens_max - 2) / float(len(midis))
        lowest_share = sum(1 for m in midis if m <= ens_min + 2) / float(len(midis))

    repetition = _local_repetition_score(attacks)

    return {
        "coverage": coverage,
        "attack_density": attack_density,
        "mean_duration_quarters": mean_dur,
        "max_simultaneity": max_sim,
        "is_monophonic": is_monophonic,
        "register_band": register_band,
        "mean_midi": mean_midi,
        "highest_share": highest_share,
        "lowest_share": lowest_share,
        "repetition": repetition,
    }


def _apply_behavioral_scores(
    scores: dict[str, float],
    features: dict[str, float | int | str | bool],
) -> None:
    register = features["register_band"]
    is_mono = bool(features["is_monophonic"])
    attack_density = float(features["attack_density"])
    mean_dur = float(features["mean_duration_quarters"])
    coverage = float(features["coverage"])
    highest = float(features["highest_share"])
    lowest = float(features["lowest_share"])
    repetition = float(features["repetition"])
    max_sim = int(features["max_simultaneity"])

    if register == "high":
        scores["melody"] += 0.25
        scores["lead"] += 0.2
        scores["countermelody"] += 0.1
    elif register == "low":
        scores["bass"] += 0.35
        scores["harmony"] += 0.05
    else:
        scores["harmony"] += 0.1
        scores["countermelody"] += 0.08
        scores["melody"] += 0.05

    if is_mono:
        scores["melody"] += 0.2
        scores["lead"] += 0.15
        scores["bass"] += 0.12
        scores["countermelody"] += 0.1
        scores["rhythm"] += 0.05
    else:
        scores["harmony"] += 0.25 + min(0.2, 0.05 * max(0, max_sim - 1))
        scores["pad"] += 0.15
        if max_sim >= 3:
            scores["pad"] += 0.1

    scores["melody"] += 0.25 * highest
    scores["lead"] += 0.2 * highest
    scores["bass"] += 0.35 * lowest

    if attack_density >= 3.0:
        scores["rhythm"] += 0.45
        scores["drums"] += 0.1
        scores["percussion"] += 0.1
        # Dense short attacks should not collapse into melody/lead.
        scores["melody"] -= 0.12
        scores["lead"] -= 0.1
    elif attack_density <= 0.75:
        scores["pad"] += 0.2
        scores["harmony"] += 0.05

    if mean_dur >= 2.0:
        scores["pad"] += 0.25
        scores["harmony"] += 0.1
    elif mean_dur <= 0.5:
        scores["rhythm"] += 0.35
        scores["percussion"] += 0.1
        scores["melody"] -= 0.08
        scores["lead"] -= 0.06

    scores["pad"] += 0.15 * coverage if mean_dur >= 1.5 else 0.0
    scores["melody"] += 0.1 * coverage if is_mono else 0.0

    # Repeated short material leans rhythmic; repeated melodic intervals lean melody.
    if repetition >= 0.55:
        if is_mono and register != "low":
            scores["melody"] += 0.12
            scores["lead"] += 0.08
        else:
            scores["rhythm"] += 0.12


def _track_max_simultaneity(
    notes: Sequence[CollapsedLogicalNote],
    start: int,
    end: int,
) -> int:
    events: list[tuple[int, int]] = []
    for note in notes:
        left = max(note.start_tick, start)
        right = min(note.end_tick, end)
        if right <= left:
            continue
        events.append((left, 1))
        events.append((right, -1))
    if not events:
        return 0
    events.sort(key=lambda item: (item[0], item[1]))
    active = 0
    max_active = 0
    for _, delta in events:
        active += delta
        max_active = max(max_active, active)
    return max_active


def _local_repetition_score(attacks: Sequence[CollapsedLogicalNote]) -> float:
    """Cheap bounded repetition cue from pitch-class n-grams (no motif dump)."""
    if len(attacks) < 4:
        return 0.0
    ordered = sorted(attacks, key=lambda note: (note.start_tick, note.midi_number))
    pcs = [note.pitch_class for note in ordered]
    grams: dict[tuple[int, int, int], int] = {}
    for i in range(len(pcs) - 2):
        gram = (pcs[i], pcs[i + 1], pcs[i + 2])
        grams[gram] = grams.get(gram, 0) + 1
    if not grams:
        return 0.0
    repeats = sum(count - 1 for count in grams.values() if count > 1)
    return min(1.0, repeats / max(1.0, len(pcs) / 3.0))


def statistics_mean(values: Sequence[float | int]) -> float:
    if not values:
        return 0.0
    return float(sum(values)) / float(len(values))
