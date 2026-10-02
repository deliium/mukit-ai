"""Commit a mechanical theme reuse and its usage row on one SQLite connection.

The destination history row and the universe document commit together. A
failure rolls both back. This module does not open a second connection for
the history write.
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass
from pathlib import Path

from app.composition_schemas import CompositionV2
from app.db.connection import get_connection, get_project_db_path
from app.musical_universe_schemas import (
    MECHANICAL_OPERATIONS,
    MUSICAL_UNIVERSE_ERROR_CODES,
    MusicalUniverseError,
    MusicalUniverseUsageV1,
    MusicalUniverseV1,
    MusicalUniverseVariantV1,
    UniverseTransformParameters,
    assert_operation_parameters,
)
from app.project_history_schemas import AiProvenance, RevisionOperationType
from app.services.collaboration_activity import record_revision_activity
from app.services.composition_embedding_invalidation import maybe_invalidate_project_embeddings
from app.services.composition_snapshot_encoding import (
    composition_snapshot_fingerprint,
    snapshot_fingerprint_log_prefix,
)
from app.services.musical_universe_realize import realize_mechanical_theme
from app.services.musical_universe_store import (
    MusicalUniverseRecord,
    get_universe,
    update_universe_document,
)
from app.services.project_history import ProjectHistoryValidationError, _ai_fields, _scope_payloads
from app.services.project_history_store import (
    ProjectRevisionConflictError,
    commit_durable_revision,
)
from app.services.project_store import ProjectNotFoundError, get_project

logger = logging.getLogger(__name__)

THEME_APPLY_OPERATION = RevisionOperationType.MUSICAL_UNIVERSE_THEME_APPLY.value


@dataclass(frozen=True)
class ThemeReuseRequest:
    destination_project_id: str
    destination_track_id: str
    destination_start_bar: int
    operation: str | None
    parameters: UniverseTransformParameters | None
    variant_id: str | None
    expected_universe_revision: int
    branch_id: str
    expected_active_branch_id: str
    expected_working_version: int
    expected_head_revision_id: str
    expected_source_fingerprint: str


@dataclass(frozen=True)
class ThemeReuseResult:
    universe: MusicalUniverseRecord
    destination_revision_id: str
    destination_fingerprint: str
    motif_id: str
    occurrence_id: str
    variant_id: str
    created_event_count: int


def reuse_theme(
    universe_id: str,
    theme_id: str,
    request: ThemeReuseRequest,
    *,
    actor_id: str | None = None,
    db_path: Path | str | None = None,
) -> ThemeReuseResult:
    """Load both stored scores, realize in memory, then commit score and usage."""
    path = Path(db_path) if db_path is not None else get_project_db_path()
    logger.debug(
        "Starting musical universe reuse",
        extra={
            "universe_id": universe_id,
            "theme_id": theme_id,
            "destination_project_id": request.destination_project_id,
            "operation": request.operation,
        },
    )
    record = get_universe(universe_id, db_path=path)
    theme = next((item for item in record.universe.themes if item.id == theme_id), None)
    if theme is None:
        _refuse("musical_universe_invalid", 422, field="theme_id")
    members = set(record.member_project_ids)
    source_project_id = theme.source.project_id
    if source_project_id not in members or request.destination_project_id not in members:
        _refuse("universe_project_not_member", 409, project_id=request.destination_project_id)

    source = _load_composition(source_project_id, path)
    destination = (
        source
        if source_project_id == request.destination_project_id
        else _load_composition(request.destination_project_id, path)
    )
    _require_original(source, theme)
    operation, parameters, variant_id, creating_variant = _resolve_variant(theme, request)
    assert_operation_parameters(operation, parameters)
    live_fingerprint = composition_snapshot_fingerprint(source)
    warnings: list[str] = []
    if live_fingerprint != theme.source_fingerprint:
        warnings.append("universe_source_fingerprint_drift")

    realized = realize_mechanical_theme(
        universe_id=universe_id,
        theme=theme,
        variant_id=variant_id,
        operation=operation,
        parameters=parameters,
        source=source,
        destination=destination,
        same_project=source_project_id == request.destination_project_id,
        destination_track_id=request.destination_track_id,
        destination_start_bar=request.destination_start_bar,
    )
    warnings.extend(code for code in realized.warning_codes if code not in warnings)
    warnings = warnings[:8]

    try:
        with get_connection(path) as conn:
            ranges_json, tracks_json, scope_meta = _scope_payloads(
                project_id=request.destination_project_id,
                branch_id=request.branch_id,
                source_fingerprint=request.expected_source_fingerprint,
                target_composition=realized.composition,
                declared_scope=None,
                conn=conn,
            )
            ai = _provenance(operation, parameters, warnings)
            ai_fields = _ai_fields(ai)
            summary = json.loads(ai_fields["summary_json"])
            if isinstance(summary, dict):
                summary.update(scope_meta)
            else:
                summary = dict(scope_meta)
            committed = commit_durable_revision(
                conn,
                request.destination_project_id,
                branch_id=request.branch_id,
                expected_active_branch_id=request.expected_active_branch_id,
                expected_working_version=request.expected_working_version,
                expected_head_revision_id=request.expected_head_revision_id,
                expected_source_fingerprint=request.expected_source_fingerprint,
                composition=realized.composition,
                operation_type=THEME_APPLY_OPERATION,
                ai_provider=ai_fields["ai_provider"],
                ai_model=ai_fields["ai_model"],
                user_instruction=ai_fields["user_instruction"],
                affected_ranges_json=ranges_json,
                affected_track_ids_json=tracks_json,
                summary_json=json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                actor_id=actor_id,
            )
            updated = _append_usage(
                record.universe,
                theme_id=theme.id,
                variant_id=variant_id,
                creating_variant=creating_variant,
                operation=operation,
                parameters=parameters,
                request=request,
                source_occurrence_id=theme.source.occurrence_id,
                destination_motif_id=realized.motif_id,
                destination_occurrence_id=realized.occurrence_id,
                destination_revision_id=committed.head_revision_id,
                source_fingerprint=live_fingerprint,
                warning_codes=warnings,
            )
            stored = update_universe_document(
                updated,
                expected_revision=request.expected_universe_revision,
                connection=conn,
            )
            maybe_invalidate_project_embeddings(
                request.destination_project_id,
                previous_fingerprint=request.expected_source_fingerprint,
                next_fingerprint=committed.working_fingerprint,
            )
            record_revision_activity(
                conn,
                project_id=request.destination_project_id,
                revision_id=committed.head_revision_id,
                operation_type=THEME_APPLY_OPERATION,
                actor_id=actor_id,
                ai_provider=ai_fields["ai_provider"],
                ai_model=ai_fields["ai_model"],
                revision_created=committed.revision_created,
            )
            from app.services.musical_dependency_capture import capture_theme_reuse_edge

            capture_theme_reuse_edge(
                conn,
                universe_id=universe_id,
                theme_id=theme.id,
                variant_id=variant_id,
                source_project_id=source_project_id,
                upstream_fingerprint=live_fingerprint,
                destination_project_id=request.destination_project_id,
                motif_id=realized.motif_id,
                occurrence_id=realized.occurrence_id,
            )
    except (ProjectRevisionConflictError, ProjectHistoryValidationError) as exc:
        logger.warning(
            "Musical universe destination conflict",
            extra={"code": "universe_destination_conflict"},
        )
        raise MusicalUniverseError(
            "universe_destination_conflict",
            MUSICAL_UNIVERSE_ERROR_CODES["universe_destination_conflict"],
            http_status=409,
        ) from exc
    except MusicalUniverseError:
        raise

    logger.info(
        "Musical universe reuse finished",
        extra={
            "universe_id": universe_id,
            "theme_id": theme.id,
            "destination_project_id": request.destination_project_id,
            "operation": operation,
            "created_event_count": realized.created_event_count,
            "destination_fingerprint_prefix": snapshot_fingerprint_log_prefix(
                committed.working_fingerprint
            ),
        },
    )
    return ThemeReuseResult(
        universe=stored,
        destination_revision_id=committed.head_revision_id,
        destination_fingerprint=committed.working_fingerprint,
        motif_id=realized.motif_id,
        occurrence_id=realized.occurrence_id,
        variant_id=variant_id,
        created_event_count=realized.created_event_count,
    )


def _load_composition(project_id: str, db_path: Path) -> CompositionV2:
    try:
        record = get_project(project_id, db_path=db_path)
    except ProjectNotFoundError as exc:
        _refuse("project_not_found", 404, project_id=project_id)
        raise AssertionError("unreachable") from exc
    if not record.composition_json:
        _refuse("universe_source_missing", 409)
    try:
        payload = json.loads(record.composition_json)
        return CompositionV2.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Musical universe reuse refused", extra={"code": "universe_source_missing"})
        raise MusicalUniverseError(
            "universe_source_missing",
            MUSICAL_UNIVERSE_ERROR_CODES["universe_source_missing"],
            http_status=409,
        ) from exc


def _require_original(source: CompositionV2, theme) -> None:
    motif = next((item for item in source.motifs if item.id == theme.source.motif_id), None)
    occurrence = None if motif is None else next(
        (item for item in motif.occurrences if item.id == theme.source.occurrence_id),
        None,
    )
    if motif is None or occurrence is None:
        _refuse("universe_source_missing", 409)
    if occurrence.relationship != "original":
        _refuse("universe_theme_source_not_original", 409)


def _resolve_variant(theme, request: ThemeReuseRequest) -> tuple[str, UniverseTransformParameters, str, bool]:
    if request.variant_id:
        variant = next((item for item in theme.variants if item.id == request.variant_id), None)
        if variant is None:
            _refuse("musical_universe_invalid", 422, field="variant_id")
        return variant.operation, variant.parameters, variant.id, False
    if request.operation is not None and request.operation not in MECHANICAL_OPERATIONS:
        _refuse("universe_operation_unsupported", 422, operation=request.operation)
    if request.operation is None or request.parameters is None:
        _refuse("musical_universe_invalid", 422, field="operation")
    if len(theme.variants) >= 32:
        _refuse("musical_universe_invalid", 422, field="variants")
    variant_id = _fresh("var_", {item.id for item in theme.variants})
    return request.operation, request.parameters, variant_id, True


def _append_usage(
    universe: MusicalUniverseV1,
    *,
    theme_id: str,
    variant_id: str,
    creating_variant: bool,
    operation: str,
    parameters: UniverseTransformParameters,
    request: ThemeReuseRequest,
    source_occurrence_id: str,
    destination_motif_id: str,
    destination_occurrence_id: str,
    destination_revision_id: str,
    source_fingerprint: str,
    warning_codes: list[str],
) -> MusicalUniverseV1:
    document = universe.model_copy(deep=True)
    theme = next(item for item in document.themes if item.id == theme_id)
    if len(theme.usages) >= 128:
        _refuse("musical_universe_invalid", 422, field="usages")
    if creating_variant:
        theme.variants.append(
            MusicalUniverseVariantV1(
                id=variant_id,
                label=theme.label[:120],
                operation=operation,  # type: ignore[arg-type]
                parameters=parameters,
                source_project_id=theme.source.project_id,
                source_motif_id=theme.source.motif_id,
                source_occurrence_id=theme.source.occurrence_id,
            )
        )
    theme.usages.append(
        MusicalUniverseUsageV1(
            id=_fresh("use_", {item.id for item in theme.usages}),
            variant_id=variant_id,
            destination_project_id=request.destination_project_id,
            destination_motif_id=destination_motif_id,
            destination_occurrence_id=destination_occurrence_id,
            source_occurrence_id=source_occurrence_id,
            destination_revision_id=destination_revision_id,
            operation=operation,  # type: ignore[arg-type]
            parameters=parameters,
            source_fingerprint=source_fingerprint,
            warning_codes=warning_codes[:8],
        )
    )
    return document


def _provenance(
    operation: str,
    parameters: UniverseTransformParameters,
    warnings: list[str],
) -> AiProvenance:
    numeric = {
        name: value
        for name in (
            "transpose_semitones",
            "time_scale_numerator",
            "time_scale_denominator",
            "sequence_steps",
            "sequence_interval_semitones",
            "sequence_step_ticks",
        )
        if isinstance((value := getattr(parameters, name)), int)
    }
    return AiProvenance(
        provider="musical-universe",
        operation=THEME_APPLY_OPERATION,
        generation_parameters={"operation": operation, **numeric},
        warning_codes=warnings[:8],
    )


def _fresh(prefix: str, existing: set[str]) -> str:
    for _ in range(8):
        candidate = f"{prefix}{secrets.token_hex(4)}"
        if candidate not in existing:
            return candidate
    _refuse("musical_universe_invalid", 422)


def _refuse(code: str, status: int, **details: object) -> None:
    if code in {"universe_identity_failed", "universe_source_missing"}:
        logger.warning("Musical universe reuse refused", extra={"code": code})
    raise MusicalUniverseError(
        code,
        MUSICAL_UNIVERSE_ERROR_CODES.get(code, "Musical universe reuse failed."),
        http_status=status,
        details={key: value for key, value in details.items() if isinstance(value, (str, int))},
    )
