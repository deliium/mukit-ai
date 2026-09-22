"""Build ``agent.revision_plan.v1`` targeting from Critic findings."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from app.ai_agents.artifact_schemas import (
    AgentRevisionPlanV1,
    RevisionPlanAffectedRange,
)
from app.ai_agents.revision_loop_schemas import RevisionAffectedRange
from app.ai_agents.schemas import CritiqueRecommendation
from app.revision_loop_settings import RevisionLoopSettings, load_revision_loop_settings

logger = logging.getLogger(__name__)

# Spine agents eligible for targeted revise (never creative_director / critic).
REVISE_SPINE_AGENT_IDS: frozenset[str] = frozenset(
    {"harmony", "melody_motif", "arrangement"}
)

_HARMONY_CODES = frozenset(
    {
        "requested_key_mismatch",
        "declared_key_conflicts_with_inference",
        "declared_harmony_conflicts_with_inference",
        "cadence_weak",
    }
)
_MELODY_CODES = frozenset(
    {
        "melodic_contour_flat",
        "motif_recurrence_missing",
        "rhythmic_diversity_low",
        "climax_lacks_contrast",
        "section_lacks_contrast",
        "tension_curve_flat",
    }
)
_ARRANGEMENT_CODES = frozenset(
    {
        "requested_instrumentation_mismatch",
        "note_outside_instrument_range",
        "dense_overlapping_material",
        "overlapping_same_pitch_timing",
        "excessive_duplicate_notes",
        "orchestration_density_high",
        "climax_lacks_contrast",
    }
)
_HARMONY_CATEGORIES = frozenset({"harmony", "tonality", "cadence"})
_MELODY_CATEGORIES = frozenset({"melody", "motif", "rhythm", "contrast", "tension", "dynamics"})
_ARRANGEMENT_CATEGORIES = frozenset(
    {"orchestration", "instrumentation", "density", "collision", "duplication"}
)


def _finding_stratum(finding: Any) -> str:
    if isinstance(finding, dict):
        return str(finding.get("stratum") or "")
    return str(getattr(finding, "stratum", "") or "")


def _finding_code(finding: Any) -> str:
    if isinstance(finding, dict):
        return str(finding.get("code") or "").strip().lower()
    return str(getattr(finding, "code", "") or "").strip().lower()


def _finding_category(finding: Any) -> str:
    if isinstance(finding, dict):
        return str(finding.get("category") or "").strip().lower()
    return str(getattr(finding, "category", "") or "").strip().lower()


def _finding_range(finding: Any) -> RevisionAffectedRange | None:
    raw = None
    if isinstance(finding, dict):
        raw = finding.get("affected_range")
    else:
        raw = getattr(finding, "affected_range", None)
    if raw is None:
        return None
    if isinstance(raw, dict):
        start = raw.get("start_bar")
        end = raw.get("end_bar")
    else:
        start = getattr(raw, "start_bar", None)
        end = getattr(raw, "end_bar", None)
    if start is None or end is None:
        return None
    try:
        return RevisionAffectedRange(start_bar=int(start), end_bar=int(end))
    except (TypeError, ValueError):
        return None


def _finding_tracks(finding: Any) -> list[str]:
    if isinstance(finding, dict):
        tracks = finding.get("affected_tracks") or []
    else:
        tracks = getattr(finding, "affected_tracks", None) or []
    out: list[str] = []
    for item in tracks:
        tid = str(item).strip()[:80]
        if tid:
            out.append(tid)
    return out


def _include_finding(finding: Any, *, revise_on_technical: bool) -> bool:
    stratum = _finding_stratum(finding)
    if stratum == "hard_constraint":
        return True
    if stratum == "technical" and revise_on_technical:
        return True
    # Subjective / stylistic never drive targeting by default.
    return False


def _agents_for_finding(finding: Any) -> set[str]:
    code = _finding_code(finding)
    category = _finding_category(finding)
    agents: set[str] = set()
    if code in _HARMONY_CODES or category in _HARMONY_CATEGORIES:
        agents.add("harmony")
    if code in _MELODY_CODES or category in _MELODY_CATEGORIES:
        agents.add("melody_motif")
    if code in _ARRANGEMENT_CODES or category in _ARRANGEMENT_CATEGORIES:
        agents.add("arrangement")
    if not agents:
        # Conservative default — still scoped by ranges when present.
        agents.update({"harmony", "melody_motif"})
    return agents & REVISE_SPINE_AGENT_IDS


def _merge_ranges(ranges: Iterable[RevisionAffectedRange]) -> list[RevisionPlanAffectedRange]:
    items = sorted(ranges, key=lambda r: (r.start_bar, r.end_bar))
    if not items:
        return []
    merged: list[list[int]] = []
    for item in items:
        if not merged or item.start_bar > merged[-1][1] + 1:
            merged.append([item.start_bar, item.end_bar])
        else:
            merged[-1][1] = max(merged[-1][1], item.end_bar)
    return [
        RevisionPlanAffectedRange(start_bar=a, end_bar=b) for a, b in merged[:8]
    ]


def build_revision_plan_from_findings(
    findings: list[Any] | None,
    *,
    pass_index: int,
    recommendation: CritiqueRecommendation | str = CritiqueRecommendation.REVISE,
    revise_on_technical: bool | None = None,
    settings: RevisionLoopSettings | None = None,
    stop_criteria: list[str] | None = None,
    comment: str | None = None,
) -> AgentRevisionPlanV1:
    """Map Critic findings → targeted RevisionPlan (pure; no I/O)."""
    cfg = settings or load_revision_loop_settings()
    use_technical = (
        cfg.revise_on_technical if revise_on_technical is None else bool(revise_on_technical)
    )
    if isinstance(recommendation, CritiqueRecommendation):
        rec = recommendation
    else:
        try:
            rec = CritiqueRecommendation(str(recommendation).strip().lower())
        except ValueError:
            rec = CritiqueRecommendation.REVISE

    selected = [
        f
        for f in (findings or [])
        if _include_finding(f, revise_on_technical=use_technical)
    ]
    # Test / AC hook: when no hard/technical drivers but Critic still says revise,
    # fall back to all findings with ranges (targeting still scoped).
    if not selected and rec == CritiqueRecommendation.REVISE:
        selected = list(findings or [])

    agent_ids: set[str] = set()
    ranges: list[RevisionAffectedRange] = []
    tracks: list[str] = []
    seen_tracks: set[str] = set()
    revise_targets: list[str] = []
    seen_targets: set[str] = set()

    for finding in selected:
        agent_ids |= _agents_for_finding(finding)
        rng = _finding_range(finding)
        if rng is not None:
            ranges.append(rng)
        for tid in _finding_tracks(finding):
            if tid not in seen_tracks:
                seen_tracks.add(tid)
                tracks.append(tid)
        code = _finding_code(finding)
        if code and code not in seen_targets:
            seen_targets.add(code)
            revise_targets.append(code)

    if not agent_ids and rec == CritiqueRecommendation.REVISE:
        agent_ids = {"harmony", "melody_motif"}

    ordered_agents = [a for a in ("harmony", "melody_motif", "arrangement") if a in agent_ids]
    criteria = stop_criteria or [
        "critic_approve",
        "hard_requirements_satisfied",
        "max_passes_reached",
        "improvement_below_threshold",
    ]
    plan = AgentRevisionPlanV1(
        critique_recommendation=rec,
        revise_targets=revise_targets[:16] or list(ordered_agents),
        stop_criteria=criteria[:16],
        comment=(comment or "revision_loop_plan")[:240],
        pass_index=pass_index,
        affected_ranges=_merge_ranges(ranges),
        affected_tracks=tracks[:16],
        target_agent_ids=ordered_agents,
        preserve_outside_targets=True,
    )
    logger.info(
        "Revision plan built",
        extra={
            "pass_index": pass_index,
            "target_agent_ids": ordered_agents,
            "range_count": len(plan.affected_ranges),
            "track_count": len(plan.affected_tracks),
        },
    )
    logger.debug(
        "Revision plan ranges",
        extra={
            "pass_index": pass_index,
            "ranges": [
                {"start_bar": r.start_bar, "end_bar": r.end_bar} for r in plan.affected_ranges
            ],
            "revise_targets": plan.revise_targets[:8],
        },
    )
    return plan
