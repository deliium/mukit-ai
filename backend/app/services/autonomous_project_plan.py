"""Deterministic compiler from creative.brief.v1 to project.plan.v1.

The compiler locks bar counts, keys, instruments, and the stage graph. A
director may replace goal summaries and section narratives only.
"""

import logging
from typing import Any, Mapping

from pydantic import ValidationError

from app.autonomous_composer_schemas import (
    AUTONOMOUS_BRIEF_INVALID,
    AUTONOMOUS_CONSTRAINT_FAILED,
    AUTONOMOUS_INSTRUMENT_UNKNOWN,
    BAR_MATH_DEFAULT_BPM,
    DEFAULT_TEMPO_MAX,
    DEFAULT_TEMPO_MIN,
    DURATION_BARS_MAX,
    GOAL_SUMMARY_MAX,
    NARRATIVE_TEXT_MAX,
    SECTION_LABEL_MAX,
    STAGE_IDS,
    AutonomousPlanError,
    CreativeBriefV1,
    NarrativeIntent,
    ProjectPlanConstraints,
    ProjectPlanGoal,
    ProjectPlanSection,
    ProjectPlanStage,
    DensityBand,
    ProjectPlanV1,
    StageId,
)
from app.services.composition_timing import parse_time_signature
from app.services.instrument_catalog import resolve_profile
from app.services.instrument_identity import normalize_instrument, normalize_instrument_family

logger = logging.getLogger(__name__)

INTENT_WEIGHT: dict[NarrativeIntent, float] = {
    "sparse_opening": 0.15,
    "establish_theme": 0.20,
    "build": 0.20,
    "climax": 0.25,
    "resolve": 0.20,
}

INTENT_SECTION: dict[NarrativeIntent, tuple[str, DensityBand]] = {
    "sparse_opening": ("intro", "sparse"),
    "establish_theme": ("verse", "moderate"),
    "build": ("bridge", "moderate"),
    "climax": ("chorus", "dense"),
    "resolve": ("outro", "sparse"),
}

_STAGE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "stage_id": "plan",
        "agent_id": "creative_director",
        "operation": "plan",
        "depends_on": [],
        "score_commit": "no",
        "approval": "auto",
        "completion_codes": ["project_plan_valid"],
    },
    {
        "stage_id": "harmony_plan",
        "agent_id": "harmony",
        "operation": "plan",
        "depends_on": ["plan"],
        "score_commit": "no",
        "approval": "auto",
        "completion_codes": ["harmony_plan_key"],
    },
    {
        "stage_id": "motif_plan",
        "agent_id": "melody_motif",
        "operation": "plan",
        "depends_on": ["harmony_plan"],
        "score_commit": "no",
        "approval": "auto",
        "completion_codes": ["motif_plan_present"],
    },
    {
        "stage_id": "symbolic",
        "agent_id": None,
        "operation": "realize",
        "depends_on": ["motif_plan"],
        "score_commit": "yes",
        "approval": "auto",
        "completion_codes": [
            "composition_v2_ok",
            "hard_constraints_ok",
            "no_forbidden_instruments",
            "motif_identity_ok",
        ],
    },
    {
        "stage_id": "critique",
        "agent_id": "critic",
        "operation": "critique",
        "depends_on": ["symbolic"],
        "score_commit": "no",
        "approval": "auto",
        "completion_codes": ["critique_stored"],
    },
    {
        "stage_id": "revision",
        "agent_id": None,
        "operation": "propose",
        "depends_on": ["critique"],
        "score_commit": "when_changed",
        "approval": "auto",
        "completion_codes": ["revision_contained"],
    },
    {
        "stage_id": "arrangement",
        "agent_id": "arrangement",
        "operation": "propose",
        "depends_on": ["revision"],
        "score_commit": "yes",
        "approval": "auto",
        "completion_codes": ["arrangement_preserved", "no_forbidden_instruments"],
    },
    {
        "stage_id": "expression",
        "agent_id": "performance_expression",
        "operation": "advise",
        "depends_on": ["arrangement"],
        "score_commit": "yes",
        "approval": "auto",
        "completion_codes": ["expression_pitch_stable"],
    },
    {
        "stage_id": "render",
        "agent_id": "production",
        "operation": "advise",
        "depends_on": ["expression"],
        "score_commit": "no",
        "approval": "required",
        "completion_codes": ["render_dispatched"],
    },
)


