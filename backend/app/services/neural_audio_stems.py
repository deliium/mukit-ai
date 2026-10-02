"""Stem-set neural audio orchestration (read-only over composition.v2).

Never mutates compositions, autosave, or revision snapshots. FluidSynth stems
are explicit-only — never a silent fallback from generative failure.
"""

from __future__ import annotations

import logging
import time
import wave
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

from app.ai_runtime.errors import ModelUnavailableError
from app.composition_schemas import CompositionV2
from app.db.connection import get_connection
from app.neural_audio_schemas import (
    NeuralAudioBarRange,
    NeuralAudioError,
    NeuralAudioStemEnqueueRequest,
    NeuralAudioStemRerenderRequest,
    NeuralAudioStemResponse,
    NeuralAudioStemSetListResponse,
    NeuralAudioStemSetResponse,
)
from app.neural_audio_settings import NeuralAudioSettings, load_neural_audio_settings
from app.operation_budget_settings import (
    BUDGET_RENDER_ATTEMPT,
    BUDGET_RUNTIME,
    load_operation_budget_settings,
)
from app.operation_trace import is_run_cancelled, render_attempt_decision
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.neural_audio_adapters import (
    artifact_to_engine_spec,
    resolve_adapter_kind,
    run_adapter,
)
from app.services.neural_audio_render import (
    _assert_prompt_bounds,
    _invoke_model_sync,
    _resolve_audio_model,
    _resolve_source,
    canonical_operation_run_id,
    emit_render_span,
)
from app.services.neural_audio_stem_partition import (
    composition_duration_ticks,
    filter_composition_bar_range,
    filter_composition_tracks,
    partition_tracks_to_stems,
    resolve_capability_for_partition,
    stem_capabilities_for_engine,
)
from app.services.neural_audio_stem_store import (
    allocate_stem_id,
    allocate_stem_set_id,
    assert_stem_enqueue_quota,
    default_db_path,
    delete_stem_set,
    get_stem_row,
    get_stem_set_row,
    increment_stem_set_attempt,
    insert_queued_stem,
    insert_queued_stem_set,
    list_stem_rows_for_set,
    list_stem_set_rows,
    open_stem_store_settings,
    requeue_failed_stems,
    stem_row_to_response,
    stem_set_row_to_response,
    update_stem_set_status,
    update_stem_status,
    write_stem_audio_bytes,
)
from app.services.neural_audio_render_store import absolute_audio_path


logger = logging.getLogger(__name__)

_DEFAULT_SAMPLE_RATE = 22050


