"""Record dependency edges beside existing writers.

Does not realize notes. A caller-owned SQLite connection is not committed here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
from typing import Any

from app.composition_schemas import CompositionV2
from app.musical_dependency_schemas import MusicalDependencyError, SHA256_RE
from app.services.composition_snapshot_encoding import (
    composition_snapshot_fingerprint,
    motif_occurrence_fingerprint,
    snapshot_fingerprint_log_prefix,
)
from app.services.musical_dependency_store import record_dependency_edge
from app.services.project_history_store import ProjectHistoryError, get_snapshot_composition
from app.services.project_store import ProjectNotFoundError, get_project

logger = logging.getLogger(__name__)


def _new_edge_id() -> str:
    return f"dep_{secrets.token_hex(8)}"


def _log_recorded(
    edge_id: str,
    dependency_type: str,
    upstream_project_id: str | None,
    downstream_project_id: str | None,
    fingerprint: str,
    asset_id: str | None,
) -> None:
    extra = {
        "dependency_type": dependency_type,
        "edge_id": edge_id,
        "upstream_project_id": upstream_project_id,
        "downstream_project_id": downstream_project_id,
        "upstream_fingerprint_prefix": snapshot_fingerprint_log_prefix(fingerprint),
    }
    if asset_id:
        extra["asset_id"] = asset_id
    logger.info("Dependency capture recorded", extra=extra)


def _record(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    try:
        stored = record_dependency_edge(conn, payload)
    except MusicalDependencyError as exc:
        logger.warning("Dependency capture rolled back", extra={"code": exc.code})
        raise
    _log_recorded(
        stored.id,
        stored.dependency_type,
        stored.upstream_project_id,
        stored.downstream_project_id,
        stored.upstream_fingerprint,
        stored.downstream_asset_id,
    )


def capture_theme_reuse_edge(
    conn: sqlite3.Connection,
    *,
    universe_id: str,
    theme_id: str,
    variant_id: str,
    source_project_id: str,
    upstream_fingerprint: str,
    destination_project_id: str,
    motif_id: str,
    occurrence_id: str,
) -> None:
    """One ``variation_of`` edge for a mechanical universe reuse."""
    if not SHA256_RE.fullmatch(upstream_fingerprint):
        logger.warning(
            "Dependency capture skipped",
            extra={"code": "dependency_fingerprint_unusable"},
        )
        return
    _record(
        conn,
        {
            "schema_version": "musical.dependency.edge.v1",
            "id": _new_edge_id(),
            "dependency_type": "variation_of",
            "upstream_kind": "theme",
            "downstream_kind": "motif_occurrence",
            "universe_id": universe_id,
            "upstream_theme_id": theme_id,
            "variant_id": variant_id,
            "upstream_project_id": source_project_id,
            "downstream_project_id": destination_project_id,
            "motif_id": motif_id,
            "occurrence_id": occurrence_id,
            "source_motif_id": None,
            "upstream_fingerprint": upstream_fingerprint,
        },
    )


def _occurrence_index(composition: CompositionV2 | None) -> dict[str, Any]:
    found: dict[str, Any] = {}
    if composition is None:
        return found
    for motif in composition.motifs or []:
        for occurrence in motif.occurrences:
            found[occurrence.id] = (motif, occurrence)
    return found


def _events_for(composition: CompositionV2, occurrence: Any) -> list[Any] | None:
    by_id = {event.id: event for track in composition.tracks for event in track.events}
    ordered: list[Any] = []
    for event_id in occurrence.event_ids:
        event = by_id.get(event_id)
        if event is None:
            return None
        ordered.append(event)
    return ordered


def _capture_motif_edges(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    previous_fingerprint: str,
    composition: CompositionV2 | None,
) -> None:
    if composition is None:
        return
    try:
        previous = get_snapshot_composition(conn, previous_fingerprint)
    except ProjectHistoryError:
        logger.warning("Dependency capture rolled back", extra={"code": "dependency_not_found"})
        previous = None
    previous_ids = set(_occurrence_index(previous))
    current = _occurrence_index(composition)
    for occurrence_id, (motif, occurrence) in current.items():
        if occurrence_id in previous_ids:
            continue
        transform = occurrence.transform
        source_occurrence_id = None if transform is None else transform.source_occurrence_id
        if not source_occurrence_id:
            continue
        source = current.get(source_occurrence_id)
        if source is None:
            continue
        _source_motif, source_occurrence = source
        events = _events_for(composition, source_occurrence)
        if events is None:
            logger.warning(
                "Dependency capture skipped",
                extra={"code": "dependency_fingerprint_unusable"},
            )
            continue
        _record(
            conn,
            {
                "schema_version": "musical.dependency.edge.v1",
                "id": _new_edge_id(),
                "dependency_type": "motif_derived_from",
                "upstream_kind": "motif_occurrence",
                "downstream_kind": "motif_occurrence",
                "upstream_project_id": project_id,
                "downstream_project_id": project_id,
                "source_motif_id": _source_motif.id,
                "source_occurrence_id": source_occurrence_id,
                "motif_id": motif.id,
                "occurrence_id": occurrence_id,
                "upstream_fingerprint": motif_occurrence_fingerprint(events),
            },
        )


def _revision_fingerprint(conn: sqlite3.Connection, project_id: str, revision_id: str, fallback: str) -> str | None:
    if SHA256_RE.fullmatch(fallback):
        return fallback
    row = conn.execute(
        """
        SELECT snapshot_fingerprint
        FROM project_revisions
        WHERE id = ? AND project_id = ?
        """,
        (revision_id, project_id),
    ).fetchone()
    if row is None:
        return None
    fingerprint = str(row["snapshot_fingerprint"])
    if SHA256_RE.fullmatch(fingerprint):
        return fingerprint
    return None


def _capture_revision_edge(
    conn: sqlite3.Connection,
    *,
    dependency_type: str,
    project_id: str,
    upstream_revision_id: str,
    downstream_revision_id: str,
    fallback_fingerprint: str,
) -> None:
    fingerprint = _revision_fingerprint(conn, project_id, upstream_revision_id, fallback_fingerprint)
    if fingerprint is None or upstream_revision_id == downstream_revision_id:
        logger.warning(
            "Dependency capture skipped",
            extra={"code": "dependency_fingerprint_unusable"},
        )
        return
    _record(
        conn,
        {
            "schema_version": "musical.dependency.edge.v1",
            "id": _new_edge_id(),
            "dependency_type": dependency_type,
            "upstream_kind": "revision",
            "downstream_kind": "revision",
            "upstream_project_id": project_id,
            "downstream_project_id": project_id,
            "upstream_revision_id": upstream_revision_id,
            "downstream_revision_id": downstream_revision_id,
            "upstream_fingerprint": fingerprint,
        },
    )


def reference_project_ids(generation_parameters: dict[str, Any] | None) -> list[str]:
    """Project ids named as references. Order is stable and duplicate-free."""
    if not isinstance(generation_parameters, dict):
        return []
    found: list[str] = []

    def _add(value: object) -> None:
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned and cleaned not in found:
                found.append(cleaned)

    _add(generation_parameters.get("reference_project_id"))
    provenance = generation_parameters.get("reference_provenance")
    if isinstance(provenance, dict):
        _add(provenance.get("project_id"))
    features = generation_parameters.get("reference_features")
    if isinstance(features, list):
        for item in features:
            if isinstance(item, dict):
                _add(item.get("project_id"))
    return found


def _capture_reference_edges(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    generation_parameters: dict[str, Any] | None,
    db_path: Any,
) -> None:
    for reference_id in reference_project_ids(generation_parameters):
        if reference_id == project_id:
            continue
        try:
            record = get_project(reference_id, db_path=db_path)
        except ProjectNotFoundError:
            logger.warning(
                "Dependency reference unresolved",
                extra={"code": "dependency_reference_unresolved", "reference_project_id": reference_id},
            )
            continue
        if not record.composition_json:
            logger.warning(
                "Dependency reference unresolved",
                extra={"code": "dependency_reference_unresolved", "reference_project_id": reference_id},
            )
            continue
        try:
            composition = CompositionV2.model_validate(json.loads(record.composition_json))
        except (ValueError, TypeError, json.JSONDecodeError):
            logger.warning(
                "Dependency reference unresolved",
                extra={"code": "dependency_reference_unresolved", "reference_project_id": reference_id},
            )
            continue
        _record(
            conn,
            {
                "schema_version": "musical.dependency.edge.v1",
                "id": _new_edge_id(),
                "dependency_type": "reference_conditioned_by",
                "upstream_kind": "project",
                "downstream_kind": "project",
                "upstream_project_id": reference_id,
                "downstream_project_id": project_id,
                "upstream_fingerprint": composition_snapshot_fingerprint(composition),
            },
        )


def capture_revision_dependencies(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    operation_type: str,
    composition: CompositionV2 | None,
    expected_source_fingerprint: str,
    expected_head_revision_id: str,
    head_revision_id: str,
    ai_operation: str | None,
    generation_parameters: dict[str, Any] | None,
    db_path: Any,
) -> None:
    """Edges for motif, arrangement, variation, and reference commits."""
    logger.debug(
        "Dependency capture start",
        extra={"project_id": project_id, "operation_type": operation_type, "ai_operation": ai_operation},
    )
    if operation_type == "creative-motif-apply":
        _capture_motif_edges(
            conn,
            project_id=project_id,
            previous_fingerprint=expected_source_fingerprint,
            composition=composition,
        )
    elif operation_type == "arrangement-apply":
        _capture_revision_edge(
            conn,
            dependency_type="arrangement_of",
            project_id=project_id,
            upstream_revision_id=expected_head_revision_id,
            downstream_revision_id=head_revision_id,
            fallback_fingerprint=expected_source_fingerprint,
        )
    elif operation_type == "development-apply" and ai_operation == "vary_section":
        _capture_revision_edge(
            conn,
            dependency_type="variation_of",
            project_id=project_id,
            upstream_revision_id=expected_head_revision_id,
            downstream_revision_id=head_revision_id,
            fallback_fingerprint=expected_source_fingerprint,
        )
    _capture_reference_edges(
        conn,
        project_id=project_id,
        generation_parameters=generation_parameters,
        db_path=db_path,
    )


def capture_rendered_edge(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    asset_id: str,
    downstream_kind: str,
    source_fingerprint: str | None,
) -> None:
    """Project → neural job or stem set. A bad fingerprint leaves the job alone."""
    if not project_id or downstream_kind not in {"neural_render", "neural_stem_set"}:
        logger.warning(
            "Dependency fingerprint unusable",
            extra={"code": "dependency_fingerprint_unusable", "job_id": asset_id},
        )
        return
    fingerprint = source_fingerprint or ""
    if SHA256_RE.fullmatch(fingerprint) is None:
        logger.warning(
            "Dependency fingerprint unusable",
            extra={"code": "dependency_fingerprint_unusable", "job_id": asset_id},
        )
        return
    _record(
        conn,
        {
            "schema_version": "musical.dependency.edge.v1",
            "id": _new_edge_id(),
            "dependency_type": "rendered_from",
            "upstream_kind": "project",
            "downstream_kind": downstream_kind,
            "upstream_project_id": project_id,
            "downstream_project_id": project_id,
            "downstream_asset_id": asset_id,
            "upstream_fingerprint": fingerprint,
        },
    )


def capture_transcribed_edge(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    asset_id: str,
    payload: bytes,
) -> None:
    """Hash the bound source bytes. The stored prefix is not the upstream fingerprint."""
    fingerprint = hashlib.sha256(payload).hexdigest()
    _record(
        conn,
        {
            "schema_version": "musical.dependency.edge.v1",
            "id": _new_edge_id(),
            "dependency_type": "transcribed_from",
            "upstream_kind": "audio_asset",
            "downstream_kind": "project",
            "downstream_project_id": project_id,
            "downstream_asset_id": asset_id,
            "upstream_fingerprint": fingerprint,
        },
    )
