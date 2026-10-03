"""Schema tests for rights.registry and model.data.provenance documents."""

from __future__ import annotations

import logging

import pytest

from app.dataset.schemas import DatasetProvenance
from app.rights_governance_schemas import (
    FORBIDDEN_NOTE_KEYS,
    RightsGovernanceError,
    map_legacy_dataset_provenance,
    map_legacy_status_fields,
    parse_model_data_provenance_manifest,
    parse_rights_registry_entry,
    project_allowed_uses,
    rights_digest_prefix,
)


def _entry(**overrides: object) -> dict:
    payload: dict[str, object] = {
        "schema_version": "rights.registry.entry.v1",
        "entry_id": "rights_" + "a1" * 8,
        "source_kind": "project",
        "source_id": "proj_demo",
        "ownership_class": "licensed",
        "use_policy": "reference_only",
        "allowed_uses": ["reference_analyze"],
        "license": "CC-BY-4.0",
        "license_spdx": "CC-BY-4.0",
        "verification_status": "verified",
        "created_at": "2026-10-03T00:00:00Z",
        "updated_at": "2026-10-03T00:00:00Z",
        "entry_version": 1,
    }
    payload.update(overrides)
    return payload


def test_reference_only_allowed_uses_exclude_train() -> None:
    uses = project_allowed_uses("reference_only")
    assert uses == ["reference_analyze"]
    assert "train" not in uses
    entry = parse_rights_registry_entry(_entry())
    assert entry.use_policy == "reference_only"
    assert entry.allowed_uses == ["reference_analyze"]
    assert "train" not in entry.allowed_uses
    assert entry.rights_digest
    assert len(rights_digest_prefix(entry.rights_digest)) == 40


def test_licensed_training_allowed_requires_license() -> None:
    with pytest.raises(RightsGovernanceError) as raised:
        parse_rights_registry_entry(
            _entry(
                use_policy="training_allowed",
                allowed_uses=["train", "reference_analyze", "eval_holdout"],
                license=None,
                license_spdx=None,
            )
        )
    assert raised.value.code == "rights_license_required"


def test_licensed_reference_only_may_omit_license() -> None:
    entry = parse_rights_registry_entry(
        _entry(
            use_policy="reference_only",
            allowed_uses=["reference_analyze"],
            license=None,
            license_spdx=None,
        )
    )
    assert entry.use_policy == "reference_only"
    assert entry.license is None
    assert entry.license_spdx is None


def test_rejects_embedded_events(caplog: pytest.LogCaptureFixture) -> None:
    with_events = _entry()
    with_events["events"] = [{"pitch": "C4"}]
    with caplog.at_level(logging.DEBUG, logger="app.rights_governance_schemas"):
        with pytest.raises(RightsGovernanceError) as raised:
            parse_rights_registry_entry(with_events)
    assert raised.value.code == "embedded_note_material"
    assert raised.value.http_status == 422
    assert any(
        getattr(record, "model", None) == "RightsRegistryEntryV1"
        and getattr(record, "code", None) == "embedded_note_material"
        for record in caplog.records
        if record.name == "app.rights_governance_schemas"
    )


def test_manifest_refuses_embedded_events() -> None:
    payload = {
        "schema_version": "model.data.provenance.manifest.v1",
        "manifest_kind": "personal_adapter_train",
        "subject_id": "adapter_1",
        "sources": [
            {
                "entry_id": "rights_" + "b2" * 8,
                "source_kind": "project",
                "source_id": "proj_a",
                "ownership_class": "user_owned",
                "use_policy": "training_allowed",
                "rights_digest_prefix": "ab" * 12,
            }
        ],
        "excluded_source_counts_by_use_policy": {"reference_only": 1},
        "created_at": "2026-10-03T00:00:00Z",
        "events": [{"pitch": 60}],
    }
    with pytest.raises(RightsGovernanceError) as raised:
        parse_model_data_provenance_manifest(payload)
    assert raised.value.code == "embedded_note_material"
    for key in FORBIDDEN_NOTE_KEYS:
        assert key in FORBIDDEN_NOTE_KEYS


