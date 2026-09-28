"""Load a score and timeline, schedule one transition, then hold the pending slot.

Does not write the score, ``projects.composition_json``, or snapshot blobs.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_ERROR_CODES,
    AdaptiveScoreError,
    AdaptiveScoreStateV1,
    AdaptiveScoreV1,
    AdaptiveTransitionScheduleRequest,
    AdaptiveTransitionScheduleV1,
)
from app.composition_schemas import CompositionV2
from app.services.adaptive_score_store import get_score
from app.services.adaptive_score_service import _has_symbolic_ref, _symbolic_revision_id
from app.services.adaptive_score_transition_pending import (
    TransitionPendingRegistry,
    get_default_registry,
)
from app.services.adaptive_score_transitions import (
    MusicalTransitionSchedule,
    TransitionMarkerProjection,
    TransitionSectionProjection,
    schedule_musical_transition,
)
from app.services.adaptive_score_validation import bind_material_refs, raise_on_error_findings
from app.services.composition_timeline import compile_timeline
from app.services.project_composition import ProjectCompositionError, normalize_project_composition
from app.services.project_history import ProjectHistoryNotFoundError, get_revision_detail
from app.services.project_store import ProjectNotFoundError, get_project

logger = logging.getLogger(__name__)


def schedule_adaptive_transition(
    project_id: str,
    score_id: str,
    request: AdaptiveTransitionScheduleRequest,
    *,
    db_path: Path | None = None,
    registry: TransitionPendingRegistry | None = None,
) -> tuple[AdaptiveTransitionScheduleV1, int]:
    """Return the pending schedule and the HTTP status (201 create, 200 replace)."""
    slots = registry or get_default_registry()
    record = get_score(project_id, score_id, db_path=db_path)
    if record.document_revision != request.expected_document_revision:
        raise AdaptiveScoreError(
            "adaptive_score_conflict",
            ADAPTIVE_SCORE_ERROR_CODES["adaptive_score_conflict"],
            http_status=409,
        )
    composition = _load_clock(project_id, record.score, db_path=db_path)
    timeline = compile_timeline(composition)
    sections = tuple(
        TransitionSectionProjection(
            section_id=section.id,
            start_bar=section.start_bar,
            bar_count=section.bar_count,
            start_tick=section.start_tick,
        )
        for section in composition.sections
        if section.id
    )
    markers = tuple(
        TransitionMarkerProjection(kind=marker.kind, label=marker.label, tick=marker.tick)
        for marker in composition.markers
    )
    plan = schedule_musical_transition(
        record.score,
        timeline,
        sections,
        markers,
        request,
    )
    _reject_bad_phrase(record.score, plan, composition)
    schedule = AdaptiveTransitionScheduleV1(
        request_id="treq_" + secrets.token_hex(4),
        project_id=project_id,
        score_id=score_id,
        document_revision=record.document_revision,
        transition_id=plan.transition_id,
        from_state_id=plan.from_state_id,
        to_state_id=plan.to_state_id,
        quantization=plan.quantization,
        boundary_tick=plan.boundary_tick,
        boundary_bar=plan.boundary_bar,
        latency_ticks=plan.latency_ticks,
        latency_ms=plan.latency_ms,
        tempo_bpm=plan.tempo_bpm,
        time_signature=plan.time_signature,
        aligned=plan.aligned,
        realization=plan.realization,
        warnings=list(plan.warnings),
    )
    replaced = slots.put(schedule)
    stored = slots.get(project_id, score_id)
    if stored is None:
        stored = schedule.model_copy(update={"replaced_request_id": replaced})
    logger.debug(
        "Adaptive transition scheduled",
        extra={
            "project_id": project_id,
            "score_id": score_id,
            "request_id": stored.request_id,
            "replaced": replaced is not None,
        },
    )
    return stored, 200 if replaced else 201


def get_pending_transition(
    project_id: str,
    score_id: str,
    *,
    db_path: Path | None = None,
    registry: TransitionPendingRegistry | None = None,
) -> AdaptiveTransitionScheduleV1 | None:
    get_score(project_id, score_id, db_path=db_path)
    slots = registry or get_default_registry()
    return slots.get(project_id, score_id)


def cancel_pending_transition(
    project_id: str,
    score_id: str,
    request_id: str,
    *,
    db_path: Path | None = None,
    registry: TransitionPendingRegistry | None = None,
) -> None:
    get_score(project_id, score_id, db_path=db_path)
    slots = registry or get_default_registry()
    slots.cancel(project_id, score_id, request_id)


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
                "Project has no composition to schedule against",
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
            "Adaptive transition clock loaded",
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
            "Revision has no composition to schedule against",
            http_status=422,
            details={"revision_id": revision_id},
        )
    logger.debug(
        "Adaptive transition clock loaded",
        extra={"project_id": project_id, "source": "revision"},
    )
    return detail.composition


def _reject_bad_phrase(
    score: AdaptiveScoreV1,
    plan: MusicalTransitionSchedule,
    composition: CompositionV2,
) -> None:
    if plan.realization.kind != "phrase":
        return
    transition = next(item for item in score.transitions if item.id == plan.transition_id)
    material = transition.realization.phrase_material
    if material is None or material.kind == "asset":
        return
    probe = AdaptiveScoreV1(
        name="Phrase probe",
        states=[
            AdaptiveScoreStateV1(
                id="phrase-probe",
                name="Phrase",
                intensity=0,
                material=material,
            )
        ],
    )
    findings = bind_material_refs(probe, composition)
    raise_on_error_findings(findings)
