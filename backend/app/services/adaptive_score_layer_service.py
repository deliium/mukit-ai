"""Load a score and project layer spans, then map runtime intensity.

Does not write ``body_json``, ``projects.composition_json``, or snapshot blobs.
Does not call ``replace_score`` or an LLM.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_ERROR_CODES,
    AdaptiveLayerIntensityLayerV1,
    AdaptiveLayerIntensityRequest,
    AdaptiveLayerIntensityV1,
    AdaptiveLayerPlanPreviewRequest,
    AdaptiveMaterialRefV1,
    AdaptiveScoreError,
    AdaptiveScoreLayerV1,
    AdaptiveScoreV1,
    AdaptiveTransitionScheduleWarningV1,
)
from app.composition_schemas import CompositionV2
from app.services.adaptive_score_layers import (
    LayerProjection,
    LayerTimelineProjection,
    map_adaptive_layers,
)
from app.services.adaptive_score_service import _has_symbolic_ref, _symbolic_revision_id
from app.services.adaptive_score_store import get_score
from app.services.adaptive_score_validation import bind_material_refs, raise_on_error_findings
from app.services.composition_timeline import CompiledTimeline, compile_timeline
from app.services.project_composition import ProjectCompositionError, normalize_project_composition
from app.services.project_history import ProjectHistoryNotFoundError, get_revision_detail
from app.services.project_store import ProjectNotFoundError, get_project

logger = logging.getLogger(__name__)

_MAX_WARNINGS = 32


def resolve_adaptive_layer_intensity(
    project_id: str,
    score_id: str,
    request: AdaptiveLayerIntensityRequest,
    *,
    db_path: Path | None = None,
) -> AdaptiveLayerIntensityV1:
    """Map stored layers. The score row and composition stay unchanged."""
    return _map_loaded(
        project_id,
        score_id,
        request,
        layers=None,
        db_path=db_path,
    )


def preview_adaptive_layer_plan(
    project_id: str,
    score_id: str,
    request: AdaptiveLayerPlanPreviewRequest,
    *,
    db_path: Path | None = None,
) -> AdaptiveLayerIntensityV1:
    """Map proposal layers only. Does not merge or store them."""
    layers = _proposal_layers(request)
    return _map_loaded(
        project_id,
        score_id,
        request,
        layers=layers,
        db_path=db_path,
    )


def _map_loaded(
    project_id: str,
    score_id: str,
    request: AdaptiveLayerIntensityRequest,
    *,
    layers: list[AdaptiveScoreLayerV1] | None,
    db_path: Path | None,
) -> AdaptiveLayerIntensityV1:
    try:
        body = _map_loaded_body(
            project_id,
            score_id,
            request,
            layers=layers,
            db_path=db_path,
        )
    except AdaptiveScoreError as exc:
        logger.error(
            "Adaptive layer intensity rejected",
            extra={"code": exc.code},
        )
        raise
    logger.info(
        "Adaptive layer intensity mapped",
        extra={
            "project_id": project_id,
            "score_id": score_id,
            "state_id": body.state_id,
            "intensity": body.intensity,
            "active_count": sum(1 for row in body.layers if row.active),
            "warning_count": len(body.warnings),
        },
    )
    return body


def _map_loaded_body(
    project_id: str,
    score_id: str,
    request: AdaptiveLayerIntensityRequest,
    *,
    layers: list[AdaptiveScoreLayerV1] | None,
    db_path: Path | None,
) -> AdaptiveLayerIntensityV1:
    record = get_score(project_id, score_id, db_path=db_path)
    if record.document_revision != request.expected_document_revision:
        raise AdaptiveScoreError(
            "adaptive_score_conflict",
            ADAPTIVE_SCORE_ERROR_CODES["adaptive_score_conflict"],
            http_status=409,
        )
    mapped_layers = record.score.layers if layers is None else layers
    working = record.score.model_copy(update={"layers": mapped_layers})
    state = next((item for item in working.states if item.id == request.state_id), None)
    if state is None:
        raise AdaptiveScoreError(
            "dangling_state_ref",
            ADAPTIVE_SCORE_ERROR_CODES["dangling_state_ref"],
            http_status=422,
            details={"target_id": request.state_id},
        )
    intensity = request.intensity if request.intensity is not None else state.intensity
    composition = _load_clock(project_id, working, db_path=db_path)
    findings = bind_material_refs(working, composition)
    raise_on_error_findings(findings)
    timeline = compile_timeline(composition)
    position_tick = 0 if request.position_tick is None else request.position_tick
    if position_tick > timeline.duration_ticks:
        raise AdaptiveScoreError(
            "position_outside",
            ADAPTIVE_SCORE_ERROR_CODES["position_outside"],
            http_status=422,
        )
    sections = {section.id: section for section in composition.sections if section.id}
    projections: list[LayerProjection] = []
    span_warnings: list[AdaptiveTransitionScheduleWarningV1] = []
    for layer in working.layers:
        span_start, span_end, disagrees = _span(layer, timeline, sections)
        if disagrees and len(span_warnings) < _MAX_WARNINGS:
            span_warnings.append(
                AdaptiveTransitionScheduleWarningV1(
                    code="section_tick_disagrees",
                    target_id=layer.id,
                    message="Section start tick disagrees with the bar grid.",
                )
            )
        projections.append(
            LayerProjection(
                id=layer.id,
                state_id=layer.state_id,
                role=layer.role,
                intensity_min=layer.intensity_min,
                intensity_max=layer.intensity_max,
                exclusive_group=layer.exclusive_group,
                priority=layer.priority,
                in_policy=layer.fade.in_policy,
                out_policy=layer.fade.out_policy,
                fade_in_ms=layer.fade.fade_in_ms,
                fade_out_ms=layer.fade.fade_out_ms,
                span_start_tick=span_start,
                span_end_tick=span_end,
                track_ids=tuple(layer.material.track_ids[:16]),
                material_kind=layer.material.kind,
            )
        )
    mapped = map_adaptive_layers(
        tuple(projections),
        state_id=request.state_id,
        intensity=intensity,
        position_tick=position_tick,
        previous_intensity=request.previous_intensity,
        timeline=LayerTimelineProjection(
            bar_boundaries=timeline.bar_boundaries,
            duration_ticks=timeline.duration_ticks,
            ticks_per_quarter=timeline.ticks_per_quarter,
            tempo_bpm=timeline.active_tempo(position_tick),
        ),
    )
    warnings = (span_warnings + list(mapped.warnings))[:_MAX_WARNINGS]
    return AdaptiveLayerIntensityV1(
        project_id=project_id,
        score_id=score_id,
        document_revision=record.document_revision,
        state_id=request.state_id,
        intensity=intensity,
        position_tick=position_tick,
        layers=[
            AdaptiveLayerIntensityLayerV1.model_validate(
                {
                    "layer_id": row.layer_id,
                    "role": row.role,
                    "material_kind": row.material_kind,
                    "track_ids": list(row.track_ids),
                    "active": row.active,
                    "audible": row.audible,
                    "target_gain": row.target_gain,
                    "reason": row.reason,
                    "suppressed_by": row.suppressed_by,
                    "in_policy": row.in_policy,
                    "out_policy": row.out_policy,
                    "fade_ms": row.fade_ms,
                    "fade_end_tick": row.fade_end_tick,
                }
            )
            for row in mapped.layers
        ],
        warnings=warnings,
    )


def _proposal_layers(request: AdaptiveLayerPlanPreviewRequest) -> list[AdaptiveScoreLayerV1]:
    layers: list[AdaptiveScoreLayerV1] = []
    for index, proposal in enumerate(request.proposals):
        state_id = proposal.state_id
        if "state_id" not in proposal.model_fields_set:
            state_id = request.state_id
        layers.append(
            AdaptiveScoreLayerV1(
                id=f"preview-{index:02d}",
                name=proposal.name,
                state_id=state_id,
                material=proposal.material,
                intensity_min=proposal.intensity_min,
                intensity_max=proposal.intensity_max,
                mix_hint=proposal.mix_hint,
                default_active=proposal.default_active,
                role=proposal.role,
                exclusive_group=proposal.exclusive_group,
                priority=proposal.priority,
                fade=proposal.fade,
            )
        )
    return layers


def _span(
    layer: AdaptiveScoreLayerV1,
    timeline: CompiledTimeline,
    sections: dict[str, object],
) -> tuple[int | None, int | None, bool]:
    material = layer.material
    try:
        return _span_for_material(material, timeline, sections)
    except ValueError as exc:
        raise AdaptiveScoreError(
            "material_range_outside",
            ADAPTIVE_SCORE_ERROR_CODES["material_range_outside"],
            http_status=422,
            details={"target_id": layer.id},
        ) from exc


def _span_for_material(
    material: AdaptiveMaterialRefV1,
    timeline: CompiledTimeline,
    sections: dict[str, object],
) -> tuple[int | None, int | None, bool]:
    if material.kind == "bar_range":
        assert material.start_bar is not None and material.end_bar is not None
        start, end = timeline.bar_range_ticks(material.start_bar, material.end_bar)
        return start, end, False
    if material.kind == "section":
        return _section_span(material.section_id, timeline, sections)
    if material.kind == "track_range":
        if material.start_bar is not None and material.end_bar is not None:
            start, end = timeline.bar_range_ticks(material.start_bar, material.end_bar)
            return start, end, False
        return 0, timeline.duration_ticks, False
    if material.kind == "revision_region":
        if material.start_bar is not None and material.end_bar is not None:
            start, end = timeline.bar_range_ticks(material.start_bar, material.end_bar)
            return start, end, False
        if material.section_id:
            return _section_span(material.section_id, timeline, sections)
        return None, None, False
    return None, None, False


def _section_span(
    section_id: str | None,
    timeline: CompiledTimeline,
    sections: dict[str, object],
) -> tuple[int | None, int | None, bool]:
    section = sections.get(section_id or "")
    if section is None:
        raise ValueError("section missing")
    start_bar = getattr(section, "start_bar")
    bar_count = getattr(section, "bar_count")
    stored_tick = getattr(section, "start_tick")
    end_bar = start_bar + bar_count - 1
    start = timeline.bar_start_tick(start_bar)
    end = timeline.bar_end_tick(end_bar)
    return start, end, stored_tick != start


def _load_clock(
    project_id: str,
    score: AdaptiveScoreV1,
    *,
    db_path: Path | None,
) -> CompositionV2:
    revision_id = _symbolic_revision_id(score) if _has_symbolic_ref(score) else None
    if revision_id is None:
        try:
            project = get_project(project_id, db_path=db_path)
        except ProjectNotFoundError as exc:
            raise AdaptiveScoreError(
                "project_not_found",
                "Project id was not found",
                http_status=404,
                details={"project_id": project_id},
            ) from exc
        if not project.composition_json:
            raise AdaptiveScoreError(
                "composition_unavailable",
                "Project has no composition to map layers against",
                http_status=422,
            )
        try:
            normalized = normalize_project_composition(
                project.composition_json,
                project_id=project_id,
                persist_canonical=False,
            )
        except ProjectCompositionError as exc:
            raise AdaptiveScoreError(
                "composition_unavailable",
                "Project composition could not be read",
                http_status=422,
            ) from exc
        logger.debug(
            "Adaptive layer clock loaded",
            extra={"project_id": project_id, "source": "working"},
        )
        return normalized.composition
    try:
        detail = get_revision_detail(project_id, revision_id, db_path=db_path)
    except ProjectHistoryNotFoundError as exc:
        raise AdaptiveScoreError(
            "revision_not_found",
            "Revision id was not found on this project",
            http_status=404,
            details={"revision_id": revision_id},
        ) from exc
    if detail.composition is None:
        raise AdaptiveScoreError(
            "composition_unavailable",
            "Revision has no composition to map layers against",
            http_status=422,
            details={"revision_id": revision_id},
        )
    logger.debug(
        "Adaptive layer clock loaded",
        extra={"project_id": project_id, "source": "revision"},
    )
    return detail.composition
