"""Load a composition read-only, bind adaptive material refs, then call the store.

Does not write ``projects.composition_json`` or snapshot blobs. Does not import routers.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from app.adaptive_score_schemas import (
    AdaptiveBindingStatus,
    AdaptiveScoreCommand,
    AdaptiveScoreCommandResponse,
    AdaptiveScoreCreateRequest,
    AdaptiveScoreError,
    AdaptiveScoreFindingV1,
    AdaptiveScoreGetResponse,
    AdaptiveScoreSummaryV1,
    AdaptiveScoreUpdateRequest,
    AdaptiveScoreV1,
    AdaptiveScoreValidateResponse,
    parse_adaptive_score,
)
from app.composition_schemas import CompositionV2
from app.services.adaptive_score_commands import (
    apply_adaptive_score_command as apply_graph_command,
)
from app.services.adaptive_score_store import (
    create_score,
    delete_score,
    get_score,
    list_scores,
    replace_score,
)
from app.services.adaptive_score_validation import (
    bind_material_refs,
    binding_status_for,
    raise_on_error_findings,
    score_is_ready,
    validate_adaptive_score_graph,
    reject_embedded_note_material,
)
from app.services.composition_snapshot_encoding import (
    composition_snapshot_fingerprint,
    snapshot_fingerprint_log_prefix,
)
from app.services.project_composition import (
    ProjectCompositionError,
    normalize_project_composition,
)
from app.services.project_history import ProjectHistoryNotFoundError, get_revision_detail
from app.services.project_store import ProjectNotFoundError, get_project

logger = logging.getLogger(__name__)


def _require_project(project_id: str, db_path: Path | None) -> None:
    try:
        get_project(project_id, db_path=db_path)
    except ProjectNotFoundError as exc:
        raise AdaptiveScoreError(
            "project_not_found",
            "Project id was not found",
            http_status=404,
            details={"project_id": project_id},
        ) from exc


def _parse_incoming(raw: dict, *, project_id: str, score_id: str | None) -> AdaptiveScoreV1:
    reject_embedded_note_material(raw)
    client_id = raw.get("id")
    client_project = raw.get("project_id")
    if score_id is None:
        if client_id not in (None, ""):
            raise AdaptiveScoreError(
                "identity_mismatch",
                "Create does not accept a client score id",
                http_status=422,
            )
        if client_project not in (None, "", project_id):
            raise AdaptiveScoreError(
                "identity_mismatch",
                "project_id does not match the route",
                http_status=422,
                details={"project_id": project_id},
            )
    else:
        if client_id != score_id:
            raise AdaptiveScoreError(
                "identity_mismatch",
                "Score id does not match the route",
                http_status=422,
                details={"score_id": score_id},
            )
        if client_project != project_id:
            raise AdaptiveScoreError(
                "identity_mismatch",
                "project_id does not match the route",
                http_status=422,
                details={"project_id": project_id},
            )
    score = parse_adaptive_score(raw)
    if score_id is None:
        return score.model_copy(update={"id": None, "project_id": project_id})
    return score.model_copy(update={"id": score_id, "project_id": project_id})


def _symbolic_revision_id(score: AdaptiveScoreV1) -> str | None:
    revision_ids = {
        material.revision_id
        for _target, material in (
            (state.id, state.material) for state in score.states
        )
        if material.kind != "asset"
    }
    for collection in (score.variants, score.layers, score.stingers):
        for item in collection:
            if item.material.kind != "asset":
                revision_ids.add(item.material.revision_id)
    if len(revision_ids) > 1:
        raise AdaptiveScoreError(
            "mixed_revision_targets",
            "Non-asset material refs must share one revision id",
            http_status=422,
        )
    if not revision_ids:
        return None
    return next(iter(revision_ids))


def _has_symbolic_ref(score: AdaptiveScoreV1) -> bool:
    materials = [state.material for state in score.states]
    materials.extend(item.material for item in score.variants)
    materials.extend(item.material for item in score.layers)
    materials.extend(item.material for item in score.stingers)
    return any(material.kind != "asset" for material in materials)


def _load_composition(
    project_id: str,
    score: AdaptiveScoreV1,
    *,
    db_path: Path | None,
) -> tuple[CompositionV2 | None, str | None, str]:
    """Return composition, fingerprint, and source label. Does not persist."""
    if not _has_symbolic_ref(score):
        logger.info(
            "Adaptive score skipped composition lookup",
            extra={"project_id": project_id, "source": "unchecked", "binding_status": "unchecked"},
        )
        return None, None, "unchecked"
    revision_id = _symbolic_revision_id(score)
    started = time.perf_counter()
    if revision_id is None:
        project = get_project(project_id, db_path=db_path)
        if not project.composition_json:
            raise AdaptiveScoreError(
                "composition_unavailable",
                "Project has no composition to bind",
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
        composition = normalized.composition
        source = "working"
    else:
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
                "Revision has no composition to bind",
                http_status=422,
                details={"revision_id": revision_id},
            )
        composition = detail.composition
        source = "revision"
    fingerprint = composition_snapshot_fingerprint(composition)
    status = binding_status_for(score, fingerprint)
    logger.info(
        "Adaptive score composition loaded",
        extra={
            "project_id": project_id,
            "source": source,
            "binding_status": status,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    logger.debug(
        "Adaptive score composition pin",
        extra={
            "source": source,
            "revision_id": revision_id,
            "fingerprint_prefix": snapshot_fingerprint_log_prefix(fingerprint),
        },
    )
    return composition, fingerprint, source


def _checked_findings(
    score: AdaptiveScoreV1,
    *,
    project_id: str,
    db_path: Path | None,
    strict: bool,
    enforce_errors: bool,
) -> tuple[list[AdaptiveScoreFindingV1], AdaptiveBindingStatus]:
    graph = validate_adaptive_score_graph(score, strict=strict)
    if enforce_errors:
        raise_on_error_findings(graph)
    if not _has_symbolic_ref(score):
        return graph, "unchecked"
    composition, fingerprint, _source = _load_composition(project_id, score, db_path=db_path)
    bound = bind_material_refs(
        score,
        composition,
        fingerprint=fingerprint,
        strict=strict,
    )
    findings = graph + bound
    if enforce_errors:
        raise_on_error_findings(findings)
    return findings, binding_status_for(score, fingerprint)


def _to_response(record, binding_status: AdaptiveBindingStatus) -> AdaptiveScoreGetResponse:
    return AdaptiveScoreGetResponse(
        score=record.score,
        document_revision=record.document_revision,
        is_default=record.is_default,
        binding_status=binding_status,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def create_adaptive_score(
    project_id: str,
    request: AdaptiveScoreCreateRequest,
    *,
    db_path: Path | None = None,
) -> AdaptiveScoreGetResponse:
    _require_project(project_id, db_path)
    score = _parse_incoming(request.score, project_id=project_id, score_id=None)
    _findings, status = _checked_findings(
        score,
        project_id=project_id,
        db_path=db_path,
        strict=False,
        enforce_errors=True,
    )
    record = create_score(
        project_id,
        score.model_copy(update={"id": None}),
        is_default=request.is_default,
        db_path=db_path,
    )
    return _to_response(record, status)


def list_adaptive_scores(
    project_id: str,
    *,
    db_path: Path | None = None,
) -> list[AdaptiveScoreSummaryV1]:
    _require_project(project_id, db_path)
    return list_scores(project_id, db_path=db_path)


def get_adaptive_score(
    project_id: str,
    score_id: str,
    *,
    db_path: Path | None = None,
) -> AdaptiveScoreGetResponse:
    _require_project(project_id, db_path)
    record = get_score(project_id, score_id, db_path=db_path)
    _findings, status = _checked_findings(
        record.score,
        project_id=project_id,
        db_path=db_path,
        strict=False,
        enforce_errors=False,
    )
    return _to_response(record, status)


def replace_adaptive_score(
    project_id: str,
    score_id: str,
    request: AdaptiveScoreUpdateRequest,
    *,
    db_path: Path | None = None,
) -> AdaptiveScoreGetResponse:
    _require_project(project_id, db_path)
    score = _parse_incoming(request.score, project_id=project_id, score_id=score_id)
    _findings, status = _checked_findings(
        score,
        project_id=project_id,
        db_path=db_path,
        strict=False,
        enforce_errors=True,
    )
    record = replace_score(
        project_id,
        score_id,
        score,
        expected_document_revision=request.expected_document_revision,
        is_default=request.is_default,
        db_path=db_path,
    )
    return _to_response(record, status)


def delete_adaptive_score(
    project_id: str,
    score_id: str,
    *,
    db_path: Path | None = None,
) -> None:
    _require_project(project_id, db_path)
    delete_score(project_id, score_id, db_path=db_path)


def validate_adaptive_score(
    project_id: str,
    score_id: str,
    *,
    strict: bool = False,
    db_path: Path | None = None,
) -> AdaptiveScoreValidateResponse:
    _require_project(project_id, db_path)
    record = get_score(project_id, score_id, db_path=db_path)
    findings, status = _checked_findings(
        record.score,
        project_id=project_id,
        db_path=db_path,
        strict=strict,
        enforce_errors=False,
    )
    errors = sum(1 for item in findings if item.severity == "error")
    warnings = sum(1 for item in findings if item.severity == "warning")
    logger.info(
        "Adaptive score validated",
        extra={
            "project_id": project_id,
            "score_id": score_id,
            "binding_status": status,
            "error_count": errors,
            "warning_count": warnings,
            "strict": strict,
        },
    )
    for item in findings:
        logger.debug(
            "Adaptive score validate finding",
            extra={"code": item.code, "target_id": item.target_id},
        )
    return AdaptiveScoreValidateResponse(
        ready=score_is_ready(record.score, findings),
        binding_status=status,
        findings=findings,
        error_count=errors,
        warning_count=warnings,
    )


def _error_finding_details(findings: list[AdaptiveScoreFindingV1]) -> list[dict[str, str | None]]:
    errors = [item for item in findings if item.severity == "error"][:64]
    return [
        {
            "code": item.code,
            "severity": item.severity,
            "target_id": item.target_id,
            "message": item.message,
        }
        for item in errors
    ]


def apply_adaptive_score_command(
    project_id: str,
    score_id: str,
    command: AdaptiveScoreCommand,
    *,
    db_path: Path | None = None,
) -> AdaptiveScoreCommandResponse:
    """Apply one command, then bind and CAS-write only when findings have no errors."""
    _require_project(project_id, db_path)
    reject_embedded_note_material(command.payload.model_dump(mode="json"))
    record = get_score(project_id, score_id, db_path=db_path)
    updated = apply_graph_command(record.score, command)
    findings, status = _checked_findings(
        updated,
        project_id=project_id,
        db_path=db_path,
        strict=False,
        enforce_errors=False,
    )
    errors = [item for item in findings if item.severity == "error"]
    if errors:
        codes = [item.code for item in errors[:64]]
        logger.debug(
            "Adaptive score command findings rejected",
            extra={"project_id": project_id, "score_id": score_id, "op": command.op, "codes": codes},
        )
        first = errors[0]
        raise AdaptiveScoreError(
            first.code,
            first.message or first.code,
            http_status=422,
            details={"findings": _error_finding_details(findings), "target_id": first.target_id},
        )
    saved = replace_score(
        project_id,
        score_id,
        updated,
        expected_document_revision=command.expected_document_revision,
        db_path=db_path,
    )
    warnings = [item for item in findings if item.severity == "warning"]
    logger.debug(
        "Adaptive score command warnings kept",
        extra={"score_id": score_id, "op": command.op, "warning_count": len(warnings)},
    )
    return AdaptiveScoreCommandResponse(
        score=saved.score,
        document_revision=saved.document_revision,
        is_default=saved.is_default,
        binding_status=status,
        created_at=saved.created_at,
        updated_at=saved.updated_at,
        findings=warnings,
    )
