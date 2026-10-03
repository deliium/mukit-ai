"""Provenance enforcement and train eligibility policy.

Train eligibility delegates to the shared rights-governance evaluator so
``use_policy=reference_only`` never trains even when legacy status is
train-shaped. Rights gates hard-fail; unsafe pollution is an explicit ERROR path.
"""

from __future__ import annotations

import logging
from typing import Iterable

from app.dataset.errors import DatasetProvenanceError
from app.dataset.schemas import (
    TRAIN_ELIGIBLE_STATUSES,
    DatasetEligibilityPolicy,
    DatasetProvenance,
    ProvenanceStatus,
)
from app.rights_governance_schemas import (
    RightsRegistryEntryV1,
    map_legacy_dataset_provenance,
)
from app.services.rights_governance_policy import evaluate_rights_use


logger = logging.getLogger(__name__)


def map_item_rights_entry(
    provenance: DatasetProvenance,
    *,
    use_policy_override: str | None = None,
    source_id: str = "eligibility_check",
    include_non_trainable_in_eval: bool = False,
) -> RightsRegistryEntryV1:
    """Part H map for a dataset provenance (+ optional sidecar use_policy)."""
    return map_legacy_dataset_provenance(
        provenance,
        use_policy_override=use_policy_override,
        source_kind="dataset_item",
        source_id=source_id,
        include_non_trainable_in_eval=include_non_trainable_in_eval,
    )


def compute_train_eligible(
    provenance: DatasetProvenance,
    *,
    eligibility: DatasetEligibilityPolicy | None = None,
    use_policy_override: str | None = None,
    source_id: str = "eligibility_check",
) -> bool:
    """True only when shared evaluator allows ``train`` (and legacy status allowlist).

    ``allow_unsafe_train_pollution`` does not flip eligibility here — it only
    permits an already-assembled train split to keep non-eligible rows when
    ``assert_train_split_policy`` runs (ERROR log).
    """
    policy = eligibility or DatasetEligibilityPolicy()
    allowed_statuses = set(policy.train_statuses) or set(TRAIN_ELIGIBLE_STATUSES)
    entry = map_item_rights_entry(
        provenance,
        use_policy_override=use_policy_override,
        source_id=source_id,
        include_non_trainable_in_eval=policy.include_non_trainable_in_eval,
    )
    result = evaluate_rights_use(entry, "train")
    eligible = bool(result.allowed) and provenance.status in allowed_statuses
    logger.debug(
        "Train eligibility evaluated",
        extra={
            "source_id_prefix": source_id[:12],
            "legacy_status": provenance.status,
            "use_policy": entry.use_policy,
            "train_eligible": eligible,
            "refuse_code": result.code,
        },
    )
    return eligible


def assert_train_split_policy(
    train_entries: Iterable[RightsRegistryEntryV1] | list[ProvenanceStatus],
    *,
    eligibility: DatasetEligibilityPolicy,
) -> None:
    """Hard-fail when non-train-eligible entries would pollute train without override.

    Accepts either mapped ``RightsRegistryEntryV1`` rows (preferred) or a legacy
    list of ``ProvenanceStatus`` for backward-compatible unit tests.
    """
    entries: list[RightsRegistryEntryV1] = []
    statuses: list[str] = []
    for item in train_entries:
        if isinstance(item, RightsRegistryEntryV1):
            entries.append(item)
            if item.legacy_status:
                statuses.append(item.legacy_status)
            else:
                statuses.append(item.use_policy)
        else:
            statuses.append(str(item))

    unsafe_codes: list[str] = []
    if entries:
        for entry in entries:
            result = evaluate_rights_use(entry, "train")
            if not result.allowed:
                unsafe_codes.append(entry.use_policy)
    else:
        for status in statuses:
            if status not in TRAIN_ELIGIBLE_STATUSES:
                unsafe_codes.append(status)

    if not unsafe_codes:
        return
    if eligibility.allow_unsafe_train_pollution:
        logger.error(
            "Unsafe train pollution override enabled",
            extra={
                "unsafe_statuses": sorted(set(unsafe_codes)),
                "allow_unsafe_train_pollution": True,
            },
        )
        return
    logger.error(
        "Attempted train pollution with non-eligible provenance",
        extra={"unsafe_statuses": sorted(set(unsafe_codes))},
    )
    raise DatasetProvenanceError(
        "train_pollution_blocked",
        "unknown/restricted/reference_only items cannot enter the train split",
        details={"unsafe_statuses": sorted(set(unsafe_codes))},
    )


def summarize_eligibility(items: list[tuple[str, bool]]) -> dict[str, int]:
    """items: list of (provenance_status, train_eligible)."""
    by_status: dict[str, int] = {}
    eligible = 0
    for status, is_eligible in items:
        by_status[status] = by_status.get(status, 0) + 1
        if is_eligible:
            eligible += 1
    logger.info(
        "Provenance eligibility summary",
        extra={
            "eligibility_by_status": by_status,
            "train_eligible_count": eligible,
            "excluded_count": len(items) - eligible,
        },
    )
    return by_status


def summarize_eligibility_by_use_policy(
    items: list[tuple[str, bool]],
) -> dict[str, int]:
    """items: list of (use_policy, train_eligible)."""
    by_policy: dict[str, int] = {}
    eligible = 0
    for use_policy, is_eligible in items:
        by_policy[use_policy] = by_policy.get(use_policy, 0) + 1
        if is_eligible:
            eligible += 1
    logger.info(
        "Rights use_policy eligibility summary",
        extra={
            "eligibility_by_use_policy": by_policy,
            "train_eligible_count": eligible,
            "excluded_count": len(items) - eligible,
        },
    )
    return by_policy