def enqueue_stem_set(
    request: NeuralAudioStemEnqueueRequest,
    *,
    db_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    run_inline: bool = True,
) -> NeuralAudioStemSetResponse:
    settings = open_stem_store_settings(env)
    path = default_db_path(db_path)

    # Reuse mix enqueue source resolver shape via a thin adapter object.
    from app.neural_audio_schemas import NeuralAudioEnqueueRequest

    mix_like = NeuralAudioEnqueueRequest.model_validate(
        {
            "project_id": request.project_id,
            "source_revision_id": request.source_revision_id,
            "composition": request.composition,
            "instructions": request.instructions,
            "genre": request.genre,
            "mood": request.mood,
            "model_id": request.model_id,
            "adapter_kind": request.adapter_kind,
            "tempo_bpm": request.tempo_bpm,
            "instrumentation_summary": request.instrumentation_summary,
            "seed": request.seed,
        }
    )
    _assert_prompt_bounds(mix_like, settings)
    composition, source_revision_id, source_fingerprint, project_id = _resolve_source(
        mix_like, db_path=path
    )

    engine = request.engine
    model = None
    fidelity_class = "deterministic"
    preferred_adapter = "midi_projection"
    model_id = "fluidsynth"
    model_version: str | None = None

    if engine == "fluidsynth":
        capabilities = stem_capabilities_for_engine(
            model_id=None, engine="fluidsynth", fidelity_class="deterministic"
        )
    else:
        from app.ai_runtime.registry import reload_registry

        reload_registry(env)
        model, fidelity_class, preferred_adapter = _resolve_audio_model(
            request.model_id, settings=settings, env=env
        )
        model_id = model.model_id if hasattr(model, "model_id") else str(request.model_id)
        model_version = getattr(model, "model_version", None)
        capabilities = stem_capabilities_for_engine(
            model_id=model_id, engine="neural", fidelity_class=fidelity_class
        )
        # Merge descriptor limits if present.
        limits = getattr(getattr(model, "descriptor", None), "limits", None) or {}
        listed = limits.get("stem_capabilities")
        if isinstance(listed, (list, tuple)) and listed:
            capabilities = frozenset(str(item) for item in listed)

    explicit = None
    if request.stems:
        explicit = [item.model_dump(mode="json") for item in request.stems]
    groups = partition_tracks_to_stems(
        composition,
        explicit=explicit,
        stem_roles_filter=list(request.stem_roles) if request.stem_roles else None,
    )
    if not groups:
        raise NeuralAudioError(
            "stem_partition_empty",
            "Stem partition produced no stems",
            http_status=422,
        )

    prefer_direct = (
        engine == "neural"
        and "direct_stems" in capabilities
        and request.bar_range is None
        and model_id == "fake:neural-audio"
    )

    planned: list[dict[str, Any]] = []
    for role, track_ids in groups:
        capability = resolve_capability_for_partition(
            capabilities=capabilities,
            track_ids=track_ids,
            bar_range=request.bar_range,
            engine=engine,
            prefer_direct=prefer_direct,
        )
        if not capability:
            code = (
                "section_render_unsupported"
                if request.bar_range is not None
                else "stem_capability_unsupported"
            )
            raise NeuralAudioError(
                code,  # type: ignore[arg-type]
                "Configured renderer does not support the requested stem capability",
                http_status=422,
                details={
                    "stem_role": role,
                    "track_count": len(track_ids),
                    "capabilities": sorted(capabilities),
                    "has_bar_range": request.bar_range is not None,
                },
            )
        planned.append(
            {
                "stem_role": role,
                "track_ids": track_ids,
                "capability_used": capability,
            }
        )

    tempo = request.tempo_bpm
    if tempo is None:
        from app.services.neural_audio_adapters import derive_tempo_bpm

        tempo = derive_tempo_bpm(composition)
    duration_ticks = composition_duration_ticks(composition)
    sample_rate = _DEFAULT_SAMPLE_RATE
    stem_set_id = allocate_stem_set_id()
    operation_run_id = canonical_operation_run_id(request.operation_run_id)

    with get_connection(path) as conn:
        assert_stem_enqueue_quota(
            conn, settings, project_id, upcoming_stems=len(planned)
        )
        insert_queued_stem_set(
            conn,
            stem_set_id=stem_set_id,
            project_id=project_id,
            source_revision_id=source_revision_id,
            source_fingerprint=source_fingerprint,
            model_id=model_id,
            engine=engine,
            origin_tick=0,
            duration_ticks=duration_ticks,
            tempo_bpm=tempo,
            sample_rate=sample_rate,
            operation_run_id=operation_run_id,
        )
        for item in planned:
            sync_class = _sync_class_for(
                engine=engine, fidelity_class=fidelity_class, capability=item["capability_used"]
            )
            adapter_kind = None
            if engine != "fluidsynth":
                adapter_kind = resolve_adapter_kind(
                    requested=request.adapter_kind,
                    model_preferred=preferred_adapter,
                    fidelity_class=fidelity_class,
                )
            insert_queued_stem(
                conn,
                stem_id=allocate_stem_id(),
                stem_set_id=stem_set_id,
                project_id=project_id,
                stem_role=item["stem_role"],
                source_track_ids=item["track_ids"],
                source_revision_id=source_revision_id,
                source_fingerprint=source_fingerprint,
                model_id=model_id,
                model_version=model_version,
                adapter_kind=adapter_kind,
                fidelity_class=fidelity_class if engine != "fluidsynth" else "deterministic",
                capability_used=item["capability_used"],
                sync_class=sync_class,
                engine=engine,
                instructions=request.instructions or "",
                genre=request.genre,
                mood=request.mood,
                tempo_bpm=tempo,
                seed=request.seed,
                sample_rate=sample_rate,
                origin_tick=0,
                duration_ticks=duration_ticks,
                bar_start=request.bar_range.start_bar if request.bar_range else None,
                bar_end=request.bar_range.end_bar if request.bar_range else None,
            )

    logger.info(
        "Neural stem set enqueue accepted",
        extra={
            "stem_set_id": stem_set_id,
            "model_id": model_id,
            "engine": engine,
            "stem_count": len(planned),
            "fingerprint_prefix": source_fingerprint[:12],
        },
    )

    if run_inline:
        return run_stem_set(
            stem_set_id,
            composition=composition,
            model=model,
            settings=settings,
            db_path=path,
            expected_fingerprint=source_fingerprint,
            env=env,
        )
    return get_stem_set(stem_set_id, db_path=path)


