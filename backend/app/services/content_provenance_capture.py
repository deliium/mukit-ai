"""Soft-fail provenance capture beside existing writers.

Unlike ``musical_dependency_capture`` (which hard-fails on cycle), this module
catches store cycle/cap/``ContentProvenanceError``, logs WARNING, and returns
``None`` so the primary writer commits. Only ``PersistenceSecretError`` is
re-raised for explicit provenance API bodies.
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
from typing import Any

from app.content_provenance_schemas import (
    ContentProvenanceError,
    ContentProvenanceRecordV1,
    ProvenanceActorKind,
    ProvenanceArtifactKind,
    ProvenanceOperation,
)
from app.project_history_schemas import RevisionOperationType
from app.services.content_provenance_store import (
    insert_provenance_record,
    list_records_for_artifact,
)
from app.services.persistence_secret_guard import PersistenceSecretError

logger = logging.getLogger(__name__)

_PERSONAL_PREFIX = "personal:pcomp_"


def new_provenance_record_id() -> str:
    return f"cprov_{secrets.token_hex(8)}"


def fingerprint_prefix(value: str | None, *, max_len: int = 40) -> str | None:
    if not value:
        return None
    cleaned = str(value).strip().lower()
    if not cleaned:
        return None
    # Prefer hex-looking prefixes; truncate otherwise.
    hexish = "".join(ch for ch in cleaned if ch in "0123456789abcdef")
    if len(hexish) >= 8:
        return hexish[:max_len]
    return cleaned[:max_len] if all(c.isalnum() or c in "-_" for c in cleaned[:max_len]) else None


def record_provenance_safe(
    connection: sqlite3.Connection | None,
    payload: ContentProvenanceRecordV1 | dict[str, Any],
    *,
    db_path: Any = None,
    re_raise_secrets: bool = False,
) -> ContentProvenanceRecordV1 | None:
    """Insert a record; soft-skip on cycle/cap/schema errors.

    When ``re_raise_secrets`` is True (explicit provenance write APIs),
    ``PersistenceSecretError`` propagates. Capture hooks leave it False.
    """
    try:
        stored = insert_provenance_record(connection, payload, db_path=db_path)
    except PersistenceSecretError:
        if re_raise_secrets:
            raise
        logger.warning(
            "Provenance capture soft-skipped",
            extra={"code": "forbidden_secret_field"},
        )
        return None
    except ContentProvenanceError as exc:
        artifact_kind = None
        artifact_id = None
        if isinstance(payload, dict):
            artifact_kind = payload.get("artifact_kind")
            artifact_id = payload.get("artifact_id")
        elif isinstance(payload, ContentProvenanceRecordV1):
            artifact_kind = payload.artifact_kind
            artifact_id = payload.artifact_id
        logger.warning(
            "Provenance capture soft-skipped",
            extra={
                "code": exc.code,
                "artifact_kind": artifact_kind,
                "artifact_id_prefix": (
                    str(artifact_id)[:12] if artifact_id else None
                ),
            },
        )
        return None
    except Exception as exc:  # noqa: BLE001 — soft-fail must not abort writers
        logger.warning(
            "Provenance capture soft-skipped",
            extra={
                "code": "provenance_capture_unexpected",
                "error_type": type(exc).__name__,
            },
        )
        return None
    logger.info(
        "Provenance capture recorded",
        extra={
            "record_id": stored.record_id,
            "operation": stored.operation,
            "artifact_kind": stored.artifact_kind,
            "project_id": stored.project_id,
        },
    )
    return stored


def map_revision_operation(
    operation_type: str,
    *,
    ai_operation: str | None = None,
    import_format: str | None = None,
    user_action: str | None = None,
    generation_parameters: dict[str, Any] | None = None,
) -> tuple[ProvenanceOperation, ProvenanceActorKind, str | None]:
    """Map history signals to provenance operation / actor / user_action."""
    op = (operation_type or "").strip()
    action = (user_action or "").strip() or None
    if action and action.lower() == "audio_transcribe":
        return "transcription_apply", "human", "audio_transcribe"
    if op == RevisionOperationType.IMPORT.value:
        fmt = (import_format or "").strip().lower()
        if "musicxml" in fmt or fmt in {"xml", "mxl"}:
            return "import_musicxml", "import", action or "import"
        return "import_midi", "import", action or "import"
    if op == RevisionOperationType.GENERATE_APPLY.value:
        if _has_personal_adapter(generation_parameters):
            return "personal_adapter_generate", "ai", action or "apply"
        return "ai_generate", "ai", action or "apply"
    if op == RevisionOperationType.AI_REGION_EDIT_APPLY.value:
        return "ai_edit_region", "ai", action or "apply"
    if op == RevisionOperationType.ARRANGEMENT_APPLY.value:
        return "ai_arrange", "ai", action or "apply"
    if op == RevisionOperationType.DEVELOPMENT_APPLY.value:
        if (ai_operation or "").strip() == "vary_section":
            return "ai_develop", "ai", action or "apply"
        return "ai_develop", "ai", action or "apply"
    if op == RevisionOperationType.CREATIVE_MOTIF_APPLY.value:
        return "ai_motif_apply", "ai", action or "apply"
    if op == RevisionOperationType.MUSICAL_UNIVERSE_THEME_APPLY.value:
        return "universe_theme_reuse", "system", action or "apply"
    if op == RevisionOperationType.ASSET_PACK_GENERATE.value:
        return "asset_pack_generate", "system", action or "commit"
    if op == RevisionOperationType.ASSET_PACK_SLOT_REGENERATE.value:
        return "asset_pack_slot_regenerate", "system", action or "commit"
    if op == RevisionOperationType.PROJECT_CREATE.value:
        return "human_edit", "human", action or "project_create"
    if op in {
        RevisionOperationType.MANUAL_CHECKPOINT.value,
        RevisionOperationType.PRE_AI_CHECKPOINT.value,
        RevisionOperationType.REVISION_RESTORE.value,
        RevisionOperationType.MIGRATION.value,
        RevisionOperationType.REHARMONIZE_APPLY.value,
        RevisionOperationType.MULTI_AGENT_APPLY.value,
        RevisionOperationType.FILM_SCORE_ADAPT_APPLY.value,
        RevisionOperationType.AUTONOMOUS_STAGE.value,
    }:
        return "human_edit", "human", action or "commit"
    return "human_edit", "human", action or "commit"


def _has_personal_adapter(generation_parameters: dict[str, Any] | None) -> bool:
    if not isinstance(generation_parameters, dict):
        return False
    composer = generation_parameters.get("composer_model_id")
    if isinstance(composer, str) and composer.strip().startswith(_PERSONAL_PREFIX):
        return True
    stages = generation_parameters.get("stages")
    if isinstance(stages, list):
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            mid = stage.get("model_id")
            if isinstance(mid, str) and mid.strip().startswith(_PERSONAL_PREFIX):
                return True
    return False


def _parent_artifacts_from_generation(
    generation_parameters: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    parents: list[dict[str, Any]] = []
    if not isinstance(generation_parameters, dict):
        return parents
    ref = generation_parameters.get("reference_project_id") or generation_parameters.get(
        "reference_project"
    )
    if isinstance(ref, dict):
        ref_id = ref.get("id") or ref.get("project_id")
    else:
        ref_id = ref
    if isinstance(ref_id, str) and ref_id.strip():
        parents.append(
            {
                "kind": "reference_project",
                "id": ref_id.strip()[:120],
                "fingerprint_prefix": fingerprint_prefix(
                    generation_parameters.get("reference_fingerprint")
                    if isinstance(generation_parameters.get("reference_fingerprint"), str)
                    else None
                ),
            }
        )
    profile = generation_parameters.get("composer_profile_id")
    if isinstance(profile, str) and profile.strip():
        parents.append(
            {
                "kind": "composer_profile",
                "id": profile.strip()[:120],
                "fingerprint_prefix": None,
            }
        )
    personal_id: str | None = None
    composer = generation_parameters.get("composer_model_id")
    if isinstance(composer, str) and composer.strip().startswith(_PERSONAL_PREFIX):
        personal_id = composer.strip()
    else:
        stages = generation_parameters.get("stages")
        if isinstance(stages, list):
            for stage in stages:
                if isinstance(stage, dict):
                    mid = stage.get("model_id")
                    if isinstance(mid, str) and mid.strip().startswith(_PERSONAL_PREFIX):
                        personal_id = mid.strip()
                        break
    if personal_id:
        # Strip personal: prefix for artifact id stability.
        adapter_id = personal_id[len("personal:") :] if personal_id.startswith("personal:") else personal_id
        parents.append(
            {
                "kind": "personal_adapter",
                "id": adapter_id[:120],
                "fingerprint_prefix": None,
            }
        )
    return parents[:8]


def _prior_revision_parent_ids(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    prior_revision_id: str | None,
) -> list[str]:
    if not prior_revision_id:
        return []
    rows = list_records_for_artifact(
        conn,
        artifact_kind="composition_revision",
        artifact_id=prior_revision_id,
        project_id=project_id,
    )
    if not rows:
        return []
    # Prefer the latest record for that revision.
    return [rows[-1].record_id]


def _compact_generation_provenance(
    generation_parameters: dict[str, Any] | None,
    *,
    operation: ProvenanceOperation,
) -> dict[str, Any] | None:
    if generation_parameters is None:
        return None
    if operation not in {
        "ai_generate",
        "ai_edit_region",
        "ai_arrange",
        "ai_develop",
        "ai_motif_apply",
        "reference_condition",
        "personal_adapter_generate",
    }:
        return None
    # Only nest when the fragment already looks like generation.provenance.v1.
    if generation_parameters.get("schema") == "generation.provenance.v1":
        return {
            key: generation_parameters[key]
            for key in (
                "schema",
                "pipeline_id",
                "stages",
                "seed",
                "constraints_digest_prefix",
                "model_id",
                "runtime",
            )
            if key in generation_parameters
        }
    if generation_parameters.get("schema_version") == "generation.provenance.v1":
        compact = dict(generation_parameters)
        compact.pop("events", None)
        compact.pop("notes", None)
        return compact
    return None


def capture_revision_provenance(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    revision_id: str,
    operation_type: str,
    fingerprint: str | None,
    prior_revision_id: str | None = None,
    ai_operation: str | None = None,
    generation_parameters: dict[str, Any] | None = None,
    model_id: str | None = None,
    model_version: str | None = None,
    runtime: str | None = None,
    import_format: str | None = None,
    user_action: str | None = None,
    pack_id: str | None = None,
    revision_created: bool = True,
) -> ContentProvenanceRecordV1 | None:
    """Capture a composition_revision node after a durable commit."""
    if not revision_created or not revision_id:
        logger.debug(
            "Provenance revision capture skipped",
            extra={"reason": "no_revision", "project_id": project_id},
        )
        return None
    prefix = fingerprint_prefix(fingerprint)
    if fingerprint and prefix is None:
        logger.warning(
            "Provenance capture soft-skipped",
            extra={"code": "provenance_fingerprint_unusable", "artifact_kind": "composition_revision"},
        )
        # Still allow capture with null fingerprint — plan soft-skips only when unusable *required*.
    operation, actor_kind, action = map_revision_operation(
        operation_type,
        ai_operation=ai_operation,
        import_format=import_format,
        user_action=user_action,
        generation_parameters=generation_parameters,
    )
    parent_ids = _prior_revision_parent_ids(
        conn, project_id=project_id, prior_revision_id=prior_revision_id
    )
    parent_artifacts = _parent_artifacts_from_generation(generation_parameters)
    if prior_revision_id and not parent_ids:
        parent_artifacts.insert(
            0,
            {
                "kind": "composition_revision",
                "id": prior_revision_id[:120],
                "fingerprint_prefix": None,
            },
        )
    if pack_id and operation in {"asset_pack_generate", "asset_pack_slot_regenerate"}:
        parent_artifacts.append(
            {"kind": "asset_pack", "id": pack_id[:120], "fingerprint_prefix": None}
        )
    parent_artifacts = parent_artifacts[:8]
    payload: dict[str, Any] = {
        "schema_version": "content.provenance.record.v1",
        "record_id": new_provenance_record_id(),
        "project_id": project_id,
        "artifact_kind": "composition_revision",
        "artifact_id": revision_id,
        "artifact_fingerprint_prefix": prefix,
        "operation": operation,
        "actor_kind": actor_kind,
        "model_id": model_id,
        "model_version": model_version,
        "runtime": runtime,
        "user_action": action,
        "parent_record_ids": parent_ids[:8],
        "parent_artifacts": parent_artifacts,
        "trust_class": "mukit_internal",
    }
    compact = _compact_generation_provenance(generation_parameters, operation=operation)
    if compact is not None:
        payload["source_generation_provenance"] = compact
    return record_provenance_safe(conn, payload)


def capture_neural_provenance(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    artifact_kind: ProvenanceArtifactKind,
    artifact_id: str,
    operation: ProvenanceOperation,
    fingerprint: str | None = None,
    source_revision_id: str | None = None,
    model_id: str | None = None,
    model_version: str | None = None,
    runtime: str | None = None,
    parent_stem_id: str | None = None,
) -> ContentProvenanceRecordV1 | None:
    """Capture neural_render / neural_stem / neural_stem_rerender. Never invents stub AI ops."""
    if not project_id or not artifact_id:
        logger.warning(
            "Provenance capture soft-skipped",
            extra={"code": "provenance_invalid", "artifact_kind": artifact_kind},
        )
        return None
    parent_ids: list[str] = []
    parent_artifacts: list[dict[str, Any]] = []
    if source_revision_id:
        prior = list_records_for_artifact(
            conn,
            artifact_kind="composition_revision",
            artifact_id=source_revision_id,
            project_id=project_id,
        )
        if prior:
            parent_ids.append(prior[-1].record_id)
        else:
            parent_artifacts.append(
                {
                    "kind": "composition_revision",
                    "id": source_revision_id[:120],
                    "fingerprint_prefix": None,
                }
            )
    if parent_stem_id:
        stem_rows = list_records_for_artifact(
            conn,
            artifact_kind="neural_stem",
            artifact_id=parent_stem_id,
            project_id=project_id,
        )
        if stem_rows:
            parent_ids.append(stem_rows[-1].record_id)
        else:
            parent_artifacts.append(
                {
                    "kind": "neural_stem",
                    "id": parent_stem_id[:120],
                    "fingerprint_prefix": None,
                }
            )
    payload = {
        "schema_version": "content.provenance.record.v1",
        "record_id": new_provenance_record_id(),
        "project_id": project_id,
        "artifact_kind": artifact_kind,
        "artifact_id": artifact_id,
        "artifact_fingerprint_prefix": fingerprint_prefix(fingerprint),
        "operation": operation,
        "actor_kind": "ai",
        "model_id": model_id,
        "model_version": model_version,
        "runtime": runtime,
        "user_action": "commit",
        "parent_record_ids": parent_ids[:8],
        "parent_artifacts": parent_artifacts[:8],
        "trust_class": "mukit_internal",
    }
    return record_provenance_safe(conn, payload)


def capture_recovery_bind_provenance(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    bind_id: str,
    source_sha256: str | None = None,
    revision_id: str | None = None,
) -> ContentProvenanceRecordV1 | None:
    parent_ids: list[str] = []
    parent_artifacts: list[dict[str, Any]] = []
    if revision_id:
        prior = list_records_for_artifact(
            conn,
            artifact_kind="composition_revision",
            artifact_id=revision_id,
            project_id=project_id,
        )
        if prior:
            parent_ids.append(prior[-1].record_id)
        else:
            parent_artifacts.append(
                {
                    "kind": "composition_revision",
                    "id": revision_id[:120],
                    "fingerprint_prefix": None,
                }
            )
    if source_sha256:
        parent_artifacts.append(
            {
                "kind": "external_file",
                "id": f"sha256:{fingerprint_prefix(source_sha256) or 'unknown'}",
                "fingerprint_prefix": fingerprint_prefix(source_sha256),
            }
        )
    payload = {
        "schema_version": "content.provenance.record.v1",
        "record_id": new_provenance_record_id(),
        "project_id": project_id,
        "artifact_kind": "audio_recovery_bind",
        "artifact_id": bind_id,
        "artifact_fingerprint_prefix": fingerprint_prefix(source_sha256),
        "operation": "recovery_bind",
        "actor_kind": "system",
        "user_action": "bind",
        "parent_record_ids": parent_ids[:8],
        "parent_artifacts": parent_artifacts[:8],
        "trust_class": "mukit_internal",
    }
    return record_provenance_safe(conn, payload)


def capture_mix_plan_apply_provenance(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    revision_id: str,
    fingerprint: str | None = None,
    stem_set_id: str | None = None,
    prior_mix_revision_id: str | None = None,
) -> ContentProvenanceRecordV1 | None:
    parent_ids: list[str] = []
    parent_artifacts: list[dict[str, Any]] = []
    if stem_set_id:
        rows = list_records_for_artifact(
            conn,
            artifact_kind="neural_stem_set",
            artifact_id=stem_set_id,
            project_id=project_id,
        )
        if rows:
            parent_ids.append(rows[-1].record_id)
        else:
            parent_artifacts.append(
                {
                    "kind": "neural_stem_set",
                    "id": stem_set_id[:120],
                    "fingerprint_prefix": None,
                }
            )
    if prior_mix_revision_id:
        rows = list_records_for_artifact(
            conn,
            artifact_kind="mix_plan_revision",
            artifact_id=prior_mix_revision_id,
            project_id=project_id,
        )
        if rows:
            parent_ids.append(rows[-1].record_id)
        else:
            parent_artifacts.append(
                {
                    "kind": "mix_plan_revision",
                    "id": prior_mix_revision_id[:120],
                    "fingerprint_prefix": None,
                }
            )
    payload = {
        "schema_version": "content.provenance.record.v1",
        "record_id": new_provenance_record_id(),
        "project_id": project_id,
        "artifact_kind": "mix_plan_revision",
        "artifact_id": revision_id,
        "artifact_fingerprint_prefix": fingerprint_prefix(fingerprint),
        "operation": "mix_plan_apply",
        "actor_kind": "system",
        "user_action": "apply",
        "parent_record_ids": parent_ids[:8],
        "parent_artifacts": parent_artifacts[:8],
        "trust_class": "mukit_internal",
    }
    return record_provenance_safe(conn, payload)
