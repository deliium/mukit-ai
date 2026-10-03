"""Pure evaluator and Part K resolve matrix."""

from __future__ import annotations

from app.dataset.schemas import DatasetProvenance
from app.rights_governance_schemas import (
    RightsRegistryEntryV1,
    map_legacy_dataset_provenance,
    project_allowed_uses,
)
from app.services.rights_governance_policy import (
    evaluate_rights_use,
    resolve_rights_entry,
)


def _entry(
    *,
    use_policy: str = "training_allowed",
    ownership_class: str = "licensed",
    verification_status: str = "verified",
    license: str | None = "CC0-1.0",
    source_id: str = "proj_a",
) -> RightsRegistryEntryV1:
    return RightsRegistryEntryV1(
        entry_id="rights_" + "d4" * 8,
        source_kind="project",
        source_id=source_id,
        ownership_class=ownership_class,  # type: ignore[arg-type]
        use_policy=use_policy,  # type: ignore[arg-type]
        allowed_uses=project_allowed_uses(use_policy),  # type: ignore[arg-type]
        license=license,
        license_spdx=license,
        verification_status=verification_status,  # type: ignore[arg-type]
    )


def test_train_and_reference_matrix() -> None:
    train_ok = _entry(use_policy="training_allowed")
    assert evaluate_rights_use(train_ok, "train").allowed is True
    assert evaluate_rights_use(train_ok, "reference_analyze").allowed is True

    ref_only = _entry(use_policy="reference_only", license=None)
    assert evaluate_rights_use(ref_only, "reference_analyze").allowed is True
    refused = evaluate_rights_use(ref_only, "train")
    assert refused.allowed is False
    assert refused.code == "rights_train_refused"

    no_train = _entry(use_policy="no_training", ownership_class="unknown", license=None)
    assert evaluate_rights_use(no_train, "train").allowed is False
    assert evaluate_rights_use(no_train, "reference_analyze").code == "rights_reference_refused"


def test_disputed_refuses_both() -> None:
    disputed = _entry(verification_status="disputed")
    assert evaluate_rights_use(disputed, "train").code == "rights_disputed"
    assert evaluate_rights_use(disputed, "reference_analyze").code == "rights_disputed"


def test_user_owned_requires_attested_or_verified_for_train() -> None:
    unattested = _entry(
        ownership_class="user_owned",
        use_policy="training_allowed",
        verification_status="unverified",
        license=None,
    )
    assert evaluate_rights_use(unattested, "train").allowed is False
    attested = _entry(
        ownership_class="user_owned",
        use_policy="training_allowed",
        verification_status="attested",
        license=None,
    )
    assert evaluate_rights_use(attested, "train").allowed is True


def test_resolve_registry_wins_over_request() -> None:
    registry = _entry(use_policy="reference_only", license=None)
    request = DatasetProvenance(
        status="user_owned",
        user_owned_attested=True,
    )
    entry, resolution = resolve_rights_entry(
        "project",
        "proj_a",
        registry_row=registry,
        request_rights=request,
    )
    assert resolution == "registry"
    assert entry.use_policy == "reference_only"
    assert evaluate_rights_use(entry, "train").allowed is False


def test_resolve_attestation_fallback() -> None:
    request = DatasetProvenance(
        status="user_owned",
        user_owned_attested=True,
    )
    entry, resolution = resolve_rights_entry(
        "project",
        "proj_b",
        registry_row=None,
        request_rights=request,
    )
    assert resolution == "request_attestation"
    assert entry.use_policy == "training_allowed"
    assert evaluate_rights_use(entry, "train").allowed is True
    assert evaluate_rights_use(entry, "reference_analyze").allowed is True


def test_resolve_missing_synthetic_refuse() -> None:
    entry, resolution = resolve_rights_entry(
        "project",
        "proj_missing",
        registry_row=None,
        request_rights=None,
    )
    assert resolution == "synthetic_unknown"
    assert entry.ownership_class == "unknown"
    assert entry.use_policy == "no_training"
    assert evaluate_rights_use(entry, "train").allowed is False
    assert evaluate_rights_use(entry, "reference_analyze").allowed is False


def test_map_then_evaluate_reference_only_override() -> None:
    provenance = DatasetProvenance(
        status="verified_redistributable",
        license="CC-BY-4.0",
        license_spdx="CC-BY-4.0",
        source_reference="corpus/x",
    )
    mapped = map_legacy_dataset_provenance(
        provenance,
        use_policy_override="reference_only",
        source_id="item_x",
        source_kind="dataset_item",
    )
    assert evaluate_rights_use(mapped, "reference_analyze").allowed is True
    assert evaluate_rights_use(mapped, "train").allowed is False
