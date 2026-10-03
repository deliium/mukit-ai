"""Synchronous soundtrack asset-pack generate and partial regenerate.

Creates independent projects per slot, joins one Musical Universe, seeds Theme A,
mechanically reuses into non-seed slots, and optionally scaffolds adaptive scores.
Never stores note events on the pack document. Never imports from ``ai_agents/``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from app.adaptive_score_schemas import AdaptiveScoreError, parse_adaptive_score
from app.asset_pack_schemas import (
    AssetPackError,
    AssetPackPlanSlotV1,
    AssetPackPlanV1,
    AssetPackPropagateOp,
)
from app.composition_schemas import CompositionV2, reconcile_motifs_for_removed_event_ids
from app.db.connection import get_connection, get_project_db_path
from app.musical_universe_schemas import (
    MusicalUniverseError,
    UniverseTransformParameters,
)
from app.services.adaptive_score_store import create_score
from app.services.asset_pack_brief import (
    build_slot_creative_brief,
    resolve_pack_profile_merge,
)
from app.services.asset_pack_store import (
    AssetPackRecord,
    get_pack,
    update_pack_cas,
    upsert_slot_status,
)
from app.services.autonomous_composer import execute_autonomous_run, prepare_run, run_view
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.musical_universe_bind import bind_theme_source
from app.services.musical_universe_commands import apply_command_payload
from app.services.musical_universe_reuse import ThemeReuseRequest, reuse_theme
from app.services import musical_universe_store as universe_store
from app.project_history_schemas import RevisionOperationType
from app.services.project_history_store import (
    ProjectRevisionConflictError,
    commit_durable_revision,
)
from app.services.project_store import get_project

logger = logging.getLogger(__name__)

_ADAPTIVE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _truncate_id(value: str | None, *, n: int = 16) -> str:
    if not value:
        return ""
    return value[:n]


def _load_composition(project_id: str, db_path: Path) -> CompositionV2:
    record = get_project(project_id, db_path=db_path)
    if not record.composition_json:
        raise AssetPackError("asset_pack_generate_failed", "Project has no composition")
    return CompositionV2.model_validate(json.loads(record.composition_json))


def _branch_cas(db_path: Path, project_id: str) -> dict[str, Any]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT b.id, b.working_version, b.head_revision_id, b.working_fingerprint,
                   p.active_branch_id
            FROM project_branches AS b
            JOIN projects AS p ON p.id = b.project_id AND p.active_branch_id = b.id
            WHERE b.project_id = ?
            """,
            (project_id,),
        ).fetchone()
    if row is None:
        raise AssetPackError("asset_pack_generate_failed", "Project branch state missing")
    return {
        "branch_id": str(row["id"]),
        "expected_active_branch_id": str(row["active_branch_id"]),
        "expected_working_version": int(row["working_version"]),
        "expected_head_revision_id": str(row["head_revision_id"]),
        "expected_source_fingerprint": str(row["working_fingerprint"]),
    }


def first_pitched_track_id(composition: CompositionV2) -> str | None:
    """Prefer melody/lead; else first non-drum pitched track."""
    fallback: str | None = None
    for track in composition.tracks:
        if track.is_drum or track.role in {"drums", "percussion"}:
            continue
        if track.role in {"melody", "lead"}:
            return track.id
        if fallback is None:
            fallback = track.id
    return fallback


def _reuse_track_candidates(composition: CompositionV2) -> list[str]:
    """Prefer melody/lead landing track, then other pitched non-drum tracks."""
    preferred = first_pitched_track_id(composition)
    pitched = [
        track.id
        for track in composition.tracks
        if not track.is_drum and track.role not in {"drums", "percussion"}
    ]
    if not preferred:
        return pitched
    return [preferred] + [tid for tid in pitched if tid != preferred]


def discover_seed_motif(
    composition: CompositionV2,
    *,
    motif_label: str,
) -> tuple[str, str] | None:
    """Return (motif_id, original_occurrence_id) or None."""
    for motif in composition.motifs or []:
        if (motif.label or "").strip() != motif_label.strip():
            continue
        for occurrence in motif.occurrences or []:
            if occurrence.relationship == "original":
                return motif.id, occurrence.id
    return None