def compile_project_plan(brief: CreativeBriefV1) -> ProjectPlanV1:
    """Lock structure from a validated brief. Does not call a model."""
    logger.debug(
        "Compiling autonomous project plan",
        extra={"brief_len": len(brief.model_dump_json()), "beat_count": len(brief.narrative)},
    )
    instruments = _resolve_instruments(brief)
    opening_tempo = brief.tempo_min if brief.tempo_min is not None else BAR_MATH_DEFAULT_BPM
    tempo_min = brief.tempo_min if brief.tempo_min is not None else DEFAULT_TEMPO_MIN
    tempo_max = brief.tempo_max if brief.tempo_max is not None else DEFAULT_TEMPO_MAX
    if tempo_min > tempo_max:
        tempo_max = tempo_min
    beats_per_bar, _denominator = parse_time_signature(brief.time_signature)
    duration_bars = _duration_bars(
        duration_seconds=brief.duration_seconds,
        bpm=opening_tempo,
        beats_per_bar=beats_per_bar,
    )
    weights = [INTENT_WEIGHT[beat.intent] for beat in brief.narrative]
    bar_counts = _largest_remainder(duration_bars, weights)
    if len(bar_counts) > duration_bars or any(count < 1 for count in bar_counts):
        raise AutonomousPlanError(
            "duration cannot cover one bar per section",
            code=AUTONOMOUS_BRIEF_INVALID,
        )

    motif_section_id = _motif_section_id(brief)
    sections = _build_sections(brief, bar_counts)
    goals = [
        ProjectPlanGoal(
            id=f"goal-{index}",
            summary=beat.text[:GOAL_SUMMARY_MAX],
            section_id=f"sec-{index}",
        )
        for index, beat in enumerate(brief.narrative, start=1)
    ]
    symbolic_codes = list(_STAGE_SPECS[3]["completion_codes"])
    if brief.final_section_key:
        symbolic_codes.append("final_section_mode_ok")
    stages = []
    for spec in _STAGE_SPECS:
        payload = dict(spec)
        if payload["stage_id"] == "symbolic":
            payload["completion_codes"] = symbolic_codes
        stages.append(ProjectPlanStage.model_validate(payload))

    plan = ProjectPlanV1(
        schema_version="project.plan.v1",
        goals=goals,
        constraints=ProjectPlanConstraints(
            opening_key=brief.opening_key,
            final_section_key=brief.final_section_key,
            time_signature=brief.time_signature,
            tempo_min=tempo_min,
            tempo_max=tempo_max,
            opening_tempo=opening_tempo,
            duration_bars=duration_bars,
            instruments=instruments,
            forbidden_instrument_families=_normalize_families(
                brief.forbidden_instrument_families
            ),
            motif_label=brief.motif_label,
            motif_must_remain_recognizable=brief.motif_must_remain_recognizable,
            motif_section_id=motif_section_id,
        ),
        sections=sections,
        stages=stages,
    )
    logger.info(
        "Compiled autonomous project plan",
        extra={
            "duration_bars": plan.constraints.duration_bars,
            "section_count": len(plan.sections),
            "stage_count": len(plan.stages),
            "opening_key": plan.constraints.opening_key,
        },
    )
    return plan


