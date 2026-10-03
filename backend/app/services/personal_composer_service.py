"""Start, stop, resume, evaluate, and delete a personal composer.

Training runs on a background thread. Fake mode joins that thread before
the caller returns. Startup marks an orphaned ``running`` row
``personal_interrupted``. Listing does not change a job.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.collaboration_settings import collaboration_enabled
from app.composition_schemas import CompositionV2
from app.personal_composer_schemas import (
    DISPLAY_NAME_PATTERN,
    PersonalAdapterConfigV1,
    PersonalComposerError,
    PersonalEvalV1,
    PersonalTrainingJobV1,
    PersonalTrainingManifestV1,
    reject_embedded_note_material,
    utc_now_iso,
)
from app.personal_composer_settings import (
    PersonalComposerSettings,
    assert_personal_composer_storage_root,
    load_personal_composer_settings,
)
from app.services.collaboration_access import authorize_current
from app.rights_governance_schemas import (
    ModelDataProvenanceManifestV1,
    ModelDataProvenanceSourceV1,
    rights_digest_prefix,
)
from app.services.personal_composer_rights import (
    build_writeback_entry,
    require_selected_projects,
    resolve_and_evaluate_project_train,
)
from app.services.rights_governance_store import upsert_rights_entry
from app.services.personal_composer_snapshot import (
    persist_snapshot_documents,
    plan_snapshot_documents,
)
from app.services.personal_composer_store import (
    PersonalComposerRow,
    get_adapter,
    insert_adapter,
    list_adapters,
    update_adapter,
)
from app.services.project_store import ProjectNotFoundError, get_project
from app.storage_root_policy import StorageRootError

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}


def _settings() -> PersonalComposerSettings:
    return load_personal_composer_settings()


def _db_path() -> Path:
    from app.db.connection import get_project_db_path

    return get_project_db_path()


def _refuse(code: str, message: str, *, http_status: int, details: dict[str, Any] | None = None) -> PersonalComposerError:
    logger.warning("Personal composer request refused", extra={"code": code})
    return PersonalComposerError(code, message, http_status=http_status, details=details)


def _base_reference(engine: str) -> tuple[str, str | None]:
    if engine == "fake":
        return "fake:symbolic-tiny", None
    raw = (os.environ.get("MUSIC_TRANSFORMER_CHECKPOINT") or "").strip()
    if not raw:
        return "music_transformer.tiny.v1", None
    try:
        from app.services.model_path_resolve import (
            ModelPathRejectedError,
            resolve_music_transformer_checkpoint,
        )

        path = resolve_music_transformer_checkpoint(raw)
    except (ModelPathRejectedError, OSError) as exc:
        logger.warning(
            "Personal composer base checkpoint unresolved",
            extra={"code": "personal_base_checkpoint", "error_type": type(exc).__name__},
        )
        return "music_transformer.tiny.v1", None
    if Path(path).is_file():
        return "music_transformer", Path(path).name
    return "music_transformer.tiny.v1", None


def _parse_start(payload: dict[str, Any], settings: PersonalComposerSettings) -> dict[str, Any]:
    reject_embedded_note_material(payload)
    project_ids = require_selected_projects(payload.get("project_ids"))
    if len(project_ids) != len(set(project_ids)):
        raise _refuse(
            "personal_projects_required",
            "Select at least one project to train.",
            http_status=422,
        )
    if len(project_ids) > settings.max_projects:
        raise _refuse(
            "personal_too_many_projects",
            "Too many projects were selected.",
            http_status=422,
            details={"max_projects": settings.max_projects},
        )
    display_name = str(payload.get("display_name") or "")
    if DISPLAY_NAME_PATTERN.fullmatch(display_name) is None:
        raise _refuse(
            "personal_name_invalid",
            "Display name must start with a letter and use letters, digits, dot, underscore, or hyphen.",
            http_status=422,
            details={"field_name": "display_name"},
        )
    rights = payload.get("rights")
    if not isinstance(rights, dict):
        raise _refuse(
            "personal_rights_refused",
            "Each selected project needs an explicit provenance.",
            http_status=422,
        )
    raw_steps = payload.get("max_steps", 1)
    try:
        max_steps = int(raw_steps)
    except (TypeError, ValueError) as exc:
        raise _refuse(
            "personal_name_invalid",
            "max_steps must be an integer.",
            http_status=422,
            details={"field_name": "max_steps"},
        ) from exc
    if max_steps < 1 or max_steps > settings.max_steps:
        raise _refuse(
            "personal_name_invalid",
            "max_steps is outside the allowed range.",
            http_status=422,
            details={"field_name": "max_steps"},
        )
    config_kwargs: dict[str, Any] = {"max_steps": max_steps}
    if payload.get("rank") is not None:
        config_kwargs["rank"] = payload["rank"]
    if payload.get("alpha") is not None:
        config_kwargs["alpha"] = payload["alpha"]
    try:
        config = PersonalAdapterConfigV1.model_validate(config_kwargs)
    except ValidationError as exc:
        raise _refuse(
            "personal_name_invalid",
            "Adapter configuration is not valid.",
            http_status=422,
            details={"field_name": "adapter_config"},
        ) from exc
    return {
        "display_name": display_name,
        "project_ids": project_ids,
        "rights": rights,
        "adapter_config": config,
    }


def _load_documents(project_ids: list[str]) -> list[tuple[str, CompositionV2]]:
    documents: list[tuple[str, CompositionV2]] = []
    for project_id in project_ids:
        try:
            record = get_project(project_id)
        except ProjectNotFoundError as exc:
            raise _refuse(
                "personal_project_missing",
                "A selected project was not found.",
                http_status=404,
                details={"project_id": project_id},
            ) from exc
        if not record.composition_json:
            raise _refuse(
                "personal_snapshot_empty",
                "A selected score has no note events.",
                http_status=422,
                details={"project_id": project_id},
            )
        try:
            composition = CompositionV2.model_validate(json.loads(record.composition_json))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise _refuse(
                "personal_snapshot_empty",
                "A selected score has no note events.",
                http_status=422,
                details={"project_id": project_id},
            ) from exc
        documents.append((project_id, composition))
    return documents


def _spawn(adapter_id: str, *, join: bool) -> None:
    settings = _settings()
    db_path = _db_path()

    def _target() -> None:
        logger.info("Personal composer worker started", extra={"adapter_id": adapter_id, "engine": "fake" if settings.fake else "torch"})
        try:
            from app.personal_composer.trainer import run_personal_training

            run_personal_training(adapter_id, root=settings.root, db_path=db_path)
        except Exception as exc:
            code = getattr(exc, "code", None) or "personal_train_failed"
            if not isinstance(code, str) or len(code) > 64:
                code = "personal_train_failed"
            logger.error(
                "Personal composer worker failed",
                extra={"adapter_id": adapter_id, "error_code": code, "error_type": type(exc).__name__},
            )
            try:
                current = get_adapter(adapter_id, db_path=db_path)
            except PersonalComposerError:
                return
            if current.job.status == "running":
                update_adapter(
                    adapter_id,
                    status="failed",
                    error_code=code,
                    db_path=db_path,
                )
        finally:
            logger.info("Personal composer worker exited", extra={"adapter_id": adapter_id})

    thread = threading.Thread(target=_target, name=f"personal-{adapter_id}", daemon=True)
    with _LOCK:
        _THREADS[adapter_id] = thread
    thread.start()
    if join:
        thread.join()


def start_personal_composer(payload: dict[str, Any]) -> PersonalTrainingJobV1:
    """Validate rights, copy the snapshot, and run the job."""
    settings = _settings()
    logger.info("Personal composer start requested", extra={"fake": settings.fake})
    parsed = _parse_start(payload, settings)
    project_ids: list[str] = parsed["project_ids"]
    owner_actor_id: str | None = None
    for project_id in project_ids:
        actor_id = authorize_current(project_id, "train_adapter")
        if actor_id:
            owner_actor_id = actor_id
    documents = _load_documents(project_ids)
    resolved_rights = [
        resolve_and_evaluate_project_train(
            project_id,
            parsed["rights"].get(project_id),
            db_path=_db_path(),
        )
        for project_id in project_ids
    ]
    rights = {item.project_id: item.provenance for item in resolved_rights}
    adapter_id = f"pcomp_{secrets.token_hex(8)}"
    snapshot, encoded = plan_snapshot_documents(adapter_id=adapter_id, documents=documents)
    if not settings.fake:
        from app.personal_composer.lora import torch_is_available

        if not torch_is_available():
            raise _refuse(
                "personal_torch_unavailable",
                "PyTorch is not installed for personal composer training.",
                http_status=503,
            )
    try:
        assert_personal_composer_storage_root(root=settings.root)
    except StorageRootError as exc:
        raise _refuse(
            "personal_storage_root_rejected",
            "Personal composer storage root was rejected.",
            http_status=500,
        ) from exc
    engine = "fake" if settings.fake else "torch"
    base_model_id, basename = _base_reference(engine)
    manifest = PersonalTrainingManifestV1(
        adapter_id=adapter_id,
        display_name=parsed["display_name"],
        registry_model_id=f"personal:{adapter_id}",
        project_ids=project_ids,
        rights=rights,
        snapshot_version=snapshot.snapshot_version,
        base_model_id=base_model_id,
        base_checkpoint_basename=basename,
        adapter_config=parsed["adapter_config"],
        engine=engine,
        created_at=utc_now_iso(),
    )
    row = insert_adapter(
        adapter_id=adapter_id,
        display_name=manifest.display_name,
        registry_model_id=manifest.registry_model_id,
        engine=engine,
        base_model_id=base_model_id,
        snapshot_version=snapshot.snapshot_version,
        max_steps=manifest.adapter_config.max_steps,
        manifest=manifest,
        owner_actor_id=owner_actor_id if collaboration_enabled() else None,
    )
    try:
        persist_snapshot_documents(snapshot=snapshot, encoded=encoded, settings=settings)
        adapter_dir = settings.root / adapter_id
        adapter_dir.mkdir(parents=True, exist_ok=True)
        (adapter_dir / "manifest.json").write_text(
            json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        model_data = ModelDataProvenanceManifestV1(
            manifest_kind="personal_adapter_train",
            subject_id=adapter_id,
            sources=[
                ModelDataProvenanceSourceV1(
                    entry_id=item.entry.entry_id,
                    source_kind="project",
                    source_id=item.project_id,
                    ownership_class=item.entry.ownership_class,
                    use_policy="training_allowed",
                    rights_digest_prefix=rights_digest_prefix(item.entry.rights_digest),
                )
                for item in resolved_rights
            ],
            excluded_source_counts_by_use_policy={},
            created_at=utc_now_iso(),
        )
        (adapter_dir / "model.data.provenance.manifest.json").write_text(
            json.dumps(model_data.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        # Task 4b: write-back registry upsert only after successful snapshot.
        for item in resolved_rights:
            writeback = build_writeback_entry(item)
            upsert_rights_entry(writeback, db_path=_db_path())
            logger.info(
                "Personal composer rights write-back",
                extra={
                    "entry_id": writeback.entry_id,
                    "project_id": item.project_id,
                    "use_policy": writeback.use_policy,
                },
            )
    except Exception:
        logger.error(
            "Personal composer snapshot persist failed",
            extra={"adapter_id": adapter_id, "error_code": "personal_snapshot_failed", "error_type": "Exception"},
        )
        update_adapter(adapter_id, status="failed", error_code="personal_snapshot_failed")
        raise
    _spawn(adapter_id, join=settings.fake)
    finished = get_adapter(adapter_id)
    logger.info(
        "Personal composer start finished",
        extra={"adapter_id": adapter_id, "status": finished.job.status, "engine": finished.job.engine},
    )
    return finished.job


def list_personal_composers() -> list[PersonalTrainingJobV1]:
    """Read non-deleted jobs. Does not sweep or start training."""
    filter_owner = collaboration_enabled()
    owner = None
    if filter_owner:
        from app.services.collaboration_access import request_actor_header, resolve_request_actor

        owner = resolve_request_actor(request_actor_header())
    rows = list_adapters(owner_actor_id=owner, filter_owner=filter_owner)
    logger.info("Personal composer list finished", extra={"count": len(rows)})
    return [row.job for row in rows]


def get_personal_composer(adapter_id: str) -> PersonalTrainingJobV1:
    return get_adapter(adapter_id).job


def stop_personal_composer(adapter_id: str) -> PersonalTrainingJobV1:
    row = get_adapter(adapter_id)
    if row.job.status != "running":
        raise _refuse(
            "personal_not_stoppable",
            "Only a running personal composer can be stopped.",
            http_status=409,
            details={"adapter_id": adapter_id},
        )
    updated = update_adapter(adapter_id, status="stopped")
    logger.info(
        "Personal composer stopped",
        extra={"adapter_id": adapter_id, "status": updated.job.status},
    )
    return updated.job


def resume_personal_composer(adapter_id: str) -> PersonalTrainingJobV1:
    settings = _settings()
    row = get_adapter(adapter_id)
    step_path = settings.root / adapter_id / "step.json"
    if row.job.status not in {"stopped", "failed"} or not step_path.is_file():
        raise _refuse(
            "personal_resume_unavailable",
            "Resume needs a stopped or failed job with a saved step.",
            http_status=409,
            details={"adapter_id": adapter_id},
        )
    if row.job.status == "running":
        raise _refuse("personal_busy", "That personal composer is already running.", http_status=409)
    update_adapter(adapter_id, status="running", clear_error=True)
    _spawn(adapter_id, join=settings.fake)
    finished = get_adapter(adapter_id)
    logger.info(
        "Personal composer resumed",
        extra={"adapter_id": adapter_id, "status": finished.job.status},
    )
    return finished.job


def evaluate_personal_composer(adapter_id: str) -> PersonalEvalV1:
    row = get_adapter(adapter_id)
    if row.job.status == "running":
        raise _refuse("personal_busy", "That personal composer is still running.", http_status=409)
    if row.job.status not in {"complete", "stopped"}:
        raise _refuse(
            "personal_busy",
            "Evaluate is available after training stops or completes.",
            http_status=409,
            details={"adapter_id": adapter_id},
        )
    if row.job.engine == "fake":
        report = PersonalEvalV1(
            adapter_id=adapter_id,
            engine="fake",
            step=row.job.step,
            loss=None,
            token_accuracy=None,
        )
    else:
        report = _torch_eval(row)
    update_adapter(adapter_id, eval_report=report)
    logger.info(
        "Personal composer evaluated",
        extra={"adapter_id": adapter_id, "status": row.job.status, "engine": report.engine},
    )
    return report


def _torch_eval(row: PersonalComposerRow) -> PersonalEvalV1:
    from app.personal_composer.lora import measure_next_token, torch_is_available
    from app.personal_composer.trainer import load_personal_lora_model, load_snapshot_compositions

    if not torch_is_available():
        raise _refuse(
            "personal_torch_unavailable",
            "PyTorch is not installed for personal composer evaluation.",
            http_status=503,
        )
    settings = _settings()
    adapter_dir = settings.root / row.job.adapter_id
    compositions = load_snapshot_compositions(adapter_dir)
    from app.tokenizer.encode import encode_composition

    token_rows = [list(encode_composition(composition).token_ids) for composition in compositions]
    model = load_personal_lora_model(
        adapter_dir,
        base_model_id=row.job.base_model_id,
        base_checkpoint_basename=row.job.manifest.base_checkpoint_basename,
    )
    loss_value, accuracy = measure_next_token(model, token_rows, device="cpu")
    logger.info(
        "[FIX] Personal composer eval recorded",
        extra={
            "adapter_id": row.job.adapter_id,
            "engine": "torch",
            "loss": None if loss_value is None else round(loss_value, 6),
            "token_accuracy": None if accuracy is None else round(accuracy, 6),
        },
    )
    return PersonalEvalV1(
        adapter_id=row.job.adapter_id,
        engine="torch",
        step=row.job.step,
        loss=loss_value,
        token_accuracy=accuracy,
    )


def delete_personal_composer(adapter_id: str) -> PersonalTrainingJobV1:
    row = get_adapter(adapter_id)
    if row.job.status == "running":
        update_adapter(adapter_id, status="stopped")
        with _LOCK:
            thread = _THREADS.get(adapter_id)
        if thread is not None:
            thread.join(timeout=30)
    updated = update_adapter(adapter_id, status="deleted")
    adapter_dir = _settings().root / adapter_id
    if adapter_dir.exists():
        shutil.rmtree(adapter_dir)
        logger.info(
            "Personal composer directory removed",
            extra={"adapter_id": adapter_id, "basename": adapter_id},
        )
    logger.info(
        "Personal composer deleted",
        extra={"adapter_id": adapter_id, "status": updated.job.status},
    )
    return updated.job


def sweep_orphaned_personal_jobs() -> int:
    """Mark running rows with no live thread as interrupted. Listing does not call this."""
    changed = 0
    try:
        rows = list_adapters()
    except Exception as exc:
        logger.warning(
            "Personal composer orphan sweep skipped",
            extra={"code": "personal_sweep_skipped", "error_type": type(exc).__name__},
        )
        return 0
    for row in rows:
        if row.job.status != "running":
            continue
        with _LOCK:
            thread = _THREADS.get(row.job.adapter_id)
            alive = thread is not None and thread.is_alive()
        if alive:
            continue
        update_adapter(row.job.adapter_id, status="failed", error_code="personal_interrupted")
        changed += 1
        logger.warning(
            "Personal composer interrupted",
            extra={"adapter_id": row.job.adapter_id, "code": "personal_interrupted"},
        )
    logger.info("Personal composer orphan sweep finished", extra={"changed": changed})
    return changed
