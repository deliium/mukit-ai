"""Resolve musical-universe references against member compositions.

Loads scores through the project store when the caller does not pass them.
Returns findings and does not write the universe or any composition.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from app.composition_schemas import CompositionV2
from app.musical_universe_schemas import (
    MusicalUniverseFindingV1,
    MusicalUniverseThemeV1,
    MusicalUniverseV1,
    UniverseHarmonyRefV1,
    UniverseMotifRefV1,
    UniverseTrackRefV1,
)
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.instrument_catalog import get_catalog
from app.services.project_store import ProjectNotFoundError, get_project

logger = logging.getLogger(__name__)


def bind_theme_source(
    source: UniverseMotifRefV1,
    member_project_ids: list[str],
    *,
    db_path: Path | str | None = None,
) -> list[MusicalUniverseFindingV1]:
    """Check one proposed theme source. Does not write the universe or the score."""
    findings: list[MusicalUniverseFindingV1] = []
    cache: dict[str, CompositionV2 | None] = {}
    _check_motif_ref(
        source,
        set(member_project_ids),
        cache,
        findings,
        db_path=db_path,
        require_original=True,
        target_id=source.occurrence_id,
    )
    logger.info(
        "Musical universe theme source bind finished",
        extra={
            "error_count": sum(1 for item in findings if item.severity == "error"),
            "warning_count": sum(1 for item in findings if item.severity == "warning"),
        },
    )
    return findings


def bind_universe_references(
    universe: MusicalUniverseV1,
    member_project_ids: Sequence[str],
    *,
    compositions: Mapping[str, CompositionV2] | None = None,
    db_path: Path | str | None = None,
) -> list[MusicalUniverseFindingV1]:
    """Check motif, harmony, track, and catalog references on the current scores."""
    logger.debug(
        "Binding musical universe references",
        extra={
            "universe_id": universe.id,
            "member_count": len(member_project_ids),
            "entity_count": len(universe.entities),
            "theme_count": len(universe.themes),
            "preloaded_count": 0 if compositions is None else len(compositions),
        },
    )
    members = set(member_project_ids)
    catalog_ids = {profile.instrument_id for profile in get_catalog().profiles}
    cache: dict[str, CompositionV2 | None] = {}
    if compositions is not None:
        cache.update(compositions)
    findings: list[MusicalUniverseFindingV1] = []

    for entity in universe.entities:
        for ref in entity.motif_refs:
            _check_motif_ref(ref, members, cache, findings, db_path=db_path, require_original=False)
        for ref in entity.harmony_refs:
            _check_harmony_ref(ref, members, cache, findings, db_path=db_path)
        for ref in entity.track_refs:
            _check_track_ref(ref, members, cache, findings, db_path=db_path)
        for instrument_id in entity.catalog_instrument_ids:
            if instrument_id not in catalog_ids:
                _append(
                    findings,
                    "universe_instrument_unknown",
                    "error",
                    target_id=instrument_id,
                    message="Catalog instrument id is not in the arrangement catalog",
                )
    for theme in universe.themes:
        _check_theme(theme, members, cache, findings, db_path=db_path)

    errors = sum(1 for item in findings if item.severity == "error")
    warnings = sum(1 for item in findings if item.severity == "warning")
    logger.info(
        "Musical universe bind finished",
        extra={"error_count": errors, "warning_count": warnings, "universe_id": universe.id},
    )
    for finding in findings:
        logger.debug(
            "Musical universe bind finding",
            extra={"code": finding.code, "target_id": finding.target_id},
        )
    return findings


def _check_theme(
    theme: MusicalUniverseThemeV1,
    members: set[str],
    cache: dict[str, CompositionV2 | None],
    findings: list[MusicalUniverseFindingV1],
    *,
    db_path: Path | str | None,
) -> None:
    source = theme.source
    resolved = _check_motif_ref(
        source,
        members,
        cache,
        findings,
        db_path=db_path,
        require_original=True,
        target_id=theme.id,
    )
    if not resolved:
        return
    composition = cache.get(source.project_id)
    if composition is None:
        return
    live = composition_snapshot_fingerprint(composition)
    if live != theme.source_fingerprint:
        _append(
            findings,
            "universe_source_fingerprint_drift",
            "warning",
            target_id=theme.id,
            message="Source fingerprint differs from the stored theme fingerprint",
        )


def _check_motif_ref(
    ref: UniverseMotifRefV1,
    members: set[str],
    cache: dict[str, CompositionV2 | None],
    findings: list[MusicalUniverseFindingV1],
    *,
    db_path: Path | str | None,
    require_original: bool,
    target_id: str | None = None,
) -> bool:
    target = target_id or ref.occurrence_id
    if ref.project_id not in members:
        _append(
            findings,
            "universe_project_not_member",
            "error",
            target_id=target,
            message="Project is not a member of this universe",
        )
        return False
    composition = _composition(ref.project_id, cache, db_path=db_path)
    if composition is None:
        _append(
            findings,
            "universe_source_missing",
            "error",
            target_id=target,
            message="The theme source occurrence does not resolve",
        )
        return False
    motif = next((item for item in composition.motifs if item.id == ref.motif_id), None)
    occurrence = None if motif is None else next(
        (item for item in motif.occurrences if item.id == ref.occurrence_id),
        None,
    )
    if motif is None or occurrence is None or not _events_resolve(composition, occurrence.track_id, occurrence.event_ids):
        _append(
            findings,
            "universe_source_missing",
            "error",
            target_id=target,
            message="The theme source occurrence does not resolve",
        )
        return False
    if require_original and occurrence.relationship != "original":
        _append(
            findings,
            "universe_theme_source_not_original",
            "error",
            target_id=target,
            message="Theme source occurrence must be original",
        )
        return False
    return True


def _check_harmony_ref(
    ref: UniverseHarmonyRefV1,
    members: set[str],
    cache: dict[str, CompositionV2 | None],
    findings: list[MusicalUniverseFindingV1],
    *,
    db_path: Path | str | None,
) -> None:
    target = f"{ref.project_id}:{ref.start_tick}"
    if ref.project_id not in members:
        _append(
            findings,
            "universe_project_not_member",
            "error",
            target_id=target[:120],
            message="Project is not a member of this universe",
        )
        return
    composition = _composition(ref.project_id, cache, db_path=db_path)
    if composition is None or not any(item.start_tick == ref.start_tick for item in composition.harmony):
        _append(
            findings,
            "universe_harmony_missing",
            "error",
            target_id=target[:120],
            message="Harmony start_tick does not match a member score",
        )


def _check_track_ref(
    ref: UniverseTrackRefV1,
    members: set[str],
    cache: dict[str, CompositionV2 | None],
    findings: list[MusicalUniverseFindingV1],
    *,
    db_path: Path | str | None,
) -> None:
    if ref.project_id not in members:
        _append(
            findings,
            "universe_project_not_member",
            "error",
            target_id=ref.track_id,
            message="Project is not a member of this universe",
        )
        return
    composition = _composition(ref.project_id, cache, db_path=db_path)
    if composition is None or not any(track.id == ref.track_id for track in composition.tracks):
        _append(
            findings,
            "universe_track_missing",
            "error",
            target_id=ref.track_id,
            message="Track id does not exist on that project",
        )


def _events_resolve(composition: CompositionV2, track_id: str, event_ids: list[str]) -> bool:
    track = next((item for item in composition.tracks if item.id == track_id), None)
    if track is None:
        return False
    present = {event.id for event in track.events if event.id}
    return all(event_id in present for event_id in event_ids)


def _composition(
    project_id: str,
    cache: dict[str, CompositionV2 | None],
    *,
    db_path: Path | str | None,
) -> CompositionV2 | None:
    if project_id in cache:
        return cache[project_id]
    logger.debug("Loading member composition for bind", extra={"project_id": project_id})
    try:
        record = get_project(project_id, db_path=db_path)
    except ProjectNotFoundError:
        cache[project_id] = None
        return None
    if not record.composition_json:
        cache[project_id] = None
        return None
    try:
        payload = json.loads(record.composition_json)
        composition = CompositionV2.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "Member composition failed validation during bind",
            extra={"project_id": project_id, "code": "universe_source_missing", "error_type": type(exc).__name__},
        )
        cache[project_id] = None
        return None
    cache[project_id] = composition
    return composition


def _append(
    findings: list[MusicalUniverseFindingV1],
    code: str,
    severity: str,
    *,
    target_id: str | None,
    message: str,
) -> None:
    findings.append(
        MusicalUniverseFindingV1(
            code=code,
            severity=severity,  # type: ignore[arg-type]
            target_id=target_id,
            message=message[:200],
        )
    )