def run_stem_set(
    stem_set_id: str,
    *,
    composition: dict[str, Any],
    model: Any,
    settings: NeuralAudioSettings,
    db_path: Path | str | None = None,
    expected_fingerprint: str,
    env: Mapping[str, str] | None = None,
) -> NeuralAudioStemSetResponse:
    path = default_db_path(db_path)
    before_fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    if before_fp != expected_fingerprint:
        _fail_stem_set(
            path,
            stem_set_id,
            code="neural_audio_internal_error",
            message="Source composition fingerprint mismatch",
        )
        return get_stem_set(stem_set_id, db_path=path)

    with get_connection(path) as conn:
        set_row = get_stem_set_row(conn, stem_set_id)
    operation_run_id = (set_row or {}).get("operation_run_id")
    if not isinstance(operation_run_id, str) or not operation_run_id.strip():
        operation_run_id = None
    model_id = str((set_row or {}).get("model_id") or "") or None
    max_attempts = load_operation_budget_settings().neural_audio_max_attempts

    while True:
        with get_connection(path) as conn:
            set_row = get_stem_set_row(conn, stem_set_id)
        attempt_count = int((set_row or {}).get("attempt_count") or 0)
        decision = render_attempt_decision(operation_run_id, attempt_count, max_attempts)
        attempt_started = time.perf_counter()
        if decision != "go":
            if decision == "cancelled":
                code = "operation_cancelled"
            elif decision == "runtime":
                code = BUDGET_RUNTIME
            else:
                code = BUDGET_RENDER_ATTEMPT
            if decision == "budget":
                logger.warning(
                    "Stem set attempt budget exhausted",
                    extra={
                        "stem_set_id": stem_set_id,
                        "budget_code": BUDGET_RENDER_ATTEMPT,
                        "attempt_count": attempt_count,
                    },
                )
            emit_render_span(
                job_id=stem_set_id,
                operation_run_id=operation_run_id,
                model_id=model_id,
                status="cancelled" if decision == "cancelled" else "budget_exceeded",
                failure_code=code,
                attempt_count=attempt_count,
                started_at=attempt_started,
            )
            if decision == "cancelled":
                message = "Stem set cancelled"
            elif decision == "runtime":
                message = "Stem set runtime budget exhausted"
            else:
                message = "Stem set attempt budget exhausted"
            _fail_stem_set(
                path,
                stem_set_id,
                code=code,
                message=message,
            )
            return get_stem_set(stem_set_id, db_path=path)

        with get_connection(path) as conn:
            attempt_count = increment_stem_set_attempt(conn, stem_set_id)
            update_stem_set_status(conn, stem_set_id, status="running")
            stem_rows = list_stem_rows_for_set(conn, stem_set_id)

        any_failed = False
        direct_rows = [
            row
            for row in stem_rows
            if str(row.get("capability_used") or "") == "direct_stems"
            and str(row.get("status") or "") in {"queued", "running"}
        ]
        direct_ids = {str(row["id"]) for row in direct_rows}
        other_rows = [
            row
            for row in stem_rows
            if str(row["id"]) not in direct_ids
            and str(row.get("status") or "") in {"queued", "running"}
        ]

        if direct_rows:
            ok = _run_direct_stems_batch(
                direct_rows,
                model=model,
                settings=settings,
                db_path=path,
                expected_fingerprint=expected_fingerprint,
            )
            if not ok:
                any_failed = True

        for row in other_rows:
            ok = _run_one_stem(
                row,
                composition=composition,
                model=model,
                settings=settings,
                db_path=path,
                expected_fingerprint=expected_fingerprint,
                env=env,
            )
            if not ok:
                any_failed = True

        after_fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
        if after_fp != before_fp:
            logger.error(
                "Composition mutated during stem set job (unexpected)",
                extra={"stem_set_id": stem_set_id},
            )
            _fail_stem_set(
                path,
                stem_set_id,
                code="neural_audio_internal_error",
                message="Composition changed during stem render (aborted)",
            )
            return get_stem_set(stem_set_id, db_path=path)

        if operation_run_id and is_run_cancelled(operation_run_id):
            emit_render_span(
                job_id=stem_set_id,
                operation_run_id=operation_run_id,
                model_id=model_id,
                status="cancelled",
                failure_code="operation_cancelled",
                attempt_count=attempt_count,
                started_at=attempt_started,
            )
            _fail_stem_set(
                path,
                stem_set_id,
                code="operation_cancelled",
                message="Stem set cancelled",
            )
            return get_stem_set(stem_set_id, db_path=path)

        if not any_failed:
            with get_connection(path) as conn:
                update_stem_set_status(conn, stem_set_id, status="complete")
                set_row = get_stem_set_row(conn, stem_set_id)
                if set_row is not None:
                    from app.services.musical_dependency_capture import capture_rendered_edge

                    capture_rendered_edge(
                        conn,
                        project_id=str(set_row.get("project_id") or "") or None,
                        asset_id=stem_set_id,
                        downstream_kind="neural_stem_set",
                        source_fingerprint=str(set_row.get("source_fingerprint") or ""),
                    )
            emit_render_span(
                job_id=stem_set_id,
                operation_run_id=operation_run_id,
                model_id=model_id,
                status="ok",
                failure_code=None,
                attempt_count=attempt_count,
                started_at=attempt_started,
            )
            return get_stem_set(stem_set_id, db_path=path)

        exhausted = attempt_count >= max_attempts
        emit_render_span(
            job_id=stem_set_id,
            operation_run_id=operation_run_id,
            model_id=model_id,
            status="budget_exceeded" if exhausted else "failed",
            failure_code=BUDGET_RENDER_ATTEMPT if exhausted else "neural_audio_internal_error",
            attempt_count=attempt_count,
            started_at=attempt_started,
        )
        if exhausted:
            logger.warning(
                "Stem set attempt budget exhausted",
                extra={
                    "stem_set_id": stem_set_id,
                    "budget_code": BUDGET_RENDER_ATTEMPT,
                    "attempt_count": attempt_count,
                },
            )
            _fail_stem_set(
                path,
                stem_set_id,
                code=BUDGET_RENDER_ATTEMPT,
                message="Stem set attempt budget exhausted",
            )
            return get_stem_set(stem_set_id, db_path=path)
        with get_connection(path) as conn:
            requeue_failed_stems(conn, stem_set_id)


