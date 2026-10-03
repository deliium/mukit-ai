"""Schema tests for content.provenance documents."""

from __future__ import annotations

import logging

import pytest

from app.content_provenance_schemas import (
    FORBIDDEN_NOTE_KEYS,
    ContentProvenanceError,
    compute_honesty_cryptographic,
    parse_content_credentials_status,
    parse_content_provenance_manifest,
    parse_content_provenance_record,
)

_RECORD = "cprov_" + "a1" * 8
_PARENT = "cprov_" + "b2" * 8


def _ai_record(**overrides: object) -> dict:
    payload: dict[str, object] = {
        "schema_version": "content.provenance.record.v1",
        "record_id": _RECORD,
        "project_id": "proj_demo",
        "artifact_kind": "composition_revision",
        "artifact_id": "rev_abc",
        "artifact_fingerprint_prefix": "ab" * 8,
        "operation": "ai_generate",
        "model_id": "fake:llm",
        "model_version": "fake-v1",
        "runtime": "fake",
        "actor_kind": "ai",
        "parent_record_ids": [],
        "parent_artifacts": [
            {
                "kind": "composition_revision",
                "id": "rev_prior",
                "fingerprint_prefix": "cd" * 8,
            }
        ],
        "trust_class": "mukit_internal",
        "created_at": "2026-10-03T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_accepts_minimal_ai_generate_record() -> None:
    record = parse_content_provenance_record(_ai_record())
    assert record.operation == "ai_generate"
    assert record.trust_class == "mukit_internal"
    assert len(record.parent_artifacts) == 1
    assert record.parent_artifacts[0].kind == "composition_revision"


def test_manifest_honesty_cryptographic_false_by_default() -> None:
    record = parse_content_provenance_record(_ai_record())
    manifest = parse_content_provenance_manifest(
        {
            "schema_version": "content.provenance.manifest.v1",
            "project_id": "proj_demo",
            "leaf_artifact_kind": "composition_revision",
            "leaf_artifact_id": "rev_abc",
            "records": [record.model_dump(mode="json")],
            "honesty": {
                "cryptographic": False,
                "c2pa": {"attached": False, "fake_mode": False},
            },
            "assembled_at": "2026-10-03T00:00:00Z",
        }
    )
    assert manifest.honesty.cryptographic is False
    encoded = manifest.model_dump_json()
    for key in FORBIDDEN_NOTE_KEYS:
        assert f'"{key}"' not in encoded


def test_rejects_embedded_events(caplog: pytest.LogCaptureFixture) -> None:
    with_events = _ai_record()
    with_events["events"] = [{"pitch": "C4"}]
    with caplog.at_level(logging.DEBUG, logger="app.content_provenance_schemas"):
        with pytest.raises(ContentProvenanceError) as raised:
            parse_content_provenance_record(with_events)
    assert raised.value.code == "embedded_note_material"
    assert raised.value.http_status == 422
    assert any(
        getattr(record, "model", None) == "ContentProvenanceRecordV1"
        and getattr(record, "code", None) == "embedded_note_material"
        for record in caplog.records
        if record.name == "app.content_provenance_schemas"
    )


def test_rejects_parent_record_ids_over_cap() -> None:
    parents = [f"cprov_{i:016x}" for i in range(9)]
    with pytest.raises(ContentProvenanceError) as raised:
        parse_content_provenance_record(_ai_record(parent_record_ids=parents))
    assert raised.value.code == "provenance_parent_cap"


def test_rejects_unknown_operation() -> None:
    with pytest.raises(ContentProvenanceError) as raised:
        parse_content_provenance_record(_ai_record(operation="invented_op"))
    assert raised.value.code == "provenance_invalid"


def test_honesty_formula_refuses_cryptographic_under_fake_mode() -> None:
    signed = parse_content_provenance_record(_ai_record(trust_class="c2pa_signed"))
    with pytest.raises(ContentProvenanceError) as raised:
        parse_content_provenance_manifest(
            {
                "schema_version": "content.provenance.manifest.v1",
                "project_id": "proj_demo",
                "leaf_artifact_kind": "neural_render",
                "leaf_artifact_id": "nar_1",
                "records": [signed.model_dump(mode="json")],
                "honesty": {
                    "cryptographic": True,
                    "c2pa": {"attached": True, "fake_mode": True},
                },
                "assembled_at": "2026-10-03T00:00:00Z",
            }
        )
    assert raised.value.code == "provenance_honesty_invalid"


def test_c2pa_signed_record_under_fake_mode_keeps_cryptographic_false() -> None:
    signed = parse_content_provenance_record(_ai_record(trust_class="c2pa_signed"))
    expected = compute_honesty_cryptographic(
        c2pa_attached=True,
        c2pa_fake_mode=True,
        records=[signed],
    )
    assert expected is False
    manifest = parse_content_provenance_manifest(
        {
            "schema_version": "content.provenance.manifest.v1",
            "project_id": "proj_demo",
            "leaf_artifact_kind": "neural_render",
            "leaf_artifact_id": "nar_1",
            "records": [signed.model_dump(mode="json")],
            "honesty": {
                "cryptographic": False,
                "c2pa": {"attached": True, "fake_mode": True, "status": "fake"},
            },
            "assembled_at": "2026-10-03T00:00:00Z",
        }
    )
    assert manifest.honesty.cryptographic is False


def test_credentials_status_may_include_fake_mode() -> None:
    status = parse_content_credentials_status(
        {
            "schema_version": "content.credentials.status.v1",
            "enabled": True,
            "fake_mode": True,
            "attached": True,
            "status": "fake",
            "artifact_kind": "neural_render",
            "artifact_id": "nar_1",
        }
    )
    assert status.fake_mode is True
    assert status.attached is True


def test_real_attach_with_c2pa_signed_allows_cryptographic_true() -> None:
    signed = parse_content_provenance_record(_ai_record(trust_class="c2pa_signed"))
    manifest = parse_content_provenance_manifest(
        {
            "schema_version": "content.provenance.manifest.v1",
            "project_id": "proj_demo",
            "leaf_artifact_kind": "neural_render",
            "leaf_artifact_id": "nar_1",
            "records": [signed.model_dump(mode="json")],
            "honesty": {
                "cryptographic": True,
                "c2pa": {"attached": True, "fake_mode": False, "status": "attached"},
            },
            "assembled_at": "2026-10-03T00:00:00Z",
        }
    )
    assert manifest.honesty.cryptographic is True


def test_rejects_self_parent() -> None:
    with pytest.raises(ContentProvenanceError) as raised:
        parse_content_provenance_record(_ai_record(parent_record_ids=[_RECORD]))
    assert raised.value.code == "provenance_cycle"


def test_rejects_download_manifest_user_action() -> None:
    with pytest.raises(ContentProvenanceError) as raised:
        parse_content_provenance_record(_ai_record(user_action="download_manifest"))
    assert raised.value.code == "provenance_invalid"