def _sanitize_adaptive_state_id(label: str) -> str:
    token = label.strip()
    if _ADAPTIVE_ID_RE.fullmatch(token):
        return token
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", token).strip("_")
    if not cleaned:
        cleaned = "state"
    if cleaned[0].isdigit():
        cleaned = f"s_{cleaned}"
    return cleaned[:64]


def _material_for_composition(composition: CompositionV2) -> dict[str, Any] | None:
    for section in composition.sections or []:
        section_id = getattr(section, "id", None)
        if isinstance(section_id, str) and section_id.strip():
            return {"kind": "section", "section_id": section_id.strip()}
    end_bar = int(composition.bar_count or 1)
    if end_bar < 1:
        return None
    return {"kind": "bar_range", "start_bar": 1, "end_bar": end_bar}


async def _run_slot_autonomous(
    *,
    brief,
    project_id: str | None,
    include_rendering: bool,
    seed: int,
    db_path: Path,
) -> tuple[str, str, str | None]:
    """Return (project_id, run_id, head_revision_id)."""
    operation_run_id = str(uuid.uuid4())
    cas: dict[str, Any] = {}
    if project_id is not None:
        cas = _branch_cas(db_path, project_id)
    record = prepare_run(
        brief,
        project_id=project_id,
        operation_run_id=operation_run_id,
        include_rendering=include_rendering,
        seed=seed,
        expected_working_version=cas.get("expected_working_version"),
        expected_head_revision_id=cas.get("expected_head_revision_id"),
        expected_source_fingerprint=cas.get("expected_source_fingerprint"),
        autonomy_mode="autonomous",
        db_path=db_path,
    )
    if record.status == "pending":
        await execute_autonomous_run(
            record.id,
            db_path=db_path,
            render_approval="required",
            max_agent_operations=None,
        )
    view = run_view(record.id, db_path=db_path)
    if view.status not in {"completed", "awaiting_approval"}:
        logger.error(
            "[AssetPackGenerate.run] slot autonomous failed",
            extra={
                "run_id": _truncate_id(view.run_id),
                "status": view.status,
                "failure_code": view.failure_code or "",
            },
        )
        raise AssetPackError(
            "asset_pack_generate_failed",
            f"Autonomous run ended with status {view.status}",
        )
    return view.project_id, view.run_id, view.head_revision_id


def _ensure_universe(
    *,
    pack_title: str,
    seed_project_id: str,
    existing_universe_id: str | None,
    db_path: Path,
):
    if existing_universe_id:
        record = universe_store.get_universe(existing_universe_id, db_path=db_path)
        if seed_project_id not in record.member_project_ids:
            record = universe_store.add_member(
                existing_universe_id,
                seed_project_id,
                db_path=db_path,
            )
        return record
    name = f"{pack_title} Universe"[:120]
    return universe_store.create_universe(name, seed_project_id, db_path=db_path)


def _bind_theme_a(
    *,
    universe_record,
    seed_project_id: str,
    motif_id: str,
    occurrence_id: str,
    composition: CompositionV2,
    motif_label: str,
    db_path: Path,
):
    from app.musical_universe_schemas import UniverseMotifRefV1

    fingerprint = composition_snapshot_fingerprint(composition)
    source = {
        "project_id": seed_project_id,
        "motif_id": motif_id,
        "occurrence_id": occurrence_id,
    }
    findings = bind_theme_source(
        UniverseMotifRefV1.model_validate(source),
        list(universe_record.member_project_ids),
        db_path=db_path,
    )
    if any(item.severity == "error" for item in findings):
        raise AssetPackError("asset_pack_seed_motif_missing")

    document = universe_record.universe
    if not document.entities:
        document = apply_command_payload(
            document,
            {"op": "create_entity", "kind": "concept", "label": motif_label},
        )
        universe_record = universe_store.update_universe_document(
            document,
            expected_revision=universe_record.document_revision,
            db_path=db_path,
        )
        document = universe_record.universe

    if not document.themes:
        document = apply_command_payload(
            document,
            {
                "op": "create_theme",
                "entity_id": document.entities[0].id,
                "label": motif_label,
                "source": source,
                "source_fingerprint": fingerprint,
                "source_checked": True,
            },
        )
        universe_record = universe_store.update_universe_document(
            document,
            expected_revision=universe_record.document_revision,
            db_path=db_path,
        )
    logger.debug(
        "[AssetPackGenerate.theme] Theme A bound",
        extra={
            "universe_id": _truncate_id(universe_record.id),
            "theme_count": len(universe_record.universe.themes),
        },
    )
    return universe_record