def test_manifest_accepts_train_sources_only() -> None:
    manifest = parse_model_data_provenance_manifest(
        {
            "schema_version": "model.data.provenance.manifest.v1",
            "manifest_kind": "dataset_train_split",
            "subject_id": "ds_v1",
            "sources": [
                {
                    "entry_id": "rights_" + "c3" * 8,
                    "source_kind": "dataset_item",
                    "source_id": "item_1",
                    "ownership_class": "public_domain",
                    "use_policy": "training_allowed",
                    "rights_digest_prefix": "cd" * 12,
                }
            ],
            "excluded_source_counts_by_use_policy": {
                "reference_only": 1,
                "no_training": 2,
            },
            "created_at": "2026-10-03T00:00:00Z",
        }
    )
    assert manifest.manifest_kind == "dataset_train_split"
    assert len(manifest.sources) == 1
    assert manifest.sources[0].use_policy == "training_allowed"
    assert manifest.manifest_digest_prefix


def test_map_legacy_verified_redistributable() -> None:
    provenance = DatasetProvenance(
        status="verified_redistributable",
        license="CC0-1.0",
        license_spdx="CC0-1.0",
        source_url="https://example.test/score",
    )
    entry = map_legacy_dataset_provenance(
        provenance,
        source_kind="dataset_item",
        source_id="item_licensed",
    )
    assert entry.ownership_class == "licensed"
    assert entry.use_policy == "training_allowed"
    assert entry.verification_status == "verified"
    assert "train" in entry.allowed_uses


def test_map_legacy_use_policy_override_reference_only() -> None:
    provenance = DatasetProvenance(
        status="verified_redistributable",
        license="CC-BY-4.0",
        license_spdx="CC-BY-4.0",
        source_reference="corpus/licensed",
    )
    entry = map_legacy_dataset_provenance(
        provenance,
        use_policy_override="reference_only",
        source_kind="dataset_item",
        source_id="item_ref_only",
    )
    assert entry.use_policy == "reference_only"
    assert entry.ownership_class == "licensed"
    assert entry.allowed_uses == ["reference_analyze"]
    assert "train" not in entry.allowed_uses


def test_map_legacy_user_owned_attested() -> None:
    provenance = DatasetProvenance(
        status="user_owned",
        user_owned_attested=True,
    )
    entry = map_legacy_dataset_provenance(
        provenance,
        source_kind="project",
        source_id="proj_mine",
    )
    assert entry.ownership_class == "user_owned"
    assert entry.use_policy == "training_allowed"
    assert entry.verification_status == "attested"


def test_map_legacy_user_owned_unattested_via_fields() -> None:
    entry = map_legacy_status_fields(
        status="user_owned",
        user_owned_attested=False,
        source_kind="project",
        source_id="proj_unattested",
    )
    assert entry.ownership_class == "user_owned"
    assert entry.use_policy == "no_training"
    assert entry.verification_status == "unverified"
    assert "train" not in entry.allowed_uses


def test_map_legacy_unknown_and_alias_use_policy() -> None:
    entry = map_legacy_status_fields(
        status="unknown",
        source_kind="project",
        source_id="proj_unknown",
    )
    assert entry.ownership_class == "unknown"
    assert entry.use_policy == "no_training"

    aliased = map_legacy_status_fields(
        status="reference_only",
        source_kind="external_file",
        source_id="ext_1",
    )
    assert aliased.use_policy == "reference_only"
    assert aliased.ownership_class == "unknown"


def test_unknown_use_policy_override_refused() -> None:
    provenance = DatasetProvenance(
        status="public_domain",
        source_url="https://example.test/pd",
    )
    with pytest.raises(RightsGovernanceError) as raised:
        map_legacy_dataset_provenance(
            provenance,
            use_policy_override="invented_policy",
            source_id="proj_bad",
        )
    assert raised.value.code == "rights_invalid"


def test_no_training_allowed_uses_empty_by_default() -> None:
    assert project_allowed_uses("no_training") == []
    assert project_allowed_uses("no_training", include_non_trainable_in_eval=True) == [
        "eval_holdout"
    ]