def rerender_stem(
    stem_set_id: str,
    stem_id: str,
    request: NeuralAudioStemRerenderRequest,
    *,
    db_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    run_inline: bool = True,
) -> NeuralAudioStemSetResponse:
    settings = open_stem_store_settings(env)
    path = default_db_path(db_path)
    with get_connection(path) as conn:
        set_row = get_stem_set_row(conn, stem_set_id)
        old = get_stem_row(conn, stem_id)
    if set_row is None:
        raise NeuralAudioError(
            "stem_set_not_found",
            "Neural audio stem set not found",
            http_status=404,
            details={"stem_set_id": stem_set_id},
        )
    if old is None or str(old.get("stem_set_id")) != stem_set_id:
        raise NeuralAudioError(
            "stem_not_found",
            "Neural audio stem not found in set",
            http_status=404,
            details={"stem_id": stem_id, "stem_set_id": stem_set_id},
        )
    blocked = _stem_set_attempt_blocked(path, set_row, stem_set_id)
    if blocked is not None:
        return blocked
    with get_connection(path) as conn:
        increment_stem_set_attempt(conn, stem_set_id)

    from app.neural_audio_schemas import NeuralAudioEnqueueRequest

    mix_like = NeuralAudioEnqueueRequest.model_validate(
        {
            "project_id": request.project_id or set_row.get("project_id"),
            "source_revision_id": request.source_revision_id
            or set_row.get("source_revision_id"),
            "composition": request.composition,
            "instructions": request.instructions or str(old.get("instructions") or ""),
            "genre": request.genre if request.genre is not None else old.get("genre"),
            "mood": request.mood if request.mood is not None else old.get("mood"),
            "model_id": request.model_id or old.get("model_id"),
            "adapter_kind": request.adapter_kind or old.get("adapter_kind"),
            "tempo_bpm": request.tempo_bpm
            if request.tempo_bpm is not None
            else old.get("tempo_bpm"),
            "seed": request.seed if request.seed is not None else old.get("seed"),
        }
    )
    if not mix_like.composition and not (
        mix_like.project_id and mix_like.source_revision_id
    ):
        # Fall back: require composition for rerender when no revision.
        raise NeuralAudioError(
            "neural_audio_composition_required",
            "Rerender requires composition V2 body or project+revision",
            http_status=422,
        )
    _assert_prompt_bounds(mix_like, settings)
    composition, source_revision_id, source_fingerprint, project_id = _resolve_source(
        mix_like, db_path=path
    )

    engine = request.engine or str(old.get("engine") or set_row.get("engine") or "neural")
    bar_range = request.bar_range
    if bar_range is None and old.get("bar_start") is not None:
        bar_range = NeuralAudioBarRange(
            start_bar=int(old["bar_start"]), end_bar=int(old["bar_end"])
        )

    model = None
    fidelity_class = "deterministic"
    preferred_adapter = "midi_projection"
    model_id = "fluidsynth"
    model_version = None
    if engine == "fluidsynth":
        capabilities = stem_capabilities_for_engine(
            model_id=None, engine="fluidsynth", fidelity_class="deterministic"
        )
    else:
        from app.ai_runtime.registry import reload_registry

        reload_registry(env)
        model, fidelity_class, preferred_adapter = _resolve_audio_model(
            mix_like.model_id, settings=settings, env=env
        )
        model_id = model.model_id if hasattr(model, "model_id") else str(mix_like.model_id)
        model_version = getattr(model, "model_version", None)
        capabilities = stem_capabilities_for_engine(
            model_id=model_id, engine="neural", fidelity_class=fidelity_class
        )

    from app.services.neural_audio_stem_store import _parse_track_ids

    track_ids = _parse_track_ids(old.get("source_track_ids_json"))
    capability = resolve_capability_for_partition(
        capabilities=capabilities,
        track_ids=track_ids,
        bar_range=bar_range,
        engine=engine,
        prefer_direct=False,
    )
    if not capability:
        code = (
            "section_render_unsupported"
            if bar_range is not None
            else "stem_capability_unsupported"
        )
        raise NeuralAudioError(
            code,  # type: ignore[arg-type]
            "Configured renderer does not support the requested stem capability",
            http_status=422,
        )

    sync_class = _sync_class_for(
        engine=engine, fidelity_class=fidelity_class, capability=capability
    )
    adapter_kind = None
    if engine != "fluidsynth":
        adapter_kind = resolve_adapter_kind(
            requested=mix_like.adapter_kind,
            model_preferred=preferred_adapter,
            fidelity_class=fidelity_class,
        )

    new_stem_id = allocate_stem_id()
    with get_connection(path) as conn:
        assert_stem_enqueue_quota(conn, settings, project_id, upcoming_stems=1)
        insert_queued_stem(
            conn,
            stem_id=new_stem_id,
            stem_set_id=stem_set_id,
            project_id=project_id,
            stem_role=str(old["stem_role"]),
            source_track_ids=track_ids,
            source_revision_id=source_revision_id,
            source_fingerprint=source_fingerprint,
            model_id=model_id,
            model_version=model_version,
            adapter_kind=adapter_kind,
            fidelity_class=fidelity_class if engine != "fluidsynth" else "deterministic",
            capability_used=capability,
            sync_class=sync_class,
            engine=engine,
            instructions=mix_like.instructions or "",
            genre=mix_like.genre,
            mood=mix_like.mood,
            tempo_bpm=mix_like.tempo_bpm,
            seed=mix_like.seed,
            sample_rate=int(set_row.get("sample_rate") or _DEFAULT_SAMPLE_RATE),
            origin_tick=int(set_row.get("origin_tick") or 0),
            duration_ticks=composition_duration_ticks(composition),
            bar_start=bar_range.start_bar if bar_range else None,
            bar_end=bar_range.end_bar if bar_range else None,
            supersedes_stem_id=stem_id,
        )
        update_stem_set_status(conn, stem_set_id, status="running")

    logger.info(
        "Neural stem selective rerender enqueued",
        extra={
            "stem_set_id": stem_set_id,
            "prior_stem_id": stem_id,
            "new_stem_id": new_stem_id,
            "stem_role": old.get("stem_role"),
            "fingerprint_prefix": source_fingerprint[:12],
        },
    )

    if run_inline:
        with get_connection(path) as conn:
            new_row = get_stem_row(conn, new_stem_id)
        assert new_row is not None
        ok = _run_one_stem(
            new_row,
            composition=composition,
            model=model,
            settings=settings,
            db_path=path,
            expected_fingerprint=source_fingerprint,
            env=env,
        )
        with get_connection(path) as conn:
            update_stem_set_status(
                conn,
                stem_set_id,
                status="complete" if ok else "failed",
                error_code=None if ok else "neural_audio_internal_error",
                error_message=None if ok else "Stem rerender failed",
            )
            if ok:
                set_row = get_stem_set_row(conn, stem_set_id)
                if set_row is not None:
                    from app.services.musical_dependency_capture import capture_rendered_edge

                    capture_rendered_edge(
                        conn,
                        project_id=str(set_row.get("project_id") or "") or None,
                        asset_id=stem_set_id,
                        downstream_kind="neural_stem_set",
                        source_fingerprint=str(set_row.get("source_fingerprint") or ""),
                    )
    return get_stem_set(stem_set_id, db_path=path)