def _reuse_start_bar(composition: CompositionV2, propagate: AssetPackPropagateOp) -> int:
    """Place theme where it will not overlap opening material.

    Default policy start bar is 1, but autonomous already fills early bars.
    Prefer the first outro section; else append at bar_count.
    """
    for section in composition.sections or []:
        if getattr(section, "type", None) == "outro" and int(section.start_bar or 0) >= 1:
            return int(section.start_bar)
    end = int(composition.bar_count or 1)
    return max(int(propagate.destination_start_bar or 1), end)


def _clear_track_for_reuse(
    project_id: str,
    track_id: str,
    *,
    db_path: Path,
    operation_type: str = RevisionOperationType.ASSET_PACK_GENERATE.value,
) -> CompositionV2:
    """Clear one track so mechanical Theme A reuse has room (no overlap)."""
    composition = _load_composition(project_id, db_path)
    removed_event_ids: set[str] = set()
    new_tracks = []
    for track in composition.tracks:
        if track.id == track_id:
            removed_event_ids = {event.id for event in track.events if getattr(event, "id", None)}
            new_tracks.append(track.model_copy(update={"events": []}))
        else:
            new_tracks.append(track)
    motifs = composition.motifs or []
    if removed_event_ids and motifs:
        reconciled = reconcile_motifs_for_removed_event_ids(motifs, removed_event_ids)
        motifs = reconciled.motifs
    cleared = composition.model_copy(update={"tracks": new_tracks, "motifs": motifs})
    cas = _branch_cas(db_path, project_id)
    with get_connection(db_path) as conn:
        try:
            committed = commit_durable_revision(
                conn,
                project_id,
                branch_id=str(cas["branch_id"]),
                expected_active_branch_id=str(cas["expected_active_branch_id"]),
                expected_working_version=int(cas["expected_working_version"]),
                expected_head_revision_id=str(cas["expected_head_revision_id"]),
                expected_source_fingerprint=str(cas["expected_source_fingerprint"]),
                composition=cleared,
                operation_type=operation_type,
            )
            from app.services.content_provenance_capture import capture_revision_provenance

            capture_revision_provenance(
                conn,
                project_id=project_id,
                revision_id=committed.head_revision_id,
                operation_type=operation_type,
                fingerprint=committed.working_fingerprint,
                prior_revision_id=str(cas["expected_head_revision_id"]),
                revision_created=committed.revision_created,
            )
        except ProjectRevisionConflictError as exc:
            raise AssetPackError("asset_pack_conflict", "CAS failed clearing reuse track") from exc
    logger.debug(
        "[AssetPackGenerate.reuse] cleared track for theme landing",
        extra={"project_id": _truncate_id(project_id), "track_id": track_id},
    )
    return _load_composition(project_id, db_path)


