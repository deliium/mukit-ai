"""Compile a film score plan, call the existing agents, and return a candidate.

``ai_agents/`` does not import this module. Preview does not write the project.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.ai_agents.artifact_schemas import AgentMotifPlanV1
from app.ai_agents.registry import get_agent
from app.ai_agents.schemas import (
    AgentArtifactKind,
    AgentArtifactProvenance,
    AgentArtifactV1,
    AgentOperation,
    AgentRunRequest,
    AgentWorkflowContext,
)
from app.composition_plan_schemas import CompositionPlan
from app.composition_schemas import (
    CompositionV2,
    CompositionV2Section,
    CompositionV2TempoChange,
    bar_duration_ticks,
)
from app.film_score_schemas import (
    FilmCueSnapshot,
    FilmHarmonicArcEntry,
    FilmMotifAppearance,
    FilmScoreError,
    FilmScorePlanV1,
    FilmScorePreviewRequest,
    FilmScorePreviewResponse,
    FilmScoreSection,
)
from app.services.composer_profile_merge import resolve_profile_merge
from app.services.composition_edit_fingerprint import composition_edit_fingerprint
from app.services.composition_planner import ComposerFormPlan, ComposerFormSection
from app.services.composition_timeline import compile_timeline
from app.services.film_score_accents import apply_film_score_accents
from app.services.film_score_tempo import compile_film_score_plan
from app.services.instrument_catalog import resolve_profile
from app.services.symbolic_composition_generate import generate_symbolic_composition

logger = logging.getLogger(__name__)

_AGENT_STEPS: tuple[tuple[str, str], ...] = (
    ("creative_director", "plan"),
    ("structure_form", "plan"),
    ("harmony", "propose"),
    ("melody_motif", "propose"),
    ("arrangement", "propose"),
    ("orchestration", "propose"),
    ("critic", "critique"),
)

_ROLE_CONTENT_TYPES = {
    "brief": "agent.brief.v1",
    "harmony_plan": "agent.harmony_plan.v1",
    "motif_plan": "agent.motif_plan.v1",
    "arrangement_plan": "agent.arrangement_plan.v1",
    "critique": "agent.critique.v1",
    "revision_plan": "agent.revision_plan.v1",
}


async def run_film_score_preview(
    *,
    project_id: str,
    source: CompositionV2,
    source_fingerprint: str,
    scoring_document_revision: int,
    cues: list[FilmCueSnapshot],
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    asset_duration_seconds: float,
    request: FilmScorePreviewRequest,
    timecode_mode: str = "non_drop",
    start_timecode: str = "00:00:00:00",
) -> FilmScorePreviewResponse:
    """Return an inspectable plan and a candidate. ``committed`` stays false."""
    started = time.perf_counter()
    logger.info(
        "film score preview started project_id=%s cue_count=%s",
        project_id,
        len(cues),
    )
    _require_motifs(source, request.motif_ids)
    instruments = _resolve_instruments(request.instruments)
    key = request.key or source.key or "C major"
    meter = request.time_signature or source.time_signature or "4/4"
    tempo_min = int(request.tempo_min or 96)
    tempo_max = int(request.tempo_max or 132)
    opening = _opening_tempo(request.opening_tempo, source.tempo, tempo_min, tempo_max)
    target = request.target_duration_seconds or asset_duration_seconds
    soft_fragment = ""
    if request.profile_strength != "off":
        merged = resolve_profile_merge(
            profile_id=request.profile_id,
            profile_strength=request.profile_strength,
        )
        soft_fragment = merged.soft_fragment
    plan = compile_film_score_plan(
        cues,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
        opening_tempo=opening,
        tempo_min=tempo_min,
        tempo_max=tempo_max,
        time_signature=meter,
        key=key,
        target_duration_seconds=float(target),
        project_id=project_id,
        source_fingerprint=source_fingerprint,
        scoring_document_revision=scoring_document_revision,
        ticks_per_quarter=source.ticks_per_quarter,
    )
    warnings = list(plan.warnings)
    if not request.motif_ids and "film_motif_absent" not in warnings:
        warnings.append("film_motif_absent")
    duration_bars = sum(section.bar_count for section in plan.sections)
    composition_plan = _composition_plan(plan, instruments, duration_bars)
    generated = generate_symbolic_composition(composition_plan)
    candidate = _apply_tempo_grid(generated.composition, plan)
    plan = _refresh_video_edges(plan, candidate)
    context = AgentWorkflowContext(
        source_composition=candidate,
        source_fingerprint=composition_edit_fingerprint(candidate),
        working_draft_composition=candidate,
    )
    sequence: list[str] = []
    artifact_log: list[AgentArtifactV1] = []
    recommendation = None
    for agent_id, operation in _AGENT_STEPS:
        if agent_id == "melody_motif" and not request.motif_ids:
            artifact_log.append(_empty_motif_artifact(context.source_fingerprint))
            continue
        logger.debug("film score agent call agent_id=%s", agent_id)
        sequence.append(agent_id)
        try:
            result = await get_agent(agent_id).run(
                AgentRunRequest(
                    agent_id=agent_id,
                    operation=AgentOperation(operation),
                    context=context,
                    selection={
                        "start_bar": 1,
                        "end_bar": duration_bars,
                        "content_policy": "preserve_melody_adapt_harmony",
                        "instruments": instruments,
                        "motif_ids": list(request.motif_ids),
                    },
                    parameters=_agent_parameters(request.brief, soft_fragment, agent_id),
                )
            )
        except FilmScoreError:
            raise
        except Exception as exc:
            logger.warning(
                "film_agent_failed agent_id=%s error_code=%s",
                agent_id,
                type(exc).__name__,
            )
            failed = _response(
                plan,
                candidate=None,
                fingerprint=None,
                artifact_log=_with_empty_motif(artifact_log, context.source_fingerprint, request.motif_ids),
                warnings=warnings,
                sequence=sequence,
                recommendation=None,
            )
            raise FilmScoreError("film_agent_failed", preview=failed) from exc
        artifact_log.extend(result.artifacts)
        if result.recommendation is not None:
            recommendation = result.recommendation.value
        if result.working_draft_update is not None:
            candidate = _apply_tempo_grid(result.working_draft_update, plan)
            context = context.with_working_draft(candidate)
    if len(candidate.sections) != len(plan.sections) or candidate.bar_count != duration_bars:
        candidate = _apply_tempo_grid(candidate, plan)
    candidate = apply_film_score_accents(
        candidate,
        plan,
        cues,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
        duration_seconds=float(plan.music_end_seconds),
        timecode_mode=timecode_mode,
        start_timecode=start_timecode,
    )
    candidate = CompositionV2.model_validate(candidate.model_dump(mode="json"))
    arc, harmony_empty = _harmonic_arc(artifact_log, plan.key)
    if harmony_empty and "harmony_plan_empty" not in warnings:
        warnings.append("harmony_plan_empty")
    appearances = _motif_appearances(request.motif_ids, plan)
    plan = plan.model_copy(
        update={
            "warnings": warnings[:32],
            "agent_sequence": sequence,
            "harmonic_arc": arc,
            "motif_appearances": appearances,
            "committed": False,
        }
    )
    fingerprint = composition_edit_fingerprint(candidate)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "film score preview finished project_id=%s cue_count=%s section_count=%s tempo_change_count=%s warning_codes=%s duration_ms=%s",
        project_id,
        len(cues),
        len(plan.sections),
        len(plan.tempo_strategy.changes),
        ",".join(plan.warnings),
        elapsed_ms,
    )
    return _response(
        plan,
        candidate=candidate.model_dump(mode="json"),
        fingerprint=fingerprint,
        artifact_log=artifact_log,
        warnings=plan.warnings,
        sequence=sequence,
        recommendation=recommendation,
    )


def _require_motifs(source: CompositionV2, motif_ids: list[str]) -> None:
    known = {motif.id for motif in source.motifs}
    for motif_id in motif_ids:
        if motif_id not in known:
            logger.warning("film_motif_missing")
            raise FilmScoreError("film_motif_missing")


def _resolve_instruments(instruments: list[str]) -> list[str]:
    resolved: list[str] = []
    for name in instruments:
        profile = resolve_profile(instrument_id=name) or resolve_profile(alias=name)
        if profile is None:
            logger.warning("film_instrument_unknown")
            raise FilmScoreError("film_instrument_unknown")
        resolved.append(profile.instrument_id)
    return resolved


def _opening_tempo(requested: int | None, source_tempo: int, tempo_min: int, tempo_max: int) -> int:
    if requested is not None:
        return requested
    if tempo_min <= source_tempo <= tempo_max:
        return int(source_tempo)
    return min(tempo_max, max(tempo_min, 120))


def _section_type(index: int, count: int) -> str:
    if count <= 1:
        return "verse"
    if index == 0:
        return "intro"
    if index == count - 1:
        return "outro"
    return "verse"


def _composition_plan(plan: FilmScorePlanV1, instruments: list[str], duration_bars: int) -> CompositionPlan:
    count = len(plan.sections)
    sections = [
        ComposerFormSection(
            type=_section_type(index, count),
            start_bar=section.start_bar,
            bar_count=section.bar_count,
        )
        for index, section in enumerate(plan.sections)
    ]
    return CompositionPlan(
        form=ComposerFormPlan(
            tempo=plan.root_tempo,
            key=plan.key,
            time_signature=plan.time_signature,
            bar_count=duration_bars,
            sections=sections,
            instrumentation=instruments,
        )
    )


def _apply_tempo_grid(composition: CompositionV2, plan: FilmScorePlanV1) -> CompositionV2:
    bar_ticks = bar_duration_ticks(plan.time_signature, composition.ticks_per_quarter)
    sections: list[CompositionV2Section] = []
    count = len(plan.sections)
    for index, section in enumerate(plan.sections):
        start_tick = (section.start_bar - 1) * bar_ticks
        sections.append(
            CompositionV2Section(
                type=_section_type(index, count),
                label=section.label,
                start_bar=section.start_bar,
                bar_count=section.bar_count,
                start_tick=start_tick,
                duration_ticks=section.bar_count * bar_ticks,
            )
        )
    duration = bar_ticks * sum(section.bar_count for section in plan.sections)
    changes = [
        CompositionV2TempoChange(tick=change.tick, bpm=change.bpm)
        for change in plan.tempo_strategy.changes
        if change.tick > 0
    ]
    return composition.model_copy(
        update={
            "tempo": plan.root_tempo,
            "key": plan.key,
            "time_signature": plan.time_signature,
            "bar_count": sum(section.bar_count for section in plan.sections),
            "duration_ticks": duration,
            "sections": sections,
            "tempo_changes": changes,
        }
    )


def _refresh_video_edges(plan: FilmScorePlanV1, composition: CompositionV2) -> FilmScorePlanV1:
    timeline = compile_timeline(composition)
    bar_ticks = bar_duration_ticks(plan.time_signature, composition.ticks_per_quarter)
    sections: list[FilmScoreSection] = []
    for section in plan.sections:
        start_tick = (section.start_bar - 1) * bar_ticks
        end_tick = start_tick + section.bar_count * bar_ticks
        sections.append(
            section.model_copy(
                update={
                    "tempo_bpm": section.tempo_bpm,
                    "start_video_seconds": plan.music_start_seconds + timeline.tick_to_seconds(start_tick),
                    "end_video_seconds": plan.music_start_seconds + timeline.tick_to_seconds(min(end_tick, timeline.duration_ticks)),
                }
            )
        )
    return plan.model_copy(update={"sections": sections})


def _agent_parameters(brief: str, soft_fragment: str, agent_id: str) -> dict[str, Any]:
    if agent_id != "creative_director":
        return {}
    intent = brief if not soft_fragment else f"{brief}\n{soft_fragment}"
    return {"intent": intent[:2000]}


def _empty_motif_artifact(fingerprint: str) -> AgentArtifactV1:
    payload = AgentMotifPlanV1().model_dump(mode="json")
    return AgentArtifactV1(
        kind=AgentArtifactKind.PLAN,
        producer_agent_id="melody_motif",
        content_type="agent.motif_plan.v1",
        payload=payload,
        source_fingerprint=fingerprint,
        provenance=AgentArtifactProvenance(operation="propose", agent_id="melody_motif"),
    )


def _with_empty_motif(
    artifact_log: list[AgentArtifactV1],
    fingerprint: str,
    motif_ids: list[str],
) -> list[AgentArtifactV1]:
    if motif_ids:
        return artifact_log
    if any(item.content_type == "agent.motif_plan.v1" for item in artifact_log):
        return artifact_log
    return [*artifact_log, _empty_motif_artifact(fingerprint)]


def _harmonic_arc(artifact_log: list[AgentArtifactV1], key: str) -> tuple[list[FilmHarmonicArcEntry], bool]:
    payload = None
    for item in artifact_log:
        if item.content_type == "agent.harmony_plan.v1":
            payload = item.payload
    events = []
    if isinstance(payload, dict):
        events = payload.get("chord_events") or []
    if not events:
        return [], True
    arc: list[FilmHarmonicArcEntry] = []
    for event in events[:32]:
        if not isinstance(event, dict):
            continue
        function = str(event.get("function") or event.get("chord") or "").strip()
        if not function:
            continue
        bar = int(event.get("bar") or 1)
        arc.append(
            FilmHarmonicArcEntry(start_bar=bar, end_bar=bar, key=key, function=function[:80])
        )
    return arc, False


def _motif_appearances(motif_ids: list[str], plan: FilmScorePlanV1) -> list[FilmMotifAppearance]:
    non_sparse = [section for section in plan.sections if section.density != "sparse"] or list(plan.sections)
    if not motif_ids or not non_sparse:
        return []
    bar_count = sum(section.bar_count for section in plan.sections)
    later = non_sparse[-1] if len(non_sparse) > 1 else None
    rows: list[FilmMotifAppearance] = []
    for motif_id in motif_ids:
        rows.append(FilmMotifAppearance(motif_id=motif_id, section_id=non_sparse[0].id))
        if bar_count >= 16 and later is not None and later.id != non_sparse[0].id:
            rows.append(FilmMotifAppearance(motif_id=motif_id, section_id=later.id))
        if len(rows) >= 16:
            break
    return rows[:16]


def build_film_artifact_role_map(artifact_log: list[dict[str, Any]]) -> dict[str, dict[str, str] | None]:
    """Same role lookup the spine Apply client uses."""
    by_type: dict[str, dict[str, str]] = {}
    for entry in artifact_log:
        content_type = str(entry.get("content_type") or "")
        artifact_id = str(entry.get("artifact_id") or "")
        if content_type and artifact_id:
            by_type[content_type] = {"artifact_id": artifact_id, "content_type": content_type}
    return {role: by_type.get(content_type) for role, content_type in _ROLE_CONTENT_TYPES.items()}


def _response(
    plan: FilmScorePlanV1,
    *,
    candidate: dict[str, Any] | None,
    fingerprint: str | None,
    artifact_log: list[AgentArtifactV1],
    warnings: list[str],
    sequence: list[str],
    recommendation: str | None,
) -> FilmScorePreviewResponse:
    plan = plan.model_copy(update={"warnings": warnings[:32], "agent_sequence": sequence, "committed": False})
    dumped = [item.model_dump(mode="json") for item in artifact_log]
    critic = recommendation
    if critic not in {"approve", "revise"}:
        critic = None
    return FilmScorePreviewResponse(
        plan=plan,
        candidate=candidate,
        candidate_fingerprint=fingerprint,
        artifact_log=dumped,
        artifact_role_map=build_film_artifact_role_map(dumped),
        committed=False,
        recommendation=critic,  # type: ignore[arg-type]
    )