def get_stem_set(
    stem_set_id: str,
    *,
    db_path: Path | str | None = None,
) -> NeuralAudioStemSetResponse:
    path = default_db_path(db_path)
    with get_connection(path) as conn:
        set_row = get_stem_set_row(conn, stem_set_id)
        if set_row is None:
            raise NeuralAudioError(
                "stem_set_not_found",
                "Neural audio stem set not found",
                http_status=404,
                details={"stem_set_id": stem_set_id},
            )
        stems = list_stem_rows_for_set(conn, stem_set_id)
    return stem_set_row_to_response(set_row, stems)


def list_stem_sets(
    project_id: str,
    *,
    db_path: Path | str | None = None,
    limit: int = 50,
) -> NeuralAudioStemSetListResponse:
    path = default_db_path(db_path)
    with get_connection(path) as conn:
        sets = list_stem_set_rows(conn, project_id=project_id, limit=limit)
        items = [
            stem_set_row_to_response(set_row, list_stem_rows_for_set(conn, str(set_row["id"])))
            for set_row in sets
        ]
    logger.info(
        "Neural stem sets listed",
        extra={"project_id": project_id, "count": len(items)},
    )
    return NeuralAudioStemSetListResponse(items=items, total=len(items))


def delete_stem_set_job(
    stem_set_id: str,
    *,
    db_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    settings = load_neural_audio_settings(env)
    path = default_db_path(db_path)
    with get_connection(path) as conn:
        delete_stem_set(conn, settings, stem_set_id)


def get_stem(
    stem_id: str,
    *,
    db_path: Path | str | None = None,
) -> NeuralAudioStemResponse:
    path = default_db_path(db_path)
    with get_connection(path) as conn:
        row = get_stem_row(conn, stem_id)
    if row is None:
        raise NeuralAudioError(
            "stem_not_found",
            "Neural audio stem not found",
            http_status=404,
            details={"stem_id": stem_id},
        )
    return stem_row_to_response(row)


def resolve_stem_audio_file_path(
    stem_id: str,
    *,
    db_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[Path, str, NeuralAudioStemResponse]:
    settings = load_neural_audio_settings(env)
    stem = get_stem(stem_id, db_path=db_path)
    if stem.status != "complete" or not stem.audio_relpath:
        raise NeuralAudioError(
            "render_not_ready",
            "Audio download is only available when the stem is complete",
            http_status=409,
            details={"stem_id": stem_id, "status": stem.status},
        )
    path = absolute_audio_path(settings, stem.audio_relpath)
    if not path.is_file():
        raise NeuralAudioError(
            "stem_not_found",
            "Neural stem audio file missing on disk",
            http_status=404,
            details={"stem_id": stem_id},
        )
    return path, stem.content_type or "audio/wav", stem


def _run_direct_stems_batch(
    rows: list[Mapping[str, Any]],
    *,
    model: Any,
    settings: NeuralAudioSettings,
    db_path: Path,
    expected_fingerprint: str,
) -> bool:
    """Consume one ``render_stems`` call and write each member WAV.

    Honest ``direct_stems`` only — never silent FluidSynth fallback.
    """
    if not rows:
        return True
    if model is None or not hasattr(model, "render_stems"):
        logger.error(
            "direct_stems labeled but model lacks render_stems",
            extra={"stem_count": len(rows)},
        )
        for row in rows:
            with get_connection(db_path) as conn:
                update_stem_status(
                    conn,
                    str(row["id"]),
                    status="failed",
                    error_code="stem_capability_unsupported",
                    error_message="Model does not support direct_stems",
                )
        return False

    roles = [str(row.get("stem_role") or "other") for row in rows]
    seed = rows[0].get("seed")
    stem_set_id = str(rows[0]["stem_set_id"])
    started = time.perf_counter()
    for row in rows:
        with get_connection(db_path) as conn:
            update_stem_status(conn, str(row["id"]), status="running")

    try:
        result = _invoke_render_stems_sync(
            model,
            {
                "source_fingerprint": expected_fingerprint,
                "seed": seed,
                "stem_roles": roles,
                "max_audio_seconds": settings.max_audio_seconds,
            },
        )
        stems_map = result.get("stems") if isinstance(result, dict) else None
        if not isinstance(stems_map, dict) or not stems_map:
            raise RuntimeError("empty direct_stems map")
        model_version = result.get("model_version")
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "direct_stems batch failed",
            extra={
                "stem_set_id": stem_set_id,
                "stem_role_count": len(roles),
                "error_type": type(exc).__name__,
            },
        )
        for row in rows:
            with get_connection(db_path) as conn:
                update_stem_status(
                    conn,
                    str(row["id"]),
                    status="failed",
                    error_code="neural_audio_internal_error",
                    error_message="direct_stems render failed",
                )
        return False

    all_ok = True
    for row in rows:
        role = str(row.get("stem_role") or "other")
        stem_id = str(row["id"])
        payload = stems_map.get(role)
        if not isinstance(payload, dict):
            logger.error(
                "direct_stems missing role in map",
                extra={"stem_id": stem_id, "stem_role": role},
            )
            with get_connection(db_path) as conn:
                update_stem_status(
                    conn,
                    stem_id,
                    status="failed",
                    error_code="neural_audio_internal_error",
                    error_message=f"direct_stems missing role {role}",
                )
            all_ok = False
            continue
        audio_bytes = payload.get("audio_bytes")
        if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
            with get_connection(db_path) as conn:
                update_stem_status(
                    conn,
                    stem_id,
                    status="failed",
                    error_code="neural_audio_internal_error",
                    error_message="empty direct_stems audio",
                )
            all_ok = False
            continue
        content_type = str(payload.get("content_type") or "audio/wav")
        ext = str(payload.get("ext") or "wav")
        write_result = write_stem_audio_bytes(
            settings,
            project_id=row.get("project_id"),
            stem_set_id=stem_set_id,
            stem_id=stem_id,
            payload=bytes(audio_bytes),
            content_type=content_type,
            ext=ext,
        )
        sample_rate = _wav_sample_rate(bytes(audio_bytes)) or _DEFAULT_SAMPLE_RATE
        with get_connection(db_path) as conn:
            update_stem_status(
                conn,
                stem_id,
                status="complete",
                audio_relpath=write_result.audio_relpath,
                content_type=write_result.content_type,
                byte_size=write_result.byte_size,
                sha256_prefix=write_result.sha256_prefix,
                model_version=str(model_version) if model_version else None,
                sample_rate=sample_rate,
            )
        logger.info(
            "Neural stem complete (direct_stems)",
            extra={
                "stem_id": stem_id,
                "stem_set_id": stem_set_id,
                "stem_role": role,
                "capability_used": "direct_stems",
                "sha256_prefix": write_result.sha256_prefix,
                "byte_size": write_result.byte_size,
            },
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "direct_stems batch finished",
        extra={
            "stem_set_id": stem_set_id,
            "capability_used": "direct_stems",
            "stem_role_count": len(roles),
            "duration_ms": duration_ms,
            "ok": all_ok,
        },
    )
    return all_ok


def _invoke_render_stems_sync(model: Any, engine_input: dict[str, Any]) -> dict[str, Any]:
    import asyncio

    render = model.render_stems(engine_input)
    if asyncio.iscoroutine(render):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, render).result()
        return asyncio.run(render)
    return render