def merge_director_text(
    compiled: ProjectPlanV1,
    director_plan: Mapping[str, Any] | ProjectPlanV1,
) -> ProjectPlanV1:
    """Copy goal summaries and section narratives. Discard every structural edit."""
    payload = (
        director_plan.model_dump()
        if isinstance(director_plan, ProjectPlanV1)
        else dict(director_plan)
    )
    merged = compiled.model_copy(deep=True)
    _log_discarded_structure(compiled, payload)
    _apply_goal_summaries(merged, payload.get("goals"))
    _apply_section_narratives(merged, payload.get("sections"))
    logger.debug(
        "Merged director text into compiled project plan",
        extra={"stage_count": len(merged.stages), "section_count": len(merged.sections)},
    )
    return merged


def _resolve_instruments(brief: CreativeBriefV1) -> list[str]:
    forbidden = set(_normalize_families(brief.forbidden_instrument_families))
    resolved: list[str] = []
    for label in brief.instrumentation:
        profile = resolve_profile(alias=label)
        normalized = normalize_instrument(label)
        if profile is None or normalized is None:
            logger.warning(
                "Autonomous instrument rejected",
                extra={"code": AUTONOMOUS_INSTRUMENT_UNKNOWN},
            )
            raise AutonomousPlanError(
                "unknown instrument",
                code=AUTONOMOUS_INSTRUMENT_UNKNOWN,
            )
        family = normalized.family
        if profile.is_drum or normalized.is_drum or family in forbidden:
            logger.warning(
                "Autonomous instrument rejected",
                extra={"code": AUTONOMOUS_CONSTRAINT_FAILED},
            )
            raise AutonomousPlanError(
                "instrument family is forbidden",
                code=AUTONOMOUS_CONSTRAINT_FAILED,
            )
        resolved.append(normalized.identity)
    return resolved


def _normalize_families(labels: list[str]) -> list[str]:
    families: list[str] = []
    for label in labels:
        family = normalize_instrument_family(label) or label.strip().lower()
        if family not in families:
            families.append(family)
    return families


def _duration_bars(*, duration_seconds: int, bpm: int, beats_per_bar: int) -> int:
    raw = round(duration_seconds * bpm / 60 / beats_per_bar)
    return max(1, min(DURATION_BARS_MAX, int(raw)))


def _largest_remainder(total: int, weights: list[float]) -> list[int]:
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise AutonomousPlanError("narrative weights are empty", code=AUTONOMOUS_BRIEF_INVALID)
    exact = [total * weight / weight_sum for weight in weights]
    floors = [int(value) for value in exact]
    remainder = total - sum(floors)
    order = sorted(
        range(len(weights)),
        key=lambda index: (exact[index] - floors[index], -index),
        reverse=True,
    )
    for index in order[:remainder]:
        floors[index] += 1
    return floors


def _motif_section_id(brief: CreativeBriefV1) -> str:
    for index, beat in enumerate(brief.narrative, start=1):
        if beat.intent == "establish_theme":
            return f"sec-{index}"
    return "sec-1"


def _build_sections(brief: CreativeBriefV1, bar_counts: list[int]) -> list[ProjectPlanSection]:
    sections: list[ProjectPlanSection] = []
    cursor = 1
    for index, (beat, bar_count) in enumerate(zip(brief.narrative, bar_counts, strict=True), start=1):
        section_type, density = INTENT_SECTION[beat.intent]
        key = brief.opening_key
        if beat.intent == "resolve" and brief.final_section_key:
            key = brief.final_section_key
        label = beat.text.strip()[:SECTION_LABEL_MAX] or section_type
        sections.append(
            ProjectPlanSection(
                id=f"sec-{index}",
                type=section_type,
                label=label,
                start_bar=cursor,
                bar_count=bar_count,
                density=density,
                narrative=beat.text[:NARRATIVE_TEXT_MAX],
                key=key,
            )
        )
        cursor += bar_count
    return sections


