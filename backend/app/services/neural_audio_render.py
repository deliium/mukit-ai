"""Neural audio render job orchestration (read-only over composition.v2).

Jobs pin ``source_revision_id`` / fingerprint, write audio under
``NEURAL_AUDIO_RENDER_ROOT``, and never mutate compositions or snapshots.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from app.ai_runtime.errors import ModelUnavailableError
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.registry import get_model, reload_registry
from app.ai_runtime.routing import ModelSelectionInput, resolve_model_for_operation
from app.ai_runtime.runtimes.fake_neural_audio import (
    FAKE_NEURAL_AUDIO_MODEL_ID,
    build_fake_neural_audio_model,
)
from app.ai_runtime.runtimes.local_midi_ddsp import build_local_midi_ddsp_model
from app.ai_runtime.runtimes.sidecar_musicgen import build_sidecar_musicgen_model
from app.ai_runtime.runtimes.stub import build_stub_model
from app.composition_schemas import CompositionV2
from app.db.connection import get_connection, get_project_db_path
from app.neural_audio_schemas import (
    NeuralAudioEnqueueRequest,
    NeuralAudioError,
    NeuralAudioJobListResponse,
    NeuralAudioJobResponse,
)
from app.neural_audio_settings import NeuralAudioSettings, load_neural_audio_settings
from app.operation_budget_settings import (
    BUDGET_RENDER_ATTEMPT,
    BUDGET_RUNTIME,
    load_operation_budget_settings,
)
from app.operation_trace import (
    MutableSpan,
    OperationRunIdError,
    adopt_operation_run_id,
    is_run_cancelled,
    record_finished_span,
    render_attempt_decision,
)
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.neural_audio_adapters import (
    artifact_to_engine_spec,
    resolve_adapter_kind,
    run_adapter,
)
from app.services.neural_audio_render_store import (
    allocate_render_id,
    assert_enqueue_quota,
    delete_job,
    delete_project_render_files,
    get_job_row,
    increment_attempt_count,
    insert_queued_job,
    list_job_rows,
    load_settings_and_root,
    row_to_response,
    update_job_status,
    write_audio_bytes,
)
from app.services.project_history_store import get_snapshot_composition


logger = logging.getLogger(__name__)


def canonical_operation_run_id(raw: str | None) -> str | None:
    """Adopt a client run id, or return null when the field was omitted."""

    if raw is None or str(raw).strip() == "":
        return None
    try:
        return adopt_operation_run_id(raw)
    except OperationRunIdError as exc:
        raise NeuralAudioError(
            "operation_run_id_invalid",
            "operation_run_id must be a UUID",
            http_status=422,
        ) from exc


def emit_render_span(
    *,
    job_id: str,
    operation_run_id: str | None,
    model_id: str | None,
    status: str,
    failure_code: str | None,
    attempt_count: int,
    started_at: float,
    byte_size: int | None = None,
    instructions_len: int | None = None,
    error_type: str | None = None,
) -> None:
    """Log one render span. Workers do not inherit the request ContextVar."""

    span = MutableSpan(
        run_id=operation_run_id or str(uuid.uuid4()),
        span_id=str(uuid.uuid4()),
        parent_span_id=None,
        kind="render",
        started_at=started_at,
        status=status,
        model_id=model_id,
        failure_code=failure_code,
        retry_count=max(0, int(attempt_count) - 1),
        byte_size=byte_size,
        instructions_len=instructions_len,
        error_type=error_type,
    )
    record_finished_span(span)
    logger.info(
        "Neural render attempt",
        extra={
            "render_id": job_id,
            "operation_run_id_prefix": (operation_run_id or "")[:12],
            "attempt_count": attempt_count,
            "status": status,
            "byte_size": byte_size,
            "failure_code": failure_code,
        },
    )


def enqueue_neural_audio_render(
    request: NeuralAudioEnqueueRequest,
    *,
    db_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    run_inline: bool = True,
) -> NeuralAudioJobResponse:
    """Validate, enqueue, and optionally run the job inline (single-worker v1)."""
    settings = load_settings_and_root(env)
    path = Path(db_path) if db_path is not None else get_project_db_path()
    composition, source_revision_id, source_fingerprint, project_id = _resolve_source(
        request, db_path=path
    )
    _assert_prompt_bounds(request, settings)

    reload_registry(env)
    model, fidelity_class, preferred_adapter = _resolve_audio_model(
        request.model_id, settings=settings, env=env
    )
    adapter_kind = resolve_adapter_kind(
        requested=request.adapter_kind,
        model_preferred=preferred_adapter,
        fidelity_class=fidelity_class,
    )
    artifact = run_adapter(
        adapter_kind,
        composition,
        instructions=request.instructions or "",
        genre=request.genre,
        mood=request.mood,
        instrumentation_summary=request.instrumentation_summary,
        tempo_bpm=request.tempo_bpm,
    )
    if request.tempo_bpm is not None and "tempo_override_applied" not in artifact.warnings:
        if artifact.tempo_bpm != request.tempo_bpm:
            artifact.warnings.append("tempo_override_applied")
        artifact.tempo_bpm = request.tempo_bpm

    render_id = allocate_render_id()
    operation_run_id = canonical_operation_run_id(request.operation_run_id)
    with get_connection(path) as conn:
        assert_enqueue_quota(conn, settings, project_id)
        row = insert_queued_job(
            conn,
            render_id=render_id,
            project_id=project_id,
            source_revision_id=source_revision_id,
            source_fingerprint=source_fingerprint,
            model_id=model.model_id if hasattr(model, "model_id") else str(request.model_id),
            model_version=getattr(model, "model_version", None),
            adapter_kind=adapter_kind,
            fidelity_class=fidelity_class,
            instructions=request.instructions or "",
            genre=request.genre,
            mood=request.mood,
            instrumentation_summary=artifact.instrumentation_summary,
            tempo_bpm=artifact.tempo_bpm,
            seed=request.seed,
            adapter_warnings=artifact.warnings,
            operation_run_id=operation_run_id,
        )

    logger.info(
        "Neural audio enqueue accepted",
        extra={
            "render_id": render_id,
            "model_id": row["model_id"],
            "adapter_kind": adapter_kind,
            "fidelity_class": fidelity_class,
            "source_revision_id": source_revision_id,
            "fingerprint_prefix": source_fingerprint[:12],
        },
    )

    if run_inline:
        return run_neural_audio_job(
            render_id,
            composition=composition,
            artifact_spec=artifact_to_engine_spec(artifact, seed=request.seed),
            model=model,
            settings=settings,
            db_path=path,
            expected_fingerprint=source_fingerprint,
        )
    return row_to_response(row)


def run_neural_audio_job(
    render_id: str,
    *,
    composition: dict[str, Any],
    artifact_spec: dict[str, Any],
    model: Any,
    settings: NeuralAudioSettings,
    db_path: Path | str | None = None,
    expected_fingerprint: str,
) -> NeuralAudioJobResponse:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    started = time.perf_counter()
    with get_connection(path) as conn:
        existing = get_job_row(conn, render_id)
    if existing is None:
        raise NeuralAudioError(
            "render_not_found",
            "Neural audio render not found",
            http_status=404,
            details={"render_id": render_id},
        )
    operation_run_id = existing.get("operation_run_id")
    if not isinstance(operation_run_id, str) or not operation_run_id.strip():
        operation_run_id = None
    model_id = str(existing.get("model_id") or "") or None
    instructions_len = len(str(existing.get("instructions") or ""))
    max_attempts = load_operation_budget_settings().neural_audio_max_attempts

    # Re-assert composition fingerprint before/after — never write composition.
    before_fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    if before_fp != expected_fingerprint:
        logger.error(
            "Neural audio source fingerprint mismatch before render",
            extra={"render_id": render_id, "expected_prefix": expected_fingerprint[:12]},
        )
        return _fail_job(
            path,
            render_id,
            code="neural_audio_internal_error",
            message="Source composition fingerprint mismatch",
        )

    result: dict[str, Any] | None = None
    while True:
        with get_connection(path) as conn:
            current = get_job_row(conn, render_id)
        attempt_count = int((current or {}).get("attempt_count") or 0)
        decision = render_attempt_decision(operation_run_id, attempt_count, max_attempts)
        attempt_started = time.perf_counter()
        if decision != "go":
            if decision == "cancelled":
                code, status = "operation_cancelled", "cancelled"
            elif decision == "runtime":
                code, status = BUDGET_RUNTIME, "budget_exceeded"
            else:
                code, status = BUDGET_RENDER_ATTEMPT, "budget_exceeded"
            if decision == "budget":
                logger.warning(
                    "Neural render attempt budget exhausted",
                    extra={
                        "render_id": render_id,
                        "budget_code": BUDGET_RENDER_ATTEMPT,
                        "attempt_count": attempt_count,
                    },
                )
            emit_render_span(
                job_id=render_id,
                operation_run_id=operation_run_id,
                model_id=model_id,
                status=status,
                failure_code=code,
                attempt_count=attempt_count,
                started_at=attempt_started,
                instructions_len=instructions_len,
            )
            if decision == "cancelled":
                message = "Neural render cancelled"
            elif decision == "runtime":
                message = "Neural render runtime budget exhausted"
            else:
                message = "Neural render attempt budget exhausted"
            return _fail_job(
                path,
                render_id,
                code=code,
                message=message,
            )

        with get_connection(path) as conn:
            attempt_count = increment_attempt_count(conn, render_id)
            update_job_status(conn, render_id, status="running")
        try:
            engine_input = {
                **artifact_spec,
                "source_fingerprint": expected_fingerprint,
                "max_audio_seconds": settings.max_audio_seconds,
            }
            result = _invoke_model_sync(model, engine_input)
        except ModelUnavailableError as exc:
            logger.error(
                "Neural audio engine unavailable during job",
                extra={"render_id": render_id, "error_type": type(exc).__name__},
            )
            if _render_attempt_failed(
                path,
                render_id,
                operation_run_id=operation_run_id,
                model_id=model_id,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                started_at=attempt_started,
                instructions_len=instructions_len,
                error_type=type(exc).__name__,
                engine_code="neural_audio_engine_unavailable",
                engine_message="Neural audio engine unavailable",
            ):
                return _fail_job(
                    path,
                    render_id,
                    code=BUDGET_RENDER_ATTEMPT,
                    message="Neural render attempt budget exhausted",
                )
            continue
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Neural audio job failed",
                extra={"render_id": render_id, "error_type": type(exc).__name__},
            )
            if _render_attempt_failed(
                path,
                render_id,
                operation_run_id=operation_run_id,
                model_id=model_id,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                started_at=attempt_started,
                instructions_len=instructions_len,
                error_type=type(exc).__name__,
                engine_code="neural_audio_internal_error",
                engine_message="Neural audio render failed",
            ):
                return _fail_job(
                    path,
                    render_id,
                    code=BUDGET_RENDER_ATTEMPT,
                    message="Neural render attempt budget exhausted",
                )
            continue

        if operation_run_id and is_run_cancelled(operation_run_id):
            emit_render_span(
                job_id=render_id,
                operation_run_id=operation_run_id,
                model_id=model_id,
                status="cancelled",
                failure_code="operation_cancelled",
                attempt_count=attempt_count,
                started_at=attempt_started,
                instructions_len=instructions_len,
            )
            return _fail_job(
                path,
                render_id,
                code="operation_cancelled",
                message="Neural render cancelled",
            )
        audio_bytes = result.get("audio_bytes") if isinstance(result, dict) else None
        if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
            if _render_attempt_failed(
                path,
                render_id,
                operation_run_id=operation_run_id,
                model_id=model_id,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                started_at=attempt_started,
                instructions_len=instructions_len,
                error_type="EmptyAudio",
                engine_code="neural_audio_internal_error",
                engine_message="Engine returned empty audio",
            ):
                return _fail_job(
                    path,
                    render_id,
                    code=BUDGET_RENDER_ATTEMPT,
                    message="Neural render attempt budget exhausted",
                )
            result = None
            continue
        break

    after_fp = composition_snapshot_fingerprint(CompositionV2.model_validate(composition))
    if after_fp != before_fp:
        logger.error(
            "Composition mutated during neural audio job (unexpected)",
            extra={"render_id": render_id},
        )
        return _fail_job(
            path,
            render_id,
            code="neural_audio_internal_error",
            message="Composition changed during render (aborted)",
        )

    assert result is not None
    audio_bytes = result.get("audio_bytes")
    if operation_run_id and is_run_cancelled(operation_run_id):
        emit_render_span(
            job_id=render_id,
            operation_run_id=operation_run_id,
            model_id=model_id,
            status="cancelled",
            failure_code="operation_cancelled",
            attempt_count=attempt_count,
            started_at=attempt_started,
            instructions_len=instructions_len,
        )
        return _fail_job(
            path,
            render_id,
            code="operation_cancelled",
            message="Neural render cancelled",
        )

    with get_connection(path) as conn:
        row = get_job_row(conn, render_id)
    project_id = row.get("project_id") if row else None
    write_result = write_audio_bytes(
        settings,
        project_id=project_id,
        render_id=render_id,
        payload=bytes(audio_bytes),
        content_type=str(result.get("content_type") or "audio/wav"),
        ext=str(result.get("ext") or "wav"),
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    if operation_run_id and is_run_cancelled(operation_run_id):
        emit_render_span(
            job_id=render_id,
            operation_run_id=operation_run_id,
            model_id=model_id,
            status="cancelled",
            failure_code="operation_cancelled",
            attempt_count=attempt_count,
            started_at=attempt_started,
            instructions_len=instructions_len,
            byte_size=write_result.byte_size,
        )
        return _fail_job(
            path,
            render_id,
            code="operation_cancelled",
            message="Neural render cancelled",
        )
    with get_connection(path) as conn:
        updated = update_job_status(
            conn,
            render_id,
            status="complete",
            audio_relpath=write_result.audio_relpath,
            content_type=write_result.content_type,
            byte_size=write_result.byte_size,
            sha256_prefix=write_result.sha256_prefix,
        )
        if str(updated.get("error_code") or "") == "operation_cancelled":
            emit_render_span(
                job_id=render_id,
                operation_run_id=operation_run_id,
                model_id=model_id,
                status="cancelled",
                failure_code="operation_cancelled",
                attempt_count=int(updated.get("attempt_count") or attempt_count),
                started_at=attempt_started,
                instructions_len=instructions_len,
            )
            return row_to_response(updated)
        if result.get("model_version"):
            conn.execute(
                "UPDATE neural_audio_renders SET model_version = ? WHERE id = ?",
                (str(result["model_version"]), render_id),
            )
            updated = get_job_row(conn, render_id)
        if updated and str(updated.get("error_code") or "") != "operation_cancelled":
            from app.services.musical_dependency_capture import capture_rendered_edge
            from app.services.content_provenance_capture import capture_neural_provenance

            capture_rendered_edge(
                conn,
                project_id=str(updated.get("project_id") or "") or None,
                asset_id=render_id,
                downstream_kind="neural_render",
                source_fingerprint=str(updated.get("source_fingerprint") or ""),
            )
            capture_neural_provenance(
                conn,
                project_id=str(updated.get("project_id") or "") or None,
                artifact_kind="neural_render",
                artifact_id=render_id,
                operation="neural_render",
                fingerprint=str(updated.get("sha256_prefix") or updated.get("source_fingerprint") or ""),
                source_revision_id=str(updated.get("source_revision_id") or "") or None,
                model_id=str(updated.get("model_id") or "") or None,
                model_version=str(updated.get("model_version") or "") or None,
                runtime=str(updated.get("adapter_kind") or "") or None,
            )
    emit_render_span(
        job_id=render_id,
        operation_run_id=operation_run_id,
        model_id=model_id,
        status="ok",
        failure_code=None,
        attempt_count=attempt_count,
        started_at=attempt_started,
        byte_size=write_result.byte_size,
        instructions_len=instructions_len,
    )
    logger.info(
        "Neural audio job complete",
        extra={
            "render_id": render_id,
            "status": "complete",
            "model_id": updated["model_id"] if updated else None,
            "adapter_kind": updated["adapter_kind"] if updated else None,
            "fidelity_class": updated["fidelity_class"] if updated else None,
            "duration_ms": duration_ms,
            "audio_bytes": write_result.byte_size,
            "sha256_prefix": write_result.sha256_prefix,
            "source_revision_id": updated.get("source_revision_id") if updated else None,
            "operation_run_id_prefix": (operation_run_id or "")[:12],
            "attempt_count": attempt_count,
        },
    )
    return row_to_response(updated)  # type: ignore[arg-type]


def _render_attempt_failed(
    path: Path,
    render_id: str,
    *,
    operation_run_id: str | None,
    model_id: str | None,
    attempt_count: int,
    max_attempts: int,
    started_at: float,
    instructions_len: int | None,
    error_type: str,
    engine_code: str,
    engine_message: str,
) -> bool:
    """Log the failed attempt. Return True when the attempt cap is exhausted."""

    exhausted = attempt_count >= max_attempts
    code = BUDGET_RENDER_ATTEMPT if exhausted else engine_code
    status = "budget_exceeded" if exhausted else "failed"
    if exhausted:
        logger.warning(
            "Neural render attempt budget exhausted",
            extra={
                "render_id": render_id,
                "budget_code": BUDGET_RENDER_ATTEMPT,
                "attempt_count": attempt_count,
            },
        )
    emit_render_span(
        job_id=render_id,
        operation_run_id=operation_run_id,
        model_id=model_id,
        status=status,
        failure_code=code,
        attempt_count=attempt_count,
        started_at=started_at,
        instructions_len=instructions_len,
        error_type=error_type,
    )
    return exhausted


def get_neural_audio_job(
    render_id: str,
    *,
    db_path: Path | str | None = None,
) -> NeuralAudioJobResponse:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        row = get_job_row(conn, render_id)
    if row is None:
        raise NeuralAudioError(
            "render_not_found",
            "Neural audio render not found",
            http_status=404,
            details={"render_id": render_id},
        )
    return row_to_response(row)


def list_neural_audio_jobs(
    project_id: str,
    *,
    db_path: Path | str | None = None,
    limit: int = 50,
) -> NeuralAudioJobListResponse:
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        rows = list_job_rows(conn, project_id=project_id, limit=limit)
    items = [row_to_response(row) for row in rows]
    logger.info(
        "Neural audio jobs listed",
        extra={"project_id": project_id, "count": len(items)},
    )
    return NeuralAudioJobListResponse(items=items, total=len(items))


def delete_neural_audio_job(
    render_id: str,
    *,
    db_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    settings = load_neural_audio_settings(env)
    path = Path(db_path) if db_path is not None else get_project_db_path()
    with get_connection(path) as conn:
        delete_job(conn, settings, render_id)


def cleanup_project_neural_audio(
    project_id: str,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    settings = load_neural_audio_settings(env)
    delete_project_render_files(settings, project_id)


def resolve_audio_file_path(
    render_id: str,
    *,
    db_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[Path, str, NeuralAudioJobResponse]:
    settings = load_neural_audio_settings(env)
    job = get_neural_audio_job(render_id, db_path=db_path)
    if job.status != "complete" or not job.audio_relpath:
        raise NeuralAudioError(
            "render_not_ready",
            "Audio download is only available when the job is complete",
            http_status=409,
            details={"render_id": render_id, "status": job.status},
        )
    from app.services.neural_audio_render_store import absolute_audio_path

    path = absolute_audio_path(settings, job.audio_relpath)
    if not path.is_file():
        raise NeuralAudioError(
            "render_not_found",
            "Neural audio file missing on disk",
            http_status=404,
            details={"render_id": render_id},
        )
    return path, job.content_type or "audio/wav", job


def _resolve_source(
    request: NeuralAudioEnqueueRequest,
    *,
    db_path: Path,
) -> tuple[dict[str, Any], str | None, str, str | None]:
    project_id = request.project_id
    source_revision_id = request.source_revision_id
    composition_dict: dict[str, Any] | None = None

    if project_id and source_revision_id:
        with get_connection(db_path) as conn:
            rev = conn.execute(
                """
                SELECT id, project_id, snapshot_fingerprint
                FROM project_revisions
                WHERE id = ? AND project_id = ?
                """,
                (source_revision_id, project_id),
            ).fetchone()
            if rev is None:
                raise NeuralAudioError(
                    "source_revision_not_found",
                    "Source revision not found for project",
                    http_status=404,
                    details={
                        "project_id": project_id,
                        "source_revision_id": source_revision_id,
                    },
                )
            composition_obj = get_snapshot_composition(conn, rev["snapshot_fingerprint"])
            if composition_obj is None:
                raise NeuralAudioError(
                    "source_revision_not_found",
                    "Source revision snapshot has no composition",
                    http_status=404,
                    details={
                        "project_id": project_id,
                        "source_revision_id": source_revision_id,
                    },
                )
            composition_dict = composition_obj.model_dump(mode="json")
            fingerprint = str(rev["snapshot_fingerprint"])
    elif isinstance(request.composition, dict) and request.composition:
        composition_dict = request.composition
        parsed = CompositionV2.model_validate(composition_dict)
        fingerprint = composition_snapshot_fingerprint(parsed)
        composition_dict = parsed.model_dump(mode="json")
    else:
        raise NeuralAudioError(
            "neural_audio_composition_required",
            "Composition V2 body or project+revision is required",
            http_status=422,
        )

    # Prefer revision fingerprint when both provided; still validate body if present.
    if request.composition and project_id and source_revision_id:
        body_fp = composition_snapshot_fingerprint(
            CompositionV2.model_validate(request.composition)
        )
        if body_fp != fingerprint:
            logger.warning(
                "Enqueue body fingerprint differs from revision; using revision snapshot",
                extra={
                    "project_id": project_id,
                    "source_revision_id": source_revision_id,
                },
            )
    assert composition_dict is not None
    return composition_dict, source_revision_id, fingerprint, project_id


def _assert_prompt_bounds(
    request: NeuralAudioEnqueueRequest,
    settings: NeuralAudioSettings,
) -> None:
    instructions = request.instructions or ""
    if len(instructions) > settings.max_prompt_chars:
        raise NeuralAudioError(
            "neural_audio_prompt_too_long",
            "Production instructions exceed the configured character limit",
            http_status=422,
            details={"limit": settings.max_prompt_chars, "length": len(instructions)},
        )
    if request.genre and len(request.genre) > settings.max_genre_chars:
        raise NeuralAudioError(
            "neural_audio_invalid_request",
            "Genre exceeds configured length",
            http_status=422,
        )
    if request.mood and len(request.mood) > settings.max_mood_chars:
        raise NeuralAudioError(
            "neural_audio_invalid_request",
            "Mood exceeds configured length",
            http_status=422,
        )


def _resolve_audio_model(
    model_id: str | None,
    *,
    settings: NeuralAudioSettings,
    env: Mapping[str, str] | None,
) -> tuple[Any, str, str]:
    selection = ModelSelectionInput(model_id=model_id)
    try:
        resolved = resolve_model_for_operation(
            AiOperation.AUDIO_RENDER,
            selection,
            env=env,
        )
    except Exception as exc:  # noqa: BLE001
        # Fall through to engine policy.
        logger.info(
            "AUDIO_RENDER resolve fell through to engine policy",
            extra={"error_type": type(exc).__name__, "requested_model_id": model_id},
        )
        resolved = None

    engine = settings.engine
    chosen_id = model_id
    if resolved is not None and resolved.descriptor.primary_capability.value == "audio_generation":
        chosen_id = resolved.descriptor.id
        if resolved.descriptor.runtime == "stub" or resolved.descriptor.status in {
            "unconfigured",
            "unavailable",
        }:
            chosen_id = None

    if not chosen_id:
        if engine == "fake:neural-audio" or settings.fake_mode:
            chosen_id = FAKE_NEURAL_AUDIO_MODEL_ID
        elif engine == "sidecar:musicgen":
            chosen_id = "sidecar:musicgen"
        elif engine == "local:midi-ddsp":
            chosen_id = "local:midi-ddsp"
        elif engine == "auto":
            # Prefer healthy sidecar, else fake only in fake mode.
            try:
                sidecar = get_model("sidecar:musicgen", env=env)
                if sidecar.status == "ready":
                    chosen_id = "sidecar:musicgen"
            except Exception:  # noqa: BLE001
                pass
            if not chosen_id and settings.fake_mode:
                chosen_id = FAKE_NEURAL_AUDIO_MODEL_ID

    if not chosen_id:
        raise NeuralAudioError(
            "neural_audio_engine_unavailable",
            "No neural audio engine is available (enable fake mode or neural-audio profile)",
            http_status=503,
        )

    try:
        descriptor = get_model(chosen_id, env=env)
    except Exception as exc:  # noqa: BLE001
        raise NeuralAudioError(
            "neural_audio_engine_unavailable",
            "Requested neural audio model is not registered",
            http_status=503,
            details={"model_id": chosen_id},
        ) from exc

    if descriptor.runtime == "stub" or descriptor.status in {"unconfigured", "unavailable"}:
        if chosen_id == FAKE_NEURAL_AUDIO_MODEL_ID and (
            settings.fake_mode or engine == "fake:neural-audio"
        ):
            from app.ai_runtime.runtimes.fake_neural_audio import (
                default_fake_neural_audio_descriptor,
            )

            descriptor = default_fake_neural_audio_descriptor()
        else:
            raise NeuralAudioError(
                "neural_audio_engine_unavailable",
                "Neural audio engine is not ready",
                http_status=503,
                details={"model_id": chosen_id, "status": descriptor.status},
            )

    model = _build_model_instance(descriptor, env=env)
    fidelity = str((descriptor.limits or {}).get("fidelity_class") or "generative")
    preferred = str((descriptor.limits or {}).get("preferred_adapter") or "text_prompt")
    return model, fidelity, preferred


def _build_model_instance(descriptor: Any, *, env: Mapping[str, str] | None) -> Any:
    runtime = descriptor.runtime
    if runtime == "fake_neural_audio" or descriptor.id == FAKE_NEURAL_AUDIO_MODEL_ID:
        return build_fake_neural_audio_model(descriptor)
    if runtime == "sidecar_musicgen" or descriptor.id == "sidecar:musicgen":
        return build_sidecar_musicgen_model(descriptor, env=env)
    if runtime == "local_midi_ddsp" or descriptor.id == "local:midi-ddsp":
        return build_local_midi_ddsp_model(descriptor)
    if runtime == "stub":
        return build_stub_model(descriptor)
    raise NeuralAudioError(
        "neural_audio_engine_unavailable",
        "Unsupported neural audio runtime",
        http_status=503,
        details={"runtime": runtime},
    )


def _invoke_model_sync(model: Any, engine_input: dict[str, Any]) -> dict[str, Any]:
    import asyncio

    render = model.render(engine_input)
    if asyncio.iscoroutine(render):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            # Nested event loop: schedule on a worker thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, render).result()
        return asyncio.run(render)
    return render


async def _invoke_model(model: Any, engine_input: dict[str, Any]) -> dict[str, Any]:
    render = model.render(engine_input)
    if hasattr(render, "__await__"):
        return await render
    return render


def _fail_job(
    db_path: Path,
    render_id: str,
    *,
    code: str,
    message: str,
) -> NeuralAudioJobResponse:
    with get_connection(db_path) as conn:
        row = update_job_status(
            conn,
            render_id,
            status="failed",
            error_code=code,
            error_message=message[:300],
        )
    return row_to_response(row)
