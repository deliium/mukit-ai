"""Assemble content.provenance.chain.v1 and content.provenance.manifest.v1."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from typing import Any

from app.content_provenance_schemas import (
    ContentProvenanceChainV1,
    ContentProvenanceError,
    ContentProvenanceManifestV1,
    ContentProvenanceRecordV1,
    ProvenanceChainDiagnosticV1,
    ProvenanceHonestyV1,
    ProvenanceC2paHonestyV1,
    compute_honesty_cryptographic,
    parse_content_provenance_manifest,
    reject_embedded_note_material,
)
from app.content_provenance_settings import load_content_provenance_settings
from app.services.content_provenance_store import (
    list_records_for_artifact,
    walk_parent_records,
)
from app.services.persistence_secret_guard import (
    assert_no_secret_fields,
    assert_payload_has_no_secret_values,
)

logger = logging.getLogger(__name__)


def _unavailable_stub(
    *,
    project_id: str,
    kind: str,
    artifact_id: str,
    fingerprint_prefix: str | None,
) -> ContentProvenanceRecordV1:
    from app.services.content_provenance_capture import new_provenance_record_id

    # Synthetic display-only node for missing parents — not persisted.
    return ContentProvenanceRecordV1.model_validate(
        {
            "schema_version": "content.provenance.record.v1",
            "record_id": new_provenance_record_id(),
            "project_id": project_id,
            "artifact_kind": kind,
            "artifact_id": artifact_id,
            "artifact_fingerprint_prefix": fingerprint_prefix,
            "operation": "human_edit",
            "actor_kind": "system",
            "parent_record_ids": [],
            "parent_artifacts": [],
            "trust_class": "unavailable",
        }
    )


def assemble_provenance_chain(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    artifact_kind: str,
    artifact_id: str,
) -> ContentProvenanceChainV1:
    """Walk parents from leaf records toward roots."""
    settings = load_content_provenance_settings()
    leaf_rows = list_records_for_artifact(
        conn,
        artifact_kind=artifact_kind,
        artifact_id=artifact_id,
        project_id=project_id,
    )
    diagnostics: list[ProvenanceChainDiagnosticV1] = []
    if not leaf_rows:
        raise ContentProvenanceError(
            "provenance_not_found",
            "Provenance record or artifact was not found.",
            http_status=404,
            details={"artifact_kind": artifact_kind, "artifact_id": artifact_id},
        )
    walked = walk_parent_records(conn, leaf_rows, max_depth=settings.chain_max_depth)
    # Surface missing parent_artifacts as unavailable stubs (capped).
    seen_ids = {record.record_id for record in walked}
    extras: list[ContentProvenanceRecordV1] = []
    for record in list(walked):
        for parent_id in record.parent_record_ids:
            if parent_id not in seen_ids:
                diagnostics.append(
                    ProvenanceChainDiagnosticV1(
                        code="parent_missing",
                        message="Parent record id not found",
                        record_id=parent_id,
                    )
                )
        for parent in record.parent_artifacts:
            stub_key = f"{parent.kind}:{parent.id}"
            if any(
                f"{item.artifact_kind}:{item.artifact_id}" == stub_key for item in walked + extras
            ):
                continue
            if parent.fingerprint_prefix and record.artifact_fingerprint_prefix:
                # Soft diagnostic only when both present and diverge meaningfully —
                # missing parent fingerprint never invents a mismatch.
                pass
            extras.append(
                _unavailable_stub(
                    project_id=project_id,
                    kind=parent.kind,
                    artifact_id=parent.id,
                    fingerprint_prefix=parent.fingerprint_prefix,
                )
            )
    records = (walked + extras)[: settings.manifest_max_records]
    chain = ContentProvenanceChainV1(
        project_id=project_id,
        leaf_artifact_kind=artifact_kind,  # type: ignore[arg-type]
        leaf_artifact_id=artifact_id,
        records=records,
        diagnostics=diagnostics[:32],
    )
    logger.info(
        "Provenance chain assembled",
        extra={
            "project_id": project_id,
            "leaf_artifact_kind": artifact_kind,
            "leaf_artifact_id_prefix": artifact_id[:12],
            "record_count": len(records),
        },
    )
    return chain


def _manifest_digest_prefix(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:40]


def assemble_provenance_manifest(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    artifact_kind: str,
    artifact_id: str,
    c2pa_attached: bool = False,
    c2pa_fake_mode: bool = False,
    c2pa_status: str | None = None,
) -> ContentProvenanceManifestV1:
    """Exportable manifest with Part K honesty block."""
    chain = assemble_provenance_chain(
        conn,
        project_id=project_id,
        artifact_kind=artifact_kind,
        artifact_id=artifact_id,
    )
    leaf_prefix = None
    if chain.records:
        leaf_prefix = chain.records[0].artifact_fingerprint_prefix
    cryptographic = compute_honesty_cryptographic(
        c2pa_attached=c2pa_attached,
        c2pa_fake_mode=c2pa_fake_mode,
        records=chain.records,
    )
    honesty = ProvenanceHonestyV1(
        cryptographic=cryptographic,
        c2pa=ProvenanceC2paHonestyV1(
            attached=c2pa_attached,
            fake_mode=c2pa_fake_mode,
            status=c2pa_status,
        ),
    )
    draft: dict[str, Any] = {
        "schema_version": "content.provenance.manifest.v1",
        "project_id": project_id,
        "leaf_artifact_kind": artifact_kind,
        "leaf_artifact_id": artifact_id,
        "leaf_fingerprint_prefix": leaf_prefix,
        "records": [record.model_dump(mode="json") for record in chain.records],
        "honesty": honesty.model_dump(mode="json"),
        "diagnostics": [item.model_dump(mode="json") for item in chain.diagnostics],
    }
    reject_embedded_note_material(draft, model="ContentProvenanceManifestV1")
    assert_no_secret_fields(draft, context="content_provenance_manifest")
    assert_payload_has_no_secret_values(draft, context="content_provenance_manifest")
    digest = _manifest_digest_prefix(
        {key: draft[key] for key in ("project_id", "leaf_artifact_kind", "leaf_artifact_id", "records", "honesty")}
    )
    draft["manifest_digest_prefix"] = digest
    manifest = parse_content_provenance_manifest(draft)
    logger.info(
        "Provenance manifest assembled",
        extra={
            "project_id": project_id,
            "leaf_artifact_kind": artifact_kind,
            "leaf_artifact_id_prefix": artifact_id[:12],
            "record_count": len(manifest.records),
            "cryptographic": manifest.honesty.cryptographic,
            "manifest_digest_prefix": digest[:12],
        },
    )
    logger.debug(
        "Provenance manifest digest",
        extra={"manifest_digest_prefix": digest},
    )
    return manifest