def _reuse_into_slot(
    *,
    universe_record,
    theme_id: str,
    destination_project_id: str,
    propagate: AssetPackPropagateOp,
    db_path: Path,
    clear_operation_type: str = RevisionOperationType.ASSET_PACK_GENERATE.value,
):
    composition = _load_composition(destination_project_id, db_path)
    track_ids = _reuse_track_candidates(composition)
    if not track_ids:
        raise AssetPackError(
            "asset_pack_generate_failed",
            "Destination has no pitched track for theme reuse",
        )
    # Clear the preferred (melody/lead) landing lane so reuse has empty space.
    landing_track = track_ids[0]
    composition = _clear_track_for_reuse(
        destination_project_id,
        landing_track,
        db_path=db_path,
        operation_type=clear_operation_type,
    )
    track_ids = [landing_track] + [tid for tid in track_ids if tid != landing_track]
    start_bar = _reuse_start_bar(composition, propagate)
    start_candidates = sorted(
        {
            start_bar,
            1,
            max(1, int(composition.bar_count or 1) // 2),
            max(1, int(composition.bar_count or 1)),
        }
    )
    def _params_for(operation: str) -> UniverseTransformParameters:
        # reuse_theme requires a parameters object even when the bag is empty (repeat).
        if operation == "transpose":
            return UniverseTransformParameters(
                transpose_semitones=int(propagate.transpose_semitones or 0)
            )
        return UniverseTransformParameters()

    ops: list[tuple[str, UniverseTransformParameters]] = [
        (propagate.operation, _params_for(propagate.operation))
    ]
    if propagate.operation != "repeat":
        ops.append(("repeat", _params_for("repeat")))

    last_error: Exception | None = None
    for operation, parameters in ops:
        for track_id in track_ids:
            for bar in start_candidates:
                branch = _branch_cas(db_path, destination_project_id)
                live_universe = universe_store.get_universe(universe_record.id, db_path=db_path)
                try:
                    result = reuse_theme(
                        live_universe.id,
                        theme_id,
                        ThemeReuseRequest(
                            destination_project_id=destination_project_id,
                            destination_track_id=track_id,
                            destination_start_bar=bar,
                            operation=operation,
                            parameters=parameters,
                            variant_id=None,
                            expected_universe_revision=live_universe.document_revision,
                            branch_id=str(branch["branch_id"]),
                            expected_active_branch_id=str(branch["expected_active_branch_id"]),
                            expected_working_version=int(branch["expected_working_version"]),
                            expected_head_revision_id=str(branch["expected_head_revision_id"]),
                            expected_source_fingerprint=str(branch["expected_source_fingerprint"]),
                        ),
                        db_path=db_path,
                    )
                    logger.debug(
                        "[AssetPackGenerate.reuse] theme reused",
                        extra={
                            "operation": operation,
                            "transpose_semitones": propagate.transpose_semitones
                            if operation == "transpose"
                            else None,
                            "destination_project_id": _truncate_id(destination_project_id),
                            "destination_start_bar": bar,
                        },
                    )
                    return result
                except MusicalUniverseError as exc:
                    last_error = exc
                    logger.debug(
                        "[AssetPackGenerate.reuse] attempt refused",
                        extra={
                            "code": exc.code,
                            "track_id": track_id,
                            "start_bar": bar,
                            "operation": operation,
                        },
                    )
                    continue
    if last_error is not None:
        raise last_error
    raise AssetPackError("asset_pack_generate_failed", "Theme reuse failed")


def _maybe_adaptive_scaffold(
    *,
    project_id: str,
    slot: AssetPackPlanSlotV1,
    enabled: bool,
    db_path: Path,
    existing_adaptive_score_id: str | None = None,
) -> str | None:
    if not enabled:
        return None
    # Partial regen: keep the prior scaffold id rather than colliding on name.
    if existing_adaptive_score_id:
        logger.debug(
            "[AssetPackGenerate.adaptive] scaffold reused",
            extra={
                "adaptive_score_id": _truncate_id(existing_adaptive_score_id),
                "slot_id": slot.slot_id,
            },
        )
        return existing_adaptive_score_id
    try:
        from app.services.adaptive_score_store import list_scores

        existing = list_scores(project_id, db_path=db_path)
        if existing:
            default = next((item for item in existing if item.is_default), existing[0])
            logger.debug(
                "[AssetPackGenerate.adaptive] scaffold reused from project",
                extra={
                    "adaptive_score_id": _truncate_id(default.id),
                    "slot_id": slot.slot_id,
                },
            )
            return default.id
        composition = _load_composition(project_id, db_path)
        material = _material_for_composition(composition)
        if material is None:
            logger.warning(
                "[AssetPackGenerate.adaptive] scaffold skipped",
                extra={
                    "slot_id": slot.slot_id,
                    "code": "adaptive_scaffold_material_missing",
                },
            )
            return None
        state_id = _sanitize_adaptive_state_id(slot.adaptive_label)
        payload = {
            "schema_version": "adaptive.score.v1",
            "name": f"{slot.label} adaptive",
            "initial_state_id": state_id,
            "default_state_id": state_id,
            "fallback": {
                "on_missing_material": "hold",
                "on_invalid_transition": "stay",
                "on_unresolved_condition": "stay",
            },
            "states": [
                {
                    "id": state_id,
                    "name": slot.label,
                    "intensity": 0.5,
                    "material": material,
                    "transition_ids": [],
                }
            ],
            "variants": [],
            "transitions": [],
            "layers": [],
            "stingers": [],
        }
        score = parse_adaptive_score(payload)
        record = create_score(project_id, score, is_default=True, db_path=db_path)
        logger.debug(
            "[AssetPackGenerate.adaptive] scaffold created",
            extra={
                "adaptive_score_id": _truncate_id(record.id),
                "material_kind": material["kind"],
                "slot_id": slot.slot_id,
            },
        )
        return record.id
    except (AdaptiveScoreError, AssetPackError, ValueError) as exc:
        logger.warning(
            "[AssetPackGenerate.adaptive] scaffold skipped",
            extra={
                "slot_id": slot.slot_id,
                "code": getattr(exc, "code", type(exc).__name__),
            },
        )
        return None


async def generate_asset_pack(
    pack_id: str,
    *,
    expected_plan_digest: str,
    expected_revision: int,
    db_path: Path | None = None,
    slot_ids: list[str] | None = None,
) -> AssetPackRecord:
    """Generate pending (or selected) slots synchronously in deterministic order."""
    path = db_path or get_project_db_path()
    record = get_pack(pack_id, db_path=path)
    plan = record.plan
    if plan.plan_digest != expected_plan_digest.strip().lower():
        logger.warning(
            "[AssetPackGenerate] plan digest mismatch",
            extra={"pack_id": _truncate_id(pack_id), "code": "asset_pack_plan_mismatch"},
        )
        raise AssetPackError("asset_pack_plan_mismatch")
    if record.document_revision != expected_revision:
        raise AssetPackError("asset_pack_conflict")

    selected = set(slot_ids) if slot_ids is not None else None
    if selected is not None:
        known = {slot.slot_id for slot in plan.slots}
        unknown = sorted(selected - known)
        if unknown:
            raise AssetPackError("asset_pack_slot_unknown")

    logger.info(
        "[AssetPackGenerate] start",
        extra={
            "pack_id": _truncate_id(pack_id),
            "digest_prefix": plan.plan_digest[:12],
            "slot_filter_count": len(selected) if selected is not None else 0,
        },
    )

    merge = resolve_pack_profile_merge(plan, db_path=path)
    record = update_pack_cas(
        pack_id,
        expected_revision=expected_revision,
        status="generating",
        db_path=path,
    )

    ordered_slots = sorted(plan.slots, key=lambda s: s.slot_id)
    seed_slot_id = plan.theme_policy.seed_slot_id
    # Process seed first when generating/regenerating it or when universe needs Theme A.
    seed_slot = next(s for s in plan.slots if s.slot_id == seed_slot_id)
    non_seed = [s for s in ordered_slots if s.slot_id != seed_slot_id]

    universe_record = None
    theme_id: str | None = None
    failures = 0
    completions = 0

    slot_by_id = {s.slot_id: s for s in record.slots}
    seed_row = slot_by_id.get(seed_slot_id)

    need_seed = selected is None or seed_slot_id in selected
    seed_project_id = seed_row.project_id if seed_row else None

    if need_seed or (seed_project_id is None and selected is not None):
        # Full generate always runs seed first; regen of non-seed requires existing seed.
        pass

    clear_op = (
        RevisionOperationType.ASSET_PACK_SLOT_REGENERATE.value
        if selected is not None
        else RevisionOperationType.ASSET_PACK_GENERATE.value
    )

    async def _process_slot(
        slot: AssetPackPlanSlotV1,
        *,
        existing_project_id: str | None,
        do_reuse: bool,
    ) -> None:
        nonlocal universe_record, theme_id, failures, completions, seed_project_id
        if selected is not None and slot.slot_id not in selected:
            return
        upsert_slot_status(pack_id, slot.slot_id, status="running", db_path=path)
        try:
            built = build_slot_creative_brief(plan, slot, merge=merge, db_path=path)
            project_id, run_id, head_revision_id = await _run_slot_autonomous(
                brief=built.brief,
                project_id=existing_project_id,
                include_rendering=plan.include_rendering,
                seed=int(plan.seed or 0),
                db_path=path,
            )
            if slot.slot_id == seed_slot_id:
                seed_project_id = project_id
                universe_record = _ensure_universe(
                    pack_title=plan.title,
                    seed_project_id=project_id,
                    existing_universe_id=record.universe_id or (
                        universe_record.id if universe_record else None
                    ),
                    db_path=path,
                )
                composition = _load_composition(project_id, path)
                motif = discover_seed_motif(
                    composition,
                    motif_label=plan.constraints.motif_label,
                )
                if motif is None:
                    raise AssetPackError("asset_pack_seed_motif_missing")
                motif_id, occurrence_id = motif
                universe_record = _bind_theme_a(
                    universe_record=universe_record,
                    seed_project_id=project_id,
                    motif_id=motif_id,
                    occurrence_id=occurrence_id,
                    composition=composition,
                    motif_label=plan.constraints.motif_label,
                    db_path=path,
                )
                theme_id = universe_record.universe.themes[0].id
            else:
                if universe_record is None:
                    if not seed_project_id:
                        raise AssetPackError(
                            "asset_pack_generate_failed",
                            "Seed project missing before non-seed generate",
                        )
                    universe_record = _ensure_universe(
                        pack_title=plan.title,
                        seed_project_id=seed_project_id,
                        existing_universe_id=record.universe_id,
                        db_path=path,
                    )
                    if not universe_record.universe.themes:
                        raise AssetPackError("asset_pack_seed_motif_missing")
                    theme_id = universe_record.universe.themes[0].id
                if project_id not in universe_record.member_project_ids:
                    universe_record = universe_store.add_member(
                        universe_record.id,
                        project_id,
                        db_path=path,
                    )
                if do_reuse and theme_id:
                    prop = plan.theme_policy.propagate.get(slot.slot_id)
                    if prop is None:
                        prop = AssetPackPropagateOp.model_validate({"operation": "repeat"})
                    reuse_result = _reuse_into_slot(
                        universe_record=universe_record,
                        theme_id=theme_id,
                        destination_project_id=project_id,
                        propagate=prop,
                        db_path=path,
                        clear_operation_type=clear_op,
                    )
                    universe_record = reuse_result.universe
                    head_revision_id = reuse_result.destination_revision_id

            prior_row = slot_by_id.get(slot.slot_id)
            adaptive_id = _maybe_adaptive_scaffold(
                project_id=project_id,
                slot=slot,
                enabled=plan.include_adaptive_scaffolds,
                db_path=path,
                existing_adaptive_score_id=(
                    prior_row.adaptive_score_id if prior_row else None
                ),
            )
            upsert_slot_status(
                pack_id,
                slot.slot_id,
                status="completed",
                project_id=project_id,
                autonomous_run_id=run_id,
                adaptive_score_id=adaptive_id,
                head_revision_id=head_revision_id,
                db_path=path,
            )
            completions += 1
            logger.info(
                "[AssetPackGenerate] slot completed",
                extra={
                    "pack_id": _truncate_id(pack_id),
                    "slot_id": slot.slot_id,
                    "project_id": _truncate_id(project_id),
                    "status": "completed",
                },
            )
        except AssetPackError as exc:
            failures += 1
            upsert_slot_status(pack_id, slot.slot_id, status="failed", db_path=path)
            logger.error(
                "[AssetPackGenerate] slot failed",
                extra={
                    "pack_id": _truncate_id(pack_id),
                    "slot_id": slot.slot_id,
                    "code": exc.code,
                },
            )
            if exc.code == "asset_pack_seed_motif_missing" and slot.slot_id == seed_slot_id:
                raise
        except Exception as exc:
            failures += 1
            upsert_slot_status(pack_id, slot.slot_id, status="failed", db_path=path)
            logger.exception(
                "[AssetPackGenerate] slot failed slot_id=%s exc_type=%s exc=%s",
                slot.slot_id,
                type(exc).__name__,
                str(exc)[:200],
            )

    try:
        # Seed first when needed.
        if need_seed or seed_project_id is None:
            await _process_slot(
                seed_slot,
                existing_project_id=seed_row.project_id if seed_row and selected else None,
                do_reuse=False,
            )
        else:
            # Ensure universe/theme loaded for non-seed-only regen.
            if seed_project_id:
                universe_record = _ensure_universe(
                    pack_title=plan.title,
                    seed_project_id=seed_project_id,
                    existing_universe_id=record.universe_id,
                    db_path=path,
                )
                if universe_record.universe.themes:
                    theme_id = universe_record.universe.themes[0].id

        for slot in non_seed:
            row = slot_by_id.get(slot.slot_id)
            existing = row.project_id if row and selected is not None else None
            # On full generate, always create new projects (existing should be None).
            if selected is None:
                existing = None
            await _process_slot(slot, existing_project_id=existing, do_reuse=True)
    except AssetPackError:
        failures += 1
        # Fall through to finalize pack status.

    if failures and not completions:
        final_status = "failed"
    elif failures:
        final_status = "partial"
    else:
        final_status = "completed"

    current = get_pack(pack_id, db_path=path)
    final = update_pack_cas(
        pack_id,
        expected_revision=current.document_revision,
        status=final_status,  # type: ignore[arg-type]
        universe_id=universe_record.id if universe_record else current.universe_id,
        body_updates={
            "composer_profile_id": merge.profile_id or plan.composer_profile_id,
            "composer_profile_strength": plan.composer_profile_strength,
        },
        db_path=path,
    )
    logger.info(
        "[AssetPackGenerate] end",
        extra={
            "pack_id": _truncate_id(pack_id),
            "status": final_status,
            "completed_slots": completions,
            "failed_slots": failures,
            "universe_id": _truncate_id(final.universe_id),
        },
    )
    if final_status == "failed":
        raise AssetPackError("asset_pack_generate_failed")
    return final


async def regenerate_asset_pack_slots(
    pack_id: str,
    *,
    slot_ids: list[str],
    expected_revision: int,
    db_path: Path | None = None,
) -> AssetPackRecord:
    """Partial regenerate: rewrite only named slots; others stay byte-identical."""
    path = db_path or get_project_db_path()
    record = get_pack(pack_id, db_path=path)
    logger.info(
        "[AssetPackGenerate.regenerate] start",
        extra={
            "pack_id": _truncate_id(pack_id),
            "slot_count": len(slot_ids),
            "slot_ids": ",".join(slot_ids),
        },
    )
    known = {slot.slot_id for slot in record.plan.slots}
    unknown = [sid for sid in slot_ids if sid not in known]
    if unknown:
        logger.warning(
            "[AssetPackGenerate.regenerate] unknown slots",
            extra={"code": "asset_pack_slot_unknown"},
        )
        raise AssetPackError("asset_pack_slot_unknown")
    return await generate_asset_pack(
        pack_id,
        expected_plan_digest=record.plan.plan_digest,
        expected_revision=expected_revision,
        db_path=path,
        slot_ids=list(slot_ids),
    )


__all__ = [
    "discover_seed_motif",
    "first_pitched_track_id",
    "generate_asset_pack",
    "regenerate_asset_pack_slots",
]