def _run_one_stem(
    row: Mapping[str, Any],
    *,
    composition: dict[str, Any],
    model: Any,
    settings: NeuralAudioSettings,
    db_path: Path,
    expected_fingerprint: str,
    env: Mapping[str, str] | None,
) -> bool:
    stem_id = str(row["id"])
    stem_set_id = str(row["stem_set_id"])
    started = time.perf_counter()
    with get_connection(db_path) as conn:
        update_stem_status(conn, stem_id, status="running")

    from app.services.neural_audio_stem_store import _parse_track_ids

    track_ids = _parse_track_ids(row.get("source_track_ids_json"))
    sliced = filter_composition_tracks(composition, track_ids)
    if row.get("bar_start") is not None and row.get("bar_end") is not None:
        sliced = filter_composition_bar_range(
            sliced,
            {"start_bar": int(row["bar_start"]), "end_bar": int(row["bar_end"])},
        )

    engine = str(row.get("engine") or "neural")
    try:
        if engine == "fluidsynth":
            audio_bytes, content_type, sample_rate, ext = _render_fluidsynth_stem(sliced)
            model_version = "fluidsynth"
        else:
            adapter_kind = str(row.get("adapter_kind") or "text_prompt")
            artifact = run_adapter(
                adapter_kind,
                sliced,
                instructions=str(row.get("instructions") or ""),
                genre=row.get("genre"),
                mood=row.get("mood"),
                tempo_bpm=row.get("tempo_bpm"),
            )
            # Bias prompt with stem role for generative honesty (no event dump).
            if artifact.prompt_text:
                artifact.prompt_text = (
                    f"stem_role={row.get('stem_role')} | {artifact.prompt_text}"
                )[:4000]
            spec = artifact_to_engine_spec(artifact, seed=row.get("seed"))
            spec["source_fingerprint"] = expected_fingerprint
            spec["max_audio_seconds"] = settings.max_audio_seconds
            spec["stem_role"] = row.get("stem_role")
            result = _invoke_model_sync(model, spec)
            audio_bytes = result.get("audio_bytes") if isinstance(result, dict) else None
            if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
                raise RuntimeError("empty stem audio")
            content_type = str(result.get("content_type") or "audio/wav")
            ext = str(result.get("ext") or "wav")
            model_version = result.get("model_version")
            sample_rate = _wav_sample_rate(bytes(audio_bytes)) or _DEFAULT_SAMPLE_RATE
    except ModelUnavailableError:
        logger.error(
            "Neural stem engine unavailable",
            extra={"stem_id": stem_id, "stem_set_id": stem_set_id},
        )
        with get_connection(db_path) as conn:
            update_stem_status(
                conn,
                stem_id,
                status="failed",
                error_code="neural_audio_engine_unavailable",
                error_message="Neural audio engine unavailable",
            )
        return False
    except Exception as exc:  # noqa: BLE001
        from app.services.composition_wav import CompositionWavError

        if isinstance(exc, CompositionWavError) and exc.unavailable:
            code = "neural_audio_engine_unavailable"
            message = "FluidSynth stem renderer unavailable"
            http_hint = 503
        else:
            code = "neural_audio_internal_error"
            message = "Stem render failed"
            http_hint = 500
        logger.error(
            "Neural stem render failed",
            extra={
                "stem_id": stem_id,
                "stem_set_id": stem_set_id,
                "error_type": type(exc).__name__,
                "error_code": code,
                "http_hint": http_hint,
            },
        )
        with get_connection(db_path) as conn:
            update_stem_status(
                conn,
                stem_id,
                status="failed",
                error_code=code,
                error_message=message,
            )
        return False

    write_result = write_stem_audio_bytes(
        settings,
        project_id=row.get("project_id"),
        stem_set_id=stem_set_id,
        stem_id=stem_id,
        payload=bytes(audio_bytes),
        content_type=content_type,
        ext=ext,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    with get_connection(db_path) as conn:
        update_stem_status(
            conn,
            stem_id,
            status="complete",
            audio_relpath=write_result.audio_relpath,
            content_type=write_result.content_type,
            byte_size=write_result.byte_size,
            sha256_prefix=write_result.sha256_prefix,
            model_version=str(model_version) if model_version else None,
            sample_rate=sample_rate,
        )
    logger.info(
        "Neural stem complete",
        extra={
            "stem_id": stem_id,
            "stem_set_id": stem_set_id,
            "stem_role": row.get("stem_role"),
            "capability_used": row.get("capability_used"),
            "duration_ms": duration_ms,
            "sha256_prefix": write_result.sha256_prefix,
            "byte_size": write_result.byte_size,
        },
    )
    return True


def _render_fluidsynth_stem(
    composition: dict[str, Any],
) -> tuple[bytes, str, int, str]:
    from app.services.composition_wav import CompositionWavError, render_wav_with_report

    parsed = CompositionV2.model_validate(composition)
    try:
        result = render_wav_with_report(parsed)
    except CompositionWavError:
        raise
    wav_bytes = result.wav_bytes
    sample_rate = _wav_sample_rate(wav_bytes) or 44100
    logger.info(
        "FluidSynth stem render complete",
        extra={"byte_size": len(wav_bytes), "sample_rate": sample_rate},
    )
    return wav_bytes, "audio/wav", sample_rate, "wav"


def _wav_sample_rate(payload: bytes) -> int | None:
    try:
        with wave.open(BytesIO(payload), "rb") as handle:
            return int(handle.getframerate())
    except Exception:  # noqa: BLE001
        return None


def _sync_class_for(*, engine: str, fidelity_class: str, capability: str) -> str:
    if engine == "fluidsynth" or capability == "fluidsynth_deterministic":
        return "deterministic_midi"
    if fidelity_class == "neural_instrument":
        return "timeline_aligned"
    return "generative_independent"


def _stem_set_attempt_blocked(
    path: Path,
    set_row: Mapping[str, Any],
    stem_set_id: str,
) -> NeuralAudioStemSetResponse | None:
    operation_run_id = set_row.get("operation_run_id")
    if not isinstance(operation_run_id, str) or not operation_run_id.strip():
        operation_run_id = None
    attempt_count = int(set_row.get("attempt_count") or 0)
    decision = render_attempt_decision(
        operation_run_id,
        attempt_count,
        load_operation_budget_settings().neural_audio_max_attempts,
    )
    if decision == "go":
        return None
    if decision == "cancelled":
        code = "operation_cancelled"
    elif decision == "runtime":
        code = BUDGET_RUNTIME
    else:
        code = BUDGET_RENDER_ATTEMPT
    if decision == "budget":
        logger.warning(
            "Stem set attempt budget exhausted",
            extra={
                "stem_set_id": stem_set_id,
                "budget_code": BUDGET_RENDER_ATTEMPT,
                "attempt_count": attempt_count,
            },
        )
    emit_render_span(
        job_id=stem_set_id,
        operation_run_id=operation_run_id,
        model_id=str(set_row.get("model_id") or "") or None,
        status="cancelled" if decision == "cancelled" else "budget_exceeded",
        failure_code=code,
        attempt_count=attempt_count,
        started_at=time.perf_counter(),
    )
    if decision == "cancelled":
        message = "Stem set cancelled"
    elif decision == "runtime":
        message = "Stem set runtime budget exhausted"
    else:
        message = "Stem set attempt budget exhausted"
    _fail_stem_set(
        path,
        stem_set_id,
        code=code,
        message=message,
    )
    return get_stem_set(stem_set_id, db_path=path)


def _fail_stem_set(
    db_path: Path,
    stem_set_id: str,
    *,
    code: str,
    message: str,
) -> None:
    with get_connection(db_path) as conn:
        update_stem_set_status(
            conn,
            stem_set_id,
            status="failed",
            error_code=code,
            error_message=message,
        )
        for row in list_stem_rows_for_set(conn, stem_set_id):
            if row.get("status") in {"queued", "running"}:
                update_stem_status(
                    conn,
                    str(row["id"]),
                    status="failed",
                    error_code=code,
                    error_message=message,
                )