def _apply_goal_summaries(plan: ProjectPlanV1, raw_goals: Any) -> None:
    if not isinstance(raw_goals, list):
        return
    by_id = {goal.id: goal for goal in plan.goals}
    for item in raw_goals:
        if not isinstance(item, dict):
            continue
        goal_id = item.get("id")
        summary = item.get("summary")
        if not isinstance(goal_id, str) or goal_id not in by_id or not isinstance(summary, str):
            continue
        cleaned = " ".join(summary.split()).strip()
        if not cleaned:
            continue
        by_id[goal_id].summary = cleaned[:GOAL_SUMMARY_MAX]


def _apply_section_narratives(plan: ProjectPlanV1, raw_sections: Any) -> None:
    if not isinstance(raw_sections, list):
        return
    by_id = {section.id: section for section in plan.sections}
    for item in raw_sections:
        if not isinstance(item, dict):
            continue
        section_id = item.get("id")
        narrative = item.get("narrative")
        if (
            not isinstance(section_id, str)
            or section_id not in by_id
            or not isinstance(narrative, str)
        ):
            continue
        cleaned = " ".join(narrative.split()).strip()
        if not cleaned:
            continue
        by_id[section_id].narrative = cleaned[:NARRATIVE_TEXT_MAX]


_TEXT_FIELDS = frozenset({"summary", "narrative"})


def _log_discarded_structure(compiled: ProjectPlanV1, payload: Mapping[str, Any]) -> None:
    if "schema_version" in payload and payload["schema_version"] != compiled.schema_version:
        _debug_field("schema_version")
    raw_constraints = payload.get("constraints")
    if isinstance(raw_constraints, dict):
        compiled_constraints = compiled.constraints.model_dump()
        for key, value in raw_constraints.items():
            if compiled_constraints.get(key) != value:
                _debug_field(str(key))
    elif "constraints" in payload:
        _debug_field("constraints")
    _log_discarded_stages(compiled, payload.get("stages"))
    raw_sections = payload.get("sections")
    if isinstance(raw_sections, list):
        compiled_sections = {section.id: section for section in compiled.sections}
        if len(raw_sections) != len(compiled.sections):
            _debug_field("sections")
        for item in raw_sections:
            if not isinstance(item, dict):
                _debug_field("sections")
                continue
            section_id = item.get("id")
            section = compiled_sections.get(section_id) if isinstance(section_id, str) else None
            if section is None:
                _debug_field("sections")
                continue
            for key, value in item.items():
                if key in _TEXT_FIELDS:
                    continue
                if value != getattr(section, key, None):
                    _debug_field(str(key))
    elif "sections" in payload:
        _debug_field("sections")


def _log_discarded_stages(compiled: ProjectPlanV1, raw_stages: Any) -> None:
    if raw_stages is None:
        return
    if not isinstance(raw_stages, list):
        _debug_field("stages")
        return
    compiled_ids: list[StageId] = [stage.stage_id for stage in compiled.stages]
    director_ids = [
        item.get("stage_id") if isinstance(item, dict) else None for item in raw_stages
    ]
    if director_ids != compiled_ids or len(raw_stages) != len(STAGE_IDS):
        _debug_field("stages")
        return
    compiled_by_id = {stage.stage_id: stage.model_dump() for stage in compiled.stages}
    for item in raw_stages:
        if not isinstance(item, dict):
            _debug_field("stages")
            continue
        stage_id = item.get("stage_id")
        compiled_stage = compiled_by_id.get(stage_id)
        if compiled_stage is None:
            _debug_field("stages")
            continue
        for key, value in item.items():
            if compiled_stage.get(key) != value:
                _debug_field(str(key))


def _debug_field(field: str) -> None:
    logger.debug("Director structural field discarded", extra={"field": field})


def plan_from_payload(payload: Mapping[str, Any]) -> ProjectPlanV1:
    """Validate a stored plan object. Structural failures use the brief code."""
    try:
        return ProjectPlanV1.model_validate(payload)
    except ValidationError as exc:
        raise AutonomousPlanError("project plan invalid", code=AUTONOMOUS_BRIEF_INVALID) from exc
