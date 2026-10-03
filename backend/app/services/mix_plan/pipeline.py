"""Preview, apply, reject, and undo orchestration for mix plans.

Stem files are read and hashed. New audio is written only under MIX_PLAN_ROOT.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from app.db.connection import get_connection, get_project_db_path
from app.mix_analysis_schemas import MixAnalysisError
from app.mix_plan_schemas import (
    MIX_PLAN_INPUT_TOO_LARGE,
    MIX_PLAN_INVALID_REQUEST,
    MIX_PLAN_PREVIEW_STALE,
    MIX_PLAN_STEM_CHANGED,
    MIX_PLAN_STEM_SET_INCOMPLETE,
    MIX_PLAN_TIMEOUT,
    MixPlanApplyRequest,
    MixPlanApplyResponse,
    MixPlanError,
    MixPlanMasterOutcome,
    MixPlanObservationRef,
    MixPlanPreviewRequest,
    MixPlanPreviewResponse,
    MixPlanUndoResponse,
    MixPlanV1,
)
from app.mix_plan_settings import MixPlanSettings, load_mix_plan_settings
from app.services.mix_analysis.active_stems import select_active_head_stems
from app.services.mix_plan.compile import change_from_op, compile_plan, overlay_head_before
from app.services.mix_plan.intent import resolve_phrase_intent
from app.services.mix_plan.render import (
    content_digest,
    pin_from_path,
    render_mix_wav,
    sha256_file,
    structure_digest,
    wav_duration_seconds,
)
from app.services.mix_plan.targets import master_target_document
from app.services.mix_plan_store import (
    allocate_id,
    delete_preview_files,
    find_preview_project,
    get_revision_row,
    head_revision_id,
    insert_revision,
    load_revision_plan,
    preview_audio_path,
    read_preview_meta,
    revision_audio_path,
    undo_revision,
    write_preview_bundle,
)
from app.services.neural_audio_render_store import absolute_audio_path, load_settings_and_root
from app.services.neural_audio_stem_store import (
    get_stem_set_row,
    list_stem_rows_for_set,
    parse_stem_track_ids,
)

logger = logging.getLogger(__name__)


def preview_mix(request: MixPlanPreviewRequest, *, env: dict[str, str] | None = None) -> MixPlanPreviewResponse:
    started = time.perf_counter()
    settings = load_mix_plan_settings(env)
    logger.info(
        "Mix plan preview started",
        extra={
            "stem_set_id": request.stem_set_id,
            "project_id": request.project_id,
            "master_target": request.master_target,
            "include_audio_preview": request.include_audio_preview,
            "char_count": len(request.phrase or ""),
        },
    )
    with get_connection(get_project_db_path()) as conn:
        selected, fingerprint, stem_set = _load_stems(conn, request.stem_set_id, settings)
    set_project = stem_set.get("project_id")
    if set_project and str(set_project) != request.project_id:
        raise MixPlanError(
            "Stem set belongs to a different project",
            code=MIX_PLAN_INVALID_REQUEST,
            details={"stem_set_id": request.stem_set_id},
        )
    stems, pins, full_hashes, duration, truncated = _open_stems(
        selected, max_seconds=settings.preview_max_seconds if request.include_audio_preview else settings.max_audio_seconds
    )
    _check_timeout(started, settings)
    observations = _collect_observations(request)
    active_roles = {str(stem["role"]) for stem in stems}
    intent = None
    intent_warnings = []
    if request.phrase:
        intent, intent_warnings = resolve_phrase_intent(
            request.phrase,
            active_roles,
            include_llm=request.include_llm_intent,
            fake_mode=settings.fake_mode,
            llm_fake_mode=settings.llm_fake_mode,
        )
    if intent is None and not observations:
        raise MixPlanError(
            "Provide a phrase or mix-analysis observations",
            code=MIX_PLAN_INVALID_REQUEST,
        )
    plan = compile_plan(
        intent=intent,
        observations=observations,
        active_stems=stems,
        master_target=request.master_target,
        stem_set_id=request.stem_set_id,
        project_id=request.project_id,
        fingerprint=fingerprint,
        duration_seconds=duration,
        explicit_observation_codes=request.observation_codes,
    )
    plan = _dedupe_ops(plan)
    with get_connection(get_project_db_path()) as conn:
        current_head = head_revision_id(conn, request.project_id, request.stem_set_id)
        head_plan = None
        if current_head:
            head_row = get_revision_row(conn, current_head)
            if head_row is not None:
                head_plan = load_revision_plan(settings, head_row)
    plan, matched_ops = overlay_head_before(plan, head_plan)
    if current_head:
        logger.info(
            "Mix plan head before overlay",
            extra={"revision_id": current_head, "matched_op_count": matched_ops},
        )
    plan = plan.model_copy(
        update={
            "stem_pins": pins,
            "warnings": [*intent_warnings, *plan.warnings],
            "preview_truncated": truncated,
            "dsp_backend": "fake" if settings.fake_mode else "stdlib",
        }
    )
    digest = structure_digest(plan)
    preview_id = allocate_id()
    dry_wav = None
    processed_wav = None
    peak = None
    backend = plan.dsp_backend
    if request.include_audio_preview:
        _check_timeout(started, settings)
        cap = settings.preview_max_seconds
        processed_wav, backend, peak = render_mix_wav(
            plan, stems, settings, max_seconds=cap, fake=settings.fake_mode
        )
        dry_plan = plan.model_copy(update={"ops": [], "changes": []})
        dry_wav, _, _ = render_mix_wav(
            dry_plan, stems, settings, max_seconds=cap, fake=settings.fake_mode
        )
        plan = plan.model_copy(update={"dsp_backend": backend})
    outcome = _outcome(plan, peak)
    meta = {
        "preview_id": preview_id,
        "project_id": request.project_id,
        "stem_set_id": request.stem_set_id,
        "digest": digest,
        "applied": False,
        "master_target": plan.master_target,
        "full_hashes": full_hashes,
        "plan": plan.model_dump(mode="json"),
        "stem_set_status": stem_set.get("status"),
    }
    write_preview_bundle(
        settings,
        project_id=request.project_id,
        preview_id=preview_id,
        meta=meta,
        dry_wav=dry_wav,
        processed_wav=processed_wav,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Mix plan preview ready",
        extra={
            "preview_id": preview_id,
            "stem_set_id": request.stem_set_id,
            "op_count": len(plan.ops),
            "master_target": plan.master_target,
            "dsp_backend": plan.dsp_backend,
            "duration_ms": duration_ms,
            "warning_codes": [item.code for item in plan.warnings],
        },
    )
    return MixPlanPreviewResponse(
        preview_id=preview_id,
        digest=digest,
        plan=plan,
        changes=plan.changes,
        warnings=plan.warnings,
        preview_truncated=truncated,
        dry_audio=dry_wav is not None,
        processed_audio=processed_wav is not None,
        master_outcome=outcome,
    )


def reject_preview(preview_id: str, *, env: dict[str, str] | None = None) -> None:
    settings = load_mix_plan_settings(env)
    project_id = find_preview_project(settings, preview_id)
    if project_id is None:
        raise MixPlanError("Mix plan preview not found", code="mix_plan_not_found")
    meta = read_preview_meta(settings, project_id, preview_id)
    if meta.get("applied"):
        raise MixPlanError(
            "Applied previews cannot be rejected",
            code=MIX_PLAN_INVALID_REQUEST,
            http_status=409,
        )
    delete_preview_files(settings, project_id, preview_id)


def apply_mix(request: MixPlanApplyRequest, *, env: dict[str, str] | None = None) -> MixPlanApplyResponse:
    started = time.perf_counter()
    settings = load_mix_plan_settings(env)
    project_id = request.plan.project_id
    meta = read_preview_meta(settings, project_id, request.preview_id)
    if meta.get("applied"):
        raise MixPlanError(
            "Preview was already applied",
            code=MIX_PLAN_PREVIEW_STALE,
        )
    if request.digest != meta.get("digest") or structure_digest(request.plan) != meta.get("digest"):
        raise MixPlanError(
            "Preview digest does not match the plan being applied",
            code=MIX_PLAN_PREVIEW_STALE,
        )
    if request.plan.master_target != meta.get("master_target"):
        raise MixPlanError(
            "Master target changed since preview",
            code=MIX_PLAN_PREVIEW_STALE,
        )
    if request.plan.stem_set_id != meta.get("stem_set_id"):
        raise MixPlanError(
            "Stem set changed since preview",
            code=MIX_PLAN_PREVIEW_STALE,
        )
    with get_connection(get_project_db_path()) as conn:
        selected, fingerprint, stem_set = _load_stems(conn, request.plan.stem_set_id, settings)
        parent = head_revision_id(conn, project_id, request.plan.stem_set_id)
    set_project = stem_set.get("project_id")
    if set_project and str(set_project) != project_id:
        raise MixPlanError(
            "Stem set belongs to a different project",
            code=MIX_PLAN_INVALID_REQUEST,
        )
    stems, pins, full_hashes, _duration, _truncated = _open_stems(
        selected, max_seconds=settings.max_audio_seconds
    )
    expected = meta.get("full_hashes") or {}
    for stem_id, digest in full_hashes.items():
        if expected.get(stem_id) != digest:
            logger.warning(
                "Mix plan stem hash changed before apply",
                extra={"stem_id": stem_id, "stem_set_id": request.plan.stem_set_id},
            )
            raise MixPlanError(
                "A source stem changed since preview",
                code=MIX_PLAN_STEM_CHANGED,
                details={"stem_id": stem_id},
            )
    _check_timeout(started, settings)
    plan = request.plan.model_copy(
        update={"stem_pins": pins, "source_stem_set_fingerprint": fingerprint, "guarantee": False}
    )
    wav, backend, peak = render_mix_wav(
        plan,
        stems,
        settings,
        max_seconds=settings.max_audio_seconds,
        fake=settings.fake_mode,
    )
    plan = plan.model_copy(update={"dsp_backend": backend})
    # Re-check hashes after bounce before publishing the revision file.
    for stem in stems:
        again = sha256_file(Path(stem["path"]))
        if again != full_hashes.get(stem["id"]):
            raise MixPlanError(
                "A source stem changed during bounce",
                code=MIX_PLAN_STEM_CHANGED,
                details={"stem_id": stem["id"]},
            )
    with get_connection(get_project_db_path()) as conn:
        revision = insert_revision(
            conn,
            settings,
            project_id=project_id,
            stem_set_id=plan.stem_set_id,
            parent_revision_id=parent,
            master_target=plan.master_target,
            plan=plan,
            mix_bytes=wav,
            stem_pins_json=json.dumps([pin.model_dump() for pin in pins]),
            dsp_backend=backend,
            fingerprint=fingerprint,
        )
        from app.services.content_provenance_capture import capture_mix_plan_apply_provenance

        capture_mix_plan_apply_provenance(
            conn,
            project_id=project_id,
            revision_id=revision.id,
            fingerprint=revision.sha256_prefix if hasattr(revision, "sha256_prefix") else fingerprint,
            stem_set_id=plan.stem_set_id,
            prior_mix_revision_id=parent,
        )
    meta["applied"] = True
    meta["revision_id"] = revision.id
    from app.services.mix_plan_store import absolute_under_root, preview_dir_rel

    meta_path = absolute_under_root(settings, preview_dir_rel(project_id, request.preview_id)) / "meta.json"
    meta_path.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Mix plan applied",
        extra={
            "revision_id": revision.id,
            "preview_id": request.preview_id,
            "stem_set_id": plan.stem_set_id,
            "op_count": len(plan.ops),
            "dsp_backend": backend,
            "master_target": plan.master_target,
            "sha256_prefix": revision.sha256_prefix,
            "duration_ms": duration_ms,
            "content_digest_prefix": content_digest(plan)[:16],
        },
    )
    return MixPlanApplyResponse(
        revision=revision,
        head_revision_id=revision.id,
        master_outcome=_outcome(plan, peak),
    )


def undo_mix(revision_id: str) -> MixPlanUndoResponse:
    with get_connection(get_project_db_path()) as conn:
        parent = undo_revision(conn, revision_id)
    return MixPlanUndoResponse(
        head_revision_id=parent.id if parent else None,
        revision=parent,
    )


def preview_wav_path(preview_id: str, which: str, *, env: dict[str, str] | None = None) -> Path:
    settings = load_mix_plan_settings(env)
    project_id = find_preview_project(settings, preview_id)
    if project_id is None:
        raise MixPlanError("Mix plan preview not found", code="mix_plan_not_found")
    return preview_audio_path(settings, project_id, preview_id, which)


def revision_wav_path(revision_id: str) -> Path:
    settings = load_mix_plan_settings()
    with get_connection(get_project_db_path()) as conn:
        from app.services.mix_plan_store import get_revision_row

        row = get_revision_row(conn, revision_id)
    if row is None:
        raise MixPlanError("Mix plan revision not found", code="mix_plan_not_found")
    return revision_audio_path(settings, row)


def get_revision_detail(revision_id: str):
    settings = load_mix_plan_settings()
    with get_connection(get_project_db_path()) as conn:
        from app.services.mix_plan_store import get_revision_row, row_to_meta

        row = get_revision_row(conn, revision_id)
        if row is None:
            raise MixPlanError("Mix plan revision not found", code="mix_plan_not_found")
        return row_to_meta(row), load_revision_plan(settings, row)


def list_revisions(project_id: str, stem_set_id: str | None):
    with get_connection(get_project_db_path()) as conn:
        from app.services.mix_plan_store import list_revision_rows, row_to_meta

        rows = list_revision_rows(conn, project_id=project_id, stem_set_id=stem_set_id)
    return [row_to_meta(row) for row in rows]


def _load_stems(conn, stem_set_id: str, settings: MixPlanSettings) -> tuple[list[dict], str, dict]:
    stem_set = get_stem_set_row(conn, stem_set_id)
    if stem_set is None:
        raise MixPlanError(
            "Stem set not found",
            code="mix_plan_not_found",
            details={"stem_set_id": stem_set_id},
        )
    members = list_stem_rows_for_set(conn, stem_set_id)
    selected = select_active_head_stems(members)
    if not selected or str(stem_set.get("status") or "") != "complete":
        raise MixPlanError(
            "Stem set is not complete",
            code=MIX_PLAN_STEM_SET_INCOMPLETE,
            details={"stem_set_id": stem_set_id},
        )
    total = sum(int(row.get("byte_size") or 0) for row in selected)
    if total > settings.max_total_input_bytes:
        raise MixPlanError(
            "Total stem input bytes exceed mix plan cap",
            code=MIX_PLAN_INPUT_TOO_LARGE,
            details={"total_bytes": total, "limit": settings.max_total_input_bytes},
        )
    fingerprint = str(stem_set.get("source_fingerprint") or "")
    return selected, fingerprint, stem_set


def _open_stems(
    selected: list[dict], *, max_seconds: float
) -> tuple[list[dict], list, dict[str, str], float, bool]:
    neural = load_settings_and_root()
    stems: list[dict] = []
    pins = []
    hashes: dict[str, str] = {}
    duration = 0.0
    truncated = False
    for row in selected:
        relpath = row.get("audio_relpath")
        if not relpath:
            raise MixPlanError(
                "Stem audio path missing",
                code=MIX_PLAN_STEM_SET_INCOMPLETE,
                details={"stem_id": row.get("id")},
            )
        path = absolute_audio_path(neural, str(relpath))
        stem_id = str(row["id"])
        role = str(row.get("stem_role") or "other")
        pin, full = pin_from_path(path, stem_id=stem_id, role=role)
        pins.append(pin)
        hashes[stem_id] = full
        try:
            seconds = wav_duration_seconds(path)
        except wave_error_type() as exc:
            logger.warning(
                "Mix plan could not read stem duration",
                extra={"stem_id": stem_id, "error_type": type(exc).__name__},
            )
            seconds = 0.0
        duration = max(duration, seconds)
        if seconds > max_seconds:
            truncated = True
        stems.append(
            {
                "id": stem_id,
                "role": role,
                "track_ids": parse_stem_track_ids(row.get("source_track_ids_json")),
                "path": str(path),
            }
        )
    return stems, pins, hashes, duration, truncated


def _collect_observations(request: MixPlanPreviewRequest) -> list[MixPlanObservationRef]:
    items = list(request.observations or [])
    if not request.report_id:
        return items
    from app.mix_analysis_settings import load_mix_analysis_settings
    from app.services.mix_analysis_store import get_report_row, load_report_body

    settings = load_mix_analysis_settings()
    with get_connection(get_project_db_path()) as conn:
        row = get_report_row(conn, request.report_id)
    if row is None:
        raise MixPlanError(
            "Mix analysis report not found",
            code="mix_plan_not_found",
            details={"report_id": request.report_id},
        )
    try:
        report = load_report_body(settings, row)
    except MixAnalysisError as exc:
        raise MixPlanError(str(exc), code="mix_plan_not_found") from exc
    for obs in report.observations:
        items.append(
            MixPlanObservationRef(
                code=obs.code,
                stem_ids=list(obs.locus.stem_ids),
                stem_roles=list(obs.locus.stem_roles),
                source_track_ids=list(obs.locus.source_track_ids),
                freq_hz_low=obs.locus.freq_hz_low,
                freq_hz_high=obs.locus.freq_hz_high,
                start_bar=obs.locus.start_bar,
                end_bar=obs.locus.end_bar,
                start_seconds=obs.locus.start_seconds,
                end_seconds=obs.locus.end_seconds,
                measurement_value=_stereo_measurement_value(report, obs),
            )
        )
    return items


def _loci_overlap(left_ids: list[str], left_roles: list[str], right_ids: list[str], right_roles: list[str]) -> bool:
    ids = set(left_ids) & set(right_ids)
    if ids:
        return True
    roles = set(left_roles) & set(right_roles)
    return bool(roles)


def _stereo_measurement_value(report, obs) -> float | None:
    """Copy signed left-minus-right dB when the measurement locus overlaps the observation."""
    if obs.code != "stereo_imbalance":
        return None
    for measurement in report.measurements:
        if measurement.code != "stereo_lr_rms_balance_db" or measurement.value is None:
            continue
        if _loci_overlap(
            list(obs.locus.stem_ids),
            list(obs.locus.stem_roles),
            list(measurement.locus.stem_ids),
            list(measurement.locus.stem_roles),
        ):
            return float(measurement.value)
    return None


def _dedupe_ops(plan: MixPlanV1) -> MixPlanV1:
    seen: set[str] = set()
    ops = []
    for op in plan.ops:
        if op.op_id in seen:
            continue
        seen.add(op.op_id)
        ops.append(op)
    return plan.model_copy(update={"ops": ops, "changes": [change_from_op(op) for op in ops]})


def _outcome(plan: MixPlanV1, peak: float | None) -> MixPlanMasterOutcome:
    doc = master_target_document(plan.master_target)
    return MixPlanMasterOutcome(
        target_id=plan.master_target,
        guarantee=False,
        sample_peak_dbfs=peak,
        lufs_approx=None,
        loudness_goal_lufs=doc.loudness_goal_lufs,
    )


def _check_timeout(started: float, settings: MixPlanSettings) -> None:
    if time.perf_counter() - started > settings.job_timeout_seconds:
        raise MixPlanError("Mix plan exceeded wall-clock timeout", code=MIX_PLAN_TIMEOUT)


def wave_error_type() -> type[BaseException]:
    import wave

    return wave.Error
